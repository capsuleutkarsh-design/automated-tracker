"""
The project-file end of scene setup (roadmap 1.4, 1.5, 1.6).

What a shot remembers about its world - the similarity transform that sets
scale, ground and origin - and about its delivery: overscan, whether an
undistorted plate is written, and the shape of a pixel. Pure: no Qt, no COLMAP.
"""
import json

import numpy as np
import pytest

from core import project as project_file
from core import scene_transform


def _levelled_transform():
    """A transform with all three parts set, as the panel would build one."""
    floor = [(0.0, 1.0, 0.0), (2.0, 1.05, 0.0), (0.0, 1.0, 3.0)]
    return scene_transform.build(
        points_for_ground=floor,
        scale_pair=((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)),
        real_distance=1.5,
        origin_point=(0.0, 1.0, 0.0),
        up=scene_transform.COLMAP_UP)


# =============================================================================
# THE TRANSFORM SURVIVES THE PROJECT FILE
# =============================================================================
def test_the_scene_transform_round_trips_through_the_project_file(tmp_path):
    original = _levelled_transform()
    data = project_file.default_project()
    data["scene_transform"] = original

    path = tmp_path / "shot" / "project.json"
    assert project_file.save_project(path, data)
    restored = project_file.load_project(path)["scene_transform"]

    assert restored is not None
    assert restored["scale"] == pytest.approx(original["scale"], abs=1e-12)
    assert np.allclose(restored["rotation"], original["rotation"], atol=1e-12)
    assert np.allclose(restored["translation"], original["translation"], atol=1e-12)

    # It has to be plain JSON on the way out, or a numpy scalar would take the
    # whole save down and the shot would lose the morning's work with it.
    raw = json.loads(path.read_text(encoding="utf-8"))["scene_transform"]
    assert isinstance(raw["scale"], float)
    assert all(isinstance(v, float) for row in raw["rotation"] for v in row)


def test_a_restored_transform_still_moves_points_the_same_way(tmp_path):
    original = _levelled_transform()
    data = project_file.default_project()
    data["scene_transform"] = original
    path = tmp_path / "shot" / "project.json"
    project_file.save_project(path, data)

    restored = project_file.load_project(path)["scene_transform"]
    probe = np.array([[1.0, 2.0, 3.0], [-4.0, 0.5, 6.0]])
    assert np.allclose(scene_transform.apply_to_points(restored, probe),
                       scene_transform.apply_to_points(original, probe), atol=1e-12)


def test_no_transform_saves_and_loads_as_none(tmp_path):
    path = tmp_path / "shot" / "project.json"
    project_file.save_project(path, project_file.default_project())
    assert project_file.load_project(path)["scene_transform"] is None


# =============================================================================
# MIGRATION OF AN OLDER FILE
# =============================================================================
def _file_from_before_these_settings_existed():
    """A 1.1.1 project: every key that shipped then, none that did not."""
    return {
        "schema_version": 1,
        "app_version": "1.1.1",
        "fps": 25.0,
        "timeline_start": 1001,
        "in_point": 0,
        "out_point": -1,
        "layers": [],
        "settings_2d": {"model": "CoTracker3 Offline (High Accuracy)"},
        "settings_3d": {
            "preset": "Handheld / Walking (Recommended)",
            "frame_step": 3,
            "blender_path": "",
        },
        "scene_transform": None,
    }


def test_migration_fills_the_new_keys_for_an_older_file():
    out = project_file.migrate(_file_from_before_these_settings_existed())
    s3 = out["settings_3d"]
    assert s3["write_undistort"] is False
    assert s3["overscan"] == 0.0
    assert s3["pixel_aspect"] == 1.0
    assert out["scene_transform"] is None
    # ...without losing what the older file did carry.
    assert s3["frame_step"] == 3
    assert s3["preset"] == "Handheld / Walking (Recommended)"
    assert out["timeline_start"] == 1001


def test_an_older_file_on_disk_opens_with_the_new_keys(tmp_path):
    path = tmp_path / "shot" / "project.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_file_from_before_these_settings_existed()), encoding="utf-8")

    loaded = project_file.load_project(path)
    assert loaded["settings_3d"]["pixel_aspect"] == 1.0
    assert loaded["settings_3d"]["overscan"] == 0.0
    assert loaded["settings_3d"]["write_undistort"] is False


