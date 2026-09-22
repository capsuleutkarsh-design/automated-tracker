# Automated Tracker — issue report

Code review of the local project on 2026-09-22, at commit `6443182`.
Three passes: 2D tracking pipeline, 3D solve and exporters, app shell and
build. Every item was verified by reading the code; anything not fully
confirmed is marked *uncertain*.

Severity: **S1** wrong results or crash in normal use · **S2** breaks in a
common situation or loses data · **S3** slow, fragile or misleading ·
**S4** cleanup and hygiene.

Summary: 14 S1, 17 S2, 20 S3, 14 S4. No automated tests exist.

---

## A. Wrong results (S1)

| # | Where | Trigger | Effect | Fix |
|---|---|---|---|---|
| A1 | `05 SCRIPT/export_tools.py:1211` | Any 3D solve where COLMAP fails to register frame 1 (very common) | `offset = start_frame - min(frame)` shifts every exported key by the number of unregistered leading frames, even at the default Timeline Start | Use `start_frame - 1`; the extractor always numbers from 1 |
| A2 | `export_tools.py:700-705` | USD export | `Gf.Matrix4d` is row-vector; the 3×3 block must be `Rᵀ`. Rotation is written untransposed, so camera position is right and orientation is inverted | Transpose the rotation block |
| A3 | `export_tools.py:879, 942-945` | Nuke 3D export with Timeline Start ≠ 1 | Read node gets `first 1001` but files are `frame_000001.jpg…`; Nuke looks for frames that don't exist | Add a `frame` offset expression on the Read node |
| A4 | `export_tools.py:942`, `483` | Source is a PNG / EXR / TIF sequence | Nuke Read and Blender import hardcode `.jpg`; `workers.py:86` keeps the source suffix | Pass the real extension through |
| A5 | `export_tools.py:748-752, 830-834` | Nuke 3D export | Camera, cloud and card use the Blender Z-up world matrix `(x, z, −y)`; consistent, so reprojection matches, but the whole rig lies on its side in Nuke's Y-up world | Use `diag(1, −1, −1)` for the Nuke world |
| A6 | `05 SCRIPT/cotracker_2d.py:383` | 2D track at "Original" or 1080p on a 4K clip | Chunk size is budgeted from input H×W, but CoTracker resamples every chunk to its model resolution, so activations don't scale with input. Chunk collapses to `MIN_CHUNK` (8 frames) on any GPU, tracking quality collapses, user is told the GPU is too small | Budget from `model_resolution`; the Resolution combo's "more precise" text at `tab_2d.py:174` is also false |
| A7 | `cotracker_2d.py:476, 501` | Manual point on a late frame (e.g. 200), first block runs out of memory | Query `t` is clamped to `chunk−1` before the chunk is halved again on OOM; predictor indexes out of range → CUDA device-side assert, GPU context poisoned for the session | Re-clamp after every halving |
| A8 | `cotracker_2d.py:476, 521-523` | Manual point placed at frame t inside a chunk | Next block is seeded from a frame *before* the query, where only the query frame was constrained; rest of the track starts from a bogus position | Seed from the query frame or later |
| A9 | `cotracker_2d.py:271-275, 1448` | Animated mask with an In point or frame step set | Keyframes are absolute canvas frames; the grid seed uses `get_interpolated_geometry(0)` and the filter uses local `t`. Culling is shifted by the In point | Convert local index → absolute frame (`in_pt + t*step`) before evaluating masks |
| A10 | `cotracker_2d.py:558` | Manual point not visible at frame 0 | Filter anchors `last_valid` on frame 0 regardless of visibility; first visible sample reads as a jump, whole track culled | Anchor on the first visible frame |
| A11 | `cotracker_2d.py:1495` | Different working resolutions | `max_jump_distance` is in processing pixels: 30% of the frame at 512p, 4% at 4K | Scale the threshold by resolution |
| A12 | `05 SCRIPT/core/tracking_layer.py:61`, `cotracker_2d.py:1519, 1531` | Any 4-point layer, e.g. a 2×2 grid | Treated as corner pin; grid order is TL,TR,BL,BR but corner pin expects TL,TR,BR,BL → bow-tie quad | Only corner-pin layers created in corner-pin mode, and order the corners explicitly |
| A13 | `05 SCRIPT/gui/tab_3d.py:82-83`, `tracker_gui.py:692` | Choosing SPHERICAL or EUCM | Passed straight to `--ImageReader.camera_model`; COLMAP has neither, feature extraction fails | Remove them, or map to real COLMAP models |
| A14 | `05 SCRIPT/core/workers.py:76-77, 385` | Re-solving after changing Frame Step | Existing `images/` reused regardless of the step that produced them; `eff_fps = src_fps / current_step` is wrong and sampling is wrong, silently | Stamp the cache with the step and re-extract on mismatch |

