"""PyInstaller spec for SWAGcleaner — one .exe, console + GUI fallback.

The spec collects PySide6, shiboken6, and all app packages, then
builds a single console executable.  The app chooses GUI or CLI mode
based on its arguments, so the exe works for both.
"""
from __future__ import annotations

datas: list = []
binaries: list = []
hiddenimports: list = []

import sys
import PyInstaller

_HAS_BUILD_API = False
try:
    from PyInstaller.building.api import Analysis, EXE, COLLECT  # type: ignore[no-redef]
    _HAS_BUILD_API = True
except ImportError:
    pass

if not _HAS_BUILD_API:
    try:
        from PyInstaller.build_api import Analysis, EXE, COLLECT  # type: ignore[no-redef]
        _HAS_BUILD_API = True
    except ImportError:
        sys.exit("PyInstaller >= 6.0 is required (Analysis, EXE, COLLECT).")

from PyInstaller.utils.hooks import collect_all  # noqa: E402


for pkg in ("PySide6", "shiboken6", "core", "ui", "services"):
    try:
        d, b, i = collect_all(pkg)
        datas.extend(d)
        binaries.extend(b)
        hiddenimports.extend(i)
    except Exception as e:
        print(f"WARNING: cannot collect {pkg}: {e}")


a = Analysis(
    ["swagcleaner.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

exe = EXE(
    a,
    name="SWAGcleaner",
    debug=False,
    bootloader_ignore_signals=False,
    strip=None,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.datas,
    a.binaries,
    name="SWAGcleaner",
    upx=True,
    upx_exclude=[],
    distpath="dist",
    runtime_tmpdir=None,
)
