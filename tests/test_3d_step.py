"""
Frame step: a stepped solve still belongs on the shot's own frames.

The pipeline extracts every Nth frame and renumbers them 1..M, so on-disk index
k is source frame 1 + (k - 1) * N and belongs on timeline frame
timeline_start + (k - 1) * N. A 60-frame plate numbered 1001-1060 solved at
step 3 therefore exports keys on 1001, 1004, ... 1058 at the plate's own frame
rate - not twenty consecutive frames at a third of the rate.

Also here: which plate the exports point at, since the extracted frames are an
internal cache and at step > 1 do not sit on timeline frames at all.
"""
import json
import re
from pathlib import Path

import pytest

import export_tools as et

DATA = Path(__file__).parent / "data"

CAM = {1: {"id": 1, "model": "PINHOLE", "width": 1920, "height": 1080,
           "focal_x": 1000.0, "focal_y": 1000.0, "cx": 960.0, "cy": 540.0,
           "k1": 0.0, "k2": 0.0, "fisheye": False, "params": [1000.0, 1000.0, 960.0, 540.0]}}


def write_images_txt(path, indices):
    """A COLMAP images.txt whose image k sits at camera centre (0, 0, -k)."""
    lines = ["# Image list\n"]
    for image_id, k in enumerate(indices, start=1):
        lines.append(f"{image_id} 1 0 0 0 0 0 {float(k)} 1 frame_{k:06d}.png\n")
        lines.append("1.0 2.0 1\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def scene_with_frames(tmp_path, count, ext=".png", name="scene"):
    scene = (tmp_path / name).resolve()
    (scene / "images").mkdir(parents=True)
    for k in range(1, count + 1):
        (scene / "images" / f"frame_{k:06d}{ext}").write_bytes(b"")
    return scene


def solved(tmp_path, count=20, start=1001, step=3):
    images = et.parse_colmap_images(write_images_txt(tmp_path / "images.txt", range(1, count + 1)))
    et.apply_timeline_start(images, start, step)
    return images


def source_plate(tmp_path, first=1001, count=60, ext=".exr", stem="shot_a"):
    folder = tmp_path / "plate"
    folder.mkdir()
    for n in range(first, first + count):
        (folder / f"{stem}_{n:04d}{ext}").write_bytes(b"")
    return folder


def camera_key_frames(nk):
    """The frame numbers of the Camera3 translate curve."""
    curve = nk.split("translate {{curve ")[1].split("}}")[0]
    return [int(tok[1:]) for tok in curve.split() if tok.startswith("x")]


# -----------------------------------------------------------------------------
# the index -> frame rule
# -----------------------------------------------------------------------------
def test_timeline_start_round_trips_with_a_step(tmp_path):
    images = solved(tmp_path, count=20, start=1001, step=3)
    frames = sorted(img["frame"] for img in images.values())
    assert frames == list(range(1001, 1059, 3))
    assert frames[0] == 1001 and frames[-1] == 1058 and len(frames) == 20
    assert et.timeline_start_of(images, 3) == 1001
    # step 1 is the old behaviour, untouched
    et.apply_timeline_start(images, 1001, 1)
    assert et.timeline_start_of(images) == 1001
    assert sorted(img["frame"] for img in images.values()) == list(range(1001, 1021))


def test_apply_timeline_start_ignores_a_missing_or_silly_step(tmp_path):
    images = solved(tmp_path, count=5, start=1001, step=1)
    plain = sorted(img["frame"] for img in images.values())
    for step in (None, 0, 1):
        et.apply_timeline_start(images, 1001, step)
        assert sorted(img["frame"] for img in images.values()) == plain


# -----------------------------------------------------------------------------
# what the exports say
# -----------------------------------------------------------------------------
def test_chan_keys_land_every_third_frame(tmp_path):
    scene = scene_with_frames(tmp_path, 20)
    out = scene / "camera_track.chan"
    assert et.export_nuke_chan(scene, CAM, solved(tmp_path), out, frame_step=3)
    lines = out.read_text(encoding="utf-8").rstrip("\n").split("\n")
    comments = [l for l in lines if l.startswith("#")]
    assert len(comments) == 3
    assert "frame step 3" in comments[2] and "1001-1058" in comments[2]
    frames = [int(l.split("\t")[0]) for l in lines if not l.startswith("#")]
    assert frames == list(range(1001, 1059, 3))
    # the pose still belongs to its own frame: image k sits at z = -k in COLMAP,
    # which is +k in Nuke's Y-up world
    row = dict(l.split("\t", 1) for l in lines if not l.startswith("#"))
    assert row["1058"].startswith("0.000000\t0.000000\t20.000000")


def test_nuke_camera_keys_match_the_chan(tmp_path):
    scene = scene_with_frames(tmp_path, 20)
    out = scene / "camera_track_nuke.nk"
    assert et.export_nuke_camera_script(scene, CAM, solved(tmp_path), et.PointCloud(), out,
                                        fps=24.0, frame_step=3)
    nk = out.read_text(encoding="utf-8")
    assert camera_key_frames(nk) == list(range(1001, 1059, 3))
    # the plate rate is never divided by the step, and the label says the keys are sparse
    assert " frame_rate 24.0000\n" in nk
    assert "Frames: 1001-1058 (key every 3) @ 24.000 fps | Plate: 1001-1058" in nk


def test_blender_scene_range_covers_the_real_shot(tmp_path):
    scene = scene_with_frames(tmp_path, 20)
    out = scene / "import_to_blender.py"
    assert et.export_blender_script(scene, CAM, solved(tmp_path), et.PointCloud(), out,
                                    fps=24.0, frame_step=3)
    src = out.read_text(encoding="utf-8")
    assert "scene.frame_start = 1001" in src and "scene.frame_end = 1058" in src
    assert "scene.render.fps = 24" in src
    assert '"frame": 1001' in src and '"frame": 1058' in src and '"frame": 1002' not in src
    # Blender's image user cannot step, so the header says so rather than
    # quietly showing a background that drifts
    assert "Frame Step 3" in src and "do NOT line up with the timeline" in src
    compile(src, "import_to_blender.py", "exec")


def test_step_one_is_byte_identical_to_not_saying_step(tmp_path):
    scene = scene_with_frames(tmp_path, 3)
    images = et.parse_colmap_images(DATA / "images.txt")
    et.apply_timeline_start(images, 1001)

    # the same output path twice, so nothing but the step can differ
    for writer, name in ((et.export_nuke_chan, "camera_track.chan"),
                         (et.export_nuke_camera_script, "camera_track_nuke.nk"),
                         (et.export_blender_script, "import_to_blender.py")):
        out = scene / name
        if writer is et.export_nuke_chan:
            writer(scene, CAM, images, out)
            silent = out.read_bytes()
            writer(scene, CAM, images, out, frame_step=1)
        else:
            writer(scene, CAM, images, et.PointCloud(), out, fps=24.0)
            silent = out.read_bytes()
            writer(scene, CAM, images, et.PointCloud(), out, fps=24.0, frame_step=1)
        assert silent == out.read_bytes(), name


# -----------------------------------------------------------------------------
# the plate the exports point at
# -----------------------------------------------------------------------------
def test_source_sequence_plate_reads_the_real_numbering(tmp_path):
    plate = et.source_sequence_plate(source_plate(tmp_path, first=1001, count=60))
    assert plate["pattern"].endswith("shot_a_%04d.exr")
    assert (plate["first"], plate["last"], plate["count"], plate["ext"]) == (1001, 1060, 60, ".exr")

    # the frame number is the LAST digit run, as everywhere else in the pipeline
    other = tmp_path / "v2"
    other.mkdir()
    for n in (7, 8):
        (other / f"shot2_v3_{n:06d}.png").write_bytes(b"")
    assert et.source_sequence_plate(other)["pattern"].endswith("shot2_v3_%06d.png")

    # not a numbered sequence, not a folder, nothing to point at
    unnumbered = tmp_path / "flat"
    unnumbered.mkdir()
    (unnumbered / "plate.exr").write_bytes(b"")
    assert et.source_sequence_plate(unnumbered) is None
    assert et.source_sequence_plate(tmp_path / "nowhere") is None
    assert et.source_sequence_plate(tmp_path / "v2" / "shot2_v3_000007.png") is None


def test_nuke_read_points_at_the_source_sequence_when_there_is_one(tmp_path):
    scene = scene_with_frames(tmp_path, 20)
    plate = et.source_sequence_plate(source_plate(tmp_path, first=1001, count=60))
    out = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, CAM, solved(tmp_path), et.PointCloud(), out,
                                 fps=24.0, frame_step=3, source_sequence=plate)
    nk = out.read_text(encoding="utf-8")
    assert f' file "{plate["pattern"]}"' in nk
    assert "frame_%06d" not in nk and "images/" not in nk
    # the artist's own numbering already matches the timeline: no offset, no TimeWarp
    assert "\n first 1001\n last 1060\n origfirst 1001\n origlast 1060\n frame_rate" in nk
    assert "frame_mode" not in nk and "TimeWarp" not in nk
    # and the plate is the whole 60 frames, not the 20 that were solved
    assert "Plate: 1001-1060" in nk


