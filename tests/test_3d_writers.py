"""
Golden-string tests for the .chan and Nuke .nk writers (2 frames, timeline
start 1001, frame 1 unregistered), the PLY writer, and export_all_formats end
to end on a text model. No COLMAP, no Blender, no Qt.
"""
from pathlib import Path

import numpy as np
import pytest

import export_tools as et

DATA = Path(__file__).parent / "data"

CAM_CENTRED = {1: {"id": 1, "model": "PINHOLE", "width": 1920, "height": 1080,
                   "focal_x": 1000.0, "focal_y": 1000.0, "cx": 960.0, "cy": 540.0,
                   "k1": 0.0, "k2": 0.0, "fisheye": False, "params": [1000.0, 1000.0, 960.0, 540.0]}}
CAM_SHIFTED = {1: {"id": 1, "model": "SIMPLE_RADIAL", "width": 1920, "height": 1080,
                   "focal_x": 1000.0, "focal_y": 1000.0, "cx": 950.0, "cy": 530.0,
                   "k1": -0.05, "k2": 0.0, "fisheye": False, "params": [1000.0, 950.0, 530.0, -0.05]}}


def two_frames_on_1001():
    """frame_000002 = identity pose at the origin, frame_000003 = half turn about x at C = (-1, 2, 3)."""
    images = et.parse_colmap_images(DATA / "images.txt")
    et.apply_timeline_start(images, 1001)
    return images


def make_scene(tmp_path, n_frames=3, ext=".png"):
    scene = (tmp_path / "scene").resolve()
    (scene / "images").mkdir(parents=True)
    for k in range(1, n_frames + 1):
        (scene / "images" / f"frame_{k:06d}{ext}").write_bytes(b"")
    return scene


def test_chan_golden(tmp_path):
    scene = make_scene(tmp_path)
    out = scene / "camera_track.chan"
    assert et.export_nuke_chan(scene, CAM_CENTRED, two_frames_on_1001(), out)
    text = out.read_text(encoding="utf-8")
    lines = text.split("\n")
    # comment header documents the column layout and rot_order XYZ
    assert lines[0] == "# Nuke .chan camera: frame tx ty tz rx ry rz vfov"
    assert "rot_order XYZ" in lines[1] and "vertical" in lines[1]
    # vfov = 2*atan(1080 / (2*1000)) = 56.7381 deg (vertical, NOT the 87.66 horizontal)
    assert lines[2] == "1002\t0.000000\t0.000000\t0.000000\t0.000000\t0.000000\t0.000000\t56.7381"
    # Nuke Y-up: C = (-1, 2, 3) -> (-1, -2, -3); R = diag(1,-1,-1) -> rx 180
    assert lines[3] == "1003\t-1.000000\t-2.000000\t-3.000000\t180.000000\t0.000000\t0.000000\t56.7381"
    assert lines[4] == "" and len(lines) == 5
    assert "\r" not in text and "-0.000000" not in text


