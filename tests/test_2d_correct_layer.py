"""
Corrections as the layer and the project file carry them (roadmap 2.1).

A correction is the artist's decision about one frame of one track; it has to
survive a clip switch and a restart the way the roto does, and the canvas has
to be able to say which frames carry one so it can draw them differently.
"""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import project as project_file  # noqa: E402
from core.tracking_layer import TrackingLayer  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.canvas import VideoPointPickerCanvas  # noqa: E402


# ----------------------------------------------------------------- the layer
def test_one_correction_per_point_and_frame():
    layer = TrackingLayer("Wall")
    layer.set_correction(2, 40, 10.0, 20.0)
    layer.set_correction(2, 40, 11.5, 21.5)          # same marker, same frame: a revision
    layer.set_correction(2, 41, 12.0, 22.0)          # next frame: a second decision
    layer.set_correction(3, 40, 13.0, 23.0)          # another marker on the same frame

    assert len(layer.corrections) == 3
    assert layer.correction_at(40, 2) == {"point": 2, "frame": 40, "x": 11.5, "y": 21.5}
    assert layer.corrected_frames() == [40, 41]
    assert layer.corrected_frames(point_index=3) == [40]
    assert layer.correction_at(99) is None

    assert layer.clear_correction(2, 41) is True
    assert layer.clear_correction(2, 41) is False    # already gone
    assert layer.corrected_frames() == [40]


def test_corrections_round_trip_through_the_config_dict():
    layer = TrackingLayer("Wall A", "#38bdf8", "grid")
    layer.set_correction(0, 10, 100.25, 200.5)
    layer.set_correction(4, 25, 300.0, 400.0)

    cfg = layer.to_config_dict()
    back = TrackingLayer.from_config_dict(cfg)
    assert back.corrections == [
        {"point": 0, "frame": 10, "x": 100.25, "y": 200.5},
        {"point": 4, "frame": 25, "x": 300.0, "y": 400.0},
    ]
    assert back.corrected_frames() == [10, 25]

    # A copy, not the live list: the worker reads this config while the canvas
    # carries on being edited.
    layer.to_config_dict()["corrections"][0]["x"] = -1.0
    assert layer.corrections[0]["x"] == 100.25


def test_a_layer_saved_before_corrections_existed_still_loads():
    old = {"name": "Wall", "mode": "grid", "points": [], "animated_masks": []}
    assert TrackingLayer.from_config_dict(old).corrections == []


def test_a_half_written_correction_is_dropped_rather_than_guessed():
    cfg = {"name": "Wall", "corrections": [
        {"point": 1, "frame": 5, "x": 1.0, "y": 2.0},
        {"point": 1, "x": 3.0, "y": 4.0},            # no frame
        {"point": 1, "frame": 6, "x": "over there", "y": 4.0},
        None,
    ]}
    assert TrackingLayer.from_config_dict(cfg).corrections == [
        {"point": 1, "frame": 5, "x": 1.0, "y": 2.0}]


def test_corrections_survive_the_project_file(tmp_path):
    layer = TrackingLayer("Wall A")
    layer.set_correction(2, 117, 640.0, 360.0)

    data = project_file.default_project()
    data["layers"] = [layer.to_config_dict()]
    path = project_file.project_path(tmp_path, "shot_a")
    assert project_file.save_project(path, data)

    loaded = project_file.load_project(path)
    back = TrackingLayer.from_config_dict(loaded["layers"][0])
    assert back.corrections == [{"point": 2, "frame": 117, "x": 640.0, "y": 360.0}]
    # And the whole-layer backwards option is per shot too.
    assert loaded["settings_2d"]["track_backwards"] is False


# ----------------------------------------------------------------- the canvas
@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(qapp):
    c = VideoPointPickerCanvas()
    c.orig_w, c.orig_h = 200, 100
    return c


def _result(T=10, N=2):
    tracks = np.zeros((T, N, 2), dtype=np.float32)
    for n in range(N):
        tracks[:, n, 0] = 20 + 10 * n
        tracks[:, n, 1] = 30 + 10 * n
    return {"tracks": tracks, "vis": np.ones((T, N), dtype=bool)}


def test_canvas_maps_clip_frames_to_samples_through_the_tracked_range(canvas):
    canvas.layers[0].name = "Wall"
    canvas.set_tracked_result({"Wall": _result(T=5)}, in_point=10, frame_step=3)

    assert canvas.has_tracked_result()
    assert canvas.tracked_index_for_frame(10) == 0
    assert canvas.tracked_index_for_frame(13) == 1
    assert canvas.tracked_index_for_frame(22) == 4
    # A frame the step skips belongs to no sample, and so does one outside.
    assert canvas.tracked_index_for_frame(11) is None
    assert canvas.tracked_index_for_frame(9) is None
    assert canvas.tracked_index_for_frame(25) is None

    canvas.clear_tracked_result()
    assert not canvas.has_tracked_result()
    assert canvas.tracked_index_for_frame(10) is None


def test_canvas_lists_and_finds_tracked_markers(canvas):
    canvas.layers[0].name = "Wall"
    canvas.set_tracked_result({"Wall": _result(T=4, N=2)}, in_point=0, frame_step=1)
    canvas.current_frame = 2

    at = canvas.tracked_points_at()
    assert at == [("Wall", 0, 20.0, 30.0), ("Wall", 1, 30.0, 40.0)]

    # An invisible sample is not offered for dragging: there is nothing there.
    canvas.tracked_layers["Wall"]["vis"][2, 0] = False
    assert [p[1] for p in canvas.tracked_points_at()] == [1]

    # A hidden layer takes its markers with it.
    canvas.layers[0].visible = False
    assert canvas.tracked_points_at() == []
