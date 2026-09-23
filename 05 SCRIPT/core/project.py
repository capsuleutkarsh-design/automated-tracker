"""
The per-shot project file: 04 SCENES/<shot>/project.json.

Layers, masks, points, the in/out range and every solver setting used to live
only in the window, so closing the app - or just picking another clip - threw
the morning's roto away. A matchmove is iterated over days, so each shot now
carries its own file next to its renders.

Pure load/save/migrate, no Qt: tracker_gui.py owns when to call these, this
module only owns what the file looks like. Nothing here raises on a bad file -
a project that will not parse must never stop the artist selecting the clip.
"""

import datetime
import json
import os
from pathlib import Path

from core.version import APP_VERSION

# Bump when a key changes meaning (not when one is added - migrate() fills
# those in and an older file keeps working).
SCHEMA_VERSION = 1

PROJECT_FILENAME = "project.json"


def default_settings_2d():
    """The 2D tab's controls as they ship."""
    return {
        "model": "CoTracker3 Offline (High Accuracy)",
        "resolution": "720p (HD - Recommended)",
        "max_dimension": 720,
        "min_confidence": 0.70,
        "grid_size": 10,
        "auto_chunk": True,
        # Whole-layer backwards tracking (2.1). Off by default, because most
        # shots read best from the head; it is per shot because it is a
        # property of the plate, not a preference.
        "track_backwards": False,
    }


def default_settings_3d():
    """The 3D tab's controls as they ship."""
    return {
        "preset": "",
        "solver_engine": "",
        "camera_model": "",
        "tri_angle": 1.5,
        "overlap": 35,
        "inliers": 40,
        "frame_step": 1,
        "single_camera": True,
        "ba_refine_distortion": True,
        "use_gpu": True,
        "caspar_ba": True,
        "generate_mesh": False,
        "blender_path": "",
        # Lens delivery (1.5) and the plate's pixel shape (1.6). A file written
        # before these existed simply lacks them and migrate() fills them in,
        # which is why the defaults are the old behaviour: no undistorted plate,
        # no overscan, square pixels.
        "write_undistort": False,
        "overscan": 0.0,
        "pixel_aspect": 1.0,
    }


# How much overscan the panel will accept. Undistorting a strong barrel lens
# (k1 around -0.3) pushes the corners of the plate about 40 % outside the frame,
# so anything less than half again would lose them - see lens.pinhole_of.
MAX_OVERSCAN = 0.5

# What the artist can type a measured distance in, as metres. The solve is
# scaled in metres because that is what every DCC's unit defaults to, so the
# unit dropdown is purely a convenience at the point of typing.
SCALE_UNITS = {
    "metres": 1.0,
    "centimetres": 0.01,
    "feet": 0.3048,
    "inches": 0.0254,
}


