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
    # STMap delivery writes 32-bit float EXR; without these the frozen build
    # silently falls back to a 16-bit PNG that clips the map values.
    "OpenEXR",
    "Imath",
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

# --- CUDA libraries the app never reaches ----------------------------------
# PyTorch was 3.6 GB of the 4.0 GB build and nearly all of it is CUDA; after
# this list it is 3.2 GB of 3.5 GB, which is 375 MB off the installer. The list
# below was not written by reading file names: tools/measure_loaded_modules.py
# runs the self-test and a real GPU track, then reads the process's own module
# list, walks the PE import tables in torch/lib, and re-runs the whole workload
# with each candidate renamed aside. Only the four that survived that are here,
# and tools/pack_runtime.py carries the same four for the source runtime.
#
# What that measurement showed, and why most of the obvious candidates stayed:
#
#   * torch's __init__ globs torch/lib/*.dll and LoadLibraryExW's every one of
#     them on Windows, so "the process loaded it" proves nothing for that
#     folder - it only means the file was there. The import tables and the
#     rename-and-re-run test are the real evidence. (It also means a file we
#     drop is simply not globbed, so nothing raises at import. But a file we
#     *keep* whose dependency we dropped raises immediately - which is why
#     anything excluded has to take its dependents with it.)
#
#   * cudnn_engines_precompiled64_9.dll, 562 MB and the headline candidate in
#     the roadmap, is not optional: without it F.conv2d raises a RuntimeError
#     on the very first encoder layer. cudnn_heuristic64_9.dll (82 MB) fails
#     the same way - cuDNN needs it to choose an engine at all.
#
#   * cuBLASLt, the other named candidate, is a *static* import of
#     torch_cuda.dll, as are cuFFT, cuSPARSE, cuSOLVER and nvJitLink. The
#     loader refuses the whole chain if any of them is missing, so torch would
#     not import at all. 1.2 GB that cannot move.
#
#   * NVRTC was excluded, tested, and put back. It passed on this machine, but
#     cudnn_graph64_9.dll loads "nvrtc64_%d_0.dll" by name for its
#     runtime-compiled engines - the fallback cuDNN takes when no precompiled
#     engine matches the convolution. On this RTX 4080 a precompiled engine
#     always matched, so nvrtc was never asked for. On someone else's card it
#     would be, and the failure would arrive in the middle of their track.
#     caffe2_nvrtc.dll stays with it for the same reason.
#
# The 990 MB still sitting in cuDNN is reachable, but not from here. With
# torch.backends.cudnn.enabled set to False the whole cuDNN set could go, and a
# 25-frame and a 120-frame track both timed identically with it on and off
# (7.26 s against 7.23 s) - CoTracker's convolutions are not where the time
# goes. That is one line in 05 SCRIPT and it would have to be the same line for
# the source runtime and the frozen build, so it belongs with the app, not with
# the packaging. Left for whoever picks it up; the measured numbers are here so
# the argument does not have to be made twice.
#
# Names are matched case-insensitively against the file name alone.
TRIM_BINARIES = {
    # cuDNN's recurrent-network and attention engines: LSTM, GRU, fused
    # multi-head attention. CoTracker's attention is written in plain PyTorch
    # ops and there is no RNN anywhere in the app, so cudnn64_9.dll never asks
    # the shim to load this one. The single largest thing that can go.
    "cudnn_adv64_9.dll",            # 230 MB

    # The multi-GPU half of cuSOLVER. PyTorch has no bindings for the cusolverMg
    # API at all, and nothing in torch/lib names this file - not one import
    # table, not one string. Pure dead weight.
    "cusolvermg64_11.dll",          # 75 MB

    # The host-side cuRAND library. Torch's GPU random numbers come from its own
    # Philox kernels compiled into torch_cuda.dll (the curandStatePhilox symbols
    # are in there), not from this DLL, and nothing dlopens it by name. The
    # probe generated tensors on the GPU with it gone and got identical results.
    "curand64_10.dll",              # 60 MB

    # The CUDA Profiling Tools Interface, reached only through torch.profiler,
    # which the app never calls.
    "cupti64_2024.1.0.dll",         # 4 MB

    # cuDNN's precompiled convolution engines and the heuristic that picks
    # between them. These two used to be unremovable: with cuDNN enabled,
    # F.conv2d raises without them. The app now sets
    # torch.backends.cudnn.enabled = False in cotracker_2d.py, so convolutions
    # never reach cuDNN at all and neither file is ever opened. Measured on an
    # RTX 4080 SUPER over a 200-frame 12x12 grid at 720p: 9.87 s and 10.7 GB
    # peak VRAM with cuDNN, 9.91 s and 8.8 GB without - no cost, and nearly
    # 2 GB of VRAM freed for longer chunks. Probed with both hidden: self-test
    # passed, CUDA still used, the track finished, and the coordinate sum moved
    # by 1.5 parts per million, which is different convolution kernels rounding
    # differently, not different work.
    #
    # The rest of cuDNN has to stay. torch_cuda.dll imports cudnn64_9.dll
    # statically, so removing the shim or its small companions stops torch
    # importing at all - probed, and it fails before the app starts.
    "cudnn_engines_precompiled64_9.dll",   # 562 MB
    "cudnn_heuristic64_9.dll",             # 82 MB

    # imageio-ffmpeg carries its own copy of FFmpeg, the same tool we already
    # ship in 03 FFMPEG and the only thing it is used for (writing the overlay
    # video). core.app_paths.use_bundled_ffmpeg_for_imageio names ours in the
    # environment before imageio looks for one, so this duplicate can go.
    "ffmpeg-win-x86_64-v7.1.exe",   # 84 MB
}

# torch/bin holds build tools that came along in the wheel - a second copy of
# fbgemm.dll and protoc.exe, the protobuf compiler. Nothing loads either at
# runtime; the copy in torch/lib is the one that matters.
TRIM_PREFIXES = (
    os.path.join("torch", "bin") + os.sep,
)

a = Analysis(
    [str(SRC / "tracker_gui.py")],
    pathex=[str(SRC), str(COTRACKER_SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    # Lets build_app.py run one real GPU track inside the frozen process. Inert
    # unless ATRACK_VERIFY_TRACK is set - see rthook_gpu_track.py for why the
    # self-test alone is not enough once CUDA libraries are being trimmed.
    runtime_hooks=[str(Path(SPECPATH) / "rthook_gpu_track.py")],   # noqa: F821
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)


def _keep(entry):
    """True for a binary that should ship. Drops the CUDA libraries measured as
    unreachable, and torch's build-tool folder."""
    dest = entry[0]
    if Path(dest).name.lower() in TRIM_BINARIES:
        return False
    norm = dest.replace("/", os.sep)
    return not norm.startswith(TRIM_PREFIXES)


# PyInstaller 6 carries these as plain lists of (dest, source, typecode).
_before = len(a.binaries) + len(a.datas)
a.binaries = [e for e in a.binaries if _keep(e)]
a.datas = [e for e in a.datas if _keep(e)]
print("[spec] trimmed %d entries; %d binaries and %d data files remain"
      % (_before - len(a.binaries) - len(a.datas), len(a.binaries), len(a.datas)))

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
