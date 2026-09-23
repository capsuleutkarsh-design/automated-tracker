"""
The undo stack behind the canvas (roadmap 2.5).

Every test here asks the same question: after undo, is the model EXACTLY what
it was, and after redo, exactly what it became? "Exactly" is
layers_to_config() - the same dictionary the project file is written from - so
a command that restores something that only looks right on screen fails here.

Needs a QApplication because the canvas is a QLabel, so it runs offscreen and
is skipped where PySide6 is not installed, the way test_project_canvas.py does.
"""
import copy
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.canvas import VideoPointPickerCanvas  # noqa: E402
from mask_animator import AnimatedMask  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def canvas(qapp):
    c = VideoPointPickerCanvas()
    _authored(c)
    return c


def _authored(canvas):
    """A layer with points and a mask keyframed on three frames, as drawn."""
    layer = canvas.layers[0]
    layer.points = [(0, 10.0, 20.0), (4, 30.0, 40.0), (4, 50.0, 60.0)]
    mask = AnimatedMask("m_a", "Exc Poly #1", "exclusion")
    mask.set_keyframe(0, [(0, 0), (40, 0), (40, 40), (0, 40)], "poly")
    mask.set_keyframe(10, [(5, 5), (45, 5), (45, 45), (5, 45)], "poly")
    mask.set_keyframe(20, [(9, 9), (49, 9), (49, 49), (9, 49)], "poly")
    layer.animated_masks.append(mask)
    canvas.selected_mask_id = mask.id
    canvas.current_frame = 10
    return mask


def _state(canvas):
    """Everything an undo has to put back, as plain comparable data."""
    return copy.deepcopy({
        "layers": canvas.layers_to_config(),
        "in": canvas.in_point,
        "out": canvas.out_point,
        "selected": canvas.selected_mask_id,
    })


def _round_trip(canvas, edit):
    """Run `edit`, then check undo and redo land on the exact two states."""
    before = _state(canvas)
    assert edit() is not False
    after = _state(canvas)
    assert after != before, "the edit changed nothing, so there is nothing to test"

    assert canvas.undo_stack.canUndo()
    canvas.undo_stack.undo()
    assert _state(canvas) == before

    assert canvas.undo_stack.canRedo()
    canvas.undo_stack.redo()
    assert _state(canvas) == after
    return before, after


# -- masks -----------------------------------------------------------------
def test_drawing_a_polygon_mask_is_one_undo(canvas):
    canvas.interaction_mode = "exclusion_poly"
    canvas.current_poly = [(100.0, 100.0), (160.0, 100.0), (160.0, 160.0)]
    _round_trip(canvas, canvas._finish_current_poly)
    # The whole shape goes, not just its keyframe: creating and keying it was
    # one action to the artist.
    canvas.undo_stack.undo()
    assert len(canvas.layers[0].animated_masks) == 1


def test_moving_a_whole_mask_restores_every_vertex(canvas):
    mask = canvas.layers[0].animated_masks[0]

    def drag():
        before = canvas.mask_snapshot()
        kf = mask.keyframes[10]
        kf.data = [(x + 7.5, y - 3.25) for x, y in kf.data]
        return canvas.push_mask_edit("Move a mask", before)

    _round_trip(canvas, drag)
    assert canvas.layers[0].animated_masks[0].keyframes[10].data[0] == (12.5, 1.75)


def test_moving_one_vertex_leaves_the_other_keyframes_alone(canvas):
    mask = canvas.layers[0].animated_masks[0]

    def drag_vertex():
        before = canvas.mask_snapshot()
        kf = mask.keyframes[10]
        pts = list(kf.data)
        pts[2] = (pts[2][0] + 12.0, pts[2][1] + 8.0)
        kf.data = pts
        return canvas.push_mask_edit("Move a mask vertex", before)

    _round_trip(canvas, drag_vertex)
    live = canvas.layers[0].animated_masks[0]
    assert live.keyframes[0].data == [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0)]


def test_adding_a_vertex_touches_every_keyframe_and_undoes_as_one(canvas):
    mask_id = canvas.layers[0].animated_masks[0].id
    _round_trip(canvas, lambda: canvas.insert_mask_vertex(mask_id, 0))

    live = canvas.layers[0].animated_masks[0]
    # Five points on all three keys, or the 1:1 interpolation between them
    # gives up and the shape snaps between keyframes.
    assert [len(kf.data) for kf in live.keyframes.values()] == [5, 5, 5]
    assert live.keyframes[0].data[1] == (20.0, 0.0)

    canvas.undo_stack.undo()
    live = canvas.layers[0].animated_masks[0]
    assert [len(kf.data) for kf in live.keyframes.values()] == [4, 4, 4]


def test_deleting_a_vertex_undoes_on_every_keyframe(canvas):
    mask_id = canvas.layers[0].animated_masks[0].id
    _round_trip(canvas, lambda: canvas.delete_mask_vertex(mask_id, 1))
    live = canvas.layers[0].animated_masks[0]
    assert [len(kf.data) for kf in live.keyframes.values()] == [3, 3, 3]


