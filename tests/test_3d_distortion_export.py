"""
Distortion delivery through the exporters (1.5).

The lens maths is pinned in test_lens.py; here it is what the export folder and
the Nuke script end up holding: the undistorted plate (with COLMAP stood in for,
never run), the two STMaps, the pinhole camera that matches them, the STMap
nodes wired the right way round, and the fields camera_track.json carries so
another tool does not have to guess.
"""
import json
from pathlib import Path

import numpy as np
import pytest

import export_tools as et
from core import lens

DATA = Path(__file__).parent / "data"

# The fixture model's first camera: a real barrel, so there is something to undo.
BARREL = {"id": 1, "model": "SIMPLE_RADIAL", "width": 1920, "height": 1080,
          "focal_x": 1000.0, "focal_y": 1000.0, "cx": 950.0, "cy": 530.0,
          "k1": -0.3, "k2": 0.0, "fisheye": False, "params": [1000.0, 950.0, 530.0, -0.3]}
PINHOLE = {"id": 1, "model": "PINHOLE", "width": 1920, "height": 1080,
           "focal_x": 1000.0, "focal_y": 1000.0, "cx": 960.0, "cy": 540.0,
           "k1": 0.0, "k2": 0.0, "fisheye": False, "params": [1000.0, 1000.0, 960.0, 540.0]}


def scene_with_frames(tmp_path, count=3, ext=".png"):
    scene = (tmp_path / "scene").resolve()
    (scene / "images").mkdir(parents=True)
    for k in range(1, count + 1):
        (scene / "images" / f"frame_{k:06d}{ext}").write_bytes(b"")
    return scene


def two_frames_on_1001():
    images = et.parse_colmap_images(DATA / "images.txt")
    et.apply_timeline_start(images, 1001)
    return images


def fake_undistorted_plate(scene, count=3, ext=".jpg"):
    """What `colmap image_undistorter --output_type COLMAP` leaves behind."""
    out = scene / "undistorted" / "images"
    out.mkdir(parents=True, exist_ok=True)
    for k in range(1, count + 1):
        (out / f"frame_{k:06d}{ext}").write_bytes(b"")
    (scene / "undistorted" / "sparse").mkdir(parents=True, exist_ok=True)
    return et.source_sequence_plate(out)


def delivery(scene, cam=BARREL, overscan=0.5, with_plate=True, count=3):
    """A prepare_undistort result without going anywhere near colmap.exe."""
    undist_map, redist_map, fmt = et.write_stmaps(scene, cam, overscan)
    return {
        "overscan": overscan,
        "pinhole": lens.pinhole_of(cam, overscan),
        "undistort_map": undist_map,
        "redistort_map": redist_map,
        "map_format": fmt,
        "plate": fake_undistorted_plate(scene, count) if with_plate else None,
    }


# -----------------------------------------------------------------------------
# the maps themselves
# -----------------------------------------------------------------------------
def test_write_stmaps_writes_both_maps_as_exr(tmp_path):
    scene = scene_with_frames(tmp_path)
    logs = []
    undist, redist, fmt = et.write_stmaps(scene, BARREL, 0.4, lambda m, c: logs.append(m))
    # OpenEXR is bundled now; a fallback here is a warning, not a silent loss.
    assert fmt == "exr", logs
    assert Path(undist).name == "undistort.exr" and Path(redist).name == "redistort.exr"
    assert Path(undist).exists() and Path(redist).exists()
    assert any("40% overscan" in m for m in logs)
    # The map really is the one lens.py computes for this overscan.
    back = lens.read_exr(undist)
    assert np.allclose(back[..., :2], lens.undistort_stmap(BARREL, overscan=0.4), atol=1e-6)


def test_a_png_fallback_is_called_out(tmp_path, monkeypatch):
    scene = scene_with_frames(tmp_path)
    monkeypatch.setattr(lens, "write_exr",
                        lambda path, arr: (str(Path(path).with_suffix(".png")), "png16"))
    logs = []
    _u, _r, fmt = et.write_stmaps(scene, BARREL, 0.1, lambda m, c: logs.append(m))
    assert fmt == "png16"
    assert any("16-bit PNG" in m and "clips" in m for m in logs)


def test_a_lens_with_no_distortion_delivers_nothing(tmp_path):
    scene = scene_with_frames(tmp_path)
    logs = []
    assert et.prepare_undistort(scene, PINHOLE, scene / "sparse", scene / "images",
                                overscan=0.1, log_callback=lambda m, c: logs.append(m)) is None
    assert any("nothing to undistort" in m for m in logs)
    assert not (scene / "undistort.exr").exists()