NK_GOLDEN = '''set cut_paste_input [stack 0]
version 14.0 v1
BackdropNode {
 inputs 0
 name Tracker_3D_Rig
 tile_color 0x243044ff
 gl_color 0x243044ff
 label "<b>Photogrammetry 3D Tracking Rig</b>\\n\\nCamera: 18.75mm | Sensor: 36.0x20.2mm | Shift: (0.0104, -0.0185)\\nDistortion: k1=-0.050000, k2=0.000000 | Points: 0 | Frames: 1002-1003 @ 24.000 fps | Plate: 1001-1003"
 note_font_size 14
 xpos -220
 ypos -120
 bdwidth 820
 bdheight 480
 z_order 0
}
push $cut_paste_input
Read {
 inputs 0
 file "IMGDIR/frame_%06d.png"
 format "1920 1080 0 0 1920 1080 1.0 1920x1080"
 first 1
 last 3
 origfirst 1
 origlast 3
 frame_mode offset
 frame 1000
 frame_rate 24.0000
 name Plate_Footage
 selected false
 xpos -180
 ypos 0
}
push $cut_paste_input
Camera3 {
 inputs 0
 rot_order XYZ
 translate {{curve x1002 0.000000 x1003 -1.000000}} {{curve x1002 0.000000 x1003 -2.000000}} {{curve x1002 0.000000 x1003 -3.000000}}
 rotate {{curve x1002 0.000000 x1003 180.000000}} {{curve x1002 0.000000 x1003 0.000000}} {{curve x1002 0.000000 x1003 0.000000}}
 focal 18.7500
 haperture 36.0000
 vaperture 20.2500
 win_translate {0.010417 -0.018519}
 name Solved_Camera
 selected true
 xpos 0
 ypos 100
}
push $cut_paste_input
ReadGeo2 {
 inputs 0
 file "PLYPATH"
 name Sparse_PointCloud
 selected false
 xpos 160
 ypos 0
}
Scene {
 inputs 2
 name Scene3D
 selected false
 xpos 160
 ypos 140
}
ScanlineRender {
 inputs 3
 bg Plate_Footage
 obj Scene3D
 cam Solved_Camera
 output_motion_vectors false
 name ScanlineRender_Comp
 selected false
 xpos 0
 ypos 260
}
'''


def test_nuke_nk_golden(tmp_path):
    scene = make_scene(tmp_path, n_frames=3, ext=".png")
    out = scene / "camera_track_nuke.nk"
    assert et.export_nuke_camera_script(scene, CAM_SHIFTED, two_frames_on_1001(), et.PointCloud(), out, fps=24.0)
    expected = (NK_GOLDEN
                .replace("IMGDIR", str(scene / "images").replace("\\", "/"))
                .replace("PLYPATH", str(scene / "points3D.ply").replace("\\", "/")))
    assert out.read_text(encoding="utf-8") == expected


def test_nuke_read_node_shows_plate_on_timeline_and_uses_real_extension(tmp_path):
    scene = make_scene(tmp_path, n_frames=5, ext=".exr")
    out = scene / "camera_track_nuke.nk"
    images = et.parse_colmap_images(DATA / "images.txt")
    et.apply_timeline_start(images, 1001)
    et.export_nuke_camera_script(scene, CAM_CENTRED, images, et.PointCloud(), out, fps=25.0)
    nk = out.read_text(encoding="utf-8")
    assert 'frame_%06d.exr"' in nk and ".jpg" not in nk
    # files are 1..5 on disk; offset 1000 puts frame_000001 on 1001
    assert "\n first 1\n last 5\n origfirst 1\n origlast 5\n frame_mode offset\n frame 1000\n" in nk
    assert "rot_order XYZ" in nk
    assert "Plate: 1001-1005" in nk


def test_nuke_ground_card_and_mesh_are_in_the_y_up_world(tmp_path):
    scene = make_scene(tmp_path)
    (scene / "environment_mesh.ply").write_bytes(b"ply\n")
    rng = np.random.default_rng(0)
    floor = np.column_stack([rng.uniform(-5, 5, 300), 2.0 + rng.normal(0, 0.005, 300), rng.uniform(-5, 5, 300)])
    pc = et.PointCloud(floor, np.zeros((300, 3), dtype=np.uint8))
    ground = et.detect_ground_plane_ransac(pc)
    out = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, CAM_CENTRED, two_frames_on_1001(), pc, out, fps=24.0, ground=ground)
    nk = out.read_text(encoding="utf-8")
    card = nk[nk.index("Card2 {"):nk.index("name Ground_Plane")]
    assert " rot_order XYZ\n" in card
    # floor at COLMAP y=+2 sits at Nuke y=-2, and a +Z-facing card needs ~-90 about X to face +Y
    tr = [float(v) for v in card.split("translate {")[1].split("}")[0].split()]
    rot = [float(v) for v in card.split("rotate {")[1].split("}")[0].split()]
    assert tr[1] == pytest.approx(-2.0, abs=0.05)
    assert rot[0] == pytest.approx(-90.0, abs=1.0)
    assert "TransformGeo {\n inputs 1\n rot_order XYZ\n rotate {180 0 0}\n name Environment_Mesh" in nk
    assert "\n inputs 4\n name Scene3D" in nk