def test_a_triangle_will_not_give_up_its_last_vertex(canvas):
    mask = canvas.layers[0].animated_masks[0]
    for f in list(mask.keyframes):
        mask.keyframes[f].data = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert canvas.delete_mask_vertex(mask.id, 0) is False
    assert canvas.undo_stack.count() == 0


def test_setting_a_keyframe_undoes_back_to_interpolation(canvas):
    mask = canvas.layers[0].animated_masks[0]
    canvas.current_frame = 15
    info = canvas.layers[0].get_masks_at_frame(15)[0]
    _round_trip(canvas, lambda: canvas._set_keyframe_on_mask(mask, 15, info))
    assert canvas.layers[0].animated_masks[0].has_keyframe(15)
    canvas.undo_stack.undo()
    assert not canvas.layers[0].animated_masks[0].has_keyframe(15)


def test_deleting_a_keyframe_brings_back_its_exact_shape(canvas):
    _round_trip(canvas, canvas.delete_selected_keyframe)
    live = canvas.layers[0].animated_masks[0]
    assert sorted(live.keyframes) == [0, 20]
    canvas.undo_stack.undo()
    live = canvas.layers[0].animated_masks[0]
    assert live.keyframes[10].data == [(5.0, 5.0), (45.0, 5.0), (45.0, 45.0), (5.0, 45.0)]


def test_moving_a_keyframe_carries_its_shape_and_comes_back(canvas):
    mask_id = canvas.layers[0].animated_masks[0].id
    canvas.current_frame = 14
    _round_trip(canvas, lambda: canvas.move_mask_keyframe(mask_id, 10, 14))
    live = canvas.layers[0].animated_masks[0]
    assert sorted(live.keyframes) == [0, 14, 20]
    assert live.keyframes[14].data == [(5.0, 5.0), (45.0, 5.0), (45.0, 45.0), (5.0, 45.0)]


def test_deleting_a_mask_puts_it_back_with_all_three_keyframes(canvas):
    _round_trip(canvas, canvas.delete_selected_mask)
    assert canvas.layers[0].animated_masks == []
    canvas.undo_stack.undo()
    live = canvas.layers[0].animated_masks[0]
    assert sorted(live.keyframes) == [0, 10, 20]
    # The selection comes back too, or the next keypress acts on nothing.
    assert canvas.selected_mask_id == live.id


def test_clearing_every_mask_is_one_undo(canvas):
    _round_trip(canvas, canvas.clear_active_layer_masks)


# -- hand-placed tracking points -------------------------------------------
def test_adding_a_point_undoes(canvas):
    _round_trip(canvas, lambda: canvas.add_point(7, 123.0, 456.0))
    assert canvas.layers[0].points[-1] == (7, 123.0, 456.0)


def test_moving_a_point_keeps_its_frame_and_undoes(canvas):
    def drag():
        before = canvas.points_snapshot()
        f, _x, _y = canvas.layers[0].points[1]
        canvas.layers[0].points[1] = (f, 99.0, 88.0)
        return canvas.push_points_edit("Move a tracking point", before)

    _round_trip(canvas, drag)
    assert canvas.layers[0].points[1] == (4, 99.0, 88.0)
    canvas.undo_stack.undo()
    assert canvas.layers[0].points[1] == (4, 30.0, 40.0)


def test_deleting_a_point_puts_it_back_in_its_own_place(canvas):
    _round_trip(canvas, lambda: canvas.delete_point(1))
    assert canvas.layers[0].points == [(0, 10.0, 20.0), (4, 50.0, 60.0)]
    canvas.undo_stack.undo()
    assert canvas.layers[0].points[1] == (4, 30.0, 40.0)


def test_clearing_the_points_is_one_undo(canvas):
    _round_trip(canvas, canvas.clear_active_layer_points)


# -- the in and out points --------------------------------------------------
def test_the_range_undoes_to_what_it_was(canvas):
    canvas.in_point, canvas.out_point = 3, 90
    _round_trip(canvas, lambda: canvas.push_range(in_point=12, text="Set the in-point"))
    assert (canvas.in_point, canvas.out_point) == (12, 90)
    canvas.undo_stack.undo()
    assert (canvas.in_point, canvas.out_point) == (3, 90)


def test_setting_the_range_to_what_it_already_is_does_not_fill_the_stack(canvas):
    canvas.in_point, canvas.out_point = 0, -1
    assert canvas.push_range(in_point=0, out_point=-1) is False
    assert canvas.undo_stack.count() == 0


# -- corrections to a solved track -----------------------------------------
def _result(T=6, N=3):
    tracks = np.zeros((T, N, 2), dtype=np.float32)
    for t in range(T):
        for n in range(N):
            tracks[t, n] = (100.0 + t, 200.0 + n)
    return {"tracks": tracks,
            "vis": np.ones((T, N), dtype=bool),
            "conf": np.full((T, N), 0.8, dtype=np.float32)}


