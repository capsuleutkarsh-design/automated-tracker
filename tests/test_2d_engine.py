"""
Pure-function tests for the 2D tracking engine (no Qt, no GPU).

Covers: frame_number, point_in_poly, scale_mask, generate_grid_points_with_masks,
filter_trajectories_by_animated_masks (In point / step), filter_tracks_confidence
(anchor on first visible frame, resolution-relative jump), auto_chunk_size with a
mocked mem_get_info, and run_cotracker_chunked with a fake model.
"""
import numpy as np
import pytest
import torch

import cotracker_2d as c2d
from mask_animator import AnimatedMask


# ----------------------------------------------------------------- frame numbering
def test_frame_number_default_and_offsets():
    assert c2d.frame_number(0) == 1
    assert c2d.frame_number(5) == 6
    assert c2d.frame_number(3, start_frame=1001) == 1004
    assert c2d.frame_number(3, start_frame=1001, frame_step=2) == 1007
    assert c2d.frame_number(2, start_frame=10, frame_step=0) == 12  # step floors at 1


# ----------------------------------------------------------------- geometry
SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]


def test_point_in_poly_inside_outside_and_degenerate():
    assert c2d.point_in_poly(5, 5, SQUARE)
    assert not c2d.point_in_poly(15, 5, SQUARE)
    assert not c2d.point_in_poly(5, -1, SQUARE)
    assert not c2d.point_in_poly(5, 5, [(0, 0), (10, 0)])  # fewer than 3 vertices


def test_point_in_poly_is_shared_with_the_canvas():
    from core.tracking_layer import point_in_poly, is_point_in_mask
    assert c2d.point_in_poly is point_in_poly
    assert c2d.is_point_in_mask is is_point_in_mask
    assert is_point_in_mask(5, 5, {"type": "poly", "points": SQUARE})
    assert is_point_in_mask(5, 5, {"type": "rect", "coords": [0, 0, 10, 10]})
    assert is_point_in_mask(5, 5, [10, 10, 0, 0])  # legacy, unordered corners
    assert not is_point_in_mask(5, 5, [1, 2, 3])


def test_scale_mask_divides_by_scale():
    assert c2d.scale_mask([20, 40, 60, 80], 2.0, 4.0) == [10, 10, 30, 20]
    r = c2d.scale_mask({"type": "rect", "coords": [20, 40, 60, 80]}, 2.0, 4.0)
    assert r == {"type": "rect", "coords": [10, 10, 30, 20]}
    p = c2d.scale_mask({"type": "poly", "points": [(20, 40), (60, 80)]}, 2.0, 4.0)
    assert p == {"type": "poly", "points": [[10, 10], [30, 20]]}


def test_generate_grid_points_with_masks():
    pts = c2d.generate_grid_points_with_masks(100, 200, grid_size=5)
    assert len(pts) == 25
    assert all(p[0] == 0.0 for p in pts)
    xs = sorted({p[1] for p in pts})
    assert xs[0] == 10 and xs[-1] == 190

    inc = {"type": "rect", "coords": [0, 0, 100, 100]}
    pts_inc = c2d.generate_grid_points_with_masks(100, 200, grid_size=5, inclusion_masks=[inc])
    assert 0 < len(pts_inc) < 25
    assert all(p[1] <= 100 for p in pts_inc)

    exc = {"type": "rect", "coords": [0, 0, 100, 100]}
    pts_exc = c2d.generate_grid_points_with_masks(100, 200, grid_size=5, exclusion_masks=[exc])
    assert len(pts_exc) + len(pts_inc) == 25

    # everything masked out -> a single centre point
    pts_none = c2d.generate_grid_points_with_masks(100, 200, grid_size=5, exclusion_masks=[{"type": "rect", "coords": [0, 0, 200, 100]}])
    assert pts_none == [[0.0, 100.0, 50.0]]


# ----------------------------------------------------------------- animated mask filter (A9)
def _exclusion_mask_at(frames_to_boxes):
    m = AnimatedMask("m_test", "Exc", "exclusion")
    for f, box in frames_to_boxes.items():
        m.set_keyframe(f, box, "rect")
    return m


def test_filter_trajectories_uses_absolute_frames_with_in_point_and_step():
    # Exclusion box covers x in [0, 50] on source frames <= 100 and x in [100, 150] from
    # source frame 110 on. The loaded clip starts at In point 100 with step 10, so local
    # t=0 is source 100 and local t=1 is source 110.
    mask = _exclusion_mask_at({0: [0, 0, 50, 50], 100: [0, 0, 50, 50], 110: [100, 0, 150, 50]})
    tracks = np.array([
        [[25.0, 25.0], [125.0, 25.0]],   # t=0 (source 100): pt0 in box, pt1 not
        [[25.0, 25.0], [125.0, 25.0]],   # t=1 (source 110): pt0 not, pt1 in box
    ])
    vis = np.ones((2, 2), dtype=bool)

    _, v = c2d.filter_trajectories_by_animated_masks(tracks, vis, [mask], in_point=100, frame_step=10, width=200, height=100)
    assert v.tolist() == [[False, True], [True, False]]

    # Without the In point the same samples would be judged against frames 0 and 1,
    # i.e. the first box both times.
    _, v0 = c2d.filter_trajectories_by_animated_masks(tracks, vis, [mask], width=200, height=100)
    assert v0.tolist() == [[False, True], [False, True]]


