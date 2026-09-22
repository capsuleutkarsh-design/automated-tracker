"""
The per-shot project file (roadmap 1.1) and the frame rate it carries (1.2).

Covers a round-trip of the work the artist actually does - two layers, an
animated mask, a trimmed range and both settings blocks - plus the three ways
loading can go wrong: an older file missing newer keys, a corrupt file, and a
shot name that is not a tidy identifier.
"""
import json

import pytest

from core import project as project_file
from core.tracking_layer import TrackingLayer
from core.version import APP_VERSION
from mask_animator import AnimatedMask


def _layers():
    """A grid layer with an animated exclusion mask, and a corner-pin layer."""
    wall = TrackingLayer("Wall", "#38bdf8", "grid")
    wall.grid_size = 12
    wall.min_confidence = 0.55
    mask = AnimatedMask("m_abc", "Exc Poly #1", "exclusion")
    mask.set_keyframe(0, [(10, 10), (100, 10), (100, 80)], "poly")
    mask.set_keyframe(24, [(20, 30), (110, 30), (110, 100)], "poly")
    wall.animated_masks.append(mask)

    pin = TrackingLayer("Screen", "#ffaa00", "cornerpin")
    pin.points = [(3, 10.0, 20.0), (3, 90.0, 22.0), (3, 88.0, 70.0), (3, 12.0, 68.0)]
    return [wall, pin]


def _state(tmp_path, **overrides):
    data = project_file.default_project()
    data["fps"] = 25.0
    data["timeline_start"] = 1001
    data["in_point"] = 4
    data["out_point"] = 57
    data["layers"] = [l.to_config_dict() for l in _layers()]
    data["settings_2d"]["max_dimension"] = 1080
    data["settings_2d"]["resolution"] = "1080p (Full HD)"
    data["settings_2d"]["min_confidence"] = 0.55
    data["settings_2d"]["grid_size"] = 12
    data["settings_3d"]["camera_model"] = "OPENCV (Wide / Distorted)"
    data["settings_3d"]["frame_step"] = 2
    data["settings_3d"]["blender_path"] = r"C:\Blender\blender.exe"
    data.update(overrides)
    return data


# ------------------------------------------------------------------- round trip
def test_round_trip_keeps_layers_masks_range_and_settings(tmp_path):
    path = project_file.project_path(tmp_path, "dolly_a")
    project_file.save_project(path, _state(tmp_path))
    loaded = project_file.load_project(path)

    assert loaded["fps"] == 25.0
    assert loaded["timeline_start"] == 1001
    assert (loaded["in_point"], loaded["out_point"]) == (4, 57)
    assert loaded["app_version"] == APP_VERSION
    assert loaded["saved_at"]
    assert loaded["scene_transform"] is None

    assert [l["name"] for l in loaded["layers"]] == ["Wall", "Screen"]
    wall, pin = loaded["layers"]
    assert wall["grid_size"] == 12 and wall["min_confidence"] == 0.55
    kfs = wall["animated_masks"][0]["keyframes"]
    assert sorted(int(f) for f in kfs) == [0, 24]
    assert kfs["24"]["data"][0] == [20, 30]
    assert pin["export_cornerpin"] is True
    assert len(pin["points"]) == 4

    assert loaded["settings_2d"]["max_dimension"] == 1080
    assert loaded["settings_3d"]["frame_step"] == 2
    assert loaded["settings_3d"]["blender_path"] == r"C:\Blender\blender.exe"


def test_round_trip_rebuilds_tracking_layers(tmp_path):
    path = project_file.project_path(tmp_path, "dolly_a")
    project_file.save_project(path, _state(tmp_path))
    layers = [TrackingLayer.from_config_dict(c)
              for c in project_file.load_project(path)["layers"]]

    assert [l.mode for l in layers] == ["grid", "cornerpin"]
    assert layers[0].grid_size == 12
    # JSON has no tuples; the canvas and the engine both want (frame, x, y).
    assert layers[1].points[0] == (3, 10.0, 20.0)
    mask = layers[0].animated_masks[0]
    assert mask.category == "exclusion"
    assert mask.get_keyframe_frames() == [0, 24]
    assert mask.get_interpolated_geometry(12)["points"][0] == (15.0, 20.0)


def test_grid_layer_keeps_its_clicked_points(tmp_path):
    """to_config_dict() hides points from the engine in grid mode; the file keeps them."""
    layer = TrackingLayer("Wall", "#38bdf8", "grid")
    layer.points = [(0, 5.0, 6.0)]
    cfg = layer.to_config_dict()
    assert cfg["query_points"] is None
    assert TrackingLayer.from_config_dict(cfg).points == [(0, 5.0, 6.0)]


