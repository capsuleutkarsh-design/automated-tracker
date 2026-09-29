"""
How the exported Nuke graph is actually wired, and where the mesh lands.

A .nk file is a stack machine: every node is pushed when it is written, a node
with `inputs N` pops the top N, and the one on TOP becomes its input 0. The rig
used to rely on whatever happened to be on the stack and came out wired as
ScanlineRender bg = Scene, obj = Camera, cam = empty, with the STMaps' src and
stmap swapped. These tests run the file through a small stack simulator, so a
wrong push order fails here instead of in an artist's Nuke.

Also here: the environment mesh follows the Scene Setup transform like the
points do, and the 2D Blender reference camera frames exactly -1..1.

Nuke and Blender themselves are not available to the tests; the simulator
follows the documented .nk rules, and a paste into a real Nuke is still the
final word (tools/verify_nuke.py prints the same wiring from inside Nuke).
"""
import math
import re

import numpy as np
import pytest

import export_tools as et
from core import scene_transform as st

from test_3d_writers import CAM_CENTRED, CAM_SHIFTED, make_scene, two_frames_on_1001
from test_3d_distortion_export import BARREL, delivery, scene_with_frames


# =============================================================================
# A .nk STACK SIMULATOR
# =============================================================================
_NODE_START = re.compile(r"^([A-Za-z][A-Za-z0-9_]*) \{$")


def wire(nk_text):
    """
    {node name: [input 0, input 1, ...]} by the .nk stack rules.

    `set V [stack 0]` names the top, `push $V` pushes it again, `push 0`
    pushes an empty input (None). A node pops `inputs N` items - input 0 is the
    top - and is then pushed itself. Every node name has to be unique.
    """
    lines = nk_text.split("\n")
    stack, saved, inputs = [None], {}, {}
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _NODE_START.match(line)
        if m:
            body = []
            i += 1
            while lines[i] != "}":
                body.append(lines[i])
                i += 1
            n_in = next((int(b.split()[1]) for b in body if b.startswith(" inputs ")), None)
            name = next(b.split(None, 1)[1] for b in body if b.startswith(" name "))
            assert n_in is not None, "%s has no inputs line; Nuke would guess" % name
            assert name not in inputs, "%s is written twice" % name
            popped = [stack.pop() for _ in range(n_in)]
            inputs[name] = popped
            stack.append(name)
        elif line.startswith("set "):
            var = line.split()[1]
            assert line.endswith("[stack 0]")
            saved[var] = stack[-1]
        elif line == "push 0":
            stack.append(None)
        elif line.startswith("push $"):
            var = line[len("push $"):]
            assert var in saved, "push of $%s before it was set" % var
            stack.append(saved[var])
        i += 1
    return inputs


def _node_block(nk, name):
    end = nk.index(" name %s\n" % name)
    start = nk.rindex("\n", 0, nk.rindex(" {\n", 0, end)) + 1
    return nk[start:nk.index("\n}\n", end) + 3]


# =============================================================================
# THE 3D RIG
# =============================================================================
def test_the_rig_wires_plate_scene_and_camera_into_scanlinerender(tmp_path):
    scene = make_scene(tmp_path)
    out = scene / "camera_track_nuke.nk"
    assert et.export_nuke_camera_script(scene, CAM_SHIFTED, two_frames_on_1001(), et.PointCloud(),
                                        out, fps=24.0)
    nk = out.read_text(encoding="utf-8")
    w = wire(nk)
    assert w["ScanlineRender_Comp"] == ["Plate_Footage", "Scene3D", "Solved_Camera"]
    assert w["Scene3D"] == ["Sparse_PointCloud"]
    for root in ("Plate_Footage", "Solved_Camera", "Sparse_PointCloud", "Tracker_3D_Rig"):
        assert w[root] == []
    # bg / obj / cam are input names, not knobs; Nuke would reject them.
    render = _node_block(nk, "ScanlineRender_Comp")
    assert not [l for l in render.split("\n") if l.split(" ", 2)[1:2] in (["bg"], ["obj"], ["cam"])]