def to_metres(value, unit):
    """
    A distance the artist typed, in metres.

    An unknown unit is treated as metres rather than raising: the dropdown can
    only offer what is in SCALE_UNITS, and a project file written by a newer
    build must not stop this one opening the shot.
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value * SCALE_UNITS.get(str(unit).strip().lower(), 1.0)


def scene_setup_enabled(camera_track, point_count=None):
    """
    Whether the Scene setup panel can be used on this shot, and why not.

    Returns (enabled, reason). `camera_track` is the parsed camera_track.json of
    the newest solve, or None when there is no solve; `point_count` is how many
    3D points were loaded, or None when they have not been looked at yet.

    Pure so the rule can be unit-tested without a window: the panel picks solved
    points, so it needs a solve, intrinsics to project them with, and points to
    pick. Each failure carries the sentence the artist is shown - the roadmap's
    rule is that they never have to guess why something is greyed out.
    """
    if not isinstance(camera_track, dict) or not camera_track.get("images"):
        return False, "Solve this shot first - scene setup works on the solved points."
    if not camera_track.get("cameras"):
        return False, "The solve carries no camera intrinsics, so its points cannot be drawn."
    if point_count is not None and int(point_count) <= 0:
        return False, "The solve produced no 3D points to pick."
    return True, ""


def default_project():
    """An empty project: what a shot that has never been saved looks like."""
    return {
        "schema_version": SCHEMA_VERSION,
        "app_version": APP_VERSION,
        "saved_at": "",
        "fps": 24.0,
        "timeline_start": 1,
        "in_point": 0,
        "out_point": -1,
        "layers": [],
        "settings_2d": default_settings_2d(),
        "settings_3d": default_settings_3d(),
        # Scale, ground and origin (1.4), in scene_transform's storage form -
        # {"scale", "rotation", "translation"} - or null for "leave the solve
        # in COLMAP's own arbitrary world".
        "scene_transform": None,
        # How long finished solves of this shot took, newest first (2.4), as
        # {"frames": n, "seconds": s, "at": iso}. The progress bar reads them so
        # its estimate is this machine's own speed rather than a guess.
        "solve_timings": [],
    }


def project_path(scenes_dir, shot_name):
    """
    Where a shot's project file lives.

    `shot_name` is the shot folder name - the clip's stem, spaces and all, the
    same name 04 SCENES already uses for its renders. Any directory part is
    dropped so passing a full clip path by mistake still lands in the right
    place rather than somewhere outside 04 SCENES.
    """
    name = Path(str(shot_name).strip()).name
    return Path(scenes_dir) / name / PROJECT_FILENAME


def save_project(path, data):
    """
    Write the project file, stamping schema, app version and the time.

    Written to a .tmp beside it and moved into place, so a crash (or a second
    save arriving mid-write) can never leave a half-written project.json that
    the next launch would have to throw away. Returns True on success.
    """
    path = Path(path)
    payload = migrate(data)
    payload["schema_version"] = SCHEMA_VERSION
    payload["app_version"] = APP_VERSION
    payload["saved_at"] = datetime.datetime.now().isoformat(timespec="seconds")

    tmp = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return True


def load_project(path):
    """
    The shot's project as a dict, or None when there is nothing usable.

    A file that will not parse is moved aside to <name>.bad rather than being
    deleted - it is the artist's work and may be recoverable by hand - and None
    comes back, so selecting the clip carries on with a fresh project.
    """
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("project file is not an object")
        return migrate(data)
    except Exception:
        try:
            os.replace(path, path.with_name(path.name + ".bad"))
        except OSError:
            pass
        return None


def migrate(data):
    """
    A copy of `data` with every key this version expects.

    Older files simply lack the newer keys; they get today's defaults so the
    shot still opens instead of the GUI having to .get() its way around every
    field. Unknown keys are left alone - a file written by a newer build keeps
    whatever it carried when this one saves it back.
    """
    out = default_project()
    if isinstance(data, dict):
        out.update(data)

    # Nested blocks are merged key by key: an older file's settings_2d has
    # fewer entries, and update() above would have replaced the whole block.
    for key, defaults in (("settings_2d", default_settings_2d()),
                          ("settings_3d", default_settings_3d())):
        block = dict(defaults)
        if isinstance(data, dict) and isinstance(data.get(key), dict):
            block.update(data[key])
        out[key] = block

    if not isinstance(out.get("layers"), list):
        out["layers"] = []

    # The delivery settings are handed straight to the exporters, so a value
    # edited by hand into the file must not reach them as a string or as
    # something outside the range the maths can honour.
    s3 = out["settings_3d"]
    s3["write_undistort"] = bool(s3.get("write_undistort"))
    try:
        s3["overscan"] = min(MAX_OVERSCAN, max(0.0, float(s3.get("overscan", 0.0))))
    except (TypeError, ValueError):
        s3["overscan"] = 0.0
    try:
        pa = float(s3.get("pixel_aspect", 1.0))
        s3["pixel_aspect"] = pa if pa > 0.0 else 1.0
    except (TypeError, ValueError):
        s3["pixel_aspect"] = 1.0

    # A scene transform that is not the three keys the exporters read is worth
    # dropping rather than passing on: a half-written one would silently move
    # the camera somewhere nobody asked for.
    st = out.get("scene_transform")
    if not (isinstance(st, dict) and "scale" in st and "rotation" in st and "translation" in st):
        out["scene_transform"] = None

    # A timing the progress bar cannot do arithmetic on is worse than none at
    # all, so anything that is not a positive pair of numbers is dropped.
    timings = []
    for entry in (out.get("solve_timings") if isinstance(out.get("solve_timings"), list) else []):
        if not isinstance(entry, dict):
            continue
        try:
            frames, seconds = int(entry.get("frames", 0)), float(entry.get("seconds", 0))
        except (TypeError, ValueError):
            continue
        if frames > 0 and seconds > 0:
            timings.append({"frames": frames, "seconds": seconds,
                            "at": str(entry.get("at", ""))})
    out["solve_timings"] = timings

    try:
        out["fps"] = float(out.get("fps") or 0) or 24.0
    except (TypeError, ValueError):
        out["fps"] = 24.0
    for key, default in (("timeline_start", 1), ("in_point", 0), ("out_point", -1)):
        try:
            out[key] = int(out.get(key))
        except (TypeError, ValueError):
            out[key] = default

    return out


# -----------------------------------------------------------------------------
# Batch solving (2.3)
#
# Selecting five shots and pressing Start used to hand all five whatever the
# controls happened to be showing - which is the settings of the sixth shot, the
# one the artist last had open. Now that every shot carries its own file, a
# batch uses each shot's own settings and only falls back to the controls for a
# shot that has never been saved. The plan is worked out here, away from the
# window, so what the artist is told and what the solver is handed can never
# disagree.
# -----------------------------------------------------------------------------
def batch_settings_plan(saved, shots, force_current=False):
    """
    Which of `shots` solve with their own saved settings and which with the controls.

    `saved` maps shot name to that shot's loaded project (or to None, or to
    nothing at all, for a shot that has never been saved); `shots` is the
    selection, in the order it will be solved. `force_current` is the artist
    saying they really do want one setting everywhere, which puts every shot in
    "current".

    Returns {"saved": [...], "current": [...], "forced": bool} - two lists of
    shot names that together are `shots`, in the order given.
    """
    saved = saved or {}
    plan = {"saved": [], "current": [], "forced": bool(force_current)}
    for name in shots:
        key = str(name)
        if not force_current and isinstance(saved.get(key), dict):
            plan["saved"].append(key)
        else:
            plan["current"].append(key)
    return plan


def batch_summary(plan):
    """
    The sentence the artist reads before a batch starts.

    Plain counts, because the one thing they need to know is whether the shot
    they just set up is about to be solved with the settings they set up, or
    with something else.
    """
    n_saved, n_current = len(plan.get("saved") or []), len(plan.get("current") or [])
    total = n_saved + n_current
    if total == 0:
        return "No shots to solve."
    shots = "shot" if total == 1 else "shots"
    own = "its own saved settings" if n_saved == 1 else "their own saved settings"
    if plan.get("forced"):
        return ("Solving %d %s with the settings on screen - each shot's saved settings "
                "are ignored." % (total, shots))
    if not n_current:
        return "Solving %d %s with %s." % (total, shots, own)
    if not n_saved:
        return "Solving %d %s with the settings on screen." % (total, shots)
    return ("Solving %d %s: %d with %s, %d with the settings on screen."
            % (total, shots, n_saved, own, n_current))


def solve_config_from_project(base, data, presets=None):
    """
    The solve config for one shot: the controls, overlaid with its saved file.

    `base` is what the 3D tab currently holds - the one config every shot used
    to get - and `data` is that shot's project. The settings that belong to the
    shot (its lens model, its step, where it sits on the timeline, its rate and
    pixel shape, its scene transform, its delivery choices) come from the file;
    the things that belong to the run rather than to the shot (the roto masks,
    the executables, the working image size) are left exactly as `base` has
    them. Masks in particular stay put: they were drawn on one clip, and the
    worker still refuses to apply them to any other.

    `presets` is the preset table, so a saved preset name can bring back the one
    value that lives only there - how much forward motion the initialiser will
    accept. Passing None keeps whatever `base` has.
    """
    cfg = dict(base or {})
    data = migrate(data if isinstance(data, dict) else {})
    s3 = data.get("settings_3d") or {}

    preset_name = (s3.get("preset") or "").strip()
    if presets and preset_name in presets:
        cfg["init_max_forward_motion"] = presets[preset_name].get(
            "init_max_forward_motion", cfg.get("init_max_forward_motion", 1.0))

    if s3.get("solver_engine"):
        cfg["solver_engine"] = s3["solver_engine"]
    # The combo stores the whole label; COLMAP only ever sees the first token,
    # which is what the window passes it too.
    cam = str(s3.get("camera_model") or "").split()
    if cam:
        cfg["camera_model"] = cam[0]

    cfg["tri_angle"] = float(s3.get("tri_angle", cfg.get("tri_angle", 2.5)))
    cfg["overlap"] = int(s3.get("overlap", cfg.get("overlap", 35)))
    cfg["inliers"] = int(s3.get("inliers", cfg.get("inliers", 40)))
    cfg["frame_step"] = max(1, int(s3.get("frame_step", cfg.get("frame_step", 1))))
    cfg["single_camera"] = bool(s3.get("single_camera", True))
    cfg["ba_refine_distortion"] = bool(s3.get("ba_refine_distortion", True))
    cfg["use_gpu"] = bool(s3.get("use_gpu", True))
    cfg["enable_caspar_ba"] = bool(s3.get("caspar_ba", True))
    cfg["generate_mesh"] = bool(s3.get("generate_mesh", False))
    cfg["write_undistort"] = bool(s3.get("write_undistort", False))
    cfg["overscan"] = float(s3.get("overscan", 0.0))
    cfg["pixel_aspect"] = float(s3.get("pixel_aspect", 1.0))

    # An empty Blender path means "find it yourself", which is what the window
    # sends as None - an empty string would be read as a path that is not there.
    blender = (s3.get("blender_path") or "").strip()
    if blender:
        cfg["blender_path"] = blender

    cfg["fps"] = float(data.get("fps") or 0) or cfg.get("fps")
    cfg["timeline_start"] = int(data.get("timeline_start", cfg.get("timeline_start", 1)))
    # Each shot's own floor and scale, rather than those of the shot that
    # happens to be open in the window.
    cfg["scene_transform"] = data.get("scene_transform")
    return cfg


# -----------------------------------------------------------------------------
# How long a solve takes (2.4)
# -----------------------------------------------------------------------------
# What to assume before anything has been timed. A hundred-frame shot lands
# around three minutes end to end on a mid-range GPU; it is only the opening
# guess, and the first finished solve of a similar size replaces it with the
# truth about this machine.
DEFAULT_SECONDS_PER_FRAME = 1.8
MIN_SOLVE_SECONDS = 20.0

# How different in length a stored solve may be and still be worth scaling from.
SIMILAR_SIZE_RATIO = 2.0

# How many timings a shot keeps. Enough to cover a re-solve at another frame
# step without the file growing for ever.
KEEP_SOLVE_TIMINGS = 6


def estimate_solve_seconds(frame_count, timings=None):
    """
    How long a solve of `frame_count` frames is likely to take, in seconds.

    A time measured on this machine beats any formula, because it already
    carries the GPU, the disk and the kind of plate. "Similar size" is generous
    - between half and twice the frames - and the stored time is scaled by the
    ratio of frame counts, since a solve costs close enough to linearly in
    frames over that range. With nothing stored, or nothing near enough in
    length, the default rate is used; either way the answer never drops below
    MIN_SOLVE_SECONDS, so a very short shot still gets a bar that moves rather
    than one that reaches the end and then waits.
    """
    try:
        frames = int(frame_count)
    except (TypeError, ValueError):
        frames = 0
    frames = max(1, frames)

    best = None
    for entry in (timings or []):
        if not isinstance(entry, dict):
            continue
        try:
            n, seconds = int(entry.get("frames", 0)), float(entry.get("seconds", 0))
        except (TypeError, ValueError):
            continue
        if n <= 0 or seconds <= 0:
            continue
        ratio = frames / float(n)
        if ratio > SIMILAR_SIZE_RATIO or ratio < 1.0 / SIMILAR_SIZE_RATIO:
            continue
        # The closest in length wins; a tie keeps the newer one, which is first.
        distance = abs(ratio - 1.0)
        if best is None or distance < best[0]:
            best = (distance, seconds * ratio)

    if best is not None:
        return max(MIN_SOLVE_SECONDS, best[1])
    return max(MIN_SOLVE_SECONDS, frames * DEFAULT_SECONDS_PER_FRAME)


def record_solve_timing(data, frame_count, seconds, keep=KEEP_SOLVE_TIMINGS):
    """
    Add a finished solve's timing to a project dict, newest first.

    Mutates and returns `data`, so the caller can save it straight back. A
    nonsense measurement (no frames, or a solve that took no time at all
    because it failed immediately) is ignored rather than stored to mislead the
    next estimate.
    """
    if not isinstance(data, dict):
        return data
    try:
        frames, secs = int(frame_count), float(seconds)
    except (TypeError, ValueError):
        return data
    if frames <= 0 or secs <= 0:
        return data
    timings = data.get("solve_timings")
    if not isinstance(timings, list):
        timings = []
    timings.insert(0, {"frames": frames, "seconds": round(secs, 2),
                       "at": datetime.datetime.now().isoformat(timespec="seconds")})
    data["solve_timings"] = timings[:max(1, int(keep))]
    return data
