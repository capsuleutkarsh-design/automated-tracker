"""
Picking solved points on the plate (roadmap 1.4).

The maths that puts a solved 3D point on the frame the artist is looking at,
and the canvas end of clicking one. The projection helper is pure numpy, but it
lives in gui.canvas next to the overlay it feeds, so the whole file runs
offscreen and is skipped where PySide6 is not installed.
"""
import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from core import lens  # noqa: E402
from gui.canvas import (  # noqa: E402
    VideoPointPickerCanvas, project_solved_points, SOLVED_PICK_RADIUS_PX,
)

ROOT = Path(__file__).resolve().parent.parent
SOLVE = ROOT / "04 SCENES" / "move_1001" / "3D_CAMERA_TRACK" / "_latest" / "sparse"

PINHOLE = {"model": "PINHOLE", "width": 1920, "height": 1080,
           "focal_x": 1000.5, "focal_y": 1001.5, "cx": 950.25, "cy": 530.75}
BARREL = {"model": "SIMPLE_RADIAL", "width": 1280, "height": 720,
          "focal_x": 980.2, "focal_y": 980.2, "cx": 640.0, "cy": 360.0,
          "params": [980.2, 640.0, 360.0, -0.021135]}


def _rotation(yaw, pitch):
    """A plain camera-to-world rotation to test the convention with."""
    cy, sy = np.cos(yaw), np.sin(yaw)
    cp, sp = np.cos(pitch), np.sin(pitch)
    about_y = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    about_x = np.array([[1.0, 0.0, 0.0], [0.0, cp, -sp], [0.0, sp, cp]])
    return about_y @ about_x


# =============================================================================
# THE PROJECTION ITSELF
# =============================================================================
def test_a_pinhole_point_lands_on_the_pixel_worked_out_by_hand():
    # Camera at the origin looking down its own +Z, so camera space is world
    # space and the expected pixel is fx * X/Z + cx, straight out of COLMAP.
    point = np.array([[0.4, -0.25, 2.0]])
    xy, visible = project_solved_points(point, [0.0, 0.0, 0.0], np.eye(3), PINHOLE)

    assert visible[0]
    assert xy[0, 0] == pytest.approx(1000.5 * (0.4 / 2.0) + 950.25)
    assert xy[0, 1] == pytest.approx(1001.5 * (-0.25 / 2.0) + 530.75)


def test_the_stored_rotation_is_read_as_camera_to_world():
    """
    COLMAP's parsed images hold R_world = R_world_to_camera transposed, so the
    camera-space point is R_world.T @ (X - C). Using R_world the other way round
    still produces a plausible-looking pixel, which is why this is pinned here.
    """
    R = _rotation(0.4, -0.2)
    C = np.array([1.5, -0.5, 3.0])
    point = np.array([2.0, 0.25, 6.0])

    expected_cam = R.T @ (point - C)
    expected = np.array([
        PINHOLE["focal_x"] * expected_cam[0] / expected_cam[2] + PINHOLE["cx"],
        PINHOLE["focal_y"] * expected_cam[1] / expected_cam[2] + PINHOLE["cy"],
    ])
    xy, visible = project_solved_points(point[None, :], C, R, PINHOLE)

    assert visible[0]
    assert np.allclose(xy[0], expected, atol=1e-9)

    wrong_cam = R @ (point - C)
    wrong_x = PINHOLE["focal_x"] * wrong_cam[0] / wrong_cam[2] + PINHOLE["cx"]
    assert abs(wrong_x - expected[0]) > 1.0


def test_a_distorted_lens_puts_the_point_back_on_the_pixel_it_came_from():
    """
    Start from a pixel, undo the lens to get the ray, put a 3D point on that ray
    at a known depth, and project it: the dot must land back on the pixel the
    feature was measured at, which is the only thing that makes clicking one
    mean anything.
    """
    for pixel in ([100.0, 80.0], [640.0, 360.0], [1200.0, 690.0]):
        measured = np.array(pixel)
        distorted = np.array([(measured[0] - BARREL["cx"]) / BARREL["focal_x"],
                              (measured[1] - BARREL["cy"]) / BARREL["focal_y"]])
        undistorted = lens.undistort_points(BARREL, distorted)

        R = _rotation(-0.3, 0.15)
        C = np.array([-2.0, 1.0, 4.0])
        depth = 7.25
        ray = np.array([undistorted[0] * depth, undistorted[1] * depth, depth])
        world = R @ ray + C

        xy, visible = project_solved_points(world[None, :], C, R, BARREL)
        assert visible[0]
        assert np.allclose(xy[0], measured, atol=1e-6)


def test_a_point_behind_the_camera_is_not_drawn():
    behind = np.array([[0.0, 0.0, -3.0]])
    xy, visible = project_solved_points(behind, [0.0, 0.0, 0.0], np.eye(3), PINHOLE)
    assert not visible[0]
    assert xy.shape == (1, 2)


