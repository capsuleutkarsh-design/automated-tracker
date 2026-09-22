"""Every 3D preset carries every key the solver config is built from."""
from core.presets import PRESETS, PRESET_KEYS, DEFAULT_PRESET


def test_default_preset_exists():
    assert DEFAULT_PRESET in PRESETS


def test_every_preset_has_every_key_with_the_right_type():
    types = {
        "solver_engine": str,
        "tri_angle": (int, float),
        "init_max_forward_motion": (int, float),
        "overlap": int,
        "inliers": int,
        "camera_model": str,
        "single_camera": bool,
        "use_gpu": bool,
        "max_image_size": int,
        "frame_step": int,
    }
    assert set(types) == set(PRESET_KEYS)
    for name, data in PRESETS.items():
        assert "description" in data and data["description"], name
        for key in PRESET_KEYS:
            assert key in data, "%s lacks %s" % (name, key)
            assert isinstance(data[key], types[key]), "%s.%s" % (name, key)


def test_values_within_the_gui_ranges():
    """tab_3d.py clamps these spin boxes; a preset outside would silently clip."""
    for name, data in PRESETS.items():
        assert 0.5 <= data["tri_angle"] <= 30.0, name
        assert 5 <= data["overlap"] <= 100, name
        assert 15 <= data["inliers"] <= 500, name
        assert 1 <= data["frame_step"] <= 10, name
        assert data["solver_engine"] in ("Incremental", "Hierarchical"), name
        assert 0.0 < data["init_max_forward_motion"] <= 1.0, name
