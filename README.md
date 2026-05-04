# Gaussian Splat Viewer — QGIS Plugin

A QGIS 3.x plugin for importing, georeferencing, and visualizing
**3D Gaussian Splatting** (3DGS) scenes on the QGIS map canvas.

---

## Features

| Feature | Status |
|---|---|
| Load `.ply` (3DGS standard) | ✅ |
| Load `.splat` (binary packed) | ✅ |
| Georeference via CRS + origin + scale + bearing | ✅ |
| Pick origin from map canvas | ✅ |
| Render to GeoTIFF raster layer (2D top-down) | ✅ |
| Background thread loading & rendering | ✅ |
| X / Y axis side-view projections | ✅ |

---

## Installation

### Via Plugin Manager

1. Download this repository as a zip.
2. In QGIS  Plugins → Manage and Install Plugins → Install from ZIP. Plugins is installed to the `gaussian_splat_qgis/` folder into your QGIS plugins directory:
   - **Linux/macOS:** `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - **Windows:** `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
3. In QGIS → **Plugins → Manage and Install Plugins → Installed** → enable **Gaussian Splat Viewer**.

---

## Workflow

```
Import (.ply / .splat)
    ↓   scene_loaded signal
Georeference (CRS, origin, scale, bearing)
    ↓   georeferenced signal
Visualize → GeoTIFF → QGIS Raster Layer
```

---

## Architecture

```
gaussian_splat_qgis/
│
├── __init__.py                  # QGIS plugin entry point
├── gaussian_splat_plugin.py     # Plugin class — toolbar, menus, lifecycle
├── metadata.txt                 # QGIS plugin metadata
│
├── core/
│   ├── __init__.py
│   ├── splat_scene.py           # GaussianSplatScene + GeoReference data model
│   ├── splat_loader.py          # .ply and .splat file parsers
│   └── splat_renderer.py        # CPU rasterizer → GeoTIFF
│
├── ui/
│   ├── __init__.py
│   ├── import_dialog.py         # File picker + background load thread
│   ├── georeference_dialog.py   # CRS/origin/scale controls + map-click tool
│   └── visualize_dialog.py      # Render settings + background render thread
│
├── resources/
│   └── icon.png                 # (add your icons here)
│
└── help/
    └── index.html               # In-plugin help page
```

---

## Dependencies

All available in the standard QGIS 3.x Python environment:

- `numpy` — gaussian attribute arrays
- `osgeo.gdal` — GeoTIFF output
- `qgis.core`, `qgis.gui` — QGIS API
- `qgis.PyQt` — Qt UI

No additional `pip install` required.

---

## Roadmap

- [ ] QGIS 3D Map View integration (volumetric splat preview)
- [ ] Ground control point (GCP) georeferencing
- [ ] Export georeferenced point cloud as QGIS vector layer
- [ ] GPU-accelerated rendering via OpenGL (QgsCustomRenderingPlugin)
- [ ] Streaming / tiled rendering for >1M gaussian scenes
- [ ] `.splat` → GeoPackage round-trip

---

## Contributing

PRs welcome. Please open an issue first for major changes.

## Licence

MIT