Probable but unconfirmed (*uncertain*):

- `export_tools.py:741` — `.chan` column 8 is written as horizontal FOV; Nuke documents it as vertical. On 16:9 the imported focal is ~1.8× too wide. Also no `rot_order XYZ` note; Nuke's default is ZXY.
- `export_tools.py:266` vs `:808` — Blender writes `shift_x = (w/2 − cx)`, Nuke writes `win_u = (cx − w/2)`, and `win_v` does not flip for COLMAP's y-down. At least one sign is wrong; Nuke's `win_translate` may also be in half-width units.
- `export_tools.py:81-90` — `parse_colmap_cameras` falls back to `cx = width/2` for FISHEYE / FOV / THIN_PRISM models instead of reading params.

## B. Crashes, data loss and broken controls (S2)

| # | Where | Trigger | Effect | Fix |
|---|---|---|---|---|
| B1 | `tracker_gui.py:539, 583, 586` + `tab_2d.py:57` | Any refresh of the video combo | `clear()`/`addItem()` fire `currentTextChanged` → `_on_2d_video_selected`, then it is called again explicitly. Two `FrameExtractorWorker`s start, two ffmpeg processes write the same `images/` folder, the first QThread loses its last Python reference while running (probable hard abort, *uncertain*) | Block signals during repopulate; keep one extractor and cancel before starting another |
| B2 | `tracker_gui.py` (no `closeEvent`) | Closing the window during a solve | Worker destroyed mid-run, `colmap.exe` orphaned, drawn layers / masks / in-out points lost without a prompt | Add `closeEvent` that cancels workers, waits, and asks about unsaved work |
| B3 | whole app | Any exception in a Qt slot after startup, in the frozen exe | No `sys.excepthook`; `sys.stderr` is `None` in the windowed build; the error vanishes and the UI just stops responding to that action | Install an excepthook that logs to `%LOCALAPPDATA%\AutomatedTracker` and shows a dialog |
| B4 | `05 SCRIPT/gui/canvas.py:463-467` vs `tab_2d.py:279` | Pressing Del on a mask | Deletes the whole mask; tooltip says it deletes the keyframe | Make Del delete the keyframe, add an explicit delete-mask action |
| B5 | `canvas.py:259-261, 334-336` | Delete mask 1 of 2, draw again on the same frame | Ids are `m_{len+1}_{frame}` → duplicate id; `selected_mask_id` lookups edit the wrong mask | Use a counter or uuid |
| B6 | `workers.py:501`, `cotracker_2d.py` | Pressing Cancel during a 2D track | Flag is only checked between videos; the tracker itself has no hook. Tooltip at `tab_2d.py:365` promises otherwise | Check the flag between chunks |
| B7 | `workers.py` cancel path | Pressing Cancel during a 3D solve | `terminate()` with no `wait()`, no timeout on any stage; finish message says "failed to reconstruct" rather than "cancelled" | Wait with timeout, report cancelled |
| B8 | `workers.py:434-464` | COLMAP prints a byte the locale can't decode | `UnicodeDecodeError` is caught, the step returns False, **COLMAP keeps running on the GPU** while the next step starts against the same database | `encoding="utf-8", errors="replace"`; kill the process on failure |
| B9 | `workers.py` per-video body | EXR input (`Image.open` at :135), or `copy2` PermissionError | Exception escapes `run()`, thread dies, `finished_signal` never fires, GUI stays locked | try/except around the per-video body, always emit finished |
| B10 | `workers.py:421-422` | `export_all_formats` returns `success: False` | Still counted as solved and reported "Successfully tracked and exported" | Check the result |
| B11 | `cotracker_2d.py:1394` | `HAS_COTRACKER` is False | Never checked; NameError instead of a clear message | Check it or remove it |
| B12 | `tracker_gui.py:1068, 1161` | Import a different clip with the same file name | Thumbnails are `%TEMP%/thumb_<stem>_<n>.jpg`, keyed by stem only, never invalidated; old clip's frames shown; files accumulate forever | Key by full path + mtime; clean on exit |
| B13 | `tracker_gui.py:1160` | Scrubbing before the frame cache exists | `sec = val / 24.0` ignores `current_fps`; wrong frame on 30 fps clips (comment at :193 claims this was fixed) | Use `current_fps` |
| B14 | `tracker_gui.py:451, 533` | Importing a multi-GB clip | `shutil.copy2` runs on the GUI thread; UI freezes; a permission error is swallowed (see B3) | Copy in a worker |
| B15 | `tracker_gui.py:868, 983` (AE), `mask_animator.py:199` (roto) | Timeline Start ≠ 1 | AE exporters offset *time* by `timeline_start` (1001 → keys at ~41 s) and comp duration ignores step; roto export ignores Timeline Start | Convert frames→seconds after offsetting frame numbers |
| B16 | `tracker_gui.py:406, 413` | COLMAP installed as `01 COLMAP/colmap.exe` rather than `bin/` | `COLMAP_EXE.parent.parent` resolves to the install root; `COLMAP_BAT` at :42 exists for this and is unused | Use `COLMAP_BAT` / `app_paths` |
| B17 | `tracker_gui.py:1527`, `tracking_layer.py`, `mask_animator.py` | Editing on the canvas while a config is being built | `to_config_dict` and `MaskKeyframe.to_dict` return live lists by reference; canvas mutates in place | `deepcopy` |

