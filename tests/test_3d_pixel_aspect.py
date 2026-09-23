"""
Pixel aspect (1.6): reading it, and de-squeezing exactly once because of it.

A squeezed plate is stretched back to square before COLMAP sees it - the camera
models COLMAP ships have no pixel aspect at all - and everything downstream is
square from then on: the frames the exports point at, the Nuke Read, the camera
aperture, Blender's render. The source's own aspect survives in
camera_track.json, where it is information rather than a second squeeze.
No ffprobe and no ffmpeg are run here.
"""
import json
from pathlib import Path

import pytest

import export_tools as et
from core import media_info
from core import workers

DATA = Path(__file__).parent / "data"

CAM = {1: {"id": 1, "model": "PINHOLE", "width": 1920, "height": 1080,
           "focal_x": 1000.0, "focal_y": 1000.0, "cx": 960.0, "cy": 540.0,
           "k1": 0.0, "k2": 0.0, "fisheye": False, "params": [1000.0, 1000.0, 960.0, 540.0]}}


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


def source_plate(tmp_path, first=1001, count=4, ext=".png", stem="shot_a"):
    """The artist's own sequence, numbered the way the timeline is."""
    folder = tmp_path / "plate"
    folder.mkdir()
    for n in range(first, first + count):
        (folder / f"{stem}_{n:04d}{ext}").write_bytes(b"")
    return folder


def scene_with_model(tmp_path, monkeypatch):
    """A scene folder with extracted frames and the fixture solve in sparse/."""
    scene = scene_with_frames(tmp_path)
    sparse = scene / "sparse"
    sparse.mkdir()
    for name in ("cameras.txt", "images.txt", "points3D.txt"):
        (sparse / name).write_bytes((DATA / name).read_bytes())
    monkeypatch.setattr(et, "find_blender_executable", lambda custom_path=None: None)
    return scene


def run_export(scene, **kwargs):
    """export_all_formats on a prepared scene; returns the log lines."""
    logs = []
    et.export_all_formats(scene, fps=24.0, start_frame=1001,
                          log_callback=lambda m, c: logs.append(m), **kwargs)
    return logs


def exported(tmp_path, monkeypatch, **kwargs):
    """A full export off the fixture model; returns (scene, logs)."""
    scene = scene_with_model(tmp_path, monkeypatch)
    return scene, run_export(scene, **kwargs)


# -----------------------------------------------------------------------------
# reading it off the clip
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("1:1", 1.0),
    ("40:33", 40.0 / 33.0),
    ("2:1", 2.0),
    ("64/45", 64.0 / 45.0),
    ("N/A", 1.0),          # ffprobe could not read the stream
    ("0:1", 1.0),          # the container simply does not say
    ("", 1.0),
    (None, 1.0),
    ("nonsense", 1.0),
    ("-4:3", 1.0),         # a damaged file, not a squeeze
])
def test_the_sar_parser_never_guesses(text, expected):
    assert media_info.parse_sample_aspect(text) == pytest.approx(expected)


def fake_ffprobe(monkeypatch, payload):
    class Done:
        returncode = 0
        stdout = payload

    monkeypatch.setattr(media_info, "_ffprobe_exe", lambda: Path("ffprobe.exe"))
    monkeypatch.setattr(media_info.subprocess, "run", lambda *a, **k: Done())


def test_probe_pixel_aspect_reads_the_stream(tmp_path, monkeypatch):
    clip = tmp_path / "shot.mp4"
    clip.write_bytes(b"")
    fake_ffprobe(monkeypatch, json.dumps({"streams": [{"sample_aspect_ratio": "2:1"}]}))
    assert media_info.probe_pixel_aspect(clip) == pytest.approx(2.0)


def test_a_missing_stream_or_file_is_square(tmp_path, monkeypatch):
    clip = tmp_path / "shot.mp4"
    clip.write_bytes(b"")
    fake_ffprobe(monkeypatch, json.dumps({"streams": []}))
    assert media_info.probe_pixel_aspect(clip) == 1.0
    # A sequence folder and a file that is not there carry no aspect at all.
    assert media_info.probe_pixel_aspect(tmp_path) == 1.0
    assert media_info.probe_pixel_aspect(tmp_path / "gone.mp4") == 1.0


# -----------------------------------------------------------------------------
# de-squeezing before the solve
# -----------------------------------------------------------------------------
def test_a_two_to_one_plate_is_scaled_at_extraction(tmp_path):
    cmd = workers.ffmpeg_extract_command("ffmpeg.exe", tmp_path / "shot.mp4",
                                         tmp_path / "frame_%06d.jpg", pixel_aspect=2.0)
    assert "-vf" in cmd
    assert cmd[cmd.index("-vf") + 1] == "scale=iw*2:ih,setsar=1"
    # The wide axis grows, so nothing is thrown away on the way in.
    assert workers.desqueeze_filter(0.5) == "scale=iw:ih/0.5,setsar=1"
    assert workers.desqueeze_filter(1.0) is None
    assert workers.desqueeze_filter(0.0) is None