def test_nuke_read_offsets_a_source_sequence_that_is_numbered_differently(tmp_path):
    scene = scene_with_frames(tmp_path, 5)
    plate = et.source_sequence_plate(source_plate(tmp_path, first=1, count=5, stem="plate"))
    images = solved(tmp_path, count=5, start=1001, step=1)
    out = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, CAM, images, et.PointCloud(), out,
                                 fps=24.0, source_sequence=plate)
    nk = out.read_text(encoding="utf-8")
    assert "\n first 1\n last 5\n origfirst 1\n origlast 5\n frame_mode offset\n frame 1000\n" in nk


def test_nuke_read_falls_back_to_the_extracted_frames(tmp_path):
    scene = scene_with_frames(tmp_path, 20, ext=".jpg")
    out = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, CAM, solved(tmp_path), et.PointCloud(), out,
                                 fps=24.0, frame_step=3)
    nk = out.read_text(encoding="utf-8")
    assert f' file "{str(scene / "images").replace(chr(92), "/")}/frame_%06d.jpg"' in nk
    assert "\n first 1\n last 20\n origfirst 1\n origlast 20\n" in nk


def test_blender_background_uses_the_source_sequence_and_its_numbering(tmp_path):
    scene = scene_with_frames(tmp_path, 20)
    folder = source_plate(tmp_path, first=1001, count=60)
    plate = et.source_sequence_plate(folder)
    out = scene / "import_to_blender.py"
    et.export_blender_script(scene, CAM, solved(tmp_path), et.PointCloud(), out,
                             fps=24.0, frame_step=3, source_sequence=plate)
    src = out.read_text(encoding="utf-8")
    assert str(folder).replace("\\", "/") in src
    assert "endswith(('.exr',))" in src
    # file number = scene frame - frame_start + 1 + frame_offset, so 1001 -> 1001
    assert "frame_start = 1001" in src and "frame_offset = 1000" in src
    assert "scene.frame_end = 1060" in src
    assert "your source sequence, frames 1001-1060" in src
    assert "do NOT line up" not in src
    compile(src, "import_to_blender.py", "exec")


