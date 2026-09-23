"""
Fixing a drifting 2D track (roadmap 2.1), the parts that need no Qt and no GPU.

Covers: splicing a re-tracked segment into a stored result, a backwards run
coming back in shot order, reading tracks_2d.json back, matching it to the
layers in the window (including the mismatch case), and a one-point re-track
driven by the same fake model tests/test_2d_engine.py uses.
"""
import numpy as np
import pytest
import torch

import cotracker_2d as c2d
# The same fake tracker the engine tests drive, so a re-tracked segment is
# checked against known positions rather than against the GPU.
from test_2d_engine import FakeModel, _video


# ----------------------------------------------------------------- splicing
def _stored(T=10, N=3):
    """A result whose point n sits at x = 100*n + t, y = 50*n, every frame visible."""
    tracks = np.zeros((T, N, 2), dtype=np.float32)
    for n in range(N):
        tracks[:, n, 0] = 100 * n + np.arange(T)
        tracks[:, n, 1] = 50 * n
    return {
        "tracks": tracks,
        "vis": np.ones((T, N), dtype=bool),
        "conf": np.full((T, N), 0.9, dtype=np.float32),
    }


def test_splice_replaces_from_the_correction_onward_and_leaves_the_rest():
    layer = _stored()
    before = layer["tracks"].copy()
    seg = np.stack([np.full(6, 999.0), np.full(6, 888.0)], axis=-1)   # frames 4..9

    count = c2d.splice_track(layer, 1, 4, seg,
                             vis=np.array([True] * 5 + [False]),
                             conf=np.linspace(0.5, 1.0, 6))

    assert count == 6
    # Everything before the correction is untouched, to the bit.
    np.testing.assert_array_equal(layer["tracks"][:4], before[:4])
    # The corrected point moved from frame 4 on, and nothing else did.
    np.testing.assert_allclose(layer["tracks"][4:, 1, 0], 999.0)
    np.testing.assert_allclose(layer["tracks"][4:, 1, 1], 888.0)
    np.testing.assert_array_equal(layer["tracks"][:, 0], before[:, 0])
    np.testing.assert_array_equal(layer["tracks"][:, 2], before[:, 2])
    # Visibility and confidence travel with the segment.
    assert layer["vis"][9, 1] == False and layer["vis"][8, 1] == True
    assert layer["conf"][4, 1] == pytest.approx(0.5)
    assert layer["conf"][3, 1] == pytest.approx(0.9)   # before the correction: as it was


def test_splice_of_a_backwards_segment_covers_only_up_to_the_correction():
    layer = _stored()
    before = layer["tracks"].copy()
    seg = np.stack([np.full(5, 7.0), np.full(5, 8.0)], axis=-1)       # frames 0..4
    assert c2d.splice_track(layer, 0, 0, seg) == 5
    np.testing.assert_allclose(layer["tracks"][:5, 0, 0], 7.0)
    np.testing.assert_array_equal(layer["tracks"][5:, 0], before[5:, 0])


def test_splice_is_clipped_to_the_clip_and_ignores_a_point_that_is_not_there():
    layer = _stored(T=10, N=3)
    long_seg = np.zeros((40, 2), dtype=np.float32)
    assert c2d.splice_track(layer, 2, 8, long_seg) == 2        # only frames 8 and 9 exist
    assert c2d.splice_track(layer, 9, 0, long_seg) == 0        # no such point
    assert c2d.splice_track(layer, 0, 99, long_seg) == 0       # past the end


def test_a_hand_correction_is_visible_and_fully_confident():
    layer = _stored()
    layer["vis"][3, 1] = False
    layer["conf"][3, 1] = 0.05
    assert c2d.set_corrected_sample(layer, 1, 3, 640.5, 360.25)
    assert layer["tracks"][3, 1].tolist() == pytest.approx([640.5, 360.25])
    assert layer["vis"][3, 1] and layer["conf"][3, 1] == pytest.approx(1.0)
    assert not c2d.set_corrected_sample(layer, 99, 3, 1.0, 2.0)