def test_square_pixels_leave_the_command_exactly_as_it_was(tmp_path):
    cmd = workers.ffmpeg_extract_command("ffmpeg.exe", tmp_path / "shot.mp4",
                                         tmp_path / "frame_%06d.jpg")
    assert "-vf" not in cmd and "-vsync" not in cmd
    assert cmd[:4] == ["ffmpeg.exe", "-loglevel", "error", "-stats"]
    assert cmd[-3:] == ["-qscale:v", "2", str(tmp_path / "frame_%06d.jpg")]


def test_a_frame_step_and_a_squeeze_share_one_filter_chain(tmp_path):
    cmd = workers.ffmpeg_extract_command("ffmpeg.exe", tmp_path / "shot.mp4",
                                         tmp_path / "frame_%06d.jpg",
                                         frame_step=3, pixel_aspect=2.0)
    assert cmd[cmd.index("-vf") + 1] == "select=not(mod(n\\,3)),scale=iw*2:ih,setsar=1"
    assert "-vsync" in cmd          # select= drops frames, so the timing is irregular
    assert workers.extraction_video_filter(3, 1.0) == "select=not(mod(n\\,3))"
    assert workers.extraction_video_filter(1, 1.0) is None


def test_a_squeezed_sequence_goes_through_ffmpeg_too(tmp_path):
    # FFmpeg reads a numbered sequence as happily as a clip, so there is no
    # reason for a sequence to reach the solver still squeezed.
    src = source_plate(tmp_path, first=1001, count=4)
    img_dir = tmp_path / "images"
    img_dir.mkdir()
    plan = workers.sequence_import_plan(src, img_dir, "ffmpeg.exe", pixel_aspect=2.0)
    assert plan["mode"] == "ffmpeg"
    cmd = plan["command"]
    assert cmd[cmd.index("-start_number") + 1] == "1001"
    assert cmd[cmd.index("-i") + 1].endswith("shot_a_%04d.png")
    assert cmd[cmd.index("-vf") + 1] == "scale=iw*2:ih,setsar=1"
    # PNG in, PNG out: the artist's own format survives the de-squeeze, and
    # -qscale:v is a JPEG knob that has no business on it.
    assert cmd[-1] == str(img_dir / "frame_%06d.png") and "-qscale:v" not in cmd


def test_a_square_sequence_is_still_copied_frame_for_frame(tmp_path):
    src = source_plate(tmp_path, first=1001, count=4)
    plan = workers.sequence_import_plan(src, tmp_path / "images", "ffmpeg.exe")
    assert plan["mode"] == "copy" and plan["command"] is None
    assert [f.name for f in plan["files"]] == [f"shot_a_{n}.png" for n in range(1001, 1005)]


def test_a_stepped_squeezed_sequence_steps_inside_ffmpeg(tmp_path):
    src = source_plate(tmp_path, first=1001, count=9, ext=".exr")
    plan = workers.sequence_import_plan(src, tmp_path / "images", "ffmpeg.exe",
                                        frame_step=3, pixel_aspect=2.0)
    cmd = plan["command"]
    assert cmd[cmd.index("-vf") + 1] == "select=not(mod(n\\,3)),scale=iw*2:ih,setsar=1"
    assert "-vsync" in cmd
    # EXR is not a format FFmpeg should be asked to write back, so the frames
    # COLMAP and the exports use are JPEGs, as they are for a movie.
    assert cmd[-1].endswith("frame_%06d.jpg") and cmd[cmd.index("-qscale:v") + 1] == "2"
    # The copy path would have taken the same frames.
    assert [f.name for f in plan["files"]] == ["shot_a_1001.exr", "shot_a_1004.exr",
                                               "shot_a_1007.exr"]


def test_a_squeezed_folder_that_is_not_a_sequence_is_not_solved_squeezed(tmp_path):
    folder = tmp_path / "loose"
    folder.mkdir()
    for name in ("plateA.png", "plateB.png"):
        (folder / name).write_bytes(b"")
    # FFmpeg cannot read these as a sequence and copying them would hand the
    # solve the squeeze, so the caller is told to stop rather than get a lens
    # that is wrong in one axis.
    assert workers.sequence_import_plan(folder, tmp_path / "images", "ffmpeg.exe",
                                        pixel_aspect=2.0)["mode"] == "unnumbered"
    # Square pixels have nothing to de-squeeze, so the same folder just copies.
    assert workers.sequence_import_plan(folder, tmp_path / "images", "ffmpeg.exe")["mode"] == "copy"


def test_the_log_can_name_both_shapes(tmp_path):
    # 1920x540 on disk, de-squeezed from a 960x540 2:1 squeeze.
    assert workers.squeezed_source_size(1920, 540, 2.0) == (960, 540)
    assert workers.squeezed_source_size(1920, 2160, 0.5) == (1920, 1080)
    assert workers.squeezed_source_size(1920, 1080, 1.0) == (1920, 1080)