def test_a_stepped_plate_goes_in_through_its_timewarp(tmp_path):
    scene = make_scene(tmp_path, n_frames=3)
    out = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, CAM_CENTRED, two_frames_on_1001(), et.PointCloud(), out,
                                 fps=24.0, frame_step=2)
    w = wire(out.read_text(encoding="utf-8"))
    assert w["Plate_Timewarp"] == ["Plate_Footage"]
    assert w["ScanlineRender_Comp"] == ["Plate_Timewarp", "Scene3D", "Solved_Camera"]


def test_ground_card_and_mesh_are_scene_inputs_and_nothing_else(tmp_path):
    scene = make_scene(tmp_path)
    (scene / "environment_mesh.ply").write_bytes(b"ply\n")
    rng = np.random.default_rng(0)
    floor = np.column_stack([rng.uniform(-5, 5, 300), 2.0 + rng.normal(0, 0.005, 300),
                             rng.uniform(-5, 5, 300)])
    pc = et.PointCloud(floor, np.zeros((300, 3), dtype=np.uint8))
    out = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, CAM_CENTRED, two_frames_on_1001(), pc, out, fps=24.0,
                                 ground=et.detect_ground_plane_ransac(pc))
    w = wire(out.read_text(encoding="utf-8"))
    assert w["Scene3D"] == ["Sparse_PointCloud", "Ground_Plane", "Environment_Mesh"]
    assert w["Environment_Mesh"] == ["Environment_Mesh_Geo"]
    assert w["ScanlineRender_Comp"] == ["Plate_Footage", "Scene3D", "Solved_Camera"]


def test_the_stmaps_take_the_image_as_src_and_the_map_as_stmap(tmp_path):
    scene = scene_with_frames(tmp_path)
    out = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, {1: BARREL}, two_frames_on_1001(), et.PointCloud(), out,
                                 fps=24.0, undistort=delivery(scene, overscan=0.4))
    w = wire(out.read_text(encoding="utf-8"))
    # STMap: input 0 = src, input 1 = stmap.
    assert w["Undistort_Plate"] == ["Plate_Original", "Undistort_Map"]
    assert w["Redistort_Comp"] == [None, "Redistort_Map"]
    # ...and the rig next to it is untouched by the chain.
    assert w["ScanlineRender_Comp"] == ["Plate_Footage", "Scene3D", "Solved_Camera"]


# =============================================================================
# THE ENVIRONMENT MESH FOLLOWS THE SCENE SETUP TRANSFORM
# =============================================================================
def _euler_xyz_deg(rx, ry, rz):
    """The rotation Nuke and Blender build from XYZ-order Euler angles: Rz @ Ry @ Rx."""
    x, y, z = (math.radians(a) for a in (rx, ry, rz))
    Rx = np.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    Ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    Rz = np.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def _a_transform():
    rot = st.rotation_between(np.array([0.2, 0.9, 0.1]), np.array([0.0, 1.0, 0.0]))
    return st.make(2.5, rot, [0.4, -1.5, 3.0])


@pytest.mark.parametrize("target", ["nuke", "blender"])
@pytest.mark.parametrize("transform", [None, "set"])
def test_mesh_placement_moves_vertices_exactly_like_the_points(target, transform):
    T = _a_transform() if transform else None
    xyz = np.random.default_rng(3).normal(size=(20, 3))
    loc, rot, scale = et.mesh_placement(target, T)
    placed = scale * (xyz @ _euler_xyz_deg(*rot).T) + np.asarray(loc)
    np.testing.assert_allclose(placed, et.colmap_points_to(xyz, target, T), atol=1e-9)


def test_without_a_transform_the_mesh_node_is_the_plain_half_turn(tmp_path):
    assert et.mesh_placement("nuke") == (pytest.approx([0, 0, 0]), (180.0, 0.0, 0.0), 1.0)
    loc, rot, scale = et.mesh_placement("blender")
    assert rot == pytest.approx((-90.0, 0.0, 0.0)) and scale == 1.0