def test_migration_keeps_settings_the_artist_chose():
    data = _file_from_before_these_settings_existed()
    data["settings_3d"].update({"write_undistort": True, "overscan": 0.4, "pixel_aspect": 2.0})
    s3 = project_file.migrate(data)["settings_3d"]
    assert s3["write_undistort"] is True
    assert s3["overscan"] == pytest.approx(0.4)
    assert s3["pixel_aspect"] == pytest.approx(2.0)


@pytest.mark.parametrize("bad, expected", [
    ({"overscan": 5.0}, ("overscan", project_file.MAX_OVERSCAN)),
    ({"overscan": -1.0}, ("overscan", 0.0)),
    ({"overscan": "wide"}, ("overscan", 0.0)),
    ({"pixel_aspect": 0.0}, ("pixel_aspect", 1.0)),
    ({"pixel_aspect": None}, ("pixel_aspect", 1.0)),
])
def test_migration_brings_an_impossible_value_back_into_range(bad, expected):
    data = _file_from_before_these_settings_existed()
    data["settings_3d"].update(bad)
    key, value = expected
    assert project_file.migrate(data)["settings_3d"][key] == pytest.approx(value)


@pytest.mark.parametrize("half_written", [
    {"scale": 2.0},                       # no rotation, no translation
    {"rotation": [[1, 0, 0]], "translation": [0, 0, 0]},
    "not a dict at all",
    [],
])
def test_a_half_written_transform_is_dropped_rather_than_passed_on(half_written):
    data = _file_from_before_these_settings_existed()
    data["scene_transform"] = half_written
    assert project_file.migrate(data)["scene_transform"] is None


# =============================================================================
# UNITS
# =============================================================================
@pytest.mark.parametrize("value, unit, metres", [
    (2.5, "metres", 2.5),
    (250.0, "centimetres", 2.5),
    (6.0, "feet", 1.8288),
    (12.0, "inches", 0.3048),
    (1.0, "Feet", 0.3048),          # the dropdown's capitalisation must not matter
])
def test_a_typed_distance_converts_to_metres(value, unit, metres):
    assert project_file.to_metres(value, unit) == pytest.approx(metres, abs=1e-9)


def test_an_unknown_unit_is_treated_as_metres_rather_than_raising():
    assert project_file.to_metres(3.0, "cubits") == pytest.approx(3.0)


def test_an_unreadable_distance_is_zero_so_nothing_is_scaled_by_accident():
    assert project_file.to_metres(None, "metres") == 0.0
    assert project_file.to_metres("", "metres") == 0.0


def test_a_foot_and_an_inch_agree_with_each_other():
    assert (project_file.to_metres(1.0, "feet")
            == pytest.approx(project_file.to_metres(12.0, "inches")))


# =============================================================================
# WHEN THE PANEL IS USABLE
# =============================================================================
def _solved(images=2, cameras=1):
    return {
        "images": {str(i): {"frame": 1000 + i} for i in range(1, images + 1)},
        "cameras": {str(i): {"model": "PINHOLE"} for i in range(1, cameras + 1)},
    }


def test_the_panel_is_off_without_a_solve():
    enabled, reason = project_file.scene_setup_enabled(None)
    assert enabled is False
    assert "Solve this shot first" in reason


def test_the_panel_is_off_when_the_solve_registered_nothing():
    enabled, reason = project_file.scene_setup_enabled({"images": {}, "cameras": {"1": {}}})
    assert enabled is False
    assert reason


def test_the_panel_is_off_without_intrinsics_to_project_with():
    enabled, reason = project_file.scene_setup_enabled({"images": {"1": {}}, "cameras": {}})
    assert enabled is False
    assert "intrinsics" in reason


def test_the_panel_is_off_when_there_are_no_points_to_pick():
    enabled, reason = project_file.scene_setup_enabled(_solved(), point_count=0)
    assert enabled is False
    assert "no 3D points" in reason


def test_the_panel_is_on_once_a_solve_with_points_exists():
    enabled, reason = project_file.scene_setup_enabled(_solved(), point_count=9159)
    assert enabled is True
    assert reason == ""


def test_the_panel_is_on_before_the_points_have_been_counted():
    # point_count=None means "not looked at yet", which must not read as zero.
    assert project_file.scene_setup_enabled(_solved(), point_count=None)[0] is True


def test_a_rubbish_camera_track_never_enables_the_panel():
    for junk in (None, "", [], 7, {}):
        assert project_file.scene_setup_enabled(junk)[0] is False