# ----------------------------------------------------------------- backwards running
def test_reversed_run_comes_back_in_shot_order():
    """
    The fake model walks x by +1 per frame from the query. Run over the reversed
    clip, that is -1 per frame in shot time - and the arrays must still come
    back frame 0 first.
    """
    model = FakeModel()
    T = 50
    # Query on the LAST frame, which is the point of tracking backwards.
    q = torch.tensor([[[float(T - 1), 500.0, 7.0]]])
    tracks, vis, conf = c2d.run_cotracker_reversed(
        model, _video(T), q, chunk_size=120, device="cpu", auto_chunk=False)

    assert tracks.shape == (1, T, 1, 2)
    # Reversed index i is shot frame T-1-i, so walking +1 backwards through the
    # shot reads as -1 per frame forwards: x is highest on the first frame.
    np.testing.assert_allclose(tracks[0, :, 0, 0], 500.0 + np.arange(T)[::-1])
    assert tracks[0, T - 1, 0, 0] == 500.0          # the queried frame is where it was put
    assert tracks[0, 0, 0, 1] == 7.0
    assert vis[0, :, 0].all()
    # The model saw the clip once, queried on its own frame 0 (the shot's last).
    assert model.calls == [(T, [0])]


def test_retrack_segment_forward_and_backward_cover_the_right_frames():
    model = FakeModel()
    T = 60

    first_t, xy, vis, conf = c2d.run_retrack_segment(
        model, _video(T), 20, 300.0, 40.0, backwards=False,
        chunk_size=120, device="cpu", auto_chunk=False)
    assert first_t == 20 and len(xy) == T - 20
    np.testing.assert_allclose(xy[:, 0], 300.0 + np.arange(T - 20))
    assert xy[0, 0] == 300.0                      # leaves exactly where it was put
    # Only the frames of the segment were sent to the tracker, and one point.
    assert model.calls == [(T - 20, [0])]

    model2 = FakeModel()
    first_b, xy_b, _v, _c = c2d.run_retrack_segment(
        model2, _video(T), 20, 300.0, 40.0, backwards=True,
        chunk_size=120, device="cpu", auto_chunk=False)
    assert first_b == 0 and len(xy_b) == 21
    # Ascending frame order: the corrected frame is last and holds the seed.
    np.testing.assert_allclose(xy_b[:, 0], 300.0 + np.arange(21)[::-1])
    assert xy_b[-1, 0] == 300.0
    assert model2.calls == [(21, [0])]


# ----------------------------------------------------------------- reading a result back
def _write_result(tmp_path, tracks, vis=None, conf=None, layer_name=None, start_frame=1001,
                  step=1):
    out = tmp_path / "tracks_2d.json"
    vis = np.ones(tracks.shape[:2], dtype=bool) if vis is None else vis
    c2d.export_2d_json(tracks, vis, 200, 100, out, start_frame=start_frame, frame_step=step,
                       conf=conf, layer_name=layer_name)
    return out


def test_load_tracks_2d_round_trips_a_written_export(tmp_path):
    tracks = np.array([[[10.0, 20.0], [30.0, 40.0]],
                       [[11.0, 21.0], [31.0, 41.0]]], dtype=np.float32)
    vis = np.array([[True, False], [True, True]])
    conf = np.array([[0.9, 0.1], [0.8, 0.75]])
    path = _write_result(tmp_path, tracks, vis, conf, layer_name="Wall A", start_frame=1001, step=2)

    res = c2d.load_tracks_2d(path)
    assert res["width"] == 200 and res["height"] == 100
    assert res["start_frame"] == 1001 and res["frame_step"] == 2 and res["frame_count"] == 2
    block = res["layers"]["Wall_A"]
    np.testing.assert_allclose(block["tracks"], tracks)
    np.testing.assert_array_equal(block["vis"], vis)
    np.testing.assert_allclose(block["conf"], conf, atol=1e-4)
    assert block["frames"] == [1001, 1003]
    # Frame <-> index both ways, including a frame the step skips.
    assert c2d.result_index_for_frame(res, 1003) == 1
    assert c2d.result_index_for_frame(res, 1002) is None
    assert c2d.result_index_for_frame(res, 1000) is None
    assert c2d.result_index_for_frame(res, 1005) is None


def test_load_tracks_2d_reads_an_old_export_without_confidence(tmp_path):
    """A file written before 2.2 has no confidence; its samples are not all bad."""
    import json
    tracks = np.zeros((2, 1, 2), dtype=np.float32)
    path = _write_result(tmp_path, tracks)
    data = json.loads(path.read_text(encoding="utf-8"))
    for rec in data["tracks"][0]["frames"]:
        rec.pop("confidence")
    data.pop("layer")
    path.write_text(json.dumps(data), encoding="utf-8")

    res = c2d.load_tracks_2d(path)
    assert list(res["layers"]) == [None]
    assert (res["layers"][None]["conf"] == 1.0).all()