def test_ply_writer_is_y_up_with_unix_newlines(tmp_path):
    pc = et.parse_colmap_points3D(DATA / "points3D.txt")
    out = tmp_path / "points3D.ply"
    assert et.export_ply_pointcloud(tmp_path, pc, out)
    raw = out.read_bytes()
    assert b"\r" not in raw
    lines = raw.decode("utf-8").split("\n")
    assert lines[0] == "ply" and "element vertex 3" in lines
    body = lines[lines.index("end_header") + 1:]
    # COLMAP (1.5, -2.5, 3.5) -> Y-up (1.5, 2.5, -3.5)
    assert body[0] == "1.500000 2.500000 -3.500000 255 128 0"
    assert body[3] == ""
    assert not et.export_ply_pointcloud(tmp_path, et.PointCloud(), out)


def test_blender_script_uses_ply_extension_and_timeline(tmp_path):
    scene = make_scene(tmp_path, n_frames=3, ext=".tif")
    out = scene / "import_to_blender.py"
    assert et.export_blender_script(scene, CAM_SHIFTED, two_frames_on_1001(), et.PointCloud(), out, fps=24.0)
    src = out.read_text(encoding="utf-8")
    assert "scene.frame_start = 1001" in src and "scene.frame_end = 1003" in src
    assert "frame_start = 1001" in src
    assert "endswith(('.tif',))" in src
    assert "points3D.ply" in src and "pts_coords = " not in src
    # keys land on 1002 and 1003, Z-up world: C=(-1,2,3) -> (-1, 3, -2)
    assert '"frame": 1002' in src and '"frame": 1003' in src
    assert '"loc": [-1.0, 3.0, -2.0]' in src
    compile(src, "import_to_blender.py", "exec")


def test_export_all_formats_end_to_end(tmp_path, monkeypatch):
    scene = make_scene(tmp_path, n_frames=3, ext=".png")
    sparse = scene / "sparse"
    sparse.mkdir()
    for name in ("cameras.txt", "images.txt", "points3D.txt"):
        (sparse / name).write_bytes((DATA / name).read_bytes())
    # never launch a real Blender from the test-suite
    monkeypatch.setattr(et, "find_blender_executable", lambda custom_path=None: None)
    logs = []
    res = et.export_all_formats(scene, fps=24.0, start_frame=1001, log_callback=lambda m, c: logs.append(m))
    assert res["success"], res
    assert res["frames_count"] == 2 and res["points_count"] == 3
    for name in ("points3D.ply", "import_to_blender.py", "camera_track_nuke.nk", "camera_track.chan", "camera_track.json"):
        assert (scene / name).exists(), name
    chan = (scene / "camera_track.chan").read_text(encoding="utf-8")
    assert "\n1002\t" in chan and "\n1003\t" in chan
    assert any("shifted by +1000" in m for m in logs)


def test_export_all_formats_reports_missing_model(tmp_path):
    scene = make_scene(tmp_path)
    (scene / "sparse").mkdir()
    res = et.export_all_formats(scene, fps=24.0)
    assert res["success"] is False and "sparse" in res["error"]


def test_export_all_formats_bin_only_without_colmap_fails_cleanly(tmp_path):
    import struct
    scene = make_scene(tmp_path)
    model = scene / "sparse" / "0"
    model.mkdir(parents=True)
    for name in ("cameras.bin", "images.bin", "points3D.bin"):
        (model / name).write_bytes(struct.pack("<Q", 5))
    logs = []
    res = et.export_all_formats(scene, fps=24.0, colmap_exe=tmp_path / "nope" / "colmap.exe",
                                log_callback=lambda m, c: logs.append(m))
    assert res["success"] is False
    assert any("binary" in m for m in logs)
