"""
Pure math of the 3D exporters: rotation helpers, pose conversion per target
(checked by reprojecting a known point through the exported camera), the USD
row-vector matrix, intrinsics and the seeded ground plane.
"""
import math

import numpy as np
import pytest

import export_tools as et


def euler_xyz_to_mat(x, y, z):
    """Blender / Nuke 'XYZ' order: X applied first, then Y, then Z -> Rz @ Ry @ Rx."""
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def random_pose(rng):
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    t = rng.normal(size=3)
    return q, t


def make_image(q, t):
    R_cw = et.qvec2rotmat(q)
    R_world = R_cw.T
    C = -R_world @ t
    return {"center": C.tolist(), "R_world": R_world.tolist()}


def colmap_project(cam, q, t, X):
    R_cw = et.qvec2rotmat(q)
    p = R_cw @ X + t
    return (cam["focal_x"] * p[0] / p[2] + cam["cx"],
            cam["focal_y"] * p[1] / p[2] + cam["cy"]), p[2]


def dcc_project(cam, C, R, Xw):
    """Project through an exported camera: x right, y up, looks down -Z."""
    p = R.T @ (Xw - C)
    depth = -p[2]
    return (cam["focal_x"] * p[0] / depth + cam["cx"],
            cam["focal_y"] * (-p[1]) / depth + cam["cy"]), depth


CAM = {"width": 1920, "height": 1080, "focal_x": 1000.0, "focal_y": 1010.0, "cx": 950.0, "cy": 530.0}


def test_qvec2rotmat_is_rotation_and_euler_round_trips():
    rng = np.random.default_rng(1)
    for _ in range(50):
        q, _t = random_pose(rng)
        R = et.qvec2rotmat(q)
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-12)
        assert np.isclose(np.linalg.det(R), 1.0)
        x, y, z = et.rotmat2euler(R)
        assert np.allclose(euler_xyz_to_mat(x, y, z), R, atol=1e-9)


def test_rotmat2euler_never_returns_negative_zero():
    x, y, z = et.rotmat2euler(np.eye(3))
    assert (x, y, z) == (0.0, 0.0, 0.0)
    assert not any(math.copysign(1.0, v) < 0 for v in (x, y, z))


@pytest.mark.parametrize("target", ["blender", "nuke", "usd"])
def test_pose_conversion_reprojects_like_colmap(target):
    rng = np.random.default_rng(7)
    for _ in range(30):
        q, t = random_pose(rng)
        R_cw = et.qvec2rotmat(q)
        # a point 2..6 units in front of the camera
        p_cam = np.array([rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(2, 6)])
        X = R_cw.T @ (p_cam - t)

        uv_ref, depth_ref = colmap_project(CAM, q, t, X)
        C, R = et.colmap_pose_to(make_image(q, t), target)
        Xw = et.colmap_points_to(X, target)[0]
        uv, depth = dcc_project(CAM, C, R, Xw)

        assert np.allclose(uv, uv_ref, atol=1e-6)
        assert np.isclose(depth, depth_ref)
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-12)
        # the camera-to-world rotation must survive the XYZ Euler export
        assert np.allclose(euler_xyz_to_mat(*et.rotmat2euler(R)), R, atol=1e-9)


def test_world_bases_keep_up_up():
    up_colmap = np.array([0.0, -1.0, 0.0])          # COLMAP camera y is down
    assert np.allclose(et.WORLD_BASES["blender"] @ up_colmap, [0, 0, 1])   # Z-up
    assert np.allclose(et.WORLD_BASES["nuke"] @ up_colmap, [0, 1, 0])      # Y-up
    assert np.allclose(et.WORLD_BASES["usd"] @ up_colmap, [0, 1, 0])       # Y-up


def test_nuke_basis_is_diag_1_m1_m1_not_blender_zup():
    assert np.array_equal(et.WORLD_BASES["nuke"], np.diag([1.0, -1.0, -1.0]))
    assert not np.array_equal(et.WORLD_BASES["nuke"], et.WORLD_BASES["blender"])


