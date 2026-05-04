# -*- coding: utf-8 -*-
"""
ui/import_dialog.py

Dialog for loading a .ply or .splat file into a GaussianSplatScene.
Emits the loaded scene via a signal so other dialogs can pick it up.
"""

import os
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QProgressBar, QDialogButtonBox,
    QGroupBox, QFormLayout, QCheckBox, QMessageBox,
)
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal, QObject
from qgis.PyQt.QtGui import QFont
from qgis.core import Qgis


class _LoadWorker(QObject):
    """Runs the file load on a background thread."""
    progress = pyqtSignal(int)
    finished = pyqtSignal(object)   # GaussianSplatScene
    error = pyqtSignal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path

    def run(self):
        try:
            from ..core import load_file
            scene = load_file(self.path, progress_callback=self.progress.emit)
            self.finished.emit(scene)
        except Exception as exc:
            self.error.emit(str(exc))


class ImportDialog(QDialog):
    """
    Dialog: Import .ply / .splat file.

    After a successful load, the scene is stored on self.scene and
    scene_loaded is emitted so the main plugin can pass it to other dialogs.
    """

    scene_loaded = pyqtSignal(object)   # GaussianSplatScene

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.scene = None
        self._thread = None
        self._worker = None

        self.setWindowTitle("Import Gaussian Splatting File")
        self.setMinimumWidth(480)
        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Title ---
        title = QLabel("Import 3D Gaussian Splatting Scene")
        font = QFont()
        font.setBold(True)
        font.setPointSize(11)
        title.setFont(font)
        layout.addWidget(title)

        layout.addWidget(QLabel(
            "Supported formats: <b>.ply</b> (3DGS standard) · "
            "<b>.splat</b> (binary packed)"
        ))

        # --- File picker ---
        file_group = QGroupBox("Source File")
        file_layout = QHBoxLayout(file_group)
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("Path to .ply or .splat file…")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        file_layout.addWidget(self.file_edit)
        file_layout.addWidget(browse_btn)
        layout.addWidget(file_group)

        # --- Info panel ---
        self.info_label = QLabel("No file selected.")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        # --- Progress ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # --- Buttons ---
        self.button_box = QDialogButtonBox()
        self.load_btn = self.button_box.addButton("Load", QDialogButtonBox.AcceptRole)
        self.button_box.addButton(QDialogButtonBox.Close)
        self.load_btn.setEnabled(False)
        self.button_box.accepted.connect(self._load)
        self.button_box.rejected.connect(self.close)
        layout.addWidget(self.button_box)

        self.file_edit.textChanged.connect(
            lambda t: self.load_btn.setEnabled(bool(t.strip()))
        )

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Gaussian Splatting File",
            "",
            "Gaussian Splatting files (*.ply *.splat);;All files (*)",
        )
        if path:
            self.file_edit.setText(path)
            size_mb = os.path.getsize(path) / 1_048_576
            self.info_label.setText(
                f"<b>{os.path.basename(path)}</b> — {size_mb:.1f} MB"
            )

    def _load(self):
        path = self.file_edit.text().strip()
        if not os.path.isfile(path):
            QMessageBox.warning(self, "File Not Found", f"Cannot find:\n{path}")
            return

        self.load_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.info_label.setText("Loading…")

        # Run loader on background thread
        self._thread = QThread(self)
        self._worker = _LoadWorker(path)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self.progress_bar.setValue)
        self._worker.finished.connect(self._on_loaded)
        self._worker.error.connect(self._on_error)
        self._thread.start()

    def _on_loaded(self, scene):
        self._thread.quit()
        self._thread.wait()
        self.scene = scene
        self.progress_bar.setValue(100)
        self.info_label.setText(
            f"✓ Loaded <b>{scene.count:,} gaussians</b> "
            f"from <i>{os.path.basename(scene.source_path)}</i>"
        )
        self.load_btn.setEnabled(True)
        self.scene_loaded.emit(scene)
        self.iface.messageBar().pushMessage(
            "Gaussian Splat Viewer",
            f"Loaded {scene.count:,} gaussians — proceed to Georeference.",
            level=Qgis.Success,
        )

    def _on_error(self, msg):
        self._thread.quit()
        self._thread.wait()
        self.progress_bar.setVisible(False)
        self.load_btn.setEnabled(True)
        QMessageBox.critical(self, "Load Error", f"Failed to load file:\n\n{msg}")
        self.info_label.setText("Load failed.")
