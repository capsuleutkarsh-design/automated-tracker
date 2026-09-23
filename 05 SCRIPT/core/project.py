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