## C. Performance and fragility (S3)

| # | Where | Problem | Fix |
|---|---|---|---|
| C1 | `cotracker_2d.py:119, 1231, 1356` | All frames stacked at original resolution before resizing, `.contiguous()` copies, and the overlay builds a third full list. 700 frames of 4K ≈ 17 GB × 3 | Resize while loading; stream the overlay |
| C2 | `cotracker_2d.py:271-294` | Mask filtering is pure-Python ray casting, T×N×masks (≈3.5 M tests at 700×2500×2) | Rasterise mask per frame, index with numpy |
| C3 | `cotracker_2d.py:558` | Confidence filter is a T×N Python loop | Vectorise |
| C4 | `cotracker_2d.py:1209-1231, 1254-1282` | Overlay renderer: T×N×trail_len PIL calls (~26 M at 50×50) | Draw with numpy/OpenCV per frame |
| C5 | 2D Blender exporters | Two script lines per (track, frame) via `+=`; 50×50×700 ≈ 3.5 M lines, Blender won't load it | Emit data as a JSON blob and loop inside Blender |
| C6 | `canvas.py` `_scaled_pixmap` | Cache keyed on `cacheKey()`, so every playback frame re-runs SmoothTransformation on a full-res pixmap | Cache by frame index at display size |
| C7 | `canvas.py:299` | `masks_changed` fires on every mouse-move during a drag → list rebuild per event | Emit on release |
| C8 | `export_tools.py` point cloud | Serialised three times (PLY, USD, JSON in the Blender script up to 200k points) plus a per-point colour loop inside Blender; `parse_colmap_points3D` ≈ 300 B/point | Parse to numpy once, write PLY, have Blender read the PLY |
| C9 | `export_tools.py:317, 882` | Two 300-iteration unseeded RANSAC passes → Blender and Nuke can get different ground planes | Fit once, seed it |
| C10 | `export_tools.py:322, 565` | Ground-plane normal is computed then discarded; plane location is the closest point to origin, not the inlier centroid | Apply the rotation, use the centroid |
| C11 | `export_tools.py:1112` | Blender bake timeout 90 s is short for large clouds; `proc.stderr` is always `None` because stderr is merged | Raise timeout, read stdout |
| C12 | `export_tools.py` | Only TXT sparse models are read; a `sparse/N` with only `.bin` reports "TXT files not found" | Run `model_converter` or read .bin |
| C13 | `workers.py` `_latest` sync | Never clears stale files from earlier runs | Clear before copy |
| C14 | `workers.py:177/188` | Log says overlap default 20, command uses 35 | Align |
| C15 | `cotracker_2d.py:96-112` | FFmpeg fallback extracts the whole clip at full res, ignores in/out, `except: pass` hides the real error | Honour range, surface error |
| C16 | `cotracker_2d.py:80` | `scene_images_dir` is used whenever it exists, with no check it matches the clip's frame count / step | Validate |
| C17 | `05 SCRIPT/core/hardware.py` | No NVML → fallback thread spawns `nvidia-smi` every 2 s for the life of the process; `stop()` never called; error branch labels "GPU: Ready" (`tracker_gui.py:471`) | Detect once; stop on close |
| C18 | `tracker_gui.py` | No `QSettings`: Blender path, preset, geometry, last clip reset each launch | Persist |
| C19 | `tracker_gui.py:236, 1075, 1135, 1175` | Silent `except: pass` → empty canvas with no message when ffmpeg/thumbnail path is broken | Log + status message |
| C20 | `workers.py:529` | Worker logs `str(e)` only, no traceback | `traceback.format_exc()` |

