# Automated Tracker 1.1.1

2D point tracking with Meta CoTracker3 and 3D camera solves with COLMAP, in one
Windows desktop app, with exports for Nuke, After Effects, Blender and USD.
Everything is bundled; nothing is downloaded while the app runs.

Website: <https://capsuleutkarsh-design.github.io/automated-tracker/>

## What changed in 1.1.1

A full review pass. Every export now lands on the plate's real frame numbers,
the solver no longer hands you a two-frame camera, and the app tells you when
something goes wrong instead of going quiet.

**Frame numbers you can trust.** A plate numbered from 1001 (or any Timeline
Start) now comes back on 1001 in every format: 3D keys are anchored on frame 1
even when COLMAP skips the first frames, the Nuke Read node carries a frame
offset and the plate's real extension, After Effects and roto keys subtract
Timeline Start, and image-sequence folders are previewed and scrubbed directly
in the 2D tab.

**3D exports fixed for each package.** Nuke scenes are Y-up (they used to lie
on their side), the USD camera rotation is corrected, `.chan` carries vertical
FOV, the lens centre shift is written in Nuke's normalised units, and every
writer shares one pose conversion so they cannot drift apart again.

**Solver.** When the first COLMAP pass registers too few frames it retries with
a wide-baseline initial pair, then a relaxed pass, and keeps the best. On the
sample dolly shot this went from 2 of 60 frames to 60 of 60. A solve covering
under half the shot is refused with a plain explanation instead of being
reported as done. Lens models that COLMAP does not have were removed from the
list. Changing Frame Step invalidates the extracted-frame cache.

**2D tracker.** GPU chunking budgets the model's real working resolution (4K
no longer collapses to eight-frame chunks), query frames are re-clamped after
an out-of-memory retry and seeded from the frame you clicked, animated masks
respect the In point, the jump filter anchors on first visibility and scales
with resolution, corner pin is an explicit layer mode with correct corner
order, Del removes a keyframe (Shift+Del the mask), cancel works mid-track,
frames are resized on load, and the filters and overlay are vectorised.

**App.** Crashes write a log under `%LOCALAPPDATA%\AutomatedTracker\logs` and
show a dialog, closing during a job asks first and cancels cleanly, settings
persist between launches, thumbnails are keyed by content, a single frame
extractor runs at a time, and the hardware monitor stops when idle.

**Build.** One version source, installer upgrades clean out the old runtime,
the runtime bundle drops build-only packages and the unused 228 MB ffplay, and
a 105-test suite plus a headless end-to-end run guard the above.

## Download

There are two ways to get it. Both come from the files attached to this release.

### Installer (recommended)

Download **all three** files into one folder, then run the exe. The installer
is split because the bundled AI model and PyTorch runtime are larger than a
single Windows installer can hold. The exe on its own installs nothing.

| File | Size | SHA-256 |
|---|---|---|
| `AutomatedTracker_Setup_1.1.1.exe` | 2.4 MB | `5b100ed70aeaf97c8de9b1bddc73ffc0ba66a78d84baed495793c60a7ad148ca` |
| `AutomatedTracker_Setup_1.1.1-1.bin` | 1.90 GB | `44629a07b92315d0df9481792f6f9e4373b338bb2cbaeb17ff6c364811f64990` |
| `AutomatedTracker_Setup_1.1.1-2.bin` | 206 MB | `7bb43c7da193c085a999863e001750352ad0df485a70c92886cc35a515818c45` |

Setup asks for administrator rights once because it installs to Program Files.
It needs about 9 GB of disk. Installing over 1.1.0 is fine; the old runtime is
removed first.

### From source

Clone the repository, then run `SETUP.bat` once. It downloads the runtime
bundle below from this release, verifies the checksums and unpacks it beside
the code, using only tools that ship with Windows. Then start the app with
`LAUNCH_UI.bat`. The runtime bundle is unchanged from 1.1.0; the same three
files are attached here again so `SETUP.bat` finds them on this release.

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

## Licences

CoTracker3 and its weights are released by Meta under CC BY-NC 4.0, which
permits non-commercial use only. COLMAP is BSD-licensed. FFmpeg is distributed
under its own licence, included in the install.

---

## 1.1.0 (2026-09-22)

First packaged release: CoTracker3 2D tab, COLMAP 3D tab, exports for Nuke,
After Effects, Blender and USD, Timeline Start control, self-test, installer
split into `.bin` slices, runtime bundle for source checkouts.
