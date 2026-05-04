# -*- coding: utf-8 -*-
"""
ui/visualize_dialog.py

Controls rendering of a georeferenced GaussianSplatScene to a GeoTIFF
and adds the result to the QGIS map canvas as a raster layer.
"""

import os
import tempfile
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QGroupBox,
    QLabel, QDoubleSpinBox, QSpinBox, QComboBox,
    QProgressBar, QDialogButtonBox, QPushButton,
    QFileDialog, QMessageBox, QCheckBox, QHBoxLayout,
    QGridLayout,
)
from qgis.PyQt.QtCore import QThread, QObject, pyqtSignal
from qgis.PyQt.QtGui import QFont
from qgis.core import QgsRasterLayer, QgsProject, Qgis


class _RenderWorker(QObject):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str, dict)
    error = pyqtSignal(str)

    def __init__(self, scene, output_path, resolution, max_g, axis_mapping):
        super().__init__()
        self.scene = scene
        self.output_path = output_path
        self.resolution = resolution
        self.max_g = max_g
        self.axis_mapping = axis_mapping   # dict: {east, north, elevation} → signed source axis

    def run(self):
        try:
            from ..core import render_to_geotiff
            path, gt = render_to_geotiff(
                scene=self.scene,
                output_path=self.output_path,
                resolution=self.resolution,
                max_gaussians=self.max_g,
                axis_mapping=self.axis_mapping,
                progress_callback=self.progress.emit,
            )
            self.finished.emit(path, gt)
        except Exception as exc:
            self.error.emit(str(exc))


# Axis choices presented in each combo
_AXIS_CHOICES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]

# Maps combo label → (array_index, sign)
_AXIS_DECODE = {
    "+X": (0,  1), "-X": (0, -1),
    "+Y": (1,  1), "-Y": (1, -1),
    "+Z": (2,  1), "-Z": (2, -1),
}