def test_correcting_a_tracked_point_undoes_the_mark_and_the_sample(canvas):
    layer = canvas.layers[0]
    block = _result()
    canvas.set_tracked_result({layer.name: block}, in_point=0, frame_step=1)

    assert canvas.push_correction(layer.name, 1, 3, 3, 777.0, 888.0)
    assert layer.corrections == [{"point": 1, "frame": 3, "x": 777.0, "y": 888.0}]
    assert tuple(block["tracks"][3, 1]) == (777.0, 888.0)
    assert float(block["conf"][3, 1]) == 1.0

    canvas.undo_stack.undo()
    assert layer.corrections == []
    # The solved position is back, confidence and all - an undo that left the
    # hand-placed sample in the arrays would be written to the exports.
    assert tuple(block["tracks"][3, 1]) == (103.0, 201.0)
    assert round(float(block["conf"][3, 1]), 3) == 0.8

    canvas.undo_stack.redo()
    assert tuple(block["tracks"][3, 1]) == (777.0, 888.0)
    assert layer.corrections[0]["frame"] == 3


def test_forgetting_a_correction_undoes_to_the_mark_only(canvas):
    layer = canvas.layers[0]
    block = _result()
    canvas.set_tracked_result({layer.name: block}, in_point=0, frame_step=1)
    canvas.push_correction(layer.name, 0, 2, 2, 11.0, 22.0)

    assert canvas.push_clear_correction(layer.name, 0, 2)
    assert layer.corrections == []
    # The spliced position stays where the correction put it, by design.
    assert tuple(block["tracks"][2, 0]) == (11.0, 22.0)

    canvas.undo_stack.undo()
    assert layer.corrections == [{"point": 0, "frame": 2, "x": 11.0, "y": 22.0}]
    assert tuple(block["tracks"][2, 0]) == (11.0, 22.0)


# -- the stack's own life ---------------------------------------------------
def test_a_clip_change_clears_the_history(canvas):
    canvas.add_point(1, 5.0, 6.0)
    canvas.push_range(in_point=4)
    assert canvas.undo_stack.count() == 2

    # layers_from_config is how a different shot's project reaches the canvas.
    fresh = VideoPointPickerCanvas().layers_to_config()
    canvas.layers_from_config(fresh, in_point=0, out_point=-1)

    assert canvas.undo_stack.count() == 0
    assert canvas.undo_stack.canUndo() is False
    assert canvas.undo_stack.canRedo() is False


def test_every_command_says_what_it_did(canvas):
    canvas.add_point(1, 5.0, 6.0)
    assert canvas.undo_stack.undoText() == "Add a tracking point"
    canvas.delete_selected_keyframe()
    assert canvas.undo_stack.undoText() == "Delete a mask keyframe"
    canvas.undo_stack.undo()
    assert canvas.undo_stack.redoText() == "Delete a mask keyframe"
    assert canvas.undo_stack.undoText() == "Add a tracking point"


# -- the hit tests the undoable edits are driven from ----------------------
@pytest.fixture
def shown(canvas):
    """The canvas with a 1:1 plate on it, so a click lands where it looks."""
    from PySide6.QtGui import QImage
    canvas.resize(640, 360)
    img = QImage(640, 360, QImage.Format_RGB32)
    img.fill(0x101010)
    canvas.set_frame_image(img, 10, 50, 640, 360, 24.0)
    canvas.current_frame = 10
    return canvas


def test_a_vertex_is_grabbed_within_a_screen_pixel_radius(shown):
    hit = shown.nearest_mask_vertex(47.0, 6.0)
    assert hit is not None and hit[1] == 1
    assert shown.nearest_mask_vertex(300.0, 300.0) is None


def test_an_edge_is_offered_between_its_two_vertices(shown):
    hit = shown.nearest_mask_edge(25.0, 4.0)
    assert hit is not None and hit[1] == 0
    assert shown.nearest_mask_edge(300.0, 300.0) is None


def test_only_a_point_keyed_on_this_frame_can_be_grabbed(shown):
    shown.layers[0].points = [(10, 200.0, 100.0), (11, 300.0, 100.0)]
    assert shown.nearest_point_index(203.0, 102.0) == 0
    # The ghost of a point placed on frame 12 is there to be seen, not moved.
    assert shown.nearest_point_index(300.0, 100.0) is None


def test_a_new_edit_after_an_undo_drops_the_redone_branch(canvas):
    canvas.add_point(1, 5.0, 6.0)
    canvas.undo_stack.undo()
    canvas.add_point(2, 7.0, 8.0)
    assert canvas.undo_stack.canRedo() is False
    assert canvas.layers[0].points[-1] == (2, 7.0, 8.0)