def test_load_tracks_2d_reads_the_multi_layer_master_file(tmp_path):
    tracks = np.array([[[10.0, 20.0]], [[11.0, 21.0]]], dtype=np.float32)
    vis = np.ones((2, 1), dtype=bool)
    layers = [{"name": "Wall A", "tracks": tracks, "vis": vis, "point_count": 1,
               "conf": np.array([[0.5], [0.25]])},
              {"name": "Floor", "tracks": tracks + 5, "vis": vis, "point_count": 1}]
    c2d.write_2d_exports(layers, tmp_path, 200, 100, 24.0, None,
                         {"start_frame": 1001, "frame_step": 1}, 1001, source_name="clip.mov")

    res = c2d.load_tracks_2d(tmp_path / "tracks_2d.json")
    assert sorted(res["layers"]) == ["Floor", "Wall_A"]
    np.testing.assert_allclose(res["layers"]["Wall_A"]["conf"], [[0.5], [0.25]])
    np.testing.assert_allclose(res["layers"]["Floor"]["tracks"], tracks + 5)


def test_load_tracks_2d_refuses_what_is_not_a_result(tmp_path):
    bad = tmp_path / "tracks_2d.json"
    bad.write_text("{not json", encoding="utf-8")
    assert c2d.load_tracks_2d(bad) is None
    bad.write_text('{"hello": 1}', encoding="utf-8")
    assert c2d.load_tracks_2d(bad) is None
    assert c2d.load_tracks_2d(tmp_path / "missing.json") is None


# ----------------------------------------------------------------- matching to the layers
def _result_with(names):
    tracks = np.zeros((2, 1, 2), dtype=np.float32)
    return {"layers": {n: {"tracks": tracks} for n in names}, "frame_count": 2,
            "start_frame": 1, "frame_step": 1}


def test_match_by_name_puts_each_stored_track_on_its_own_layer():
    res = _result_with(["Wall_A", "Floor"])
    mapping, problem = c2d.match_result_to_layers(res, ["Wall A", "Floor"])
    # The window's own spelling is the key; the file's is the value.
    assert mapping == {"Wall A": "Wall_A", "Floor": "Floor"} and problem == ""


def test_match_attaches_an_unnamed_result_to_a_single_layer_only():
    res = _result_with([None])
    mapping, problem = c2d.match_result_to_layers(res, ["Layer 1 (Wall)"])
    assert mapping == {"Layer 1 (Wall)": None} and problem == ""

    mapping, problem = c2d.match_result_to_layers(res, ["Layer 1 (Wall)", "Floor"])
    assert mapping is None
    assert "does not say which layer" in problem and "2 layers" in problem


def test_match_refuses_a_result_that_does_not_line_up():
    # A layer was renamed since the solve.
    mapping, problem = c2d.match_result_to_layers(_result_with(["Wall_A"]), ["Wall B"])
    assert mapping is None and "Wall_A" in problem and "Wall B" in problem

    # A layer was added since the solve: half a result is not a match.
    mapping, problem = c2d.match_result_to_layers(_result_with(["Wall_A"]), ["Wall A", "Floor"])
    assert mapping is None and "re-run the 2D track" in problem

    # Nothing loaded at all.
    assert c2d.match_result_to_layers(None, ["Wall A"])[0] is None
    assert "no tracks" in c2d.match_result_to_layers({"layers": {}}, ["Wall A"])[1]


