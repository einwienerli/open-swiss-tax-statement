# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Open Swiss Tax Statement macOS .app bundle."""

import os
import re

block_cipher = None

# Paths relative to spec file location
ROOT = os.path.abspath(SPECPATH)

# Read version from src/__init__.py (single source of truth)
_init = open(os.path.join(ROOT, "src", "__init__.py")).read()
VERSION = re.search(r'__version__\s*=\s*"([^"]+)"', _init).group(1)

a = Analysis(
    [os.path.join(ROOT, "src", "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # UI assets (HTML, CSS, JS, i18n)
        (os.path.join(ROOT, "src", "ui", "index.html"), os.path.join("src", "ui")),
        (os.path.join(ROOT, "src", "ui", "static"), os.path.join("src", "ui", "static")),
        # XSD schemas
        (os.path.join(ROOT, "src", "generators", "ech0196", "schema"), os.path.join("src", "generators", "ech0196", "schema")),
        # Enrichment data (kursliste, ESTV rates, DA-1 rates)
        (os.path.join(ROOT, "src", "enrichment", "data"), os.path.join("src", "enrichment", "data")),
    ],
    hiddenimports=[
        "webview",
        "webview.platforms.cocoa",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        # Not used by the app — pulled in transitively
        "numpy",
        "cryptography",
        "charset_normalizer",
        # Unused Qt modules (pywebview only needs QtCore/QtGui/QtWidgets + cocoa platform)
        "PyQt5.QtQuick",
        "PyQt5.QtQml",
        "PyQt5.QtQmlModels",
        "PyQt5.QtNetwork",
        "PyQt5.QtWebChannel",
        "PyQt5.QtWebSockets",
        "PyQt5.QtDBus",
        "PyQt5.QtSvg",
        "PyQt5.QtPrintSupport",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Strip unnecessary native binaries that PyInstaller's Qt hook bundles automatically.
# pywebview only needs QtCore, QtGui, QtWidgets, and the cocoa platform plugin.
_exclude_bins = [
    "QtQuick", "QtQml", "QtQmlModels", "QtNetwork", "QtWebChannel",
    "QtWebSockets", "QtDBus", "QtSvg", "QtPrintSupport",
    "libqwebgl", "libqoffscreen", "libqminimal",
]
a.binaries = [b for b in a.binaries if not any(p in b[0] for p in _exclude_bins)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Open Swiss Tax Statement",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Open Swiss Tax Statement",
)

app = BUNDLE(
    coll,
    name="Open Swiss Tax Statement.app",
    icon=os.path.join(ROOT, "assets", "icon.icns"),
    bundle_identifier="com.einwienerli.openswisstaxstatement",
    info_plist={
        "CFBundleDisplayName": "Open Swiss Tax Statement",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.15",
    },
)
