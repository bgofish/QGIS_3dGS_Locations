# -*- coding: utf-8 -*-
"""
ui/georeference_dialog.py

Lets the user set the CRS, geographic origin, scale, and bearing correction
for a loaded GaussianSplatScene, writing a GeoReference onto the scene.
"""

import re

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QDialogButtonBox,
    QMessageBox, QPushButton, QHBoxLayout,
    QFileDialog,
)
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtCore import pyqtSignal
from qgis.gui import QgsProjectionSelectionWidget, QgsMapToolEmitPoint
from qgis.core import QgsPointXY, QgsCoordinateReferenceSystem, Qgis


class GeoreferenceDialog(QDialog):
    """
    Dialog: Georeference a loaded splat scene.

    The user specifies:
      • CRS — via the standard QGIS projection selector
      • Origin — the real-world coordinate of the scene's (0,0,0)
                  (can be picked from the map canvas with a click tool,
                   or loaded from a Coord-00.txt survey file)
      • Scale — metres per scene unit
      • Bearing correction — degrees clockwise from north to scene +X
      • Z offset — vertical shift in metres
    """

    georeferenced = pyqtSignal(object)   # emits updated GaussianSplatScene

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.scene = None
        self._pick_tool = None

        self.setWindowTitle("Georeference Splat Scene")
        self.setMinimumWidth(440)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("Georeference Splat Scene")
        font = QFont(); font.setBold(True); font.setPointSize(11)
        title.setFont(font)
        layout.addWidget(title)

        self.status_label = QLabel("No scene loaded. Import a file first.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # --- CRS ---
        crs_group = QGroupBox("Coordinate Reference System")
        crs_layout = QVBoxLayout(crs_group)
        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
        crs_layout.addWidget(self.crs_widget)
        layout.addWidget(crs_group)

        # --- Origin ---
        origin_group = QGroupBox("Scene Origin (local 0,0,0 → world coordinate)")
        origin_layout = QFormLayout(origin_group)

        self.origin_x = QDoubleSpinBox()
        self.origin_x.setRange(-1e9, 1e9)
        self.origin_x.setDecimals(8)
        self.origin_x.setPrefix("X / Lon:  ")
        origin_layout.addRow("Easting / Longitude:", self.origin_x)

        self.origin_y = QDoubleSpinBox()
        self.origin_y.setRange(-1e9, 1e9)
        self.origin_y.setDecimals(8)
        self.origin_y.setPrefix("Y / Lat:  ")
        origin_layout.addRow("Northing / Latitude:", self.origin_y)

        # Action buttons row: pick from canvas + load from Coord file
        btn_row = QHBoxLayout()
        pick_btn = QPushButton("📍  Pick from map canvas…")
        pick_btn.clicked.connect(self._pick_from_canvas)
        btn_row.addWidget(pick_btn)

        load_coord_btn = QPushButton("📄  Load from Coord file…")
        load_coord_btn.setToolTip(
            "Select a Coord-00.txt file.\n"
            "Supported format: \"00 298153.29 m E  9207873.34 m S\"\n"
            "The first point's Easting and Northing will be used."
        )
        load_coord_btn.clicked.connect(self._load_from_coord_file)
        btn_row.addWidget(load_coord_btn)

        origin_layout.addRow("", btn_row)
        layout.addWidget(origin_group)

        # --- Transform parameters ---
        xform_group = QGroupBox("Transform Parameters")
        xform_layout = QFormLayout(xform_group)

        self.scale_spin = QDoubleSpinBox()
        self.scale_spin.setRange(1e-6, 1e6)
        self.scale_spin.setDecimals(6)
        self.scale_spin.setValue(1.0)
        self.scale_spin.setSuffix("  metres / scene unit")
        xform_layout.addRow("Scale:", self.scale_spin)

        self.bearing_spin = QDoubleSpinBox()
        self.bearing_spin.setRange(-360.0, 360.0)
        self.bearing_spin.setDecimals(4)
        self.bearing_spin.setValue(0.0)
        self.bearing_spin.setSuffix("  °  (CW from north)")
        xform_layout.addRow("Bearing correction:", self.bearing_spin)

        self.z_offset_spin = QDoubleSpinBox()
        self.z_offset_spin.setRange(-1e6, 1e6)
        self.z_offset_spin.setDecimals(3)
        self.z_offset_spin.setValue(0.0)
        self.z_offset_spin.setSuffix("  m")
        xform_layout.addRow("Z offset:", self.z_offset_spin)

        layout.addWidget(xform_group)

        # --- Buttons ---
        btn_box = QDialogButtonBox()
        apply_btn = btn_box.addButton("Apply Georeference", QDialogButtonBox.AcceptRole)
        btn_box.addButton(QDialogButtonBox.Close)
        apply_btn.clicked.connect(self._apply)
        btn_box.rejected.connect(self.close)
        layout.addWidget(btn_box)

    # ------------------------------------------------------------------
    # Public: receive a scene from the import dialog
    # ------------------------------------------------------------------

    def set_scene(self, scene):
        """Called when a new scene is loaded."""
        self.scene = scene
        bounds = scene.bounds_local
        if bounds:
            self.status_label.setText(
                f"Scene: <b>{scene.count:,} gaussians</b> | "
                f"Local bounds X [{bounds[0]:.2f}…{bounds[3]:.2f}] "
                f"Y [{bounds[1]:.2f}…{bounds[4]:.2f}] "
                f"Z [{bounds[2]:.2f}…{bounds[5]:.2f}]"
            )
        else:
            self.status_label.setText(f"Scene: {scene.count:,} gaussians loaded.")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _pick_from_canvas(self):
        """Activate a map click tool to capture the origin coordinate."""
        self._pick_tool = QgsMapToolEmitPoint(self.iface.mapCanvas())
        self._pick_tool.canvasClicked.connect(self._on_canvas_click)
        self.iface.mapCanvas().setMapTool(self._pick_tool)
        self.hide()
        self.iface.messageBar().pushMessage(
            "Georeference",
            "Click on the map to set the scene origin, then the dialog will reopen.",
            level=Qgis.Info,
            duration=5,
        )

    def _on_canvas_click(self, point, button):
        from qgis.PyQt.QtCore import Qt
        if button == Qt.LeftButton:
            self.origin_x.setValue(point.x())
            self.origin_y.setValue(point.y())
            self.iface.mapCanvas().unsetMapTool(self._pick_tool)
            self.show()
            self.raise_()

    def _load_from_coord_file(self):
        """
        Open a Coord-*.txt file and parse the first valid survey point.

        Supported line format (produced by typical total-station / GNSS exports):
            <id>  <easting> m E  <northing> m S
        e.g.:
            00 298153.29 m E  9207873.34 m S

        The hemispheres E / W and N / S are honoured:
            E  → positive easting
            W  → negative easting  (west of prime meridian)
            N  → positive northing
            S  → positive northing 
        """
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Coord file",
            "",
            "Coord files (Coord*.txt *.txt);;All files (*)",
        )
        if not path:
            return

        easting, northing = self._parse_coord_file(path)
        if easting is None:
            QMessageBox.warning(
                self,
                "Parse Error",
                f"Could not find a valid coordinate line in:\n{path}\n\n"
                "Expected format:\n"
                "  00 298153.29 m E  9207873.34 m S",
            )
            return

        self.origin_x.setValue(easting)
        self.origin_y.setValue(northing)
        self.iface.messageBar().pushMessage(
            "Georeference",
            f"Loaded origin from {path}: E={easting:.3f}, N={northing:.3f}",
            level=Qgis.Info,
            duration=4,
        )

    # ------------------------------------------------------------------
    # Coord file parser
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_coord_file(path: str):
        """
        Parse a Coord-*.txt file and return (easting, northing) for the
        first valid line, or (None, None) on failure.

        Recognised line pattern:
            <anything>  <float> m E|W  <float> m N|S
        leading whitespace and extra spaces are ignored.
        """
        # Pattern: optional id, then two "number m HEMISPHERE" groups
        # Groups: (east_val, east_hemi, north_val, north_hemi)
        pattern = re.compile(
            r"^\s*\S*\s+"                           # optional point ID
            r"([\d]+\.[\d]+|\d+)\s+m\s+([EW])"     # easting  + hemisphere
            r"\s+"
            r"([\d]+\.[\d]+|\d+)\s+m\s+([NS])",    # northing + hemisphere
            re.IGNORECASE,
        )

        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    m = pattern.match(line)
                    if m:
                        e_val = float(m.group(1))
                        e_hem = m.group(2).upper()
                        n_val = float(m.group(3))
                        n_hem = m.group(4).upper()

                        easting  = e_val if e_hem == "E" else -e_val
                        northing = n_val if n_hem == "N" else n_val
                        # northing = n_val if n_hem == "N" else -n_val
                        return easting, northing
        except OSError as exc:
            pass

        return None, None

    # ------------------------------------------------------------------

    def _apply(self):
        if self.scene is None:
            QMessageBox.warning(self, "No Scene", "Import a .ply or .splat file first.")
            return

        from ..core.splat_scene import GeoReference
        geo = GeoReference(
            origin_geo=QgsPointXY(self.origin_x.value(), self.origin_y.value()),
            crs=self.crs_widget.crs(),
            scale=self.scale_spin.value(),
            rotation_deg=self.bearing_spin.value(),
            z_offset=self.z_offset_spin.value(),
        )

        if not geo.is_valid():
            QMessageBox.warning(self, "Invalid CRS", "Please select a valid CRS.")
            return

        self.scene.georeference = geo
        self.scene.is_georeferenced = True

        self.iface.messageBar().pushMessage(
            "Gaussian Splat Viewer",
            f"Georeferenced {self.scene.count:,} gaussians → "
            f"CRS: {geo.crs.authid()} | "
            f"Origin: ({geo.origin_geo.x():.6f}, {geo.origin_geo.y():.6f}) | "
            f"Scale: {geo.scale}",
            level=Qgis.Success,
        )
        self.georeferenced.emit(self.scene)