# ----------------------------------------------------------------- the whole correction pass
class _FakeFrames:
    """Stands in for load_video_frames: T frames at half the plate's size."""

    def __init__(self, T, plate=(200, 100)):
        self.T = T
        self.plate = plate

    def __call__(self, video_path, max_dimension=720, frame_step=1, in_point=0, out_point=-1):
        w, h = self.plate
        frames = np.zeros((self.T, h // 2, w // 2, 3), dtype=np.uint8)
        return frames, (w, h)


def test_retrack_correction_splices_one_point_and_leaves_the_others(monkeypatch):
    T, N = 40, 3
    layer = _stored(T=T, N=N)
    result = {"width": 200, "height": 100, "start_frame": 1001, "frame_step": 1,
              "frame_count": T, "layers": {"Wall_A": layer}}
    before = layer["tracks"].copy()

    monkeypatch.setattr(c2d, "load_video_frames", _FakeFrames(T))
    monkeypatch.setattr(c2d, "get_default_device", lambda: "cpu")
    model = FakeModel()

    info = c2d.retrack_correction(
        "clip.mov", result, "Wall_A", 1, 10, 64.0, 30.0,
        config={"auto_chunk": False}, backwards=False, model=model)

    assert info["frame"] == 1011                      # 1001 + 10
    assert info["spliced"] == {"forward": T - 10}
    # The correction is where the artist put it, in PLATE pixels: the engine
    # worked at half size, so the query went in at 32, 15 and came back doubled.
    assert model.calls == [(T - 10, [0])]
    np.testing.assert_allclose(layer["tracks"][10, 1], [64.0, 30.0])
    np.testing.assert_allclose(layer["tracks"][11:, 1, 0], 64.0 + 2 * np.arange(1, T - 10))
    # Frames before it, and every other point, are exactly as they were.
    np.testing.assert_array_equal(layer["tracks"][:10], before[:10])
    np.testing.assert_array_equal(layer["tracks"][:, 0], before[:, 0])
    np.testing.assert_array_equal(layer["tracks"][:, 2], before[:, 2])


def test_retrack_correction_backwards_also_fixes_the_run_up(monkeypatch):
    T = 30
    layer = _stored(T=T, N=1)
    result = {"width": 200, "height": 100, "start_frame": 1, "frame_step": 1,
              "frame_count": T, "layers": {None: layer}}
    monkeypatch.setattr(c2d, "load_video_frames", _FakeFrames(T))
    monkeypatch.setattr(c2d, "get_default_device", lambda: "cpu")

    info = c2d.retrack_correction("clip.mov", result, None, 0, 12, 100.0, 50.0,
                                  config={"auto_chunk": False}, backwards=True,
                                  model=FakeModel())
    assert info["spliced"] == {"forward": T - 12, "backward": 13}
    np.testing.assert_allclose(layer["tracks"][12, 0], [100.0, 50.0])
    # Backwards from the correction, in plate pixels (the engine worked at half
    # size): 2 px per frame, ending exactly on the corrected frame.
    np.testing.assert_allclose(layer["tracks"][:13, 0, 0], 100.0 + 2 * np.arange(13)[::-1])


def test_retrack_correction_refuses_a_range_that_no_longer_matches(monkeypatch):
    layer = _stored(T=40, N=1)
    result = {"width": 200, "height": 100, "start_frame": 1, "frame_step": 1,
              "frame_count": 40, "layers": {None: layer}}
    monkeypatch.setattr(c2d, "load_video_frames", _FakeFrames(25))   # In/Out moved since
    monkeypatch.setattr(c2d, "get_default_device", lambda: "cpu")
    with pytest.raises(ValueError) as err:
        c2d.retrack_correction("clip.mov", result, None, 0, 5, 1.0, 2.0,
                               config={}, model=FakeModel())
    assert "40 frames" in str(err.value) and "25" in str(err.value)


# ----------------------------------------------------------------- re-export
def test_export_corrected_result_rewrites_every_format(tmp_path):
    T = 3
    layer = _stored(T=T, N=1)
    result = {"width": 200, "height": 100, "start_frame": 1001, "frame_step": 1,
              "frame_count": T, "layers": {"Wall_A": layer}}
    c2d.set_corrected_sample(layer, 0, 1, 123.0, 45.0)

    out = tmp_path / "2026-01-01_00-00-00"
    latest = tmp_path / "_latest"
    paths = c2d.export_corrected_result(
        result, [{"name": "Wall A", "key": "Wall_A", "export_cornerpin": False}],
        out, fps=24.0, timeline_start=1001, latest_dir=latest)

    for key in ("json_path", "nuke_path", "ae_path", "blender_path"):
        assert paths[key].exists()
    nk = paths["nuke_path"].read_text(encoding="utf-8")
    assert "x1002 123.00" in nk                       # the corrected frame
    assert "{curve x1001 0.1000 x1002 0.0000 x1003 0.1000}" in nk   # error = 1 - confidence
    # _latest is what every 1-click button reads, so it has to move too.
    assert (latest / "tracks_2d.json").exists()

    reloaded = c2d.load_tracks_2d(paths["json_path"])
    np.testing.assert_allclose(reloaded["layers"]["Wall_A"]["tracks"][1, 0], [123.0, 45.0])
