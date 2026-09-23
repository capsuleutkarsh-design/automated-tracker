# Automated Tracker — roadmap

**Progress (2026-09-23).** Phase 1 is complete: per-shot project files, the
frame-rate control, registering the frames COLMAP skipped, scale/ground/origin,
lens distortion delivery and pixel aspect. From phase 2: track correction and
confidence in Nuke's error column (2.1, 2.2), per-shot settings in batch and
quiet logs (2.3, 2.4), undo with a keyboard scheme (2.5) and the update check
(2.6). Phase 3 is done except the window split: the DCC verification scripts,
the installer trim and the continuous test run are in (3.2, 3.3, 3.4).
Remaining: 3.1, splitting the main window.

Three things were found by testing rather than by reading, and fixed on the way
through. A solve at Frame Step above 1 put camera keys on consecutive frames
and faked the frame rate to hide it, disagreeing with the 2D tab about the same
plate. Pixel aspect was applied twice, because the extractor de-squeezes the
frames and the exporter then told Nuke to squeeze them again. And selecting a
shot that had never been saved left the previous shot's settings in the window,
so a Frame Step of 3 set for a long take silently followed you onto the next
one, as did its layers and masks.

Still unconfirmed inside the applications themselves, because neither Nuke nor
Blender is installed here: the sign of Nuke's window translate, whether its
`.chan` importer tolerates our two comment lines, and what Blender calls the
point cloud's colour attribute. `tools/verify_nuke.py` and
`tools/verify_blender.py` answer all three in one run each.

Written 2026-09-22 after the 1.1.1 review pass. Three phases, in the order they
should be built. Each item says what it is for, how it will be built in this
codebase, which files it touches, how it will be tested, and what "done" means.
Effort is in working sessions of a few hours.

The rule for every item: the artist must never have to guess. Frame numbers,
scale, distortion and frame rate are written down in the export, and anything
the tool could not do is said in plain words in the log.

---

## Phase 1 — handoff essentials

These turn the tool from "solves a camera" into "delivers a matchmove".

### 1.1 Per-shot project file (do first, everything else builds on it)

**Why.** Layers, masks, points, in/out and settings live only in memory. A
matchmove is iterated over days; the artist needs to come back to a shot.

**Design.** `04 SCENES/<shot>/project.json`, one file per shot, written by a
debounced saver (1.5 s after any change, and on clip switch and close) and
restored when the clip is selected. Contents: schema version, 2D layers via
`TrackingLayer.to_config_dict()` (points, masks, mode, colour, name), in/out,
timeline start, fps (see 1.2), 2D settings (model, resolution, confidence,
grid size), 3D settings (preset, engine, camera model, step, checkboxes,
Blender path override), scene transform (see 1.4), and the timestamp of the
last 2D and 3D result. The QSettings added in 1.1.1 keep only app-wide
preferences; per-shot state moves here.

**Files.** New `core/project.py` (load/save/migrate, pure), `tracker_gui.py`
(hook selection, change signals, close), `gui/canvas.py` (a `from_config`
counterpart to `to_config_dict`), `core/media_pool.py` (project path helper).

**Tests.** Round-trip a project with two layers, an animated mask and in/out;
load a file with a missing field (older schema) and get defaults; a corrupt
file is renamed `.bad` and logged, never crashes selection.

**Done when** switching clips and reopening the app brings every layer, mask
and setting back exactly, and the close prompt only appears for work that is
genuinely unsaved (which should now be nothing).

**Effort.** 1 session.

### 1.2 Frame rate for image sequences (and anything the probe can't read)

**Why.** Sequences have no rate, 24 is assumed, and After Effects keys are
placed in seconds, so a 25 fps plate lands on the wrong times.

**Design.** An fps field on the 2D tab beside Timeline start, shared by both
tabs. Auto-filled from ffprobe for video files and locked; editable for
sequences, defaulting to the last value used; stored in the project file. Every
exporter already takes `fps`; the 3D worker's `eff_fps` uses it divided by
frame step. Blender scripts set `scene.render.fps` from it.