def test_usd_matrix_rows_is_row_vector_with_transposed_rotation():
    rng = np.random.default_rng(3)
    q, t = random_pose(rng)
    C, R = et.colmap_pose_to(make_image(q, t), "usd")
    M = np.array(et.usd_matrix_rows(C, R)).reshape(4, 4)
    assert np.allclose(M[:3, :3], R.T)
    assert np.allclose(M[3, :3], C)
    assert np.allclose(M[:3, 3], 0) and M[3, 3] == 1.0
    # row-vector: [p, 1] @ M == R @ p + C
    p = rng.normal(size=3)
    assert np.allclose((np.append(p, 1.0) @ M)[:3], R @ p + C)


def test_camera_intrinsics_mm_and_vertical_fov():
    cam = {"width": 1920, "height": 1080, "focal_x": 1000.0, "focal_y": 1000.0}
    lens = et.camera_intrinsics_mm(cam)
    assert lens["lens_mm"] == pytest.approx(18.75)
    assert lens["sensor_width_mm"] == 36.0
    assert lens["sensor_height_mm"] == pytest.approx(20.25)
    assert lens["vfov_deg"] == pytest.approx(math.degrees(2 * math.atan(1080 / 2000.0)))
    assert lens["hfov_deg"] == pytest.approx(math.degrees(2 * math.atan(1920 / 2000.0)))
    assert lens["vfov_deg"] < lens["hfov_deg"]


def test_nuke_win_translate_ndc_and_y_flip():
    centred = {"width": 1920, "height": 1080, "cx": 960.0, "cy": 540.0}
    assert et.nuke_win_translate(centred) == (0.0, 0.0)
    # principal point 10 px left of centre and 10 px ABOVE centre (COLMAP y down)
    cam = {"width": 1920, "height": 1080, "cx": 950.0, "cy": 530.0}
    u, v = et.nuke_win_translate(cam)
    assert u == pytest.approx((1920 - 2 * 950) / 1920)   # +: window moves right, image moves left
    assert v == pytest.approx((2 * 530 - 1080) / 1080)   # -: COLMAP y-down flipped to Nuke y-up
    assert u > 0 and v < 0
    # full aperture spans -1..1
    edge = {"width": 1920, "height": 1080, "cx": 0.0, "cy": 0.0}
    assert et.nuke_win_translate(edge) == (1.0, -1.0)


def test_rotation_from_z_to():
    rng = np.random.default_rng(5)
    for _ in range(20):
        n = rng.normal(size=3)
        n /= np.linalg.norm(n)
        R = et.rotation_from_z_to(n)
        assert np.allclose(R @ R.T, np.eye(3), atol=1e-12)
        assert np.allclose(R @ [0, 0, 1], n)
    assert np.allclose(et.rotation_from_z_to([0, 0, 1]), np.eye(3))
    assert np.allclose(et.rotation_from_z_to([0, 0, -1]) @ [0, 0, 1], [0, 0, -1])


def test_ground_plane_is_seeded_uses_centroid_and_points_up():
    rng = np.random.default_rng(11)
    # floor at COLMAP y = +2 (y is down, so the floor is below the camera)
    floor = np.column_stack([rng.uniform(-5, 5, 400), 2.0 + rng.normal(0, 0.01, 400), rng.uniform(-5, 5, 400)])
    junk = rng.uniform(-5, 5, (100, 3))
    pc = et.PointCloud(np.vstack([floor, junk]), np.zeros((500, 3), dtype=np.uint8))

    g1 = et.detect_ground_plane_ransac(pc)
    g2 = et.detect_ground_plane_ransac(pc)
    assert g1 is not None
    assert np.array_equal(g1.normal, g2.normal) and g1.d == g2.d
    assert np.allclose(g1.normal, [0, -1, 0], atol=0.02)          # 'up' side of the floor
    assert np.allclose(g1.centroid, floor.mean(axis=0), atol=0.3)  # inlier centroid, not closest point to origin
    assert g1.inlier_ratio > 0.7

    loc_b, n_b = g1.in_convention("blender")
    assert np.allclose(n_b, [0, 0, 1], atol=0.02) and loc_b[2] == pytest.approx(-2.0, abs=0.05)
    loc_n, n_n = g1.in_convention("nuke")
    assert np.allclose(n_n, [0, 1, 0], atol=0.02) and loc_n[1] == pytest.approx(-2.0, abs=0.05)

    assert et.detect_ground_plane_ransac(et.PointCloud()) is None
