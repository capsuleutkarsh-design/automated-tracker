# Automated Tracker 1.1.0

2D point tracking with Meta CoTracker3 and 3D camera solves with COLMAP, in one
Windows desktop app, with exports for Nuke, After Effects, Blender and USD.
Everything is bundled; nothing is downloaded while the app runs.

Website: <https://capsuleutkarsh-design.github.io/automated-tracker/>

## Download

There are two ways to get it. Both come from the files attached to this release.

### Installer (recommended)

Download **all three** files into one folder, then run the exe. The installer
is split because the bundled AI model and PyTorch runtime are larger than a
single Windows installer can hold. The exe on its own installs nothing.

| File | Size | SHA-256 |
|---|---|---|
| `AutomatedTracker_Setup_1.1.0.exe` | 2.4 MB | `2d076f305efc8b174f8ffd33e56a58b92980cc6eb65bd29b71222e8aead4434d` |
| `AutomatedTracker_Setup_1.1.0-1.bin` | 1.90 GB | `d43f94a7c5e3b8856a85647f3b36ea4a9df438655778b1db8a7db61fa0804b64` |
| `AutomatedTracker_Setup_1.1.0-2.bin` | 262 MB | `7d17adf3e45b92681f637b1ce59a8f9fbb8c44495d8db0b62259163afd937442` |

Setup asks for administrator rights once because it installs to Program Files.
It needs about 9 GB of disk.

### From source

Clone the repository, then run `SETUP.bat` once. It downloads the runtime
bundle below, verifies the checksums and unpacks it beside the code, using only
tools that ship with Windows. Then start the app with `LAUNCH_UI.bat`.

| File | Size | SHA-256 |
|---|---|---|
| `runtime.parts.txt` | 204 B | part list read by `SETUP.bat` |
| `runtime_1.1.0_part01.zip` | 1.83 GB | `ed979bc2f6ea0443004062778a4fe73106393970ffa4465e826e86771e906fb0` |
| `runtime_1.1.0_part02.zip` | 1.63 GB | `933fb43bb00d578cfa0eb1c2338b04caf660f15e59ad9b99f910e4b9811d072b` |

Clone into a short path such as `C:\automated-tracker`. Deep folders can push
files inside the runtime past Windows' 260-character path limit.

## Requirements

- Windows 10 or 11, 64-bit
- NVIDIA GPU with CUDA recommended. Tracking and solving fall back to the CPU
  without one.
- About 9 GB free for the install, plus space for frame caches next to your
  footage.

## What's in 1.1.0

- **2D tab, CoTracker3.** Dense grid, interactive picker and 4-point corner pin
  modes. Offline or streaming model at 512p, 720p, 1080p or original
  resolution. Named tracking layers, keyframed inclusion and exclusion masks,
  frame cache, sub-pixel loupe, matte and alpha views.
- **3D tab, COLMAP.** Shot presets for handheld, walking, drone orbit, 360° VR,
  slow, fast and long hierarchical shots. Seven lens models. Roto masks are
  passed to the solver so moving actors stay out. Batch solving from the media
  pool. Optional environment mesh.
- **Exports.** 2D: Nuke Tracker4, CornerPin2D and Roto `.nk`, After Effects
  `.jsx`, Blender `.py`, CSV, JSON, overlay `.mp4`. 3D: Nuke `.nk` and
  `.chan`, Blender `.py` and `.blend`, Alembic `.abc`, USD `.usda`, PLY, JSON.
- **Timeline Start** control so every exported key lands on the shot's real
  frame range.
- The 3D export now uses the model COLMAP actually solved rather than assuming
  the first one.
- Rebuilt interface with a consistent dark design system. Fixes for crashes,
  wrong exports and inert controls across both pipelines.
- Built-in self-test (`--selftest`) that checks folders, binaries, weights,
  packages, CUDA and a real frame extraction.

## Licences

CoTracker3 and its weights are released by Meta under CC BY-NC 4.0, which
permits non-commercial use only. COLMAP is BSD-licensed. FFmpeg is distributed
under its own licence, included in the install.