## D. Build, release and repo hygiene (S4)

| # | Where | Problem | Fix |
|---|---|---|---|
| D1 | `build/build_app.py:30`, `installer.iss:26`, `tools/pack_runtime.py:30`, `tracker_gui.py:184, 397, 424`, `LAUNCH_UI.bat:18`, `export_tools.py:336` | Version in six places; installer says 1.1.0, About box says V001.1 | One `VERSION` file read by all |
| D2 | `installer.iss:23` | `#ifexist "version.txt"` is relative; if ISPP resolves against cwd (*uncertain*) the fallback is used silently | `{#SourcePath}\version.txt` |
| D3 | `installer.iss` | No `[InstallDelete]` for `{app}\_internal` → stale PyInstaller files accumulate across upgrades | Add it |
| D4 | `installer.iss` `[UninstallDelete]` | `{localappdata}` under admin elevation resolves to the elevating account | Use `{userappdata}`-style constants or a per-user run step |
| D5 | `SETUP.bat:24` | Always pulls `releases/latest`; an old checkout downloads a newer runtime; `runtime.parts.txt` is unversioned | Pin to the tag matching the checkout |
| D6 | `tools/pack_runtime.py`, installer | Ship build-only packages (PyInstaller, hooks-contrib) and the unused 228 MB `ffplay.exe` | Exclude |
| D7 | `build/BUILD.bat` | `--console` / `--no-verify` exist in `build_app.py` but the batch never exposes them | Pass through |
| D8 | `_ATTIC/` (869 MB) | 835 MB torch headers (ignored), 49.5 MB COLMAP test exes **committed**, `05 SCRIPT/gui_backup_v1` **committed** despite the gitignore comment, stale launch scripts. Nothing live references it | `git rm -r _ATTIC`, ignore it |
| D9 | `.gitignore:27-28` | Stale paths; `selftest.txt` and `.claude/` not ignored (`launch.json` is a personal dev config, currently tracked) | Update |
| D10 | `05 SCRIPT/launch_gui.bat`, `RUN_BATCH.bat` → `batch_reconstruct.bat` | Duplicate launcher; parallel 10 KB CLI COLMAP pipeline not shipped and duplicating `workers.py` (*unverified whether it still matches*) | Delete or fold into the worker |
| D11 | `tracker_gui.py:25` | Error text tells users to run `launch_gui.bat`, the stale duplicate | Point at `LAUNCH_UI.bat` |
| D12 | `LAUNCH_UI.bat:15` | Puts COLMAP's Qt5 plugin dir in `QT_PLUGIN_PATH` for the Qt6 PySide6 process; harmless today, fragile | Drop it; `workers.py:50` sets its own env |
| D13 | `tracker_gui.py:697/706` | Duplicate `"init_max_forward_motion"` key | Remove one |
| D14 | Dead code | `cotracker_2d.py`: unused `subprocess`, `torch.nn.functional`, duplicate `shutil`, `mode` at :1366, `inclusion_box/exclusion_box` paths, `feather_radius`/`invert`, `clear_points/clear_boxes`; `canvas.py`: unused `is_pt_in_mask_canvas` import; `mask_animator.py`: unused `math`, `total_frames`; `media_info.py`: unused `os`, `BASE_DIR`; `workers.py`: unused `hidden_kwargs`; `sys.path` hack duplicated at `export_tools.py:1127/1183` | Remove |

