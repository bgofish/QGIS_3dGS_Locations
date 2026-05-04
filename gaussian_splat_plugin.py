# -*- coding: utf-8 -*-
"""
GaussianSplatPlugin — top-level plugin class.
Manages toolbar, menu entries, and plugin lifecycle.
"""

import os
from qgis.PyQt.QtWidgets import QAction, QToolBar
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import QTranslator, QCoreApplication
from qgis.core import QgsMessageLog, Qgis


class GaussianSplatPlugin:
    """QGIS Plugin Implementation."""

    PLUGIN_NAME = "Gaussian Splat Viewer"

    def __init__(self, iface):
        """
        Constructor.

        :param iface: QgsInterface — passed in by QGIS on load.
        """
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.actions = []
        self.menu = self.PLUGIN_NAME
        self.toolbar = None

        # Lazy-loaded dialog references
        self._import_dialog = None
        self._georeference_dialog = None
        self._visualize_dialog = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def tr(self, message):
        """Translate via Qt translation system."""
        return QCoreApplication.translate("GaussianSplatPlugin", message)

    def icon(self, name):
        """Return a QIcon from the resources folder."""
        path = os.path.join(self.plugin_dir, "resources", name)
        return QIcon(path) if os.path.exists(path) else QIcon()

    def add_action(
        self,
        icon_name,
        text,
        callback,
        enabled=True,
        add_to_menu=True,
        add_to_toolbar=True,
        status_tip=None,
        whats_this=None,
    ):
        """Create a QAction and add it to toolbar and/or menu."""
        action = QAction(self.icon(icon_name), text, self.iface.mainWindow())
        action.triggered.connect(callback)
        action.setEnabled(enabled)
        if status_tip:
            action.setStatusTip(status_tip)
        if whats_this:
            action.setWhatsThis(whats_this)
        if add_to_toolbar and self.toolbar:
            self.toolbar.addAction(action)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)
        return action

    # ------------------------------------------------------------------
    # QGIS lifecycle
    # ------------------------------------------------------------------

    def initGui(self):
        """Create the menu entries and toolbar icons inside the QGIS GUI."""
        self.toolbar = self.iface.addToolBar(self.PLUGIN_NAME)
        self.toolbar.setObjectName("GaussianSplatToolbar")

        self.add_action(
            icon_name="icon_import.png",
            text=self.tr("Import .ply / .splat"),
            callback=self.run_import,
            status_tip=self.tr("Load a Gaussian Splatting file into QGIS"),
        )

        self.add_action(
            icon_name="icon_georeference.png",
            text=self.tr("Georeference Splat Scene"),
            callback=self.run_georeference,
            status_tip=self.tr("Set CRS, origin, and scale for a loaded splat scene"),
        )

        self.add_action(
            icon_name="icon_visualize.png",
            text=self.tr("Visualize on Map Canvas"),
            callback=self.run_visualize,
            status_tip=self.tr("Render splat gaussians onto the 2D map canvas"),
        )

        self.add_action(
            icon_name="icon_help.png",
            text=self.tr("Help"),
            callback=self.run_help,
            add_to_toolbar=False,
            status_tip=self.tr("Open plugin documentation"),
        )

    def unload(self):
        """Remove plugin menu items and toolbar on unload."""
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
            self.iface.removeToolBarIcon(action)
        if self.toolbar:
            del self.toolbar

    # ------------------------------------------------------------------
    # Action handlers
    # ------------------------------------------------------------------

    def _ensure_dialogs(self):
        """Lazy-create all dialogs and wire inter-dialog signals once."""
        if self._import_dialog is not None:
            return

        from .ui.import_dialog import ImportDialog
        from .ui.georeference_dialog import GeoreferenceDialog
        from .ui.visualize_dialog import VisualizeDialog

        self._import_dialog = ImportDialog(self.iface, self.iface.mainWindow())
        self._georeference_dialog = GeoreferenceDialog(self.iface, self.iface.mainWindow())
        self._visualize_dialog = VisualizeDialog(self.iface, self.iface.mainWindow())

        # Import → Georeference: pass loaded scene
        self._import_dialog.scene_loaded.connect(
            self._georeference_dialog.set_scene
        )
        # Georeference → Visualize: pass georeferenced scene
        self._georeference_dialog.georeferenced.connect(
            self._visualize_dialog.set_scene
        )

    def run_import(self):
        """Open the Import dialog."""
        self._ensure_dialogs()
        self._import_dialog.show()
        self._import_dialog.raise_()

    def run_georeference(self):
        """Open the Georeference dialog."""
        self._ensure_dialogs()
        self._georeference_dialog.show()
        self._georeference_dialog.raise_()

    def run_visualize(self):
        """Open the Visualize dialog."""
        self._ensure_dialogs()
        self._visualize_dialog.show()
        self._visualize_dialog.raise_()

    def run_help(self):
        """Open help documentation in browser."""
        import webbrowser
        help_path = os.path.join(self.plugin_dir, "help", "index.html")
        if os.path.exists(help_path):
            webbrowser.open(f"file://{help_path}")
        else:
            self.iface.messageBar().pushMessage(
                self.PLUGIN_NAME,
                "Help file not found. Visit the plugin repository for documentation.",
                level=Qgis.Info,
            )

    def log(self, message, level=Qgis.Info):
        """Convenience logger to QGIS message log."""
        QgsMessageLog.logMessage(message, self.PLUGIN_NAME, level=level)
