"""
The canvas end of the per-shot project file: layers_to_config /
layers_from_config, which is what actually puts the artist's roto back on
screen when a clip is selected again.

Needs a QApplication because the canvas is a QLabel, so it runs offscreen and
is skipped where PySide6 is not installed.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.tracking_layer import TrackingLayer  # noqa: E402
from gui.canvas import VideoPointPickerCanvas  # noqa: E402
from mask_animator import AnimatedMask  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def canvas(qapp):
    return VideoPointPickerCanvas()


def _authored(canvas):
    """Two layers, an animated mask and a trimmed range, as if drawn by hand."""
    wall = canvas.layers[0]
    wall.name = "Wall"
    wall.grid_size = 14
    mask = AnimatedMask("m_1", "Exc Poly #1", "exclusion")
    mask.set_keyframe(0, [(0, 0), (50, 0), (50, 50)], "poly")
    mask.set_keyframe(30, [(10, 10), (60, 10), (60, 60)], "poly")
    wall.animated_masks.append(mask)

    pin = TrackingLayer("Screen", "#ffaa00", "cornerpin")
    pin.points = [(2, 1.0, 2.0), (2, 9.0, 2.0), (2, 9.0, 8.0), (2, 1.0, 8.0)]
    canvas.layers.append(pin)
    canvas.in_point = 5
    canvas.out_point = 40
    return canvas.layers_to_config()


def test_layers_survive_a_trip_through_the_project_file(canvas, qapp):
    configs = _authored(canvas)
    in_pt, out_pt = canvas.in_point, canvas.out_point

    fresh = VideoPointPickerCanvas()
    assert len(fresh.layers) == 1
    restored = fresh.layers_from_config(configs, in_point=in_pt, out_point=out_pt)

    assert restored == 2
    assert [l.name for l in fresh.layers] == ["Wall", "Screen"]
    assert fresh.layers[0].grid_size == 14
    assert fresh.layers[1].mode == "cornerpin"
    assert fresh.layers[1].points[0] == (2, 1.0, 2.0)
    assert (fresh.in_point, fresh.out_point) == (5, 40)

    mask = fresh.layers[0].animated_masks[0]
    assert mask.get_keyframe_frames() == [0, 30]
    # halfway between the two keyframes, so the interpolation came back too
    assert fresh.layers[0].get_masks_at_frame(15)[0]["points"][0] == (5.0, 5.0)


def test_restore_emits_masks_changed_so_the_layer_panel_relists(canvas, qapp):
    configs = _authored(canvas)
    fresh = VideoPointPickerCanvas()
    seen = []
    fresh.masks_changed.connect(lambda: seen.append(1))
    fresh.layers_from_config(configs)
    assert seen


def test_restore_drops_the_scaled_frame_cache(canvas, qapp):
    fresh = VideoPointPickerCanvas()
    fresh._scaled_cache[("stale",)] = object()
    fresh.layers_from_config(_authored(canvas))
    assert fresh._scaled_cache == {}


def test_an_empty_project_leaves_the_default_layer_alone(canvas, qapp):
    """A shot with nothing saved must not end up with no active layer."""
    assert canvas.layers_from_config([]) == 0
    assert len(canvas.layers) == 1
    assert canvas.active_layer is not None


def test_one_unreadable_layer_does_not_cost_the_others(canvas, qapp):
    configs = _authored(canvas)
    restored = canvas.layers_from_config([configs[0], None, "junk", configs[1]])
    assert restored == 2
    assert [l.name for l in canvas.layers] == ["Wall", "Screen"]
