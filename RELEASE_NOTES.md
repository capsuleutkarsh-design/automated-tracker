# Automated Tracker 1.2.0

2D point tracking with Meta CoTracker3 and 3D camera solves with COLMAP, in one
Windows desktop app, with exports for Nuke, After Effects, Blender and USD.
Everything is bundled; nothing is downloaded while the app runs.

Website: <https://capsuleutkarsh-design.github.io/automated-tracker/>

## Read this before using it on real work

The exported cameras have never been opened in Nuke or Blender. The conventions
they are written to were corrected and unit-tested against COLMAP's own
projections, but only running them in those applications proves it. Before
trusting a shot to this tool, track something you have already solved
elsewhere, import the camera, stick a card on a feature and scrub. If it slides
or the scene lies on its side, run `tools/verify_nuke.py` from Nuke's Script
Editor or `tools/verify_blender.py` from Blender; both print a report naming
what is wrong.

There is also no per-tracker error readout yet, so a solve that looks plausible
cannot yet be told from a good one except by eye.

## What changed in 1.2.0

**A shot is now a shot.** Layers, masks, points, the in and out range, timeline
start, frame rate, pixel aspect and both tabs' settings are saved per shot in
`04 SCENES/<shot>/project.json` and come back when you select the clip. A batch
solve uses each shot's own settings rather than whatever is on screen, and says
so before it starts. Selecting a shot that has never been saved starts from the
defaults, so one shot's frame step no longer follows you onto the next.

**Scale, ground and origin.** After a solve, the solved points are drawn on the
plate. Pick two and type the real distance to set scale, pick three or more for
the floor, pick one for the origin. The transform is applied to every export,
and a Re-export button rewrites them without solving again.

**Lens distortion delivery.** Optionally write an undistorted plate, a matching
pinhole camera and undistort and redistort STMaps as 32-bit float EXR, with
overscan up to 50 percent, wired into the Nuke script ready for comp.

**Fixing a track that drifts.** The last 2D result is loaded back and drawn on
the canvas. Drag a point to its right place on any frame, then re-track that
one point forward, or forward and backward, and the result is spliced in.
Layers can be tracked from the last frame first. Nuke's Tracker4 error column
now carries one minus the confidence per frame, so a weak section shows up in
the curve editor.

**Undo and a keyboard scheme.** Every canvas edit is undoable. Space, J, K, L,
arrows, Home and End, I and O for the range, comma and full stop for mask
keyframes. Press F1 for the list.

**Solves that used to fail.** Video hands COLMAP adjacent frames with almost no
parallax, so a dolly shot could register two frames out of sixty and still be
reported as finished. The solver now retries with a wide-baseline initial pair,
registers the frames it missed afterwards, and refuses to export a camera
covering less than half the shot. On the sample footage this went from 2 of 60
frames to 60 of 60.

**Frame numbers.** A solve at Frame Step above 1 used to put camera keys on
consecutive frames and fake the frame rate to hide it. Keys now land every Nth
frame across the plate's real range at its own rate. Pixel aspect was applied
twice, once by the extractor and again by the exporter; the rule is now to
de-squeeze once, before the solve, and treat everything downstream as square.

**About a gigabyte smaller.** CoTracker is transformer-heavy and its small
convolutional backbone gains nothing from cuDNN: over a 200-frame grid at 720p,
9.87 seconds and 10.7 GB peak VRAM with cuDNN against 9.91 seconds and 8.8 GB
without. So cuDNN is off, which frees nearly 2 GB of VRAM for longer chunks and
lets the build drop 643 MB that can then never be reached. Together with the
recurrent engines, the multi-GPU solver, the profiler interface and some
duplicated tools, the frozen build went from 3.89 GB to 2.81 GB.

**Also:** an opt-in check for newer releases, quieter logs with the solver's own
chatter kept to the log file, crash reports written to
`%LOCALAPPDATA%\AutomatedTracker\logs`, and 431 automated tests where 1.1.0 had
none.

## If you used 1.1.0 or earlier, read this

Two things that looked like the tool not working were bugs, and both are fixed.
It is worth re-running a shot that disappointed you.

