"""
COLMAP text parsers against hand-written fixtures, and the timeline offset.
"""
from pathlib import Path

import numpy as np
import pytest

import export_tools as et

DATA = Path(__file__).parent / "data"


@pytest.fixture
def cameras():
    return et.parse_colmap_cameras(DATA / "cameras.txt")


@pytest.mark.parametrize("cam_id, model, fx, fy, k1, k2", [
    (1, "SIMPLE_RADIAL", 1000.5, 1000.5, -0.05, 0.0),
    (2, "RADIAL", 1000.5, 1000.5, -0.05, 0.01),
    (3, "OPENCV", 1000.5, 1001.5, -0.05, 0.01),
    (4, "OPENCV_FISHEYE", 1000.5, 1001.5, -0.05, 0.01),
    (5, "PINHOLE", 1000.5, 1001.5, 0.0, 0.0),
    (6, "SIMPLE_PINHOLE", 1000.5, 1000.5, 0.0, 0.0),
    (7, "FULL_OPENCV", 1000.5, 1001.5, -0.05, 0.01),
    (8, "FOV", 1000.5, 1001.5, 0.0, 0.0),
    (9, "SIMPLE_RADIAL_FISHEYE", 1000.5, 1000.5, -0.05, 0.0),
    (10, "RADIAL_FISHEYE", 1000.5, 1000.5, -0.05, 0.01),
    (11, "THIN_PRISM_FISHEYE", 1000.5, 1001.5, -0.05, 0.01),
])
def test_parse_colmap_cameras_per_model(cameras, cam_id, model, fx, fy, k1, k2):
    cam = cameras[cam_id]
    assert cam["model"] == model
    assert (cam["width"], cam["height"]) == (1920, 1080)
    assert cam["focal_x"] == fx and cam["focal_y"] == fy
    # every model in the fixture has an off-centre principal point; none may fall
    # back to width/2, height/2
    assert cam["cx"] == 950.25 and cam["cy"] == 530.75
    assert cam["k1"] == k1 and cam["k2"] == k2
    assert cam["fisheye"] == (model in et.FISHEYE_MODELS)


def test_parse_colmap_cameras_unknown_model_keeps_f_cx_cy(tmp_path):
    p = tmp_path / "cameras.txt"
    p.write_text("1 WEIRD 100 50 40.0 44.0 20.0\n", encoding="utf-8")
    cam = et.parse_colmap_cameras(p)[1]
    assert (cam["focal_x"], cam["cx"], cam["cy"]) == (40.0, 44.0, 20.0)


def test_parse_colmap_images_fixture():
    images = et.parse_colmap_images(DATA / "images.txt")
    assert sorted(images) == [7, 8]
    a, b = images[7], images[8]
    assert a["name"] == "frame_000002.png" and a["index"] == 2 and a["frame"] == 2
    assert b["name"] == "frame_000003.png" and b["index"] == 3 and b["frame"] == 3
    assert a["camera_id"] == 5
    assert np.allclose(a["center"], [0, 0, 0]) and np.allclose(a["R_world"], np.eye(3))
    # q = (0, 1, 0, 0) is a half turn about x: R = diag(1, -1, -1); C = -R^T t
    assert np.allclose(b["R_world"], np.diag([1, -1, -1]))
    assert np.allclose(b["center"], [-1, 2, 3])


def test_parse_colmap_images_frame_index_uses_last_digit_run(tmp_path):
    p = tmp_path / "images.txt"
    p.write_text("3 1 0 0 0 0 0 0 1 shot2_v3_000042.exr\n\n", encoding="utf-8")
    img = et.parse_colmap_images(p)[3]
    assert img["index"] == 42


def test_timeline_offset_with_first_frame_unregistered():
    images = et.parse_colmap_images(DATA / "images.txt")
    # frame_000001 is not in the solve; image k still belongs on start + (k - 1)
    offset = et.apply_timeline_start(images, 1001)
    assert offset == 1000
    assert images[7]["frame"] == 1002 and images[8]["frame"] == 1003
    assert et.timeline_start_of(images) == 1001

    # at the default timeline start nothing moves - the old code shifted by -1 here
    assert et.apply_timeline_start(images, 1) == 0
    assert images[7]["frame"] == 2 and images[8]["frame"] == 3
    assert et.apply_timeline_start(images, None) == 0
    assert images[7]["frame"] == 2


def test_parse_colmap_points3D_to_arrays():
    pc = et.parse_colmap_points3D(DATA / "points3D.txt")
    assert len(pc) == 3
    assert pc.xyz.shape == (3, 3) and pc.rgb.shape == (3, 3)
    assert pc.rgb.dtype == np.uint8
    assert np.allclose(pc.xyz[0], [1.5, -2.5, 3.5])
    assert list(pc.rgb[1]) == [0, 255, 64]
    assert len(et.parse_colmap_points3D(DATA / "missing.txt")) == 0
