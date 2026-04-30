# PyInstaller spec for MaliVK13Patcher (Linux ELF).
#
# Mirrors windows/MaliVK13Patcher.spec but bundles the Linux x86_64 toolchain
# (extract.erofs, mkfs.erofs, simg2img, img2simg, mke2fs, e2fsdroid).
#
# Build: pyinstaller linux/MaliVK13Patcher.spec --noconfirm
# Output: dist/MaliVK13Patcher (single-file ELF, run via ./MaliVK13Patcher)

# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

REPO_ROOT = Path(SPECPATH).resolve().parent  # type: ignore[name-defined]

datas = [
    (str(REPO_ROOT / "core" / "patcher.py"), "core"),
    (str(REPO_ROOT / "core" / "__init__.py"), "core"),
    (str(REPO_ROOT / "payload"), "payload"),
]

# IMPORTANT: list the toolchain ELFs under `binaries` (typecode 'b'), NOT
# `datas` (typecode 'd'). In PyInstaller's onefile mode, datas are extracted
# at runtime with mode 0644 (no execute bit) which would make subprocess.run()
# raise PermissionError when the patcher tries to invoke them.
_LINUX_TOOLS = (
    "extract.erofs",
    "mkfs.erofs",
    "simg2img",
    "img2simg",
    "mke2fs",
    "e2fsdroid",
)
binaries = [
    (str(REPO_ROOT / "bin" / "Linux" / "x86_64" / name), "bin/Linux/x86_64")
    for name in _LINUX_TOOLS
]

a = Analysis(
    [str(REPO_ROOT / "windows" / "src" / "gui.py")],
    pathex=[str(REPO_ROOT)],
    binaries=binaries,
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
)