def test_filter_trajectories_inclusion_and_dict_masks_and_out_of_frame():
    inc = AnimatedMask("m_inc", "Inc", "inclusion")
    inc.set_keyframe(0, [0, 0, 50, 50], "rect")
    tracks = np.array([[[25.0, 25.0], [75.0, 25.0], [-5.0, 25.0]]])
    vis = np.ones((1, 3), dtype=bool)
    _, v = c2d.filter_trajectories_by_animated_masks(tracks, vis, [inc.to_dict()], width=100, height=100)
    assert v.tolist() == [[True, False, False]]
    # no masks -> untouched
    t2, v2 = c2d.filter_trajectories_by_animated_masks(tracks, vis, [])
    assert v2 is vis


# ----------------------------------------------------------------- confidence filter (A10, A11)
def test_confidence_filter_anchors_on_first_visible_frame():
    # Manual point placed on frame 2: model output before that is garbage and invisible.
    tracks = np.zeros((5, 1, 2), dtype=np.float32)
    tracks[:, 0, 0] = [-999.0, -999.0, 500.0, 502.0, 504.0]
    vis = np.array([[False], [False], [True], [True], [True]])
    t, v = c2d.filter_tracks_confidence(tracks, vis, frame_size=(1000, 1000))
    assert v[:, 0].tolist() == [False, False, True, True, True]
    assert t[2:, 0, 0].tolist() == [500.0, 502.0, 504.0]


def test_confidence_filter_jump_is_relative_to_frame_diagonal():
    # A 5%-of-diagonal step per frame is fine at any resolution; a 40% step is a jump.
    for w, h in ((512, 288), (3840, 2160)):
        diag = np.hypot(w, h)
        tracks = np.zeros((4, 1, 2), dtype=np.float32)
        tracks[:, 0, 0] = [0.0, 0.05 * diag, 0.10 * diag, 0.50 * diag]
        vis = np.ones((4, 1), dtype=bool)
        t, v = c2d.filter_tracks_confidence(tracks, vis, frame_size=(w, h))
        assert v[:, 0].tolist() == [True, True, True, False], (w, h)
        assert t[3, 0, 0] == pytest.approx(0.10 * diag)  # held at the last valid position


def test_confidence_filter_threshold_and_hold():
    tracks = np.zeros((3, 2, 2), dtype=np.float32)
    tracks[:, 0, 0] = [10, 11, 12]
    tracks[:, 1, 0] = [10, 11, 12]
    vis = np.ones((3, 2), dtype=bool)
    conf = np.array([[0.95, 0.95], [0.5, 0.95], [0.95, 0.95]], dtype=np.float32)
    t, v = c2d.filter_tracks_confidence(tracks, vis, conf=conf, min_confidence=0.7, max_jump_distance=100)
    assert v.tolist() == [[True, True], [False, True], [True, True]]
    assert t[1, 0, 0] == 10  # occluded sample holds the previous position
    assert t[1, 1, 0] == 11


# ----------------------------------------------------------------- chunk budgeting (A6)
def test_auto_chunk_size_budgets_from_model_resolution(monkeypatch):
    free = 16 * 1024 ** 3  # 70% of it (11.2 GB) is the budget
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (free, free))
    logs = []
    n_720 = c2d.auto_chunk_size(700, 3, 400, 720, "cuda", requested=120, log=lambda m, c=None: logs.append(m))
    n_4k = c2d.auto_chunk_size(700, 3, 2160, 3840, "cuda", requested=120, log=lambda m, c=None: logs.append(m))
    assert n_720 == 120  # the measured case: 120 frames at 720x400 peaked near 10 GB
    assert n_4k > c2d.MIN_CHUNK  # used to collapse to MIN_CHUNK
    assert n_4k <= n_720  # the input upload still costs something at 4K
    # not enabled / cpu / unknown VRAM all fall back sensibly
    assert c2d.auto_chunk_size(700, 3, 400, 720, "cuda", requested=120, enabled=False) == 700
    assert c2d.auto_chunk_size(50, 3, 400, 720, "cpu", requested=120) == 50

    def boom():
        raise RuntimeError("no cuda")
    monkeypatch.setattr(torch.cuda, "mem_get_info", boom)
    assert c2d.auto_chunk_size(700, 3, 400, 720, "cuda", requested=120) == 120


def test_auto_chunk_size_never_below_min_chunk(monkeypatch):
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (64 * 1024 ** 2, 64 * 1024 ** 2))
    assert c2d.auto_chunk_size(700, 3, 2160, 3840, "cuda", requested=120) == c2d.MIN_CHUNK


