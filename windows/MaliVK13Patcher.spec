# PyInstaller spec for MaliVK13Patcher (Windows EXE).
#
# Produces a single-file EXE that bundles:
#   - core/patcher.py + core/payload_manifest.json
#   - bin/Windows/AMD64/{extract.erofs,mkfs.erofs,simg2img,img2simg,mke2fs,e2fsdroid}.exe
#   - payload/ (Mali libs + system.prop)
#
# Build: pyinstaller windows/MaliVK13Patcher.spec --noconfirm
# Output: dist/MaliVK13Patcher.exe

# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

REPO_ROOT = Path(SPECPATH).resolve().parent  # type: ignore[name-defined]

datas = [
    (str(REPO_ROOT / "core" / "patcher.py"), "core"),
    (str(REPO_ROOT / "core" / "__init__.py"), "core"),
    (str(REPO_ROOT / "payload"), "payload"),
    (str(REPO_ROOT / "bin" / "Windows" / "AMD64"), "bin/Windows/AMD64"),
]

a = Analysis(
    [str(REPO_ROOT / "windows" / "src" / "gui.py")],
    pathex=[str(REPO_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "setuptools", "pip", "_pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MaliVK13Patcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI app
    icon=None,
    version=None,
)