def test_frames_extracted_at_another_aspect_are_not_reused(tmp_path):
    img_dir = tmp_path / "shot" / "images"
    img_dir.mkdir(parents=True)
    src = tmp_path / "shot.mp4"
    src.write_bytes(b"x")
    workers.write_images_stamp(img_dir, src, 1, 3, 2.0)
    assert workers.images_stamp_matches(img_dir, src, 1, 2.0)
    assert not workers.images_stamp_matches(img_dir, src, 1, 1.0)
    # A stamp from before 1.6 means square pixels, not "unknown".
    workers.write_images_stamp(img_dir, src, 1, 3)
    assert workers.images_stamp_matches(img_dir, src, 1)


# -----------------------------------------------------------------------------
# the exports describe the plate they point at
# -----------------------------------------------------------------------------
def test_the_nuke_read_and_camera_are_square_whatever_was_shot(tmp_path):
    scene = scene_with_frames(tmp_path)
    out = scene / "camera_track_nuke.nk"
    et.export_nuke_camera_script(scene, CAM, two_frames_on_1001(), et.PointCloud(), out, fps=24.0)
    nk = out.read_text(encoding="utf-8")
    # The frames this Read points at were de-squeezed before the solve, so a
    # pixel_aspect knob here would squeeze them a second time, and a widened
    # aperture would no longer be the lens that was solved.
    assert "pixel_aspect" not in nk
    assert ' format "1920 1080 0 0 1920 1080 1.0 1920x1080"\n' in nk
    assert " haperture 36.0000\n" in nk and " focal 18.7500\n" in nk


def test_the_read_points_at_the_desqueezed_frames_when_the_plate_was_squeezed(tmp_path, monkeypatch):
    plate = et.source_sequence_plate(source_plate(tmp_path))
    scene, logs = exported(tmp_path, monkeypatch, source_sequence=plate, pixel_aspect=2.0)
    nk = (scene / "camera_track_nuke.nk").read_text(encoding="utf-8")
    # The artist's own sequence is still squeezed; only our frames match the camera.
    assert plate["pattern"] not in nk
    assert "/images/frame_%06d.png" in nk.replace("\\", "/")
    assert "pixel_aspect" not in nk
    assert any("de-squeezed 1920x1080 frames in images/" in m for m in logs)
    assert any("needs its own pixel aspect if you bring it in yourself" in m for m in logs)


def test_square_pixels_still_point_at_the_artists_own_sequence(tmp_path, monkeypatch):
    plate = et.source_sequence_plate(source_plate(tmp_path))
    scene, _logs = exported(tmp_path, monkeypatch, source_sequence=plate, pixel_aspect=1.0)
    nk = (scene / "camera_track_nuke.nk").read_text(encoding="utf-8")
    assert f' file "{plate["pattern"]}"\n' in nk
    assert "pixel_aspect" not in nk


def test_a_square_shot_exports_exactly_as_it_did_before(tmp_path, monkeypatch):
    # Nothing about the square-pixel path may move: stating aspect 1.0 and
    # saying nothing at all have to produce the same files, byte for byte.
    plate = et.source_sequence_plate(source_plate(tmp_path))
    scene = scene_with_model(tmp_path, monkeypatch)
    names = ("camera_track_nuke.nk", "import_to_blender.py", "camera_track.chan")
    run_export(scene, source_sequence=plate, pixel_aspect=1.0)
    stated = {name: (scene / name).read_bytes() for name in names}
    run_export(scene, source_sequence=plate)
    for name in names:
        assert stated[name] == (scene / name).read_bytes(), name


def test_blender_renders_the_desqueezed_plate_square(tmp_path):
    scene = scene_with_frames(tmp_path)
    out = scene / "import_to_blender.py"
    et.export_blender_script(scene, CAM, two_frames_on_1001(), et.PointCloud(), out,
                             fps=24.0, plate_desqueezed=True)
    src = out.read_text(encoding="utf-8")
    # Said out loud, because the .blend the artist runs this in may already
    # carry an aspect from an earlier setup.
    assert "scene.render.pixel_aspect_x = 1.0\n" in src
    assert "scene.render.pixel_aspect_y = 1.0\n" in src
    compile(src, "import_to_blender.py", "exec")

    et.export_blender_script(scene, CAM, two_frames_on_1001(), et.PointCloud(), out, fps=24.0)
    assert "pixel_aspect" not in out.read_text(encoding="utf-8")


def test_camera_track_json_keeps_the_source_aspect_and_says_what_was_delivered(tmp_path, monkeypatch):
    scene, logs = exported(tmp_path, monkeypatch, pixel_aspect=2.0)
    meta = json.loads((scene / "camera_track.json").read_text(encoding="utf-8"))
    # The squeeze the plate was shot with is real information about the source,
    # and the flag says the frames these exports point at no longer carry it.
    assert meta["pixel_aspect"] == pytest.approx(2.0)
    assert meta["plate_desqueezed"] is True
    assert any("Pixel aspect 2" in m for m in logs)

    square, _ = exported(tmp_path / "square", monkeypatch)
    meta = json.loads((square / "camera_track.json").read_text(encoding="utf-8"))
    assert meta["pixel_aspect"] == 1.0 and meta["plate_desqueezed"] is False