**The 2D track came back almost empty.** On a large frame this was close to
guaranteed. The old code decided how many frames fitted in the GPU from the
resolution of your plate, but the model resizes every chunk to its own fixed
working size, so it was measuring the wrong thing. On a 2560x1440 clip at
Original or 1080p it concluded almost nothing fitted and dropped to eight-frame
windows on a model that wants sixty. Quality collapses at that size, the points
wander, and two culling bugs then threw most of them away: the filter judged
every track from frame 1 even when the point was not visible there, and the
"this point jumped too far" limit was a fixed pixel count, far too tight on a
big frame. All three are fixed. Run the same clip at the same settings and you
should get tracks across the grid, with no warning that your card is too small.

**The 3D solve showed a point cloud and dozens of cameras instead of one moving
camera.** That is COLMAP's own viewer, which the "Open COLMAP 3D Viewport" menu
item opens, and it draws one camera frustum per solved frame the way a
photogrammetry tool does. It is a sign the solve worked. Your moving camera is
in the exported files: `import_to_blender.py`, `camera_track_nuke.nk`,
`camera_track.chan` and `camera_track.usda` each contain a single animated
camera.

**A dolly or push-in shot solved only a couple of frames and still said it
finished.** Video hands COLMAP neighbouring frames with almost no parallax, so
its first attempt at a starting pair triangulates nothing. The solver now
retries with a wide-baseline pair, registers the frames it missed afterwards,
and refuses to export a camera covering less than half the shot. On the sample
footage this took a solve from 2 frames out of 60 to 60 out of 60.

## Privacy and offline use

The application makes exactly one network request, and only if you ask it to:
the opt-in update check in the Help menu, which reads GitHub's releases API and
downloads nothing. It is off by default.

Everything else is local. The AI model loads from the checkpoint bundled in the
install, by path, with no download fallback. COLMAP, FFmpeg and PyTorch run as
local processes on local files. There is no telemetry and no crash reporting
service, and the whole pipeline works with the machine disconnected.

What it writes: solves and exports into `04 SCENES` beside your footage, and a
rotating log, thumbnails and any crash reports under
`%LOCALAPPDATA%\AutomatedTracker`. Note that logs and crash reports contain file
paths and clip names, so strip them before sending one anywhere if the job is
covered by an NDA.

## Download

### Installer (recommended)

Download both files into one folder and run the exe. The exe alone installs
nothing.

| File | Size | SHA-256 |
|---|---|---|
| `AutomatedTracker_Setup_1.2.0.exe` | 2.3 MB | `9db818408f9034f5ff7008137ff5daee9d300e14f99a2bb41c1e856de7b3ac8c` |
| `AutomatedTracker_Setup_1.2.0-1.bin` | 1.53 GB | `44005368346f4f7a753866438ea23540653378f1c2e22e175e0bb0382515a2b3` |

Setup asks for administrator rights once. Installing over an earlier version is
fine; the old runtime is removed first.

### From source

Clone the repository and run `SETUP.bat` once. It downloads the runtime below
from this release, checks it and unpacks it beside the code, then start the app
with `LAUNCH_UI.bat`.

| File | Size | SHA-256 |
|---|---|---|
| `runtime.parts.txt` | 204 B | part list read by `SETUP.bat` |
| `runtime_1.2.0_part01.zip` | 1.19 GB | `46049841aca5272db1affeab6da505c9f412c1a9a35dd7cfd75cc2b1a46da9f9` |
| `runtime_1.2.0_part02.zip` | 1.15 GB | `4ad18a19c10c588009bacde1b7e39f9705bb1504f7313d1597556ef4bf52857a` |

Clone into a short path such as `C:\automated-tracker`; deep folders can push
files inside the runtime past Windows' 260-character limit.

## Requirements

- Windows 10 or 11, 64-bit
- NVIDIA GPU with CUDA recommended; both engines fall back to the CPU
- About 6 GB free for the install

## Licences

CoTracker3 and its weights are released by Meta under CC BY-NC 4.0, which
permits non-commercial use only. COLMAP is BSD-licensed. FFmpeg is distributed
under its own licence, included in the install.

---

## 1.1.1

Frame numbers, export conventions and crash handling. Superseded by 1.2.0.

## 1.1.0

First packaged release.
