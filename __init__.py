# -*- coding: utf-8 -*-
"""
Gaussian Splat Viewer - QGIS Plugin
Entry point called by QGIS plugin loader.
"""


def classFactory(iface):
    """
    Required by QGIS. Instantiates the plugin class.

    :param iface: QgsInterface — the QGIS application interface
    """
    from .gaussian_splat_plugin import GaussianSplatPlugin
    return GaussianSplatPlugin(iface)
