# -*- coding: utf-8 -*-
"""
core/splat_scene.py

Data model for a loaded Gaussian Splatting scene.
Holds raw gaussian attributes and georeferencing parameters.

A 3DGS scene stores N gaussians, each with:
  - position (x, y, z)          — centre in local scene space
  - opacity (alpha)              — after sigmoid activation
  - colour (r, g, b)             — spherical harmonic DC term → RGB
  - scale (sx, sy, sz)           — log-scale axes of the 3D ellipsoid
  - rotation (qw, qx, qy, qz)   — unit quaternion
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY


@dataclass
class GeoReference:
    """
    Maps from local scene space → geographic coordinates.

    origin_geo  : the geographic coordinate (lon/lat or projected) that
                  corresponds to the scene's local (0, 0, 0) origin.
    crs         : target CRS for the output layer.
    scale       : metres per scene unit (default 1.0).
    rotation_deg: bearing correction — clockwise degrees from north
                  to align the scene's +X axis with geographic East.
    z_offset    : vertical offset in metres added to scene Z values.
    """
    origin_geo: QgsPointXY = field(default_factory=lambda: QgsPointXY(0.0, 0.0))
    crs: QgsCoordinateReferenceSystem = field(
        default_factory=lambda: QgsCoordinateReferenceSystem("EPSG:4326")
    )
    scale: float = 1.0
    rotation_deg: float = 0.0
    z_offset: float = 0.0

    def is_valid(self):
        return self.crs.isValid() and self.scale > 0


@dataclass
class GaussianSplatScene:
    """
    In-memory representation of a loaded 3DGS scene.

    Attributes are stored as numpy arrays of shape (N,) or (N, k)
    for efficient batch processing.
    """

    # File provenance
    source_path: str = ""
    file_format: str = ""           # "ply" | "splat"

    # Core gaussian attributes — all shape (N,) or (N, k)
    positions: Optional[np.ndarray] = None      # (N, 3)  float32
    opacities: Optional[np.ndarray] = None      # (N,)    float32  [0..1]
    colors: Optional[np.ndarray] = None         # (N, 3)  float32  [0..1] RGB
    scales: Optional[np.ndarray] = None         # (N, 3)  float32  log-scale
    rotations: Optional[np.ndarray] = None      # (N, 4)  float32  qw,qx,qy,qz

    # Spherical harmonics (optional, degree-3 = 48 coefficients per channel)
    sh_coefficients: Optional[np.ndarray] = None  # (N, 48, 3)

    # Georeferencing
    georeference: GeoReference = field(default_factory=GeoReference)
    is_georeferenced: bool = False

    @property
    def count(self) -> int:
        """Number of gaussians in the scene."""
        if self.positions is None:
            return 0
        return self.positions.shape[0]

    @property
    def bounds_local(self):
        """
        Returns (xmin, ymin, zmin, xmax, ymax, zmax) in local scene space.
        """
        if self.positions is None:
            return None
        mins = self.positions.min(axis=0)
        maxs = self.positions.max(axis=0)
        return (*mins, *maxs)

    def world_positions(
        self,
        axis_mapping: Optional[dict] = None,
    ) -> Optional[np.ndarray]:
        """
        Return positions transformed to georeferenced coordinates.

        Pipeline (in order):
          1. Scale           — multiply by geo.scale (scene units → metres)
          2. Axis remap      — reorder/negate scene axes so the output is
                               always [Easting, Northing, Elevation] using
                               the caller-supplied axis_mapping.
          3. Bearing rotate  — rotate the horizontal plane by geo.rotation_deg
                               (clockwise from north) around the vertical axis.
          4. Origin offset   — add geo.origin_geo (X→Easting, Y→Northing)
                               and geo.z_offset to Elevation.

        :param axis_mapping: Dict with keys 'east', 'north', 'elevation'.
                             Each value is (scene_col, sign) where scene_col
                             is 0=scene-X, 1=scene-Y, 2=scene-Z and sign is
                             +1 or -1.  Defaults to identity (0,1,2) → Z-up.
        :returns: (N, 3) float64 array  [Easting, Northing, Elevation]
                  in CRS units, or None if not georeferenced.
        """
        if not self.is_georeferenced or self.positions is None:
            return None

        import math
        geo = self.georeference

        # 1. Scale
        pts = self.positions.astype(np.float64) * geo.scale

        # 2. Axis remap → output columns are always [Easting, Northing, Elevation]
        #    Default is identity (scene X→East, scene Y→North, scene Z→Elev),
        #    which is correct for Z-up scenes (Colmap default).
        #    For Y-up scenes pass e.g. {'east':(0,1),'north':(2,1),'elevation':(1,1)}.
        if axis_mapping is None:
            axis_mapping = {
                "east":      (0,  1),   # scene X → Easting
                "north":     (1,  1),   # scene Y → Northing
                "elevation": (2,  1),   # scene Z → Elevation
            }
        e_col, e_sign = axis_mapping["east"]
        n_col, n_sign = axis_mapping["north"]
        z_col, z_sign = axis_mapping["elevation"]
        remapped = np.empty_like(pts)
        remapped[:, 0] = pts[:, e_col] * e_sign   # Easting
        remapped[:, 1] = pts[:, n_col] * n_sign   # Northing
        remapped[:, 2] = pts[:, z_col] * z_sign   # Elevation
        pts = remapped

        # 3. Bearing rotation around vertical (Elevation) axis
        #    geo.rotation_deg = clockwise degrees from north to scene +East axis
        angle_rad = math.radians(-geo.rotation_deg)
        cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
        rot = np.array([
            [cos_a, -sin_a, 0],
            [sin_a,  cos_a, 0],
            [0,      0,     1],
        ], dtype=np.float64)
        pts = (rot @ pts.T).T

        # 4. Origin offset (georeference translation)
        pts[:, 0] += geo.origin_geo.x()   # Easting
        pts[:, 1] += geo.origin_geo.y()   # Northing  ← always geographic Y
        pts[:, 2] += geo.z_offset         # Elevation

        return pts

    def summary(self) -> str:
        return (
            f"GaussianSplatScene | {self.count:,} gaussians | "
            f"format={self.file_format} | "
            f"georef={'yes' if self.is_georeferenced else 'no'} | "
            f"source={self.source_path}"
        )
