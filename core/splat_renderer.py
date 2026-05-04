# -*- coding: utf-8 -*-
"""
core/splat_renderer.py

Converts a georeferenced GaussianSplatScene into a raster image that can
be loaded into QGIS as a QgsRasterLayer (GeoTIFF on disk).

Rendering strategy — "splat projection":
  1. Transform 3D gaussian centres to world XY (using GeoReference).
  2. Re-map scene axes to geographic East / North / Elevation using the
     caller-supplied axis_mapping dict.
  3. Project each gaussian's covariance ellipsoid to a 2D screen-space
     ellipse (top-down: East × North plane).
  4. Splat each gaussian as an alpha-composited 2D gaussian blob on the
     output raster using its projected colour, opacity and 2D covariance.

This is a CPU-side approximation. For large scenes (>500k gaussians)
a tiled / downsampled pass is used automatically.
"""

import math
import os
import numpy as np
from typing import Optional, Tuple, Callable, Dict

from .splat_scene import GaussianSplatScene


# ---------------------------------------------------------------------------
# Default axis mapping — matches the classic 3DGS (COLMAP / LichtFeld Studio) layout:
#   scene axis 0 (X) negated  → East
#   scene axis 2 (Z)          → North
#   scene axis 1 (Y)          → Elevation  (Y-up)
# ---------------------------------------------------------------------------

# axis_mapping describes SCENE-LOCAL axes (before georeference).
# world_positions() uses this to remap scene columns to [Easting, Northing, Elevation]
# BEFORE applying the origin translation, so Northing always comes from the correct
# scene axis and is then correctly offset by geo.origin_geo.y().
#
# Values are (scene_col, sign):
#   scene_col : 0 = scene X, 1 = scene Y, 2 = scene Z
#   sign      : +1 or -1
#
# Default = Z-up (Colmap): scene X→East, scene Y→North, scene Z→Elevation.
# For Y-up (OpenGL/most 3D editors): east=(0,1), north=(2,1), elevation=(1,1).