# ----------------------------------------------------------------- chunked inference (A7, A8, B6)
class FakeModel:
    """
    Tracks each query as x = qx + (t - qt), y = qy, visible from its query frame on.
    Before the query frame the output is garbage (-999) and invisible, the way a real
    tracker gives nothing useful for a point it has not been shown yet.
    """
    interp_shape = (384, 512)

    def __init__(self, oom_above=None):
        self.calls = []
        self.oom_above = oom_above

    def __call__(self, video, queries=None):
        T = video.shape[1]
        if self.oom_above is not None and T > self.oom_above:
            raise RuntimeError("CUDA out of memory. Tried to allocate")
        q = queries[0]
        assert int(q[:, 0].max()) < T, "query frame outside the chunk"
        self.calls.append((T, [int(v) for v in q[:, 0].tolist()]))
        t = torch.arange(T).float()[:, None]
        qt = q[:, 0][None]
        x = q[:, 1][None] + (t - qt)
        y = q[:, 2][None].expand(T, -1)
        vis = t >= qt
        x = torch.where(vis, x, torch.full_like(x, -999.0))
        return torch.stack([x, y], -1)[None], vis[None]


def _video(T=300):
    return torch.zeros((1, T, 3, 16, 16), dtype=torch.uint8)


def test_chunked_seeds_from_query_frame_and_stitches_overlaps():
    model = FakeModel()
    q = torch.tensor([[[100.0, 50.0, 7.0], [0.0, 3.0, 4.0]]])
    tracks, vis, conf = c2d.run_cotracker_chunked(model, _video(300), q, chunk_size=120, overlap=30, device="cpu")

    assert tracks.shape == (1, 300, 2, 2) and vis.shape == (1, 300, 2) and conf.shape == (1, 300, 2)
    # Windows: [0,120), [90,210), [180,300). Point 0's query (frame 100) is later than the
    # overlap seed (frame 90), so the second window must query it at 100-90 = 10.
    assert model.calls[0] == (120, [100, 0])
    assert model.calls[1][1] == [10, 0]
    assert model.calls[2][1] == [0, 0]
    # Stitched track is exact everywhere from the query frame on: x = 50 + (t - 100).
    t = np.arange(100, 300)
    np.testing.assert_allclose(tracks[0, 100:, 0, 0], 50 + (t - 100))
    np.testing.assert_allclose(tracks[0, :, 1, 0], 3 + np.arange(300))
    assert not vis[0, :100, 0].any() and vis[0, 100:, 0].all()
    assert vis[0, :, 1].all()
    assert tracks[0, 299, 0, 1] == 7.0


def test_chunked_reclamps_queries_after_oom_halving():
    model = FakeModel(oom_above=60)
    q = torch.tensor([[[100.0, 50.0, 7.0]]])
    logs = []
    tracks, vis, _ = c2d.run_cotracker_chunked(model, _video(300), q, chunk_size=120, overlap=30,
                                               device="cpu", log=lambda m, c=None: logs.append(m))
    # First window shrank 120 -> 60; the query at 100 was clamped to the new last frame.
    assert model.calls[0] == (60, [59])
    assert all(T <= 60 for T, _ in model.calls)
    assert any("out of memory" in m for m in logs)
    # The track continues exactly from the re-anchored frame onward.
    np.testing.assert_allclose(tracks[0, 59:, 0, 0], 50 + np.arange(300 - 59))
    assert vis[0, 59:, 0].all()


def test_chunked_single_pass_when_clip_fits():
    model = FakeModel()
    q = torch.tensor([[[0.0, 1.0, 2.0]]])
    tracks, vis, conf = c2d.run_cotracker_chunked(model, _video(50), q, chunk_size=120, device="cpu", auto_chunk=False)
    assert model.calls == [(50, [0])]
    np.testing.assert_allclose(tracks[0, :, 0, 0], 1 + np.arange(50))


def test_chunked_single_pass_oom_falls_back_to_blocks():
    model = FakeModel(oom_above=40)
    q = torch.tensor([[[0.0, 1.0, 2.0]]])
    tracks, vis, _ = c2d.run_cotracker_chunked(model, _video(50), q, chunk_size=120, overlap=10, device="cpu")
    assert model.calls[0][0] <= 40
    np.testing.assert_allclose(tracks[0, :, 0, 0], 1 + np.arange(50))


def test_chunked_cancel_between_chunks():
    model = FakeModel()
    q = torch.tensor([[[0.0, 1.0, 2.0]]])
    state = {"n": 0}

    def cancel():
        state["n"] += 1
        return state["n"] > 1  # let the first chunk run, cancel before the second

    with pytest.raises(c2d.TrackingCancelled):
        c2d.run_cotracker_chunked(model, _video(300), q, chunk_size=120, overlap=30, device="cpu", cancel_check=cancel)
    assert len(model.calls) == 1


def test_process_signature_accepts_cancel_check():
    import inspect
    assert "cancel_check" in inspect.signature(c2d.process_cotracker_2d).parameters
