# -*- coding: utf-8 -*-
"""
core/splat_loader.py

Reads .ply (3DGS standard) and binary .splat files into a GaussianSplatScene.

PLY format (3DGS):
  Each vertex has properties:
    x, y, z                         (position)
    nx, ny, nz                      (normals — unused, present for compat)
    f_dc_0, f_dc_1, f_dc_2          (SH DC term → base colour)
    f_rest_0 … f_rest_44            (higher-order SH, optional)
    opacity                         (raw logit — apply sigmoid)
    scale_0, scale_1, scale_2       (log scale)
    rot_0, rot_1, rot_2, rot_3      (quaternion w,x,y,z)

Binary .splat format (antimatter15 / SuperSplat):
  Fixed 32 bytes per gaussian:
    xyz       3 × float32   ( 0–11)
    scale     3 × float32   (12–23)
    rgba      4 × uint8     (24–27)   — colour + opacity packed
    rot       4 × uint8     (28–31)   — quaternion packed as uint8 mapped [-1,1]
"""

import struct
import numpy as np
from pathlib import Path
from typing import Callable, Optional

from .splat_scene import GaussianSplatScene


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_file(
    path: str,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> GaussianSplatScene:
    """
    Auto-detect format and load a Gaussian Splatting file.

    :param path: Absolute path to .ply or .splat file.
    :param progress_callback: Optional callable receiving percent 0-100.
    :raises ValueError: If the file format is not recognised.
    :raises IOError: If the file cannot be read.
    :returns: Populated GaussianSplatScene.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".ply":
        return _load_ply(path, progress_callback)
    elif suffix == ".splat":
        return _load_splat(path, progress_callback)
    else:
        raise ValueError(
            f"Unsupported file format '{suffix}'. "
            "Expected .ply (3DGS) or .splat (binary packed)."
        )


# ---------------------------------------------------------------------------
# PLY loader
# ---------------------------------------------------------------------------

def _load_ply(path: str, cb) -> GaussianSplatScene:
    """Parse a 3DGS PLY file."""
    with open(path, "rb") as f:
        header, data_start = _parse_ply_header(f)
        n_vertices = header["n_vertices"]
        props = header["properties"]          # list of (name, dtype)
        row_dtype = np.dtype(props)
        f.seek(data_start)
        raw = np.frombuffer(f.read(n_vertices * row_dtype.itemsize), dtype=row_dtype)

    if cb:
        cb(50)

    scene = GaussianSplatScene(source_path=path, file_format="ply")
    names = [p[0] for p in props]

    # Position
    scene.positions = np.stack(
        [raw["x"].astype(np.float32),
         raw["y"].astype(np.float32),
         raw["z"].astype(np.float32)], axis=1
    )

    # Opacity — sigmoid activation
    if "opacity" in names:
        logit_op = raw["opacity"].astype(np.float32)
        scene.opacities = 1.0 / (1.0 + np.exp(-logit_op))

    # Colour from SH DC term
    dc_names = ["f_dc_0", "f_dc_1", "f_dc_2"]
    if all(n in names for n in dc_names):
        # SH DC → linear RGB:  C = 0.2820947918 * dc + 0.5
        SH_C0 = 0.2820947918
        r = raw["f_dc_0"].astype(np.float32) * SH_C0 + 0.5
        g = raw["f_dc_1"].astype(np.float32) * SH_C0 + 0.5
        b = raw["f_dc_2"].astype(np.float32) * SH_C0 + 0.5
        scene.colors = np.clip(np.stack([r, g, b], axis=1), 0.0, 1.0)

    # Scale
    scale_names = ["scale_0", "scale_1", "scale_2"]
    if all(n in names for n in scale_names):
        scene.scales = np.stack(
            [raw[n].astype(np.float32) for n in scale_names], axis=1
        )

    # Rotation (quaternion w, x, y, z)
    rot_names = ["rot_0", "rot_1", "rot_2", "rot_3"]
    if all(n in names for n in rot_names):
        rots = np.stack(
            [raw[n].astype(np.float32) for n in rot_names], axis=1
        )
        # Normalise quaternions
        norms = np.linalg.norm(rots, axis=1, keepdims=True)
        scene.rotations = rots / np.maximum(norms, 1e-8)

    if cb:
        cb(100)

    return scene


def _parse_ply_header(f):
    """
    Read ASCII PLY header from an open binary file handle.
    Returns (header_dict, byte_offset_to_data).
    """
    lines = []
    while True:
        line = f.readline().decode("ascii", errors="replace").strip()
        lines.append(line)
        if line == "end_header":
            break

    n_vertices = 0
    properties = []
    PLY_DTYPE_MAP = {
        "float": "f4", "float32": "f4",
        "double": "f8", "float64": "f8",
        "int": "i4",   "int32": "i4",
        "uint": "u4",  "uint32": "u4",
        "short": "i2", "ushort": "u2",
        "char": "i1",  "uchar": "u1",
    }

    in_vertex = False
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        if parts[0] == "element" and parts[1] == "vertex":
            n_vertices = int(parts[2])
            in_vertex = True
        elif parts[0] == "element" and parts[1] != "vertex":
            in_vertex = False
        elif parts[0] == "property" and in_vertex:
            dtype_str = PLY_DTYPE_MAP.get(parts[1], "f4")
            prop_name = parts[2]
            properties.append((prop_name, dtype_str))

    data_start = f.tell()
    return {"n_vertices": n_vertices, "properties": properties}, data_start


# ---------------------------------------------------------------------------
# Binary .splat loader
# ---------------------------------------------------------------------------

_SPLAT_BYTES_PER_GAUSSIAN = 32


def _load_splat(path: str, cb) -> GaussianSplatScene:
    """Parse a binary .splat file (antimatter15 / SuperSplat format)."""
    with open(path, "rb") as f:
        data = f.read()

    n = len(data) // _SPLAT_BYTES_PER_GAUSSIAN
    if n == 0:
        raise IOError("Empty or malformed .splat file.")

    raw = np.frombuffer(data[: n * _SPLAT_BYTES_PER_GAUSSIAN], dtype=np.uint8)
    raw = raw.reshape(n, _SPLAT_BYTES_PER_GAUSSIAN)

    if cb:
        cb(40)

    # Position: bytes 0-11, three float32
    positions = raw[:, 0:12].view(np.float32).reshape(n, 3)

    # Scale: bytes 12-23, three float32
    scales = raw[:, 12:24].view(np.float32).reshape(n, 3)

    # RGBA: bytes 24-27, four uint8
    rgba = raw[:, 24:28].astype(np.float32) / 255.0
    colors = rgba[:, :3]
    opacities = rgba[:, 3]

    # Rotation: bytes 28-31, four uint8 → mapped to [-1, 1]
    rot_raw = raw[:, 28:32].astype(np.float32)
    rotations = (rot_raw - 128.0) / 128.0
    norms = np.linalg.norm(rotations, axis=1, keepdims=True)
    rotations = rotations / np.maximum(norms, 1e-8)

    if cb:
        cb(100)

    scene = GaussianSplatScene(source_path=path, file_format="splat")
    scene.positions = positions.astype(np.float32)
    scene.scales = scales.astype(np.float32)
    scene.colors = colors.astype(np.float32)
    scene.opacities = opacities.astype(np.float32)
    scene.rotations = rotations.astype(np.float32)
    return scene