# -----------------------------------------------------------------------------
# the TimeWarp, which exists only for stepped video
# -----------------------------------------------------------------------------
def nuke_lookup(nk):
    """The TimeWarp's lookup expression as a callable, clamp and all."""
    expr = re.search(r"lookup \{\{(.+?)\}\}", nk).group(1)
    return lambda f: eval(expr.replace("frame", repr(float(f))),
                          {"clamp": lambda v, lo, hi: max(lo, min(hi, v))})


def test_timewarp_maps_the_timeline_onto_the_stepped_frames(tmp_path):
    scene = scene_with_frames(tmp_path, 20)
    out = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, CAM, solved(tmp_path), et.PointCloud(), out,
                                 fps=24.0, frame_step=3)
    nk = out.read_text(encoding="utf-8")
    assert "TimeWarp {\n inputs 1\n" in nk and "name Plate_Timewarp" in nk
    # the Read keeps its own 1..M numbering; the TimeWarp does the mapping
    assert "frame_mode" not in nk
    assert " bg Plate_Timewarp\n" in nk

    lookup = nuke_lookup(nk)
    assert lookup(1001) == 1
    assert lookup(1004) == 2
    assert lookup(1058) == 20
    # held at both ends rather than reading frames that do not exist
    assert lookup(900) == 1 and lookup(5000) == 20


