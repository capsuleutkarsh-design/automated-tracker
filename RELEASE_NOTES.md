# Automated Tracker 1.3.0

2D point tracking with Meta CoTracker3 and 3D camera solves with COLMAP, in one
Windows desktop app, with exports for Nuke, After Effects, Blender and USD.
Everything is bundled; nothing is downloaded while the app runs.

Website: <https://capsuleutkarsh-design.github.io/automated-tracker/>

## Read this before using it on real work

The Nuke and Blender exports were rewired in this release (see below) and are
checked by automated tests, but they have still not been opened in Nuke or
Blender. Before trusting a shot to this tool, track something you have already
solved elsewhere, import it, stick a card on a feature and scrub. In Nuke, run
`tools/verify_nuke.py` from the Script Editor: it now also prints how every
ScanlineRender, Scene and STMap is wired and marks a wrong input BAD. In
Blender, run `tools/verify_blender.py`.

There is also no per-tracker error readout yet, so a solve that looks plausible
cannot yet be told from a good one except by eye.

Finally, the tracking model is for non-commercial use only; see
[Licences](#licences).

## What changed in 1.3.0

**Fixing a track works.** In 1.2.0, *Re-track* and re-exporting a corrected
result crashed every time, and the "nothing to re-track" and "no result"
warnings crashed instead of showing their message. All fixed, with tests that
drive those buttons.

**Scene Setup pickers.** Switching the Scale, Ground or Origin pick buttons on
or off crashed. Fixed.

**EXR and DPX sequences.** They were accepted when added but could not be
tracked or previewed. Now EXR frames are read and shown in sRGB, and DPX
frames are read through the bundled FFmpeg, for the 2D track, the viewer, roto
masks and the 3D solve. A frame that cannot be read gives a message naming the
file instead of a crash.

**Nuke 3D rig wired correctly.** The ScanlineRender in `camera_track_nuke.nk`
was fed the Scene as its background and the camera as its geometry, with no
camera at all. It now gets the plate as bg, the Scene as obj and the camera as
cam, and every node is written once.

**Nuke STMaps wired correctly.** The undistort STMap had its source and map
inputs swapped. Now the plate goes into src and the map into stmap.

**Nuke Tracker4.** The pasted Tracker4 now carries the column list Nuke itself
writes, so the tracks load when pasted.

**Environment mesh follows Scene Setup.** Once scale, ground or origin was set,
the mesh drifted away from the camera and points in Nuke and Blender. It now
gets the same transform as the point cloud.

**2D export to Blender.** With a timeline starting at 1001, the background
plate asked for frames that do not exist; it now starts at the timeline start.
The reference camera now fills the frame exactly (it was about 1 percent off),
also for portrait plates.

**Anamorphic roto masks.** Masks drawn on a squeezed plate now land in the
right place on the de-squeezed frames.

**RTX 50-series cards.** This version's PyTorch has no GPU code for them. Before,
tracking failed with "no kernel image is available". Now the app notices, says
so, and tracks on the CPU instead. That is slower but works. Full support needs
a newer PyTorch and is planned for a later version.

**Smaller things.**
- The 2D `_latest` folder is emptied before new results go in, so old
  corner-pin files and layer folders no longer linger. The last overlay video
  is kept.
- The viewer no longer shows frames from a 3D solve made at a frame step or
  de-squeezed, which could put points and masks in the wrong place. Those
  frames are extracted again for scrubbing.
- Blender is found by its newest installed version, not only up to 4.3.
- The installer's optional sample clip now ships only the one sample file.

**README.** New sections: a quick start, opening the results in Nuke, After
Effects and Blender, supported footage, GPU and memory needs, disk space,
install notes, troubleshooting and where to get help.

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
| `AutomatedTracker_Setup_1.3.0.exe` | 2.4 MB | `d3ac1b73b5cd5c27ad28d59f37d26cbe6546f28a03a558ad6aa85ad91158415e` |
| `AutomatedTracker_Setup_1.3.0-1.bin` | 1.53 GB | `965e6d2788ce2c43e2c4749b4d4f66fb7000bb82c6a02ffe728ca9b4f6de4f93` |

Setup asks for administrator rights once. Installing over an earlier version is
fine; the old runtime is removed first.

### From source

Clone the repository and run `SETUP.bat` once. It downloads the runtime below
from this release, checks it and unpacks it beside the code, then start the app
with `LAUNCH_UI.bat`.

| File | Size | SHA-256 |
|---|---|---|
| `runtime.parts.txt` | 204 B | part list read by `SETUP.bat` |
| `runtime_1.3.0_part01.zip` | 1.19 GB | `46049841aca5272db1affeab6da505c9f412c1a9a35dd7cfd75cc2b1a46da9f9` |
| `runtime_1.3.0_part02.zip` | 1.15 GB | `4ad18a19c10c588009bacde1b7e39f9705bb1504f7313d1597556ef4bf52857a` |

Clone into a short path such as `C:utomated-tracker`; deep folders can push
files inside the runtime past Windows' 260-character limit.

## Requirements

- Windows 10 or 11, 64-bit
- NVIDIA GPU with driver 551.61 or newer recommended; both engines fall back
  to the CPU. RTX 50-series cards track on the CPU for now.
- About 6 GB free for the install

## Licences

This release is for non-commercial use only. Do not use it for paid or client
work.

- CoTracker3 (the 2D tab): Meta releases the code and the weights under
  CC BY-NC 4.0, which permits non-commercial use only.
- COLMAP (the 3D solve): COLMAP's own code is BSD-licensed, but the bundled
  `colmap.exe` also contains SiftGPU, which the University of North Carolina
  allows for educational, research and non-profit use only and which runs in
  every 3D solve on a machine with a CUDA GPU. It also contains the LSD line
  detector (AGPL-3.0) and GPL parts (CGAL, SuiteSparse).

FFmpeg is distributed under its own licence, included in the install.

---

## 1.2.0

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

### If you used 1.1.0 or earlier, read this

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

## 1.1.1

Frame numbers, export conventions and crash handling. Superseded by 1.2.0.

## 1.1.0

First packaged release.