# -----------------------------------------------------------------------------
# COLMAP, stood in for
# -----------------------------------------------------------------------------
def test_image_undistorter_is_called_the_way_the_roadmap_says(tmp_path, monkeypatch):
    scene = scene_with_frames(tmp_path)
    calls = []

    class Done:
        returncode = 0
        stdout = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        fake_undistorted_plate(scene)
        return Done()

    monkeypatch.setattr("core.proc.run_hidden", fake_run)
    monkeypatch.setattr(et, "_resolve_colmap_exe", lambda colmap_exe=None: tmp_path / "colmap.exe")

    plate = et.run_image_undistorter(scene / "undistorted", scene / "sparse", scene / "images")
    assert plate and plate["pattern"].endswith("undistorted/images/frame_%06d.jpg")
    cmd = calls[0]
    assert cmd[1] == "image_undistorter"
    assert cmd[cmd.index("--image_path") + 1] == str(scene / "images")
    assert cmd[cmd.index("--input_path") + 1] == str(scene / "sparse")
    assert cmd[cmd.index("--output_path") + 1] == str(scene / "undistorted")
    assert cmd[cmd.index("--output_type") + 1] == "COLMAP"


def test_a_failed_undistorter_is_logged_and_the_rest_still_exports(tmp_path, monkeypatch):
    scene = scene_with_frames(tmp_path)

    def fake_run(cmd, **kwargs):
        raise OSError("colmap fell over")

    monkeypatch.setattr("core.proc.run_hidden", fake_run)
    monkeypatch.setattr(et, "_resolve_colmap_exe", lambda colmap_exe=None: tmp_path / "colmap.exe")

    logs = []
    out = et.prepare_undistort(scene, BARREL, scene / "sparse", scene / "images",
                               overscan=0.4, log_callback=lambda m, c: logs.append(m))
    assert any("fell over" in m for m in logs)
    # No plate, but the maps - the part that does not need COLMAP - are there.
    assert out["plate"] is None
    assert Path(out["undistort_map"]).exists() and Path(out["redistort_map"]).exists()


def test_a_plate_colmap_sized_its_own_way_is_called_out(tmp_path, monkeypatch):
    """
    image_undistorter picks its own raster. The exported camera is the one that
    matches the STMaps, so a disagreement has to be said out loud.
    """
    scene = scene_with_frames(tmp_path)
    monkeypatch.setattr(et, "run_image_undistorter",
                        lambda *args, **kwargs: fake_undistorted_plate(scene))
    (scene / "undistorted" / "sparse").mkdir(parents=True, exist_ok=True)
    (scene / "undistorted" / "sparse" / "cameras.txt").write_text(
        "1 PINHOLE 2000 1200 1000 1000 1000 600\n", encoding="utf-8")

    logs = []
    out = et.prepare_undistort(scene, BARREL, scene / "sparse", scene / "images",
                               overscan=0.5, log_callback=lambda m, c: logs.append(m))
    assert out["plate"] is not None
    assert any("2000x1200" in m and "2880x1620" in m for m in logs), logs


def test_no_colmap_binary_says_so_and_carries_on(tmp_path, monkeypatch):
    scene = scene_with_frames(tmp_path)
    monkeypatch.setattr(et, "_resolve_colmap_exe", lambda colmap_exe=None: None)
    logs = []
    assert et.run_image_undistorter(scene / "undistorted", scene / "sparse", scene / "images",
                                    log_callback=lambda m, c: logs.append(m)) is None
    assert any("colmap.exe was not found" in m for m in logs)


# -----------------------------------------------------------------------------
# what the Nuke script ends up holding
# -----------------------------------------------------------------------------
def test_the_nuke_camera_is_the_pinhole_of_the_undistorted_plate(tmp_path):
    scene = scene_with_frames(tmp_path)
    out = scene / "camera_track_nuke.nk"
    given = delivery(scene, overscan=0.5)
    et.export_nuke_camera_script(scene, {1: BARREL}, two_frames_on_1001(), et.PointCloud(), out,
                                 fps=24.0, undistort=given)
    nk = out.read_text(encoding="utf-8")

    pin = given["pinhole"]
    assert (pin["width"], pin["height"]) == (2880, 1620)      # 50 % is not clamped away
    mm = et.camera_intrinsics_mm(pin)
    assert f" focal {mm['lens_mm']:.4f}\n" in nk
    assert f" haperture {mm['sensor_width_mm']:.4f}\n" in nk
    assert f' format "2880 1620 0 0 2880 1620 1.0 2880x1620"\n' in nk
    # ...and the Read the camera sits on is the undistorted sequence.
    assert f' file "{given["plate"]["pattern"]}"\n' in nk
    assert "Undistorted plate, 50% overscan" in nk