# ---------------------------------------------------------------------- migrate
def test_migrate_fills_missing_keys():
    old = {
        "schema_version": 0,
        "fps": 30.0,
        "layers": [{"name": "Wall", "mode": "grid"}],
        "settings_2d": {"grid_size": 20},
    }
    out = project_file.migrate(old)

    assert out["fps"] == 30.0
    assert out["layers"][0]["name"] == "Wall"
    assert out["timeline_start"] == 1
    assert (out["in_point"], out["out_point"]) == (0, -1)
    assert out["scene_transform"] is None
    # the block that was there keeps its value and gains the rest
    assert out["settings_2d"]["grid_size"] == 20
    assert out["settings_2d"]["max_dimension"] == 720
    assert out["settings_3d"] == project_file.default_settings_3d()
    # migrate() must not edit the caller's dict
    assert "timeline_start" not in old


def test_migrate_repairs_unusable_values():
    out = project_file.migrate({"fps": "", "timeline_start": None, "layers": "nope"})
    assert out["fps"] == 24.0
    assert out["timeline_start"] == 1
    assert out["layers"] == []


def test_load_of_an_older_file_gains_defaults(tmp_path):
    path = project_file.project_path(tmp_path, "old_shot")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"fps": 48.0, "layers": []}), encoding="utf-8")

    loaded = project_file.load_project(path)
    assert loaded["fps"] == 48.0
    assert loaded["settings_3d"]["overlap"] == 35
    assert loaded["scene_transform"] is None


# ------------------------------------------------------------------- bad inputs
def test_corrupt_file_is_moved_aside_and_loads_as_none(tmp_path):
    path = project_file.project_path(tmp_path, "broken")
    path.parent.mkdir(parents=True)
    path.write_text('{"fps": 25.0, "layers": [', encoding="utf-8")

    assert project_file.load_project(path) is None
    assert not path.exists()
    bad = path.with_name(path.name + ".bad")
    assert bad.is_file()
    assert bad.read_text(encoding="utf-8").startswith('{"fps"')


def test_a_json_file_that_is_not_an_object_is_also_bad(tmp_path):
    path = project_file.project_path(tmp_path, "listy")
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")

    assert project_file.load_project(path) is None
    assert path.with_name(path.name + ".bad").is_file()


def test_missing_file_loads_as_none_without_writing_anything(tmp_path):
    path = project_file.project_path(tmp_path, "never_saved")
    assert project_file.load_project(path) is None
    assert not path.parent.exists()


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = project_file.project_path(tmp_path, "atomic")
    project_file.save_project(path, project_file.default_project())
    project_file.save_project(path, project_file.default_project())
    assert [p.name for p in sorted(path.parent.iterdir())] == ["project.json"]


# ----------------------------------------------------------------- project_path
@pytest.mark.parametrize("shot, folder", [
    ("my shot 01", "my shot 01"),
    ("  padded shot  ", "padded shot"),
    ("shot.v2", "shot.v2"),
])
def test_project_path_keeps_the_shot_folder_name(tmp_path, shot, folder):
    path = project_file.project_path(tmp_path, shot)
    assert path == tmp_path / folder / "project.json"


def test_project_path_drops_a_directory_part(tmp_path):
    """A caller handing over a whole clip path must not escape 04 SCENES."""
    path = project_file.project_path(tmp_path, r"C:\elsewhere\a shot")
    assert path == tmp_path / "a shot" / "project.json"


def test_a_shot_with_spaces_round_trips(tmp_path):
    path = project_file.project_path(tmp_path, "wide shot 02")
    project_file.save_project(path, _state(tmp_path))
    assert project_file.load_project(path)["fps"] == 25.0


# -------------------------------------------------------------------------- fps
def test_a_25fps_sequence_restores_25_and_feeds_the_2d_config(tmp_path):
    """
    The rate typed for a sequence is what the exporters must see.

    The GUI builds the 2D worker config from the same current_fps it restores
    from the project, so a project holding 25 has to produce a config of 25 -
    not the 24 a sequence used to be assumed to run at.
    """
    path = project_file.project_path(tmp_path, "exr plate 25p")
    project_file.save_project(path, _state(tmp_path))

    restored = project_file.load_project(path)
    assert restored["fps"] == 25.0

    current_fps = restored["fps"]
    config_2d = {
        "layers": restored["layers"],
        "max_dimension": restored["settings_2d"]["max_dimension"],
        "fps": current_fps if current_fps and current_fps > 0 else 24.0,
        "timeline_start": restored["timeline_start"],
        "in_point": restored["in_point"],
        "out_point": restored["out_point"],
    }
    assert config_2d["fps"] == 25.0
    assert config_2d["timeline_start"] == 1001
    assert config_2d["in_point"] == 4
