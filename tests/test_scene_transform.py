"""
Pure maths of the scene transform: the storage form, composition and inversion,
the three constraint fits, and - the one that matters - the invariant that a
similarity leaves every reprojection alone.
"""
import json
import math

import numpy as np
import pytest

from core import scene_transform as st


def rot_x(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def rot_y(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def colmap_project(cam, R_wc, C, X):
    """COLMAP pinhole projection of a world point: y is down, pixel centres at +0.5."""
    p = R_wc @ (np.asarray(X, dtype=float) - np.asarray(C, dtype=float))
    return np.array([cam["focal_x"] * p[0] / p[2] + cam["cx"],
                     cam["focal_y"] * p[1] / p[2] + cam["cy"]])


CAM = {"model": "PINHOLE", "width": 1920, "height": 1080,
       "focal_x": 1400.0, "focal_y": 1400.0, "cx": 953.0, "cy": 542.0,
       "params": [1400.0, 1400.0, 953.0, 542.0]}


# ---------------------------------------------------------------- representation
def test_identity_is_identity():
    T = st.identity()
    assert st.is_identity(T)
    assert np.allclose(st.to_matrix(T), np.eye(4))


def test_transform_dict_round_trips_through_json():
    T = st.make(2.5, rot_y(0.3), [1.0, -2.0, 3.5])
    restored = json.loads(json.dumps(T))
    assert restored == T
    # And the numbers survived, not just the shape.
    assert np.allclose(st.to_matrix(restored), st.to_matrix(T))
    # Nothing numpy leaked into the dict, or the project file would not save.
    assert isinstance(T["scale"], float)
    assert all(isinstance(v, float) for row in T["rotation"] for v in row)
    assert all(isinstance(v, float) for v in T["translation"])


def test_matrix_round_trip():
    T = st.make(0.37, rot_x(-0.9) @ rot_y(1.4), [4.0, 5.0, 6.0])
    back = st.from_matrix(st.to_matrix(T))
    assert back["scale"] == pytest.approx(T["scale"])
    assert np.allclose(back["rotation"], T["rotation"])
    assert np.allclose(back["translation"], T["translation"])


def test_from_matrix_rejects_a_non_similarity():
    M = np.eye(4)
    M[0, 0] = 2.0          # non-uniform scale
    with pytest.raises(ValueError):
        st.from_matrix(M)
    M = np.eye(4)
    M[:3, :3] = np.diag([1.0, 1.0, -1.0])   # reflection
    with pytest.raises(ValueError):
        st.from_matrix(M)


def test_compose_applies_left_to_right():
    a = st.make(2.0, rot_y(0.4), [1.0, 0.0, 0.0])
    b = st.make(3.0, rot_x(-0.2), [0.0, 5.0, -1.0])
    both = st.compose(a, b)
    P = np.array([[1.0, 2.0, 3.0], [-4.0, 0.5, 2.0]])
    assert np.allclose(st.apply_to_points(both, P),
                       st.apply_to_points(b, st.apply_to_points(a, P)))
    # ... which is the matrix product in the other order.
    assert np.allclose(st.to_matrix(both), st.to_matrix(b) @ st.to_matrix(a))


def test_invert_undoes():
    T = st.make(4.2, rot_x(0.7) @ rot_y(-1.1), [-3.0, 2.0, 9.0])
    P = np.array([[1.0, 2.0, 3.0], [0.0, 0.0, 0.0], [-5.0, 4.0, 1.0]])
    assert np.allclose(st.apply_to_points(st.invert(T), st.apply_to_points(T, P)), P)
    assert st.is_identity(st.from_matrix(st.to_matrix(st.compose(T, st.invert(T)))), tol=1e-9)


# ------------------------------------------------------------------------- fits
def test_fit_scale_sets_a_real_distance():
    a = np.array([1.0, 1.0, 1.0])
    b = np.array([1.0, 1.0, 3.0])          # two solve units apart
    s = st.fit_scale(a, b, 5.0)
    assert s == pytest.approx(2.5)
    assert np.linalg.norm(s * b - s * a) == pytest.approx(5.0)


def test_fit_scale_rejects_degenerate_input():
    with pytest.raises(ValueError):
        st.fit_scale([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 1.0)
    with pytest.raises(ValueError):
        st.fit_scale([0.0, 0.0, 0.0], [1.0, 0.0, 0.0], 0.0)


def test_fit_ground_levels_a_tilted_plane():
    # A plane whose normal is a known tilted direction, sampled unevenly.
    normal = np.array([0.2, 0.9, -0.3])
    normal /= np.linalg.norm(normal)
    basis = np.linalg.svd(normal.reshape(1, 3))[2][1:]     # two in-plane axes
    rng = np.random.default_rng(7)
    coeffs = rng.normal(size=(30, 2))
    pts = coeffs @ basis + np.array([3.0, -1.0, 2.0])

    R, reason = st.fit_ground(pts, up=(0.0, 1.0, 0.0))
    assert reason is None
    assert np.linalg.det(R) == pytest.approx(1.0)
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-12)
    # The fitted normal really lands on up...
    assert np.allclose(R @ normal, [0.0, 1.0, 0.0], atol=1e-9)
    # ...and every point ends up at the same height, i.e. the floor is level.
    y = st.apply_to_points(st.make(1.0, R, None), pts)[:, 1]
    assert float(y.max() - y.min()) == pytest.approx(0.0, abs=1e-9)


def test_fit_ground_takes_the_shortest_arc_and_adds_no_roll():
    normal = np.array([0.0, 0.8, 0.6])
    basis = np.linalg.svd(normal.reshape(1, 3))[2][1:]
    rng = np.random.default_rng(3)
    pts = rng.normal(size=(20, 2)) @ basis

    R, reason = st.fit_ground(pts, up=(0.0, 1.0, 0.0))
    assert reason is None
    # Shortest arc: the rotation angle is exactly the angle between the two
    # directions - nothing extra, so no roll was introduced about up.
    expected = math.acos(float(np.dot(normal / np.linalg.norm(normal), [0.0, 1.0, 0.0])))
    angle = math.acos(max(-1.0, min(1.0, (float(np.trace(R)) - 1.0) / 2.0)))
    assert angle == pytest.approx(expected, abs=1e-9)
    # The rotation axis is perpendicular to up, which is what "no roll" means.
    axis = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    assert float(np.dot(axis / np.linalg.norm(axis), [0.0, 1.0, 0.0])) == pytest.approx(0.0, abs=1e-12)


def test_fit_ground_honours_colmap_up():
    # In COLMAP world the sky is -Y, so a COLMAP caller levels onto (0, -1, 0).
    normal = np.array([0.1, -0.95, 0.3])
    normal /= np.linalg.norm(normal)
    basis = np.linalg.svd(normal.reshape(1, 3))[2][1:]
    rng = np.random.default_rng(11)
    pts = rng.normal(size=(25, 2)) @ basis
    R, reason = st.fit_ground(pts, up=st.COLMAP_UP)
    assert reason is None
    assert np.allclose(R @ normal, st.COLMAP_UP, atol=1e-9)


def test_fit_ground_on_colinear_points_returns_identity_with_a_reason():
    line = np.linspace(-4.0, 4.0, 12).reshape(-1, 1) * np.array([0.3, 0.5, -0.8])
    R, reason = st.fit_ground(line)
    assert np.allclose(R, np.eye(3))
    assert reason and "straight line" in reason


def test_fit_ground_on_too_few_or_identical_points_returns_identity_with_a_reason():
    R, reason = st.fit_ground([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    assert np.allclose(R, np.eye(3))
    assert reason and "three points" in reason

    R, reason = st.fit_ground([[2.0, 2.0, 2.0]] * 5)
    assert np.allclose(R, np.eye(3))
    assert reason and "same place" in reason


def test_fit_origin_moves_a_point_to_zero():
    p = np.array([3.0, -7.0, 0.5])
    t = st.fit_origin(p)
    assert np.allclose(p + t, np.zeros(3))


# ------------------------------------------------------------------------ build
def test_build_applies_scale_then_rotation_then_translation():
    a = np.array([0.0, 0.0, 0.0])
    b = np.array([0.0, 0.0, 2.0])
    normal = np.array([0.0, 0.6, 0.8])
    basis = np.linalg.svd(normal.reshape(1, 3))[2][1:]
    rng = np.random.default_rng(5)
    floor = rng.normal(size=(40, 2)) @ basis + np.array([0.0, 1.0, 0.0])
    origin = np.array([1.5, -2.0, 0.25])

    notes = []
    T = st.build(points_for_ground=floor, scale_pair=(a, b), real_distance=7.0,
                 origin_point=origin, up=(0.0, 1.0, 0.0), notes=notes)
    assert notes == []
    s, Rot, t = st.parts(T)
    assert s == pytest.approx(3.5)
    # The origin point lands exactly on zero, which only holds if the
    # translation was fitted AFTER the scale and rotation.
    assert np.allclose(st.apply_to_points(T, origin), np.zeros(3), atol=1e-12)
    # Spelled out: X' = s * (Rot @ X) + t.
    X = np.array([2.0, -1.0, 4.0])
    assert np.allclose(st.apply_to_points(T, X), s * (Rot @ X) + t)


def test_build_notes_a_constraint_it_could_not_meet():
    notes = []
    T = st.build(points_for_ground=[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]],
                 notes=notes)
    assert np.allclose(T["rotation"], np.eye(3))
    assert len(notes) == 1 and "Ground not levelled" in notes[0]


def test_build_with_nothing_set_is_the_identity():
    assert st.is_identity(st.build())


# --------------------------------------------------------- the whole point of it
def test_reprojection_is_unchanged_by_the_transform():
    """
    A similarity moves the world and the camera together, so the plate does not
    move: every point must land on exactly the same pixel.
    """
    rng = np.random.default_rng(42)
    R_wc = rot_x(0.35) @ rot_y(-0.8)
    C = np.array([1.0, -0.5, 2.0])
    points = rng.normal(size=(200, 3)) * 2.0 + np.array([0.0, 0.0, 8.0])
    # Keep every point in front of the camera so the projection is meaningful.
    points = points[(R_wc @ (points - C).T)[2] > 0.5]
    assert len(points) > 50

    T = st.make(3.7, rot_x(math.radians(20.0)), [12.0, -4.0, 30.0])
    R_wc_new, C_new = st.apply_to_camera(T, R_wc, C)
    points_new = st.apply_to_points(T, points)

    for X, X_new in zip(points, points_new):
        before = colmap_project(CAM, R_wc, C, X)
        after = colmap_project(CAM, R_wc_new, C_new, X_new)
        assert np.allclose(before, after, atol=1e-6)


def test_reprojection_invariant_holds_through_a_distorted_lens_too():
    """The same check with a real COLMAP lens: distortion only sees the ray."""
    from core import lens

    cam = {"model": "SIMPLE_RADIAL", "width": 1920, "height": 1080,
           "focal_x": 1400.0, "focal_y": 1400.0, "cx": 953.0, "cy": 542.0,
           "params": [1400.0, 953.0, 542.0, -0.18]}

    def project(R_wc, C, X):
        p = R_wc @ (np.asarray(X, dtype=float) - np.asarray(C, dtype=float))
        d = lens.distort_points(cam, np.array([p[0] / p[2], p[1] / p[2]]))
        return np.array([cam["focal_x"] * d[0] + cam["cx"], cam["focal_y"] * d[1] + cam["cy"]])

    rng = np.random.default_rng(1)
    R_wc = rot_y(0.5) @ rot_x(-0.2)
    C = np.array([-2.0, 1.0, 0.0])
    points = rng.normal(size=(60, 3)) + np.array([0.0, 0.0, 10.0])

    T = st.make(3.7, rot_x(math.radians(20.0)) @ rot_y(math.radians(-9.0)), [5.0, 5.0, -2.0])
    R_wc_new, C_new = st.apply_to_camera(T, R_wc, C)
    for X in points:
        assert np.allclose(project(R_wc, C, X),
                           project(R_wc_new, C_new, st.apply_to_points(T, X)), atol=1e-6)


def test_apply_to_colmap_image_matches_apply_to_camera():
    """export_tools' images hold camera-to-world, so the rotation left-multiplies."""
    R_wc = rot_x(0.2) @ rot_y(1.3)
    C = np.array([4.0, 0.0, -1.0])
    img = {"center": C.tolist(), "R_world": R_wc.T.tolist()}
    T = st.make(1.8, rot_y(0.44), [2.0, 2.0, 2.0])

    C_new, R_world_new = st.apply_to_colmap_image(T, img)
    R_wc_expected, C_expected = st.apply_to_camera(T, R_wc, C)
    assert np.allclose(C_new, C_expected)
    assert np.allclose(np.asarray(R_world_new).T, R_wc_expected)


def test_apply_to_points_keeps_the_input_shape():
    T = st.make(2.0, rot_y(0.1), [1.0, 1.0, 1.0])
    assert st.apply_to_points(T, [1.0, 2.0, 3.0]).shape == (3,)
    assert st.apply_to_points(T, np.zeros((7, 3))).shape == (7, 3)
