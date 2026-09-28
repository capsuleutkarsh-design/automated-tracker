# Third-party notices and credits

**Automated Tracker · © 2026 Utkarsh Tripathi · UT Community Licence 2.0**

Automated Tracker is made by **Utkarsh Tripathi** and released under the UT Community Licence 2.0
(see [LICENSE.md](LICENSE.md)). It is built on the work of the people below. Their parts keep their own
licences, and nothing in Automated Tracker's licence changes their terms.

## ⚠️ Non-commercial parts

As shipped, the two tracking engines carry non-commercial terms, so **Automated Tracker may not be used for
paid work** until they are replaced:

| Part | By | Licence |
|---|---|---|
| CoTracker3 (code in `06 COTRACKER` and the weights) | Meta AI Research | CC BY-NC 4.0 (small portions MIT / Apache-2.0). <https://github.com/facebookresearch/co-tracker> |
| SiftGPU, compiled into `colmap.exe` | Changchang Wu, University of North Carolina at Chapel Hill | Educational, research and non-profit use without fee; other use needs a written agreement from UNC |

## Programs the app runs

| Part | By | Licence |
|---|---|---|
| COLMAP (`01 COLMAP`) | Johannes L. Schönberger, ETH Zurich and UNC Chapel Hill | BSD-3-Clause for COLMAP itself. The shipped build also contains the LSD line detector (AGPL-3.0-or-later), parts of CGAL and SuiteSparse (GPL), LibRaw (LGPL-2.1 / CDDL), the GCC runtime (GPL-3.0 with the Runtime Library Exception) and Qt 5 (LGPL-3.0). Source: <https://github.com/colmap/colmap> |
| FFmpeg and ffprobe (`03 FFMPEG`) | The FFmpeg developers; build by gyan.dev | GPL-3.0-or-later build. Source: <https://ffmpeg.org> and <https://www.gyan.dev/ffmpeg/builds/> |
| Blender and Foundry Nuke (optional, not shipped) | Blender Foundation; Foundry | The exporter scripts that run inside Blender are also under MIT (see LICENSE.md, section 8) |

## Libraries inside the app

| Part | By | Licence |
|---|---|---|
| Python | Python Software Foundation | PSF License |
| Qt 6 and Qt for Python (PySide6, shiboken6) | The Qt Company and contributors | LGPL-3.0: you may replace these libraries with your own build. Source: <https://download.qt.io> |
| PyTorch, torchvision | The PyTorch contributors | BSD-3-Clause |
| NVIDIA CUDA runtime, cuDNN and other CUDA libraries (inside PyTorch) | NVIDIA | NVIDIA redistribution terms |
| Intel OpenMP runtime (inside PyTorch) | Intel | Intel Simplified Software License |
| NumPy, OpenCV, Pillow, imageio, timm, safetensors, huggingface-hub, matplotlib and others | Their authors | BSD, MIT, Apache-2.0, HPND and similar permissive licences |
| certifi, tqdm | Their authors | MPL-2.0 (tqdm also MIT) |
| PyInstaller bootloader | PyInstaller Development Team | GPL-2.0 with the Bootloader Exception (allows any licence for the built program) |
| Inno Setup (installer) | Jordan Russell, Martijn Laan | Inno Setup License |

## Artwork and media

| Part | By | Licence |
|---|---|---|
| Sample clip in `02 VIDEOS` (optional) | Stock footage | Free stock licence; not for resale on its own |

Thank you to everyone above.