def test_the_nuke_mesh_node_carries_the_scene_transform(tmp_path):
    scene = make_scene(tmp_path)
    (scene / "environment_mesh.ply").write_bytes(b"ply\n")
    T = _a_transform()
    out = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, CAM_CENTRED, two_frames_on_1001(), et.PointCloud(), out,
                                 fps=24.0, scene_transform=T)
    block = _node_block(out.read_text(encoding="utf-8"), "Environment_Mesh")

    def knob(name):
        m = re.search(r"\n %s \{([^}]*)\}\n" % name, block)
        return [float(v) for v in m.group(1).split()]

    loc, rot, scale = et.mesh_placement("nuke", T)
    assert knob("translate") == pytest.approx(list(loc), abs=1e-6)
    assert knob("rotate") == pytest.approx(list(rot), abs=1e-6)
    assert float(re.search(r"\n uniform_scale (\S+)\n", block).group(1)) == pytest.approx(2.5)
    assert " rot_order XYZ\n" in block


def test_the_blender_mesh_import_carries_the_scene_transform(tmp_path):
    scene = make_scene(tmp_path)
    out = scene / "import_to_blender.py"
    et.export_blender_script(scene, CAM_CENTRED, two_frames_on_1001(), et.PointCloud(), out,
                             fps=24.0)
    plain = out.read_text(encoding="utf-8")
    assert ("imported_mesh.rotation_euler = (math.radians(-90.000000), math.radians(0.000000), "
            "math.radians(0.000000))") in plain
    assert "imported_mesh.location = (0.000000, 0.000000, 0.000000)" in plain
    assert "imported_mesh.scale = (1.000000, 1.000000, 1.000000)" in plain

    T = _a_transform()
    et.export_blender_script(scene, CAM_CENTRED, two_frames_on_1001(), et.PointCloud(), out,
                             fps=24.0, scene_transform=T)
    src = out.read_text(encoding="utf-8")
    compile(src, str(out), "exec")
    loc, rot, scale = et.mesh_placement("blender", T)
    assert "imported_mesh.location = (%.6f, %.6f, %.6f)" % tuple(loc) in src
    assert "imported_mesh.scale = (2.500000, 2.500000, 2.500000)" in src
    assert "math.radians(%.6f)" % rot[0] in src


# =============================================================================
# THE 2D BLENDER SCRIPT
# =============================================================================
def test_the_2d_reference_camera_frames_exactly_minus_one_to_one(tmp_path):
    import cotracker_2d as c2d
    tracks = np.array([[[0.0, 0.0], [200.0, 100.0]], [[1.0, 1.0], [199.0, 99.0]]])
    out = tmp_path / "t.py"
    c2d.export_2d_blender_empties(tracks, np.ones((2, 2), bool), 200, 100, 24.0, out,
                                  images_dir=tmp_path, start_frame=1011, timeline_start=1001)
    src = out.read_text(encoding="utf-8")
    assert "cam_data.sensor_fit = 'HORIZONTAL'" in src
    assert "cam_data.sensor_width = 36.0" in src and "cam_data.lens = 50.0" in src
    z = float(re.search(r"cam_obj\.location = \(0\.0, 0\.0, ([^)]*)\)", src).group(1).replace(" ", "")
              .split("/")[0]) / 18.0
    # The half-width of what a 50 mm lens on a 36 mm sensor sees at distance z.
    assert z * (36.0 / 2.0) / 50.0 == pytest.approx(1.0)
    # images/ starts at the clip's first frame, which sits on the timeline start
    # - not frame 1, and not the In point the tracks start on.
    assert "bg.image_user.frame_start = 1001" in src
    assert "scene.frame_start = 1011" in src


def test_the_multi_layer_2d_script_puts_the_plate_on_the_timeline_too(tmp_path):
    import cotracker_2d as c2d
    tracks = np.zeros((2, 1, 2))
    out = tmp_path / "m.py"
    c2d.export_multi_layer_blender([{"name": "A", "tracks": tracks}], 200, 100, 24.0, out,
                                   images_dir=tmp_path, start_frame=1001, timeline_start=1001)
    assert "bg.image_user.frame_start = 1001" in out.read_text(encoding="utf-8")