**Files.** `gui/tab_2d.py`, `tracker_gui.py`, `core/workers.py`,
`core/project.py`.

**Tests.** AE `.jsx` for a 25 fps sequence with Timeline start 1001 puts the
second key at 1/25 s; Blender script sets fps 25.

**Done when** a 25 fps EXR sequence lands on the right times in After Effects.

**Effort.** Half a session.

### 1.3 Register the frames COLMAP skipped

**Why.** Gaps in the camera are interpolated by the DCC and the plate swims
there. COLMAP can usually register the leftovers against the finished model.

**Design.** After the retry ladder picks the best model, if
`registered < extracted`: run `colmap image_registrator` from that model into
`sparse_registered`, then `bundle_adjuster` on it; adopt it if `model_stats`
shows more images and the mean reprojection error did not grow by more than a
threshold (parse it from the mapper log or `model_analyzer`). Log "registered
N more frames" or "could not register frames X–Y" listing the actual timeline
frame numbers, which the artist can then hand-fix or roto.

**Files.** `core/workers.py`, `core/colmap_model.py` (stats incl. mean error).

**Tests.** `model_stats` parsing of `model_analyzer` output; ladder chooses
the registered model only when it is better (unit test with fake stats).

**Done when** the sample dolly shot registers every frame after a deliberate
step-3 solve that leaves gaps.

**Effort.** Half a session.

### 1.4 Scale, ground and origin

**Why.** COLMAP's world is arbitrary. The first thing a matchmover does is set
scale from a known distance, put the floor on Y=0, and put the origin
somewhere meaningful. Without these, every export needs manual fixing.

**Design.** A "Scene setup" box on the 3D tab, enabled after a solve, working
on the 2D canvas of the same clip:

- Reproject the solved 3D points onto the current frame (COLMAP camera plus
  intrinsics, already parsed by `export_tools`) and draw them as small dots;
  clicking selects the nearest one within a radius.
- **Scale:** select two points, type the real distance (metres, with a unit
  dropdown). **Ground:** select three or more points, or "use auto plane".
  **Origin:** select one point, or "ground under camera on frame N".
- These produce one similarity transform (scale, rotation, translation) stored
  in the project file as `scene_transform`. `export_tools.colmap_pose_to` gains
  a world transform applied before the per-DCC basis, so every writer gets it
  for free; the PLY and ground plane use the same transform.
- A **Re-export** button on the 3D tab writes a new timestamped export folder
  from the existing solve without re-solving.

**Files.** `gui/tab_3d.py` (panel), `gui/canvas.py` (point overlay + picking),
`core/scene_transform.py` (pure maths: fit from constraints, compose, invert),
`export_tools.py` (apply transform), `tracker_gui.py` (wiring, Re-export),
`core/project.py`.

**Tests.** Transform fitting from two points and a distance; ground from three
points gives a rotation that puts them on Y=0 with det +1; origin translation;
reprojection of a transformed point through a transformed camera matches the
original pixel (the transform must be a similarity, not a warp); golden
`.chan` after scale 2 doubles translations and leaves rotations alone.

**Done when** the artist can set metres and floor on the sample shot and the
Nuke and Blender cameras land on the ground at the right size.

**Effort.** 2 sessions.

### 1.5 Lens distortion delivery

**Why.** The solve knows k1 and k2 but the exports only mention them. A
handoff needs an undistorted plate or an STMap so comp can undistort, work and
redistort; otherwise the camera only lines up on low-distortion lenses.

**Design.**

- After the solve, run `colmap image_undistorter` on the chosen model with an
  overscan option (0, 5, 10 %). Result: an undistorted plate sequence and a
  pinhole camera that matches it exactly. Both go in the export folder.
- Compute two STMaps with numpy from the COLMAP camera model (undistort and
  redistort), same size as the undistorted plate, and write them as 32-bit
  float EXR (bundle the `OpenEXR` wheel; fall back to 16-bit PNG with a
  warning if the import fails).