def test_the_stmap_nodes_appear_only_when_the_maps_were_written(tmp_path):
    scene = scene_with_frames(tmp_path)
    out = scene / "camera_track_nuke.nk"
    given = delivery(scene, overscan=0.4)

    et.export_nuke_camera_script(scene, {1: BARREL}, two_frames_on_1001(), et.PointCloud(), out,
                                 fps=24.0, undistort=given)
    nk = out.read_text(encoding="utf-8")

    undist = str(given["undistort_map"]).replace("\\", "/")
    redist = str(given["redistort_map"]).replace("\\", "/")
    assert f' file "{undist}"\n name Undistort_Map\n' in nk
    assert f' file "{redist}"\n name Redistort_Map\n' in nk
    # The original plate goes into the undistort STMap, the map into its second
    # input - the two nodes just above it, deepest first.
    chain = nk[nk.index("Plate_Original"):]
    assert chain.index("name Undistort_Map") < chain.index("name Undistort_Plate")
    assert "STMap {\n inputs 2\n channels rgba\n name Undistort_Plate\n" in nk
    # ...and that plate is the one the artist shot, at its own timeline offset.
    images_dir = str(scene / "images").replace(chr(92), "/")
    assert f' file "{images_dir}/frame_%06d.png"\n' in nk
    assert " name Plate_Original\n" in nk
    # The redistort one is ready but off, and its source input is left empty.
    assert "push 0\nRead {" in nk
    assert "STMap {\n inputs 2\n channels rgba\n disable true\n name Redistort_Comp\n" in nk
    assert "Lens_Distortion" in nk

    # Nothing of the sort without a delivery.
    et.export_nuke_camera_script(scene, {1: BARREL}, two_frames_on_1001(), et.PointCloud(), out,
                                 fps=24.0)
    plain = out.read_text(encoding="utf-8")
    assert "STMap" not in plain and "undistort.exr" not in plain and "Plate_Original" not in plain


def test_blender_uses_the_undistorted_plate_and_its_pinhole(tmp_path):
    scene = scene_with_frames(tmp_path)
    out = scene / "import_to_blender.py"
    given = delivery(scene, overscan=0.5)
    et.export_blender_script(scene, {1: BARREL}, two_frames_on_1001(), et.PointCloud(), out,
                             fps=24.0, undistort=given)
    src = out.read_text(encoding="utf-8")
    mm = et.camera_intrinsics_mm(given["pinhole"])
    assert "scene.render.resolution_x = 2880" in src
    assert f"cam_data.lens = {mm['lens_mm']:.4f}" in src
    assert str(Path(given["plate"]["pattern"]).parent).replace("\\", "/") in src
    assert "UNDISTORTED plate" in src and "50% overscan" in src
    compile(src, "import_to_blender.py", "exec")


# -----------------------------------------------------------------------------
# end to end, and what the JSON says
# -----------------------------------------------------------------------------
def export_scene(tmp_path, monkeypatch, plate=True, **kwargs):
    scene = scene_with_frames(tmp_path)
    sparse = scene / "sparse"
    sparse.mkdir()
    for name in ("cameras.txt", "images.txt", "points3D.txt"):
        (sparse / name).write_bytes((DATA / name).read_bytes())
    monkeypatch.setattr(et, "find_blender_executable", lambda custom_path=None: None)
    monkeypatch.setattr(
        et, "run_image_undistorter",
        lambda output_dir, model_dir, images_dir, colmap_exe=None, log_callback=None:
        fake_undistorted_plate(scene) if plate else None)
    res = et.export_all_formats(scene, fps=24.0, start_frame=1001, **kwargs)
    assert res["success"], res
    return scene, res


def test_export_all_formats_delivers_the_plate_and_the_maps(tmp_path, monkeypatch):
    scene, res = export_scene(tmp_path, monkeypatch, write_undistort=True, overscan=0.5)
    assert (scene / "undistort.exr").exists() and (scene / "redistort.exr").exists()
    assert res["undistorted_plate"].endswith("frame_%06d.jpg")
    assert len(res["stmaps"]) == 2

    meta = json.loads((scene / "camera_track.json").read_text(encoding="utf-8"))
    # The fixture's first camera is SIMPLE_RADIAL with k1 = -0.05.
    assert meta["distortion"]["model"] == "SIMPLE_RADIAL"
    assert meta["distortion"]["k1"] == pytest.approx(-0.05)
    assert meta["distortion"]["has_distortion"] is True
    assert meta["distortion"]["params"] == [1000.5, 950.25, 530.75, -0.05]
    # 50 % overscan survives: nothing clamps it to the panel's 10 %.
    assert meta["overscan"] == pytest.approx(0.5)
    assert (meta["pinhole"]["width"], meta["pinhole"]["height"]) == (2880, 1620)
    assert meta["pinhole"]["model"] == "PINHOLE" and meta["pinhole"]["source_model"] == "SIMPLE_RADIAL"
    assert meta["undistort"]["map_format"] == "exr"
    assert meta["undistort"]["plate"].endswith("frame_%06d.jpg")
    assert meta["pixel_aspect"] == 1.0
    assert meta["frame_step"] == 1 and meta["timeline_start"] == 1001
    assert meta["scene_transform"] is None


def test_not_asking_for_a_plate_leaves_the_export_as_it_was(tmp_path, monkeypatch):
    scene, res = export_scene(tmp_path, monkeypatch)
    assert not (scene / "undistort.exr").exists()
    assert res["undistorted_plate"] is None and res["stmaps"] == []
    nk = (scene / "camera_track_nuke.nk").read_text(encoding="utf-8")
    assert "STMap" not in nk
    meta = json.loads((scene / "camera_track.json").read_text(encoding="utf-8"))
    # The lens is still described - that costs nothing and answers the first
    # question comp asks - but nothing was delivered against it.
    assert meta["undistort"] is None
    assert meta["overscan"] == 0.0 and meta["pinhole"]["width"] == 1920
