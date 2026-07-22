# -*- mode: python ; coding: utf-8 -*-
"""Tracked PyInstaller definition for JWDM test and release builds."""

from pathlib import Path


project_root = Path(SPECPATH)
source_root = project_root / "src"
version_file = project_root / "assets" / "JWDM.version"
icon_file = project_root / "assets" / "JWDM.ico"

analysis = Analysis(
    [str(source_root / "jwdm" / "main.py")],
    pathex=[str(source_root)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    [],
    [],
    name="JWDM",
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
    exclude_binaries=True,
    icon=str(icon_file),
    version=str(version_file),
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="JWDM",
)