DEFAULT_AXIS_MAPPING: Dict[str, Tuple[int, int]] = {
    "east":      (0,  1),   # scene X → Easting
    "north":     (1,  1),   # scene Y → Northing   (Z-up default)
    "elevation": (2,  1),   # scene Z → Elevation
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_to_geotiff(
    scene: GaussianSplatScene,
    output_path: str,
    resolution: float = 0.2,            # metres per pixel (in CRS units)
    max_gaussians: int = 500_000,        # cap for performance
    axis_mapping: Optional[Dict[str, Tuple[int, int]]] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> Tuple[str, dict]:
    """
    Render a georeferenced GaussianSplatScene to a GeoTIFF (top-down plan view).

    :param scene:          Loaded and georeferenced scene.
    :param output_path:    Destination .tif path.
    :param resolution:     Pixel size in CRS units (metres for projected CRS).
    :param max_gaussians:  Subsample if scene exceeds this count.
    :param axis_mapping:   Dict with keys 'east', 'north', 'elevation'.
                           Each value is a (col_index, sign) tuple that
                           selects and optionally negates one column of the
                           world_positions() output:
                             col 0 = geographic Easting  (CRS X)
                             col 1 = geographic Northing (CRS Y)
                             col 2 = Elevation
                           Defaults to DEFAULT_AXIS_MAPPING (identity).
    :param progress_callback: Optional callable(percent: int).
    :returns: (output_path, geotransform_dict)
    :raises RuntimeError: If scene is not georeferenced.
    """
    if not scene.is_georeferenced:
        raise RuntimeError(
            "Scene must be georeferenced before rendering. "
            "Use GeoreferenceDialog or scene.georeference."
        )

    if axis_mapping is None:
        axis_mapping = DEFAULT_AXIS_MAPPING

    # Pass axis_mapping into world_positions() so the scene-axis → geographic
    # remapping happens BEFORE the georeference origin is added.
    # After this call, world_pts columns are always:
    #   0 = Easting  (geo X + origin.x)
    #   1 = Northing (geo Y + origin.y)   ← correctly offset by georeference
    #   2 = Elevation (+ z_offset)
    world_pts = scene.world_positions(axis_mapping=axis_mapping)
    if world_pts is None:
        raise RuntimeError("world_positions() returned None.")

    colors    = scene.colors    if scene.colors    is not None else np.ones((scene.count, 3), np.float32) * 0.7
    opacities = scene.opacities if scene.opacities is not None else np.ones(scene.count, np.float32)
    scales    = scene.scales    if scene.scales    is not None else np.ones((scene.count, 3), np.float32) * 0.1

    # Subsample if too large
    n = len(world_pts)
    if n > max_gaussians:
        idx = np.random.choice(n, max_gaussians, replace=False)
        world_pts = world_pts[idx]
        colors    = colors[idx]
        opacities = opacities[idx]
        scales    = scales[idx]
        n = max_gaussians

    if progress_callback:
        progress_callback(10)

    # -----------------------------------------------------------------------
    # world_pts is now fully georeferenced with axes already remapped by
    # world_positions().  Columns are guaranteed to be:
    #   0 = Easting  (CRS X, origin-corrected)
    #   1 = Northing (CRS Y, origin-corrected)  ← always geographic Y
    #   2 = Elevation (z_offset applied)
    # Read directly — no further axis indexing needed.
    # -----------------------------------------------------------------------

    easting   = world_pts[:, 0]
    northing  = world_pts[:, 1]
    elevation = world_pts[:, 2]

    # Screen coords: X = East, Y = North (top-down plan view)
    px, py   = easting, northing
    sort_key = elevation   # back-to-front depth sort

    # Blob radius from the two horizontal scale axes (scene X and Y after remap).
    # After axis remapping, the elevation scene-col maps to world col 2.
    # We use the original scene elevation col to exclude it from the screen scale.
    _elev_scene_col = axis_mapping["elevation"][0]
    _screen_axes = [i for i in range(3) if i != _elev_scene_col]
    screen_scale = np.exp(
        np.maximum(scales[:, _screen_axes[0]], scales[:, _screen_axes[1]])
    )

    if progress_callback:
        progress_callback(20)

    # --- Build raster extent ---
    padding = float(np.percentile(screen_scale, 95)) * scene.georeference.scale
    x_min, x_max = px.min() - padding, px.max() + padding
    y_min, y_max = py.min() - padding, py.max() + padding

    width  = max(1, int(math.ceil((x_max - x_min) / resolution)))
    height = max(1, int(math.ceil((y_max - y_min) / resolution)))

    # Cap raster size to 8192×8192
    if width > 8192 or height > 8192:
        scale_down = max(width / 8192, height / 8192)
        resolution = resolution * scale_down
        width  = max(1, int(width  / scale_down))
        height = max(1, int(height / scale_down))

    # RGBA accumulation buffers
    acc_rgb   = np.zeros((height, width, 3), dtype=np.float64)
    acc_alpha = np.zeros((height, width),    dtype=np.float64)

    # --- Pixel coordinates ---
    col     = ((px - x_min) / resolution).astype(np.float32)
    row     = ((y_max - py) / resolution).astype(np.float32)  # flip Y: top = max py
    sigma_px = (screen_scale * scene.georeference.scale / resolution).clip(0.5, 30.0)

    order = np.argsort(sort_key)   # back-to-front

    chunk       = 1000
    total_chunks = math.ceil(n / chunk)
    for ci, start in enumerate(range(0, n, chunk)):
        end = min(start + chunk, n)
        idx = order[start:end]
        _splat_chunk(
            acc_rgb, acc_alpha,
            col[idx], row[idx],
            colors[idx], opacities[idx], sigma_px[idx],
            width, height,
        )
        if progress_callback and ci % 10 == 0:
            pct = 20 + int(70 * ci / total_chunks)
            progress_callback(pct)

    if progress_callback:
        progress_callback(90)

    # --- Composite to RGBA uint8 ---
    safe_alpha = np.where(acc_alpha > 0, acc_alpha, 1.0)
    rgb_out    = np.clip(acc_rgb / safe_alpha[..., None], 0, 1)
    alpha_out  = np.clip(acc_alpha, 0, 1)
    rgba_u8    = (np.dstack([rgb_out, alpha_out[..., None]]) * 255).astype(np.uint8)

    # --- Write GeoTIFF + world file ---
    world_file_path = _write_geotiff(output_path, rgba_u8, x_min, y_max, resolution, scene)

    if progress_callback:
        progress_callback(100)

    geotransform = {
        "x_min": x_min, "y_max": y_max,
        "resolution": resolution,
        "width": width, "height": height,
        "crs_wkt": scene.georeference.crs.toWkt(),
        "world_file": world_file_path,
    }
    return output_path, geotransform


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _splat_chunk(acc_rgb, acc_alpha, col, row, colors, opacities, sigmas,
                 width, height):
    """
    Rasterise a chunk of gaussians into the accumulation buffers.
    Uses a kernel radius of 3σ per gaussian.
    """
    for i in range(len(col)):
        cx, cy  = float(col[i]), float(row[i])
        sig     = float(sigmas[i])
        alpha   = float(opacities[i])
        r, g, b = float(colors[i, 0]), float(colors[i, 1]), float(colors[i, 2])

        r0 = max(0, int(cy - 3 * sig))
        r1 = min(height, int(cy + 3 * sig) + 1)
        c0 = max(0, int(cx - 3 * sig))
        c1 = min(width,  int(cx + 3 * sig) + 1)

        if r1 <= r0 or c1 <= c0:
            continue

        rows_arr = np.arange(r0, r1, dtype=np.float32) - cy
        cols_arr = np.arange(c0, c1, dtype=np.float32) - cx
        rr, cc   = np.meshgrid(rows_arr, cols_arr, indexing="ij")

        inv_2sig2 = 1.0 / (2.0 * sig * sig + 1e-8)
        gauss     = np.exp(-(rr * rr + cc * cc) * inv_2sig2)
        w         = alpha * gauss

        acc_alpha[r0:r1, c0:c1]    += w
        acc_rgb[r0:r1, c0:c1, 0]   += w * r
        acc_rgb[r0:r1, c0:c1, 1]   += w * g
        acc_rgb[r0:r1, c0:c1, 2]   += w * b


def _write_geotiff(path, rgba_u8, x_min, y_max, resolution, scene) -> str:
    """
    Write RGBA uint8 array as GeoTIFF using GDAL and produce a companion
    ESRI world file (.tfw) alongside it.
    """
    try:
        from osgeo import gdal, osr
    except ImportError:
        raise RuntimeError(
            "GDAL is required to write GeoTIFF files. "
            "It should be available in the QGIS Python environment."
        )

    height, width, bands = rgba_u8.shape
    driver = gdal.GetDriverByName("GTiff")
    ds     = driver.Create(
        path, width, height, 4, gdal.GDT_Byte,
        options=["COMPRESS=LZW", "TILED=YES"]
    )

    ds.SetGeoTransform((x_min, resolution, 0, y_max, 0, -resolution))

    srs = osr.SpatialReference()
    srs.ImportFromWkt(scene.georeference.crs.toWkt())
    ds.SetProjection(srs.ExportToWkt())

    band_names = ["Red", "Green", "Blue", "Alpha"]
    for i in range(4):
        band = ds.GetRasterBand(i + 1)
        band.WriteArray(rgba_u8[:, :, i])
        band.SetDescription(band_names[i])
        if i == 3:
            band.SetColorInterpretation(gdal.GCI_AlphaBand)

    ds.FlushCache()
    ds = None

    # --- Companion world file (.tfw) ---
    half   = resolution * 0.5
    cx_ul  = x_min + half
    cy_ul  = y_max - half
    base, _ = os.path.splitext(path)
    world_file_path = base + ".tfw"
    with open(world_file_path, "w") as wf:
        wf.write(f"{resolution:.10f}\n")
        wf.write("0.0000000000\n")
        wf.write("0.0000000000\n")
        wf.write(f"{-resolution:.10f}\n")
        wf.write(f"{cx_ul:.10f}\n")
        wf.write(f"{cy_ul:.10f}\n")

    return world_file_path