def test_no_timewarp_at_step_one_or_with_a_source_sequence(tmp_path):
    scene = scene_with_frames(tmp_path, 20)
    plate = et.source_sequence_plate(source_plate(tmp_path, first=1001, count=60))
    out = scene / "camera_track_nuke.nk"

    et.export_nuke_camera_script(scene, CAM, solved(tmp_path, step=1), et.PointCloud(), out, fps=24.0)
    assert "TimeWarp" not in out.read_text(encoding="utf-8")

    et.export_nuke_camera_script(scene, CAM, solved(tmp_path), et.PointCloud(), out,
                                 fps=24.0, frame_step=3, source_sequence=plate)
    assert "TimeWarp" not in out.read_text(encoding="utf-8")


# -----------------------------------------------------------------------------
# end to end
# -----------------------------------------------------------------------------
def test_export_all_formats_carries_the_step_through(tmp_path, monkeypatch):
    scene = scene_with_frames(tmp_path, 3)
    sparse = scene / "sparse"
    sparse.mkdir()
    for name in ("cameras.txt", "images.txt", "points3D.txt"):
        (sparse / name).write_bytes((DATA / name).read_bytes())
    monkeypatch.setattr(et, "find_blender_executable", lambda custom_path=None: None)
    logs = []
    res = et.export_all_formats(scene, fps=24.0, start_frame=1001, frame_step=3,
                                log_callback=lambda m, c: logs.append(m))
    assert res["success"], res

    # the fixture holds on-disk frames 2 and 3, which at step 3 are 1004 and 1007
    chan = (scene / "camera_track.chan").read_text(encoding="utf-8")
    assert "\n1004\t" in chan and "\n1007\t" in chan
    assert "\n1002\t" not in chan
    assert camera_key_frames((scene / "camera_track_nuke.nk").read_text(encoding="utf-8")) == [1004, 1007]

    meta = json.loads((scene / "camera_track.json").read_text(encoding="utf-8"))
    assert meta["frame_step"] == 3 and meta["timeline_start"] == 1001
    assert meta["fps"] == pytest.approx(24.0)
    assert any("every 3 frames, 1004..1007" in m for m in logs)


def test_export_all_formats_defaults_to_step_one(tmp_path, monkeypatch):
    scene = scene_with_frames(tmp_path, 3)
    sparse = scene / "sparse"
    sparse.mkdir()
    for name in ("cameras.txt", "images.txt", "points3D.txt"):
        (sparse / name).write_bytes((DATA / name).read_bytes())
    monkeypatch.setattr(et, "find_blender_executable", lambda custom_path=None: None)
    res = et.export_all_formats(scene, fps=24.0, start_frame=1001)
    assert res["success"]
    meta = json.loads((scene / "camera_track.json").read_text(encoding="utf-8"))
    assert meta["frame_step"] == 1
    assert "\n1002\t" in (scene / "camera_track.chan").read_text(encoding="utf-8")
