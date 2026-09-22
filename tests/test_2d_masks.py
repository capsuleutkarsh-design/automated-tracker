"""
Mask and layer model tests: AnimatedMask.get_interpolated_geometry (exact / clamp /
interp / mismatch), deep copies in to_dict / to_config_dict (B17), the corner-pin
flag on layers (A12), and frame loading helpers (C1 / C15 / C16).
"""
import numpy as np
import pytest
from PIL import Image

from mask_animator import AnimatedMask, MaskKeyframe
from core.tracking_layer import TrackingLayer
import cotracker_2d as c2d


# ----------------------------------------------------------------- interpolation
def _mask():
    m = AnimatedMask("m_1", "M", "exclusion")
    m.set_keyframe(10, [(0, 0), (10, 0), (10, 10)], "poly")
    m.set_keyframe(20, [(10, 10), (20, 10), (20, 20)], "poly")
    return m


def test_get_interpolated_geometry_exact_clamp_interp():
    m = _mask()
    assert m.get_interpolated_geometry(10)["points"] == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    # before the first / after the last keyframe: clamped
    assert m.get_interpolated_geometry(0)["points"] == [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert m.get_interpolated_geometry(99)["points"] == [(10.0, 10.0), (20.0, 10.0), (20.0, 20.0)]
    # halfway
    mid = m.get_interpolated_geometry(15)["points"]
    assert mid == [(5.0, 5.0), (15.0, 5.0), (15.0, 15.0)]
    assert m.get_interpolated_geometry(12)["points"][0] == (2.0, 2.0)
    assert AnimatedMask("e", "E").get_interpolated_geometry(3) is None


def test_get_interpolated_geometry_vertex_count_mismatch_snaps_to_nearest():
    m = _mask()
    m.set_keyframe(20, [(10, 10), (20, 10), (20, 20), (10, 20)], "poly")
    assert len(m.get_interpolated_geometry(14)["points"]) == 3
    assert len(m.get_interpolated_geometry(16)["points"]) == 4


def test_rect_keyframe_becomes_ordered_polygon():
    kf = MaskKeyframe(3, "rect", [50, 60, 10, 20])
    assert kf.mask_type == "poly"
    assert kf.data == [(10, 20), (50, 20), (50, 60), (10, 60)]


# ----------------------------------------------------------------- B17 deep copies
def test_to_dict_and_from_dict_do_not_share_lists():
    m = _mask()
    d = m.to_dict()
    d["keyframes"]["10"]["data"][0] = (999, 999)
    assert m.keyframes[10].data[0] == (0.0, 0.0)
    m2 = AnimatedMask.from_dict(m.to_dict())
    assert m2.id == "m_1" and sorted(m2.keyframes) == [10, 20]
    assert m2.keyframes[20].data == m.keyframes[20].data
    assert "feather_radius" not in m.to_dict()


def test_layer_config_is_a_deep_copy():
    layer = TrackingLayer("L", "#fff", "points")
    layer.points = [(5, 1.0, 2.0)]
    layer.animated_masks = [_mask()]
    cfg = layer.to_config_dict()
    cfg["query_points"].append((6, 0.0, 0.0))
    cfg["animated_masks"][0]["keyframes"]["10"]["data"].clear()
    assert layer.points == [(5, 1.0, 2.0)]
    assert len(layer.animated_masks[0].keyframes[10].data) == 3


# ----------------------------------------------------------------- A12 corner-pin flag
def test_only_cornerpin_mode_layers_export_cornerpin():
    grid = TrackingLayer("G", "#fff", "grid")
    grid.points = [(0, 0, 0)] * 4  # four points is not a corner pin
    assert grid.to_config_dict()["export_cornerpin"] is False
    pts = TrackingLayer("P", "#fff", "points")
    pts.points = [(0, 0, 0)] * 4
    assert pts.to_config_dict()["export_cornerpin"] is False
    cp = TrackingLayer("C", "#fff", "cornerpin")
    cp.points = [(0, 0, 0)] * 4
    assert cp.to_config_dict()["export_cornerpin"] is True
    flagged = TrackingLayer("F", "#fff", "points")
    flagged.export_cornerpin = True
    assert flagged.to_config_dict()["export_cornerpin"] is True


def test_get_masks_at_frame_reports_keyframes():
    layer = TrackingLayer()
    layer.animated_masks = [_mask()]
    info = layer.get_masks_at_frame(10)
    assert info[0]["is_keyframe"] is True and info[0]["type"] == "poly"
    assert layer.get_masks_at_frame(15)[0]["is_keyframe"] is False
    assert layer.get_all_keyframe_frames() == [10, 20]


# ----------------------------------------------------------------- frame loading (C1, C15, C16)
def _write_sequence(folder, n, size=(64, 48)):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        Image.new("RGB", size, (i * 10 % 255, 0, 0)).save(folder / f"f_{i:04d}.png")


def test_processing_size_floors_to_multiple_of_eight_and_never_upscales():
    assert c2d._processing_size(3840, 2160, 720) == (720, 400)
    assert c2d._processing_size(640, 360, 720) == (640, 360)
    assert c2d._processing_size(100, 50, 0) == (96, 48)


def test_sequence_folder_range_step_and_resize(tmp_path):
    seq = tmp_path / "seq"
    _write_sequence(seq, 10)
    frames, (w, h) = c2d.load_video_frames(seq, max_dimension=32, frame_step=2, in_point=3, out_point=8)
    assert (w, h) == (64, 48)
    assert frames.shape == (3, 24, 32, 3)  # frames 3, 5, 7 at 32x24
    assert frames.dtype == np.uint8
    # first loaded frame is source frame 3 (red channel 30)
    assert abs(int(frames[0, 0, 0, 0]) - 30) <= 2


def test_scene_images_cache_is_only_used_when_it_matches_the_clip(tmp_path, monkeypatch):
    monkeypatch.setattr(c2d, "BASE_DIR", tmp_path)
    clip = tmp_path / "02 VIDEOS" / "shot.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"not really a video")
    _write_sequence(tmp_path / "04 SCENES" / "shot" / "images", 5)
    for p in (tmp_path / "04 SCENES" / "shot" / "images").glob("*.png"):
        p.rename(p.with_suffix(".jpg"))

    # count matches the probed clip length -> cache used
    monkeypatch.setattr(c2d, "probe_frame_count", lambda path: 5)
    frames, _ = c2d.load_video_frames(clip, max_dimension=0)
    assert frames.shape[0] == 5

    # count does not match (e.g. extracted with a frame step) -> FFmpeg path, which
    # surfaces its error instead of hiding it
    monkeypatch.setattr(c2d, "probe_frame_count", lambda path: 12)
    monkeypatch.setattr(c2d, "_probe_dimensions", lambda path: None)
    with pytest.raises(ValueError, match="FFmpeg could not decode|Could not extract"):
        c2d.load_video_frames(clip, max_dimension=0)


@pytest.mark.skipif(not c2d.FFMPEG_EXE.exists(), reason="bundled ffmpeg not present")
def test_ffmpeg_fallback_honours_range_step_and_scale(tmp_path, monkeypatch):
    import imageio
    monkeypatch.setattr(c2d, "BASE_DIR", tmp_path)
    clip = tmp_path / "clip.mp4"
    writer = imageio.get_writer(str(clip), fps=24, quality=8, macro_block_size=1)
    for i in range(12):
        frame = np.zeros((96, 128, 3), np.uint8)
        frame[:, :, 0] = i * 20
        writer.append_data(frame)
    writer.close()

    frames, (w, h) = c2d.load_video_frames(clip, max_dimension=64, frame_step=3, in_point=2, out_point=10)
    assert (w, h) == (128, 96)
    assert frames.shape == (3, 48, 64, 3)  # frames 2, 5, 8 scaled to 64x48
    reds = [int(frames[i, :, :, 0].mean()) for i in range(3)]
    assert reds[0] < reds[1] < reds[2]
    assert abs(reds[0] - 40) < 12 and abs(reds[2] - 160) < 12
