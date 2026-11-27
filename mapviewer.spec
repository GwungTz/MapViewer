# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build definition (one-folder, windowed).

Usage:
    pyinstaller mapviewer.spec
"""

from pathlib import Path

project = Path(SPECPATH)

datas = [
    (str(project / "web"), "web"),
    (str(project / "LICENSE"), "."),
    (str(project / "THIRD_PARTY_NOTICES.md"), "."),
    (str(project / "README.md"), "."),
    (str(project / "docs"), "docs"),
]

a = Analysis(
    [str(project / "launcher.py")],
    pathex=[str(project)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "_pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MapViewLocal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MapViewLocal",
)