## E. Structure and maintainability

- `tracker_gui.py` (1,805 lines) should be split: `PRESETS` (60-178) → `core/presets.py`; `run_selftest` (1623-1787) → `core/selftest.py`; `apply_dark_palette` → `gui/theme.py`; the player/timeline/keyframe/in-out block (1032-1335) → a `PlayerController`; layer CRUD (853-1030) → `gui/layer_panel.py`; menus (268-327) → `gui/menus.py`.
- "Find `_latest`, else newest timestamp dir, else legacy folder" exists four times (567-571, 767-782, 1363-1376, 1432-1455). Media-pool scan exists twice (541-553, 648-657).
- Exporters duplicate the world/camera basis matrices, image-dir lookup, sensor math and the `sorted_images` prologue across Blender / USD / chan / nk. One `colmap_pose_to(convention)` and one `camera_intrinsics_mm()` would have prevented A2 and A5.
- `export_blender_script` is ~400 lines of f-string; `TrackerWorker.run` ~390 lines; `process_cotracker_2d` ~370; `paintEvent` ~210.
- 2D duplicates: `point_in_poly` ≡ `point_in_poly_canvas`; `is_point_in_mask` vs `is_pt_in_mask_canvas` differ only in default type; Tracker4 row builder, Blender camera preamble, overlay renderer and per-layer vs single-layer export each exist twice; find-or-create mask duplicated in `canvas.py:251-264` / `:326-339`.
- The 2D domain model lives inside a QLabel (`canvas.layers`); `tab_2d.py` wires ~40 `win._*` attributes, an undocumented contract with `tracker_gui.py`.
- `detect_sequence_start` uses the *last* digit run, `parse_colmap_images` the *first*.
- Hard-coded hex colours (`#00ff88`, `#ff4b4b`, `#00d2ff`) throughout `tracker_gui.py` instead of the `theme` tokens it already imports.

## F. Tests to add first

None exist. These are pure functions, need no Qt or GPU, and each catches a listed bug:

| Test | Catches |
|---|---|
| Timeline offset with frame 1 unregistered | A1 |
| Pose conversion per target (Blender, Nuke, USD) by reprojecting a known 3D point | A2, A5, chan/shift uncertainties |
| `qvec2rotmat` / `rotmat2euler` round trip; `parse_colmap_images` against a hand-written `images.txt`; `parse_colmap_cameras` per model | A13 fallback |
| Golden-file tests for `.chan`, `.nk`, Tracker4, roto, AE `.jsx` (2 frames, 4 points, Timeline Start 1001) | A3, A4, A12, B15 |
| `auto_chunk_size` with mocked `mem_get_info` | A6 |
| `run_cotracker_chunked` with a fake model: seed frame, clamp after re-halving, overlap stitching, OOM retry | A7, A8 |
| `filter_trajectories_by_animated_masks` with In point and step | A9 |
| `filter_tracks_confidence` with invisible frame 0; jump threshold vs resolution | A10, A11 |
| `frame_number`, `get_interpolated_geometry` (exact / clamp / interp / mismatch), `point_in_poly`, `scale_mask`, `generate_grid_points_with_masks` | regressions while refactoring |
| `find_best_model` with sparse/0 (2 images) vs sparse/1 (500) | export of the wrong model |

## Suggested order

1. **Export math**: A1, A2, A3, A4, A5, then verify the chan FOV and principal-point signs against Nuke.
2. **2D correctness**: A6, A7, A8, A9, A10, A11, A12.
3. **Safety net**: B3 excepthook, B2 closeEvent, B1 extractor lifecycle, B6/B7 cancel, B8/B9 worker guards.
4. **Data loss and controls**: B4, B5, B12, B13, B15, C18 settings.
5. **Performance**: C1–C6, C8–C10.
6. **Hygiene**: D8 `_ATTIC`, D1 single version, D9/D10 stale files, D14 dead code.
7. **Structure**: split `tracker_gui.py` and the exporters, adding the tests in F as each area is touched.