- Nuke script: Read of the undistorted plate as the camera's plate, a second
  Read of the original plate with an `STMap` node wired to the undistort map,
  and the redistort map on a second STMap ready for the comp output; the
  camera uses the pinhole intrinsics. Blender uses the undistorted sequence as
  background. After Effects note in the script header pointing at the maps.
- Distortion coefficients, model name and overscan are also written into
  `camera_track.json` for any other tool.

**Files.** `export_tools.py` (undistorter call, STMap maths, Nuke/Blender
writers), `core/workers.py` (call after export), `gui/tab_3d.py` (overscan
combo, "write undistorted plate" checkbox), `build/automated_tracker.spec`
(OpenEXR hidden import), `tools/pack_runtime.py`.

**Tests.** STMap of a pinhole camera is the identity; a point distorted by the
model and looked up through the undistort map returns to its undistorted
position within 0.05 px; EXR written and read back equals the array;
Nuke golden test includes both STMap nodes.

**Done when** the undistorted plate and camera line up in Nuke on the sample
shot and redistorting through the map restores the original frame.

**Effort.** 2 sessions.

### 1.6 Pixel aspect

**Why.** Anamorphic or squeezed plates solve wrong and export wrong; nothing in
the pipeline knows about non-square pixels.

**Design.** Pixel aspect field on the 3D tab, auto-filled from ffprobe's
sample aspect ratio for videos, editable for sequences, saved per shot.

The rule is: de-squeeze exactly once, before the solve, and treat everything
downstream as square. The first draft of this said the exports should carry the
aspect instead (Nuke Read `pixel_aspect`, an adjusted `haperture`, Blender
`pixel_aspect_x`), which was wrong: the extractor already de-squeezes, so the
plate the scripts point at has square pixels and telling Nuke to squeeze it
again would squeeze it twice. A camera solved on square pixels is only valid
against square pixels, and the failure from getting this wrong is a subtle
horizontal drift rather than an obvious error.

So when the aspect is not 1.0 the frames go through FFmpeg's scale filter on
the way into `images/` (movies and numbered image sequences alike), the exports
point at those de-squeezed frames rather than the artist's own squeezed plate,
and no aspect is written into any script. The source aspect and a
`plate_desqueezed` flag stay in `camera_track.json` as information, and the log
says which plate the scripts use and why. A squeezed sequence whose files are
not numbered is refused with a renumber-or-use-the-movie message, because
copying it would quietly produce a lens wrong in one axis.

**Files.** `core/media_info.py` (SAR probe), `core/workers.py` (extraction and
sequence import), `export_tools.py`, `gui/tab_3d.py`, `core/project.py`.

**Tests.** A 2:1 SAR probe; the extraction command carries the scale filter for
both a movie and a numbered sequence; a square sequence is still copied; the
Read points at the de-squeezed frames when squeezed and at the artist's own
sequence when not; no aspect knob in any script; a square shot's exports are
byte-identical to before.

**Done when** a squeezed test plate (made with ffmpeg from the sample) solves
and lines up in Nuke with the correct aspect.

**Effort.** Half a session.

---

## Phase 2 — quality of life

### 2.1 Fix a drifting 2D track

**Design.** Keep the last 2D result (tracks, visibility) in memory per layer.
On the canvas, dragging a tracked point on a frame marks a correction; a
"Re-track from here" action runs CoTracker for that point only, forward from
the corrected frame (and backward if asked), and splices the result into the
layer's arrays, then re-exports. "Track backwards" for a whole layer runs the
engine on the reversed frame array and flips the result.

**Files.** `cotracker_2d.py` (single-point re-track entry point, reverse
option), `gui/canvas.py` (drag on tracked points, correction markers),
`tracker_gui.py`. **Tests.** Splicing a corrected segment; reversed run
returns to the original order. **Effort.** 1.5 sessions.

### 2.2 Confidence into the Nuke tracker

Write `1 - confidence` into the Tracker4 error column so weak sections show in
Nuke's curve editor; same value into CSV and JSON. **Files.**
`cotracker_2d.py` writers. **Tests.** golden update. **Effort.** quarter session.

### 2.3 Per-shot settings in batch

