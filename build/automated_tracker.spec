# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Automated Tracker.

Produces a one-folder build: build/dist/Automated_Tracker/Automated_Tracker.exe
plus its _internal folder. The exe is installed at the tracker root, beside
01 COLMAP / 02 VIDEOS / 03 FFMPEG / 04 SCENES / 06 COTRACKER, which is how
core/app_paths.py finds them when frozen.

COLMAP, FFmpeg and the CoTracker checkpoints are deliberately NOT bundled into
the exe - they are large external tools the app shells out to (or loads by
path), so the installer ships them as plain folders instead.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files, copy_metadata

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821  (SPECPATH is injected)
SRC = ROOT / "05 SCRIPT"
COTRACKER_SRC = ROOT / "06 COTRACKER"
ICON = Path(SPECPATH) / "app_icon.ico"          # noqa: F821

block_cipher = None

# --- what the analyser cannot see on its own -------------------------------
hidden = []
hidden += collect_submodules("cotracker")        # vendored, imported dynamically
hidden += collect_submodules("timm")             # CoTracker's backbone provider
hidden += [
    "torchvision",
    "torchvision.models",
    "torchvision.transforms",
    "imageio",
    "imageio.plugins.ffmpeg",
    "imageio_ffmpeg",
    "PIL.Image",
    "PIL.ImageDraw",
    "scipy",                                     # optional, pulled by some timm paths
]

# pxr (USD) is optional. Collect only the four modules export_tools imports -
# collect_submodules('pxr') drags in its test suite, including modules that
# raise on import by design.
try:
    import pxr  # noqa: F401
    hidden += ["pxr", "pxr.Usd", "pxr.UsdGeom", "pxr.Gf", "pxr.Sdf"]
except Exception:
    pass

datas = []

# Packages that look themselves up through importlib.metadata at runtime need
# their .dist-info copied in, or they raise PackageNotFoundError once frozen.
# imageio does this to discover its plugins, which is how the overlay video is
# written - without this the exe builds fine and fails only when you press Run.
for _pkg in ("imageio", "imageio_ffmpeg", "torch", "torchvision", "numpy",
             "timm", "Pillow", "safetensors", "huggingface-hub", "tqdm"):
    try:
        datas += copy_metadata(_pkg)
    except Exception:
        pass

# background images and the generated chevron icons the theme references
assets = SRC / "gui" / "assets"
if assets.is_dir():
    for f in assets.rglob("*"):
        if f.is_file():
            datas.append((str(f), str(Path("gui/assets") / f.relative_to(assets).parent)))

# CoTracker ships small config/weights-free data files alongside its modules
try:
    datas += collect_data_files("cotracker")
except Exception:
    pass
try:
    datas += collect_data_files("timm")
except Exception:
    pass

excludes = [
    "tkinter",
    "PyQt5",
    "PyQt6",
    "PySide2",
    "pytest",
    "IPython",
    "notebook",
]

a = Analysis(
    [str(SRC / "tracker_gui.py")],
    pathex=[str(SRC), str(COTRACKER_SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Automated_Tracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # A windowed build has no console, which also means a startup crash leaves no
    # trace. Set ATRACK_CONSOLE=1 to build a debug exe that prints its traceback.
    console=bool(os.environ.get("ATRACK_CONSOLE")),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Automated_Tracker",
)
