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
    }


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
        # Reserved for roadmap 1.4 (scale, ground and origin). Written as null
        # from the start so a file saved today loads unchanged once it lands.
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
