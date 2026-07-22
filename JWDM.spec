# -*- mode: python ; coding: utf-8 -*-
"""Tracked PyInstaller definition for JWDM test and release builds."""

import os
from pathlib import Path


project_root = Path(SPECPATH)
source_root = project_root / "src"
release_build = os.environ.get("JWDM_BUILD_KIND") == "release"

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
    analysis.binaries if release_build else [],
    analysis.datas if release_build else [],
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
    exclude_binaries=not release_build,
)

if not release_build:
    bundle = COLLECT(
        executable,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="JWDM",
    )