class VisualizeDialog(QDialog):
    """
    Dialog: Render a georeferenced splat scene onto the QGIS map canvas.

    Produces a GeoTIFF via splat_renderer and loads it as a raster layer.
    """

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.scene = None
        self._thread = None
        self._worker = None

        self.setWindowTitle("Visualize Splat Scene on Map Canvas")
        self.setMinimumWidth(460)
        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        title = QLabel("Render Splat Scene to Map Canvas")
        font = QFont(); font.setBold(True); font.setPointSize(11)
        title.setFont(font)
        layout.addWidget(title)

        self.status_label = QLabel(
            "No georeferenced scene available.\n"
            "Import and georeference a scene first."
        )
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        # --- Render settings ---
        settings_group = QGroupBox("Render Settings")
        settings_layout = QFormLayout(settings_group)

        self.resolution_spin = QDoubleSpinBox()
        self.resolution_spin.setRange(0.001, 1000.0)
        self.resolution_spin.setDecimals(4)
        self.resolution_spin.setValue(0.5)
        self.resolution_spin.setSuffix("  CRS units / pixel")
        settings_layout.addRow("Resolution:", self.resolution_spin)

        self.max_g_spin = QSpinBox()
        self.max_g_spin.setRange(1_000, 5_000_000)
        self.max_g_spin.setSingleStep(10_000)
        self.max_g_spin.setValue(200_000)
        self.max_g_spin.setSuffix("  gaussians")
        settings_layout.addRow("Max gaussians:", self.max_g_spin)

        layout.addWidget(settings_group)

        # --- Axis Mapping ---
        axis_group = QGroupBox("Axis Mapping  (scene axis → geographic direction)")
        axis_grid = QGridLayout(axis_group)
        axis_grid.setColumnStretch(1, 1)

        # Header row
        for col, text in enumerate(["Direction", "Source Axis", "Flip"]):
            lbl = QLabel(f"<b>{text}</b>")
            axis_grid.addWidget(lbl, 0, col)

        # Helper to build one row
        def _axis_row(label_text, default_axis, row):
            lbl = QLabel(label_text)
            combo = QComboBox()
            combo.addItems(_AXIS_CHOICES)
            combo.setCurrentText(default_axis)
            flip = QCheckBox()
            axis_grid.addWidget(lbl,   row, 0)
            axis_grid.addWidget(combo, row, 1)
            axis_grid.addWidget(flip,  row, 2)
            return combo, flip

        # Defaults match the old hard-coded 3DGS convention:
        #   East  = +X  (scene X  → Easting) but flip
        #   North = -Z  (scene Z → Northing)
        #   Elev  = +Y  (scene Y is up → Elevation)
        self.east_combo,  self.east_flip  = _axis_row("East  →",      "+X", 1)
        self.north_combo, self.north_flip = _axis_row("North →",      "-Z", 2)
        self.elev_combo,  self.elev_flip  = _axis_row("Elevation →",  "+Y", 3)

        hint = QLabel(
            "<small><i>Typical 3DGS (Lichtfeld Studio): East=+X(flip), North=-Z, Elev=+Y</i></small>"
        )
        hint.setWordWrap(True)
        axis_grid.addWidget(hint, 4, 0, 1, 3)

        layout.addWidget(axis_group)

        # --- Output ---
        out_group = QGroupBox("Output")
        out_layout = QFormLayout(out_group)

        self.auto_temp = QCheckBox("Use a temporary file (auto-deleted on QGIS exit)")
        self.auto_temp.setChecked(True)
        self.auto_temp.toggled.connect(self._toggle_output)
        out_layout.addRow("", self.auto_temp)

        self.output_edit = QLabel("(temporary)")
        self.browse_out_btn = QPushButton("Save as…")
        self.browse_out_btn.clicked.connect(self._browse_output)
        self.browse_out_btn.setEnabled(False)
        out_layout.addRow("Output path:", self.output_edit)
        out_layout.addRow("", self.browse_out_btn)

        layout.addWidget(out_group)

        # --- Progress ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # --- Buttons ---
        btn_box = QDialogButtonBox()
        self.render_btn = btn_box.addButton("Render & Add Layer", QDialogButtonBox.AcceptRole)
        btn_box.addButton(QDialogButtonBox.Close)
        self.render_btn.setEnabled(False)
        self.render_btn.clicked.connect(self._render)
        btn_box.rejected.connect(self.close)
        layout.addWidget(btn_box)

    # ------------------------------------------------------------------

    def _read_axis_mapping(self):
        """
        Build an axis_mapping dict from the three combo+flip rows.

        Returns:
            {
              'east':      (array_col, sign),   # e.g. (0, -1) for -X
              'north':     (array_col, sign),
              'elevation': (array_col, sign),
            }
        where sign is further negated if the flip checkbox is checked.
        """
        def _decode(combo, flip_cb):
            idx, sign = _AXIS_DECODE[combo.currentText()]
            if flip_cb.isChecked():
                sign = -sign
            return (idx, sign)

        return {
            "east":      _decode(self.east_combo,  self.east_flip),
            "north":     _decode(self.north_combo, self.north_flip),
            "elevation": _decode(self.elev_combo,  self.elev_flip),
        }

    # ------------------------------------------------------------------

    def set_scene(self, scene):
        """Receive a georeferenced scene."""
        self.scene = scene
        if scene and scene.is_georeferenced:
            self.status_label.setText(
                f"Ready: <b>{scene.count:,} gaussians</b> | "
                f"CRS: {scene.georeference.crs.authid()}"
            )
            self.render_btn.setEnabled(True)
        else:
            self.status_label.setText(
                "Scene is not georeferenced yet. Use the Georeference dialog first."
            )
            self.render_btn.setEnabled(False)

    def _toggle_output(self, checked):
        self.browse_out_btn.setEnabled(not checked)
        self.output_edit.setText("(temporary)" if checked else "")

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save GeoTIFF", "", "GeoTIFF (*.tif *.tiff)"
        )
        if path:
            self.output_edit.setText(path)

    # ------------------------------------------------------------------

    def _render(self):
        if self.scene is None or not self.scene.is_georeferenced:
            QMessageBox.warning(
                self, "Not Ready",
                "Please import and georeference a scene first."
            )
            return

        if self.auto_temp.isChecked():
            fd, out_path = tempfile.mkstemp(suffix=".tif", prefix="splat_")
            os.close(fd)
        else:
            out_path = self.output_edit.text().strip()
            if not out_path:
                QMessageBox.warning(self, "No Output", "Set an output path.")
                return

        axis_mapping = self._read_axis_mapping()

        self.render_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)

        self._thread = QThread(self)
        self._worker = _RenderWorker(
            scene=self.scene,
            output_path=out_path,
            resolution=self.resolution_spin.value(),
            max_g=self.max_g_spin.value(),
            axis_mapping=axis_mapping,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress_bar.setValue)
        self._worker.finished.connect(self._on_rendered)
        self._worker.error.connect(self._on_error)
        self._thread.start()

    def _on_rendered(self, path, geotransform):
        self._thread.quit()
        self._thread.wait()
        self.progress_bar.setValue(100)
        self.render_btn.setEnabled(True)

        layer_name = f"Splat – {os.path.basename(self.scene.source_path)}"
        rl = QgsRasterLayer(path, layer_name, "gdal")
        if not rl.isValid():
            QMessageBox.critical(
                self, "Layer Error",
                f"QGIS could not load the rendered GeoTIFF:\n{path}"
            )
            return

        QgsProject.instance().addMapLayer(rl)
        self.iface.mapCanvas().setExtent(rl.extent())
        self.iface.mapCanvas().refresh()

        res = geotransform["resolution"]
        w, h = geotransform["width"], geotransform["height"]
        wf = geotransform.get("world_file", "")
        wf_info = f" | World file: {os.path.basename(wf)}" if wf else ""
        self.iface.messageBar().pushMessage(
            "Gaussian Splat Viewer",
            f"Layer added: {layer_name} | {w}×{h} px @ {res:.4f} units/px{wf_info}",
            level=Qgis.Success,
        )

    def _on_error(self, msg):
        self._thread.quit()
        self._thread.wait()
        self.progress_bar.setVisible(False)
        self.render_btn.setEnabled(True)
        QMessageBox.critical(self, "Render Error", f"Rendering failed:\n\n{msg}")