With project files in place, batch solving uses each shot's saved 3D settings
and reports "using saved settings for N shots, defaults for M". A checkbox
"apply current settings to all selected" keeps the old behaviour. **Files.**
`tracker_gui.py`, `core/workers.py` (config per video). **Effort.** half session.

### 2.4 Quiet logs

COLMAP's INFO stream goes to the app log file only. The panel shows stage
lines, warnings, errors and the summary, with a "Show engine output" toggle.
Progress shows an estimate based on frame count and the last solve's timing.
**Files.** `core/workers.py`, `tracker_gui.py`. **Effort.** half session.

### 2.5 Undo and shortcuts

A `QUndoStack` on the canvas for add/move/delete point, mask vertex edits and
keyframe changes; Ctrl+Z / Ctrl+Y. Transport shortcuts: Space play, J/K/L,
I and O for in/out, arrows step, Home/End. Documented in a Help menu.
**Files.** `gui/canvas.py`, `gui/tab_2d.py`, `tracker_gui.py`. **Effort.**
1 session.

### 2.6 Update check

Opt-in setting; on startup fetch the GitHub latest-release tag with a 3 s
timeout in a thread; if newer than `APP_VERSION`, show a dismissible banner
with the release link. Never blocks, never downloads. **Files.**
`core/update_check.py`, `tracker_gui.py`. **Tests.** tag comparison.
**Effort.** quarter session.

---

## Phase 3 — engineering

### 3.1 Finish splitting the main window

Move the player/timeline/keyframe block into `gui/player.py`
(`PlayerController`), layer CRUD into `gui/layer_panel.py`, menus into
`gui/menus.py`, and replace the ~40 `win._*` attributes the tabs reach into
with one typed `AppContext` object passed to the tab builders. No behaviour
change; the headless end-to-end run and the smoke test are the safety net.
**Effort.** 1.5 sessions.

### 3.2 Confirm the DCC conventions

A one-page checklist plus two tiny scripts: `tools/verify_nuke.py` (run inside
Nuke: loads the `.nk`, prints camera translate/rotate on three frames and the
Read node's resolved file name at frame 1001) and `tools/verify_blender.py`
(same in Blender, plus the PLY colour attribute name). The artist runs them
once and pastes the output; any mismatch is a sign flip in one place.
**Effort.** half session plus the artist's check.

### 3.3 Trim the installer

Record which DLLs PyTorch actually loads during a real 2D track and a self-test
(list the process's loaded modules from Python after the run), then exclude the
rest in the PyInstaller spec and the runtime packer. Candidates: the 589 MB
precompiled cuDNN engine library, cuBLASLt, NVRTC. Every exclusion is verified
by the frozen self-test plus a real track before it is kept. Target: 1 to
1.5 GB off the installer. **Effort.** 1 session.

### 3.4 Continuous tests

A GitHub Actions workflow on `windows-latest` that installs numpy, Pillow,
pytest and the CPU build of PyTorch and runs the pure tests on every push.
Tests that need the GPU, COLMAP or FFmpeg get a `requires_runtime` marker and
are skipped there; they keep running locally through `BUILD.bat`. **Effort.**
half session.

---

## Order and estimate

| Step | Items | Sessions |
|---|---|---|
| 1 | 1.1 project file, 1.2 fps | 1.5 |
| 2 | 1.3 register skipped frames | 0.5 |
| 3 | 1.4 scale / ground / origin | 2 |
| 4 | 1.5 lens distortion | 2 |
| 5 | 1.6 pixel aspect | 0.5 |
| 6 | 2.2 confidence, 2.3 batch settings, 2.4 quiet logs, 2.6 update check | 1.5 |
| 7 | 2.1 track correction, 2.5 undo and shortcuts | 2.5 |
| 8 | 3.2 DCC verification scripts | 0.5 |
| 9 | 3.1 window split, 3.4 CI | 2 |
| 10 | 3.3 trim installer | 1 |

About 14 sessions. Each step ends the way 1.1.1 did: unit tests for the pure
parts, the headless end-to-end run on a 1001-numbered plate, the self-test,
a commit, and a rebuilt installer only when the step changes what ships.
