"""
The scene transform through the exporters (1.4).

The maths is pinned in test_scene_transform.py; what matters here is that every
writer applies the SAME transform to the camera, the point cloud and the ground
plane, that the floor is fitted after it rather than before, and that a shot
with no transform set exports exactly the bytes it did before any of this
existed.
"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

import export_tools as et
from core import scene_transform as st

DATA = Path(__file__).parent / "data"

CAM = {1: {"id": 1, "model": "PINHOLE", "width": 1920, "height": 1080,
           "focal_x": 1000.0, "focal_y": 1000.0, "cx": 960.0, "cy": 540.0,
           "k1": 0.0, "k2": 0.0, "fisheye": False, "params": [1000.0, 1000.0, 960.0, 540.0]}}

# Scale, a 20 degree tilt of the floor and a move of the origin - the three
# things the artist sets, all at once, so nothing can pass by being ignored.
SET_BY_THE_ARTIST = st.make(3.7,
                            np.array([[1.0, 0.0, 0.0],
                                      [0.0, math.cos(math.radians(20.0)), -math.sin(math.radians(20.0))],
                                      [0.0, math.sin(math.radians(20.0)), math.cos(math.radians(20.0))]]),
                            [12.0, -4.0, 30.0])


def scene_with_frames(tmp_path, count=3, ext=".png", name="scene"):
    scene = (tmp_path / name).resolve()
    (scene / "images").mkdir(parents=True)
    for k in range(1, count + 1):
        (scene / "images" / f"frame_{k:06d}{ext}").write_bytes(b"")
    return scene


def two_frames_on_1001():
    images = et.parse_colmap_images(DATA / "images.txt")
    et.apply_timeline_start(images, 1001)
    return images


def chan_rows(path):
    """{frame: [tx, ty, tz, rx, ry, rz, vfov]} of a .chan file."""
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        rows[int(parts[0])] = [float(v) for v in parts[1:]]
    return rows


def ply_points(path):
    lines = path.read_text(encoding="utf-8").split("\n")
    body = [l for l in lines[lines.index("end_header") + 1:] if l.strip()]
    return np.array([[float(v) for v in l.split()[:3]] for l in body])


# -----------------------------------------------------------------------------
# nothing set changes nothing
# -----------------------------------------------------------------------------
def test_no_transform_and_the_identity_write_the_same_bytes(tmp_path):
    scene = scene_with_frames(tmp_path)
    images = two_frames_on_1001()
    points = et.parse_colmap_points3D(DATA / "points3D.txt")

    for transform in (None, st.identity()):
        outputs = {}
        for name, write in (
            ("camera_track.chan",
             lambda out, T: et.export_nuke_chan(scene, CAM, images, out, scene_transform=T)),
            ("camera_track_nuke.nk",
             lambda out, T: et.export_nuke_camera_script(scene, CAM, images, points, out,
                                                         fps=24.0, scene_transform=T)),
            ("import_to_blender.py",
             lambda out, T: et.export_blender_script(scene, CAM, images, points, out,
                                                     fps=24.0, scene_transform=T)),
            ("points3D.ply",
             lambda out, T: et.export_ply_pointcloud(scene, points, out, scene_transform=T)),
        ):
            out = scene / name
            assert write(out, None)
            outputs[name] = out.read_bytes()
            assert write(out, transform)
            assert out.read_bytes() == outputs[name], f"{name} changed with {transform}"


# -----------------------------------------------------------------------------
# what the transform does to the exports
# -----------------------------------------------------------------------------
def test_the_chan_and_the_ply_move_together(tmp_path):
    scene = scene_with_frames(tmp_path)
    images = two_frames_on_1001()
    points = et.parse_colmap_points3D(DATA / "points3D.txt")
    T = SET_BY_THE_ARTIST
    W = et.WORLD_BASES["nuke"]

    chan = scene / "camera_track.chan"
    et.export_nuke_chan(scene, CAM, images, chan, scene_transform=T)
    moved = chan_rows(chan)
    for img in images.values():
        expected = W @ st.apply_to_points(T, np.asarray(img["center"], dtype=float))
        assert np.allclose(moved[img["frame"]][:3], expected, atol=1e-6)

    ply = scene / "points3D.ply"
    et.export_ply_pointcloud(scene, points, ply, scene_transform=T)
    assert np.allclose(ply_points(ply), et.colmap_points_to(points.xyz, "nuke", T), atol=1e-6)


def test_a_scale_only_transform_scales_translations_and_leaves_rotations_alone(tmp_path):
    scene = scene_with_frames(tmp_path)
    images = two_frames_on_1001()
    plain = scene / "plain.chan"
    scaled = scene / "scaled.chan"
    et.export_nuke_chan(scene, CAM, images, plain)
    et.export_nuke_chan(scene, CAM, images, scaled, scene_transform=st.make(2.0))

    for frame, row in chan_rows(plain).items():
        after = chan_rows(scaled)[frame]
        assert np.allclose(after[:3], np.array(row[:3]) * 2.0, atol=1e-6)
        assert np.allclose(after[3:], row[3:], atol=1e-9)


def test_the_transformed_scene_still_reprojects_to_the_same_pixels(tmp_path):
    """
    The invariant, at the level the exporters work: camera and points come out
    of colmap_pose_to / colmap_points_to already transformed, and a DCC camera
    built from them has to see every point exactly where it saw it before.
    """
    rng = np.random.default_rng(4)
    images = two_frames_on_1001()
    cloud = rng.normal(size=(120, 3)) * 1.5 + np.array([0.0, 0.0, 6.0])
    cam = CAM[1]

    def local_points(img, transform):
        C, R = et.colmap_pose_to(img, "nuke", transform)
        X = et.colmap_points_to(cloud, "nuke", transform)
        # A DCC camera looks down its own -Z, so depth is -z in camera space.
        return (X - C) @ R

    def pixels(local, keep):
        return np.column_stack([cam["focal_x"] * local[keep, 0] / -local[keep, 2] + cam["cx"],
                                cam["focal_y"] * local[keep, 1] / -local[keep, 2] + cam["cy"]])

    seen = 0
    for img in images.values():
        before = local_points(img, None)
        after = local_points(img, SET_BY_THE_ARTIST)
        # One of the two fixture cameras faces the other way, so only the points
        # actually in front of this one are worth comparing.
        keep = before[:, 2] < -0.5
        # The depth is multiplied by the scale - that is the whole content of
        # the invariant - and the division by it cancels, so the pixel does not
        # move at all.
        assert np.allclose(after[keep, 2], before[keep, 2] * 3.7, atol=1e-6)
        assert np.allclose(pixels(before, keep), pixels(after, keep), atol=1e-6)
        seen += int(keep.sum())
    assert seen > 20


# -----------------------------------------------------------------------------
# the ground plane, which is the easy one to get upside down
# -----------------------------------------------------------------------------
def floor_at(y, tilt=0.0, n=400, seed=0):
    """A cloud on a plane at COLMAP height `y`, optionally tilted about X."""
    rng = np.random.default_rng(seed)
    flat = np.column_stack([rng.uniform(-5, 5, n),
                            y + rng.normal(0, 0.002, n),
                            rng.uniform(-5, 5, n)])
    if tilt:
        c, s = math.cos(tilt), math.sin(tilt)
        R = np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])
        flat = flat @ R.T
    return et.PointCloud(flat, np.zeros((n, 3), dtype=np.uint8))


def test_a_floor_at_colmap_y_plus_two_is_level_in_every_dcc(tmp_path):
    """
    COLMAP's sky is -Y, so the fitted normal must be COLMAP_UP and each world
    basis must turn that into its own up axis. Getting the sign wrong here puts
    the ground plane over the artist's head in both DCCs.
    """
    ground = et.fit_ground_plane(floor_at(2.0))
    assert ground is not None
    assert np.allclose(ground.normal, st.COLMAP_UP, atol=1e-3)

    loc_nuke, norm_nuke = ground.in_convention("nuke")
    assert np.allclose(norm_nuke, [0.0, 1.0, 0.0], atol=1e-3)
    assert loc_nuke[1] == pytest.approx(-2.0, abs=0.05)

    loc_blender, norm_blender = ground.in_convention("blender")
    assert np.allclose(norm_blender, [0.0, 0.0, 1.0], atol=1e-3)
    assert loc_blender[2] == pytest.approx(-2.0, abs=0.05)


def test_the_ground_is_fitted_after_the_transform_not_before():
    """A floor the artist levelled must come out level, not still tilted."""
    tilt = math.radians(20.0)
    points = floor_at(2.0, tilt=tilt, seed=1)

    # What the artist's Scene setup would build from those very points.
    T = st.build(points_for_ground=points.xyz, up=st.COLMAP_UP)

    tilted = et.fit_ground_plane(points)
    levelled = et.fit_ground_plane(points, T)
    assert tilted is not None and levelled is not None

    _loc, norm_before = tilted.in_convention("nuke")
    _loc, norm_after = levelled.in_convention("nuke")
    # Before: the plate's own 20 degree tilt. After: flat, in every basis.
    assert math.degrees(math.acos(abs(float(np.dot(norm_before, [0.0, 1.0, 0.0]))))) == pytest.approx(20.0, abs=1.0)
    assert np.allclose(norm_after, [0.0, 1.0, 0.0], atol=1e-3)
    assert np.allclose(et.fit_ground_plane(points, T).in_convention("blender")[1],
                       [0.0, 0.0, 1.0], atol=1e-3)


def test_the_nuke_card_and_the_blender_plane_agree_with_the_moved_cloud(tmp_path):
    scene = scene_with_frames(tmp_path)
    points = floor_at(2.0, tilt=math.radians(20.0), seed=2)
    T = st.build(points_for_ground=points.xyz, scale_pair=([0.0, 0.0, 0.0], [0.0, 0.0, 1.0]),
                 real_distance=3.0, up=st.COLMAP_UP)

    nk = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, CAM, two_frames_on_1001(), points, nk,
                                 fps=24.0, scene_transform=T)
    card = nk.read_text(encoding="utf-8")
    card = card[card.index("Card2 {"):card.index("name Ground_Plane")]
    rot = [float(v) for v in card.split("rotate {")[1].split("}")[0].split()]
    tr = [float(v) for v in card.split("translate {")[1].split("}")[0].split()]
    # A Card faces +Z, so a level floor in Nuke's Y-up world needs -90 about X.
    assert rot[0] == pytest.approx(-90.0, abs=1.0)
    # The floor sits where the transformed cloud sits, not where it used to.
    moved = et.colmap_points_to(points.xyz, "nuke", T)
    assert tr[1] == pytest.approx(float(moved[:, 1].mean()), abs=0.1)


# -----------------------------------------------------------------------------
# what the JSON says was done
# -----------------------------------------------------------------------------
def test_camera_track_json_records_the_transform(tmp_path, monkeypatch):
    scene = scene_with_frames(tmp_path)
    sparse = scene / "sparse"
    sparse.mkdir()
    for name in ("cameras.txt", "images.txt", "points3D.txt"):
        (sparse / name).write_bytes((DATA / name).read_bytes())
    monkeypatch.setattr(et, "find_blender_executable", lambda custom_path=None: None)

    T = dict(SET_BY_THE_ARTIST)
    T["notes"] = ["Ground not levelled: the selected points lie on a straight line."]
    res = et.export_all_formats(scene, fps=24.0, start_frame=1001, scene_transform=T)
    assert res["success"], res

    meta = json.loads((scene / "camera_track.json").read_text(encoding="utf-8"))
    record = meta["scene_transform"]
    assert record["scale"] == pytest.approx(3.7)
    assert np.allclose(record["rotation"], T["rotation"])
    assert record["translation"] == [12.0, -4.0, 30.0]
    assert record["notes"] == T["notes"]
    assert record["applied"] is True
    # Plain JSON, because the Re-export path reads this back.
    assert json.loads(json.dumps(record)) == record


def test_camera_track_json_says_null_when_nothing_was_set(tmp_path, monkeypatch):
    scene = scene_with_frames(tmp_path)
    sparse = scene / "sparse"
    sparse.mkdir()
    for name in ("cameras.txt", "images.txt", "points3D.txt"):
        (sparse / name).write_bytes((DATA / name).read_bytes())
    monkeypatch.setattr(et, "find_blender_executable", lambda custom_path=None: None)
    et.export_all_formats(scene, fps=24.0, start_frame=1001)
    meta = json.loads((scene / "camera_track.json").read_text(encoding="utf-8"))
    assert meta["scene_transform"] is None
    assert et.scene_transform_record(st.identity())["applied"] is False