def test_a_point_outside_the_frame_is_not_drawn():
    # Far off to the side: on the sensor plane, but nowhere near the raster.
    xy, visible = project_solved_points(
        np.array([[40.0, 0.0, 2.0]]), [0.0, 0.0, 0.0], np.eye(3), PINHOLE)
    assert not visible[0]
    assert xy[0, 0] > PINHOLE["width"]


def test_every_point_keeps_its_place_in_the_cloud():
    """The arrays index the point cloud, so a hidden point must not shift the rest."""
    cloud = np.array([[0.0, 0.0, -1.0],      # behind
                      [0.1, 0.05, 2.0],      # on frame
                      [80.0, 0.0, 2.0]])     # off frame
    xy, visible = project_solved_points(cloud, [0.0, 0.0, 0.0], np.eye(3), PINHOLE)
    assert xy.shape == (3, 2)
    assert list(visible) == [False, True, False]


@pytest.mark.skipif(not (SOLVE / "images.txt").exists(),
                    reason="no solved shot on disk to check against")
def test_the_projection_agrees_with_colmaps_own_measured_features():
    """
    The strongest form of "the pixel COLMAP would predict": take the real solve
    on disk, and reproject the 3D points COLMAP itself matched to measured
    features. Each dot has to land on the feature it came from.
    """
    import export_tools as et

    cameras = et.parse_colmap_cameras(SOLVE / "cameras.txt")
    images = et.parse_colmap_images(SOLVE / "images.txt")
    points = {}
    for line in (SOLVE / "points3D.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        points[int(parts[0])] = [float(parts[1]), float(parts[2]), float(parts[3])]

    lines = [l for l in (SOLVE / "images.txt").read_text(encoding="utf-8").splitlines()
             if not l.startswith("#")]
    header, observations = lines[0].split(), lines[1].split()
    img = images[int(header[0])]
    cam = cameras[img["camera_id"]]

    checked = 0
    for i in range(0, len(observations), 3):
        x, y, pid = float(observations[i]), float(observations[i + 1]), int(observations[i + 2])
        if pid < 0 or pid not in points:
            continue
        xy, visible = project_solved_points(
            [points[pid]], img["center"], img["R_world"], cam)
        assert visible[0]
        # Half a pixel: the residual of the solve itself, not of this maths.
        assert np.allclose(xy[0], [x, y], atol=0.5)
        checked += 1
        if checked >= 25:
            break
    assert checked >= 5


# =============================================================================
# PICKING ONE ON THE CANVAS
# =============================================================================
@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def canvas(qapp):
    """A canvas showing a 1280x720 plate at half size, with points on it."""
    c = VideoPointPickerCanvas()
    c.resize(640, 360)
    c.set_frame_image(QImage(1280, 720, QImage.Format_RGB888), 0, 60, 1280, 720)
    c.set_solved_points([[640.0, 360.0], [100.0, 100.0], [1200.0, 80.0]],
                        visible=[True, True, False], src_size=(1280, 720))
    return c


def test_the_overlay_is_only_held_while_the_panel_wants_it(canvas):
    assert canvas.solved_xy is not None
    canvas.clear_solved_points()
    assert canvas.solved_xy is None
    assert canvas.nearest_solved_point(640.0, 360.0) is None


def test_a_click_on_a_point_selects_the_nearest_one(canvas):
    assert canvas.nearest_solved_point(642.0, 358.0) == 0
    assert canvas.nearest_solved_point(103.0, 97.0) == 1


def test_a_click_in_empty_space_selects_nothing(canvas):
    assert canvas.nearest_solved_point(400.0, 300.0) is None


def test_the_pick_radius_is_measured_on_screen_not_on_the_plate(canvas):
    # The plate is shown at half size, so a hit this far away on the plate is
    # just inside the radius on screen, and twice that is outside it.
    inside = 2.0 * SOLVED_PICK_RADIUS_PX - 2.0
    outside = 2.0 * SOLVED_PICK_RADIUS_PX + 4.0
    assert canvas.nearest_solved_point(640.0 + inside, 360.0) == 0
    assert canvas.nearest_solved_point(640.0 + outside, 360.0) is None


def test_a_point_not_visible_on_this_frame_cannot_be_picked(canvas):
    assert canvas.nearest_solved_point(1200.0, 80.0) is None


def test_the_points_follow_the_plate_when_the_solve_was_made_at_another_size(qapp):
    """A solve made on half-size frames still has to land on the full plate."""
    c = VideoPointPickerCanvas()
    c.resize(640, 360)
    c.set_frame_image(QImage(1280, 720, QImage.Format_RGB888), 0, 60, 1280, 720)
    c.set_solved_points([[320.0, 180.0]], visible=[True], src_size=(640, 360))
    assert c.nearest_solved_point(640.0, 360.0) == 0


def test_the_selection_is_kept_in_the_order_it_was_picked(canvas):
    canvas.set_solved_selection([2, 0])
    assert canvas.solved_selection == (2, 0)
    canvas.set_solved_selection(())
    assert canvas.solved_selection == ()
