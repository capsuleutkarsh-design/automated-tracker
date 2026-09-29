"""
Reading EXR and DPX frames, the 2D _latest folder, the GPU the bundled torch
cannot run on, finding Blender, the scrub cache check, and roto masks on an
anamorphic plate.
"""
import os
from pathlib import Path

import numpy as np
import pytest

from core import image_io, lens
from core.app_paths import ffmpeg_exe

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# =============================================================================
# EXR AND DPX FRAMES
# =============================================================================
def test_linear_to_srgb8_is_the_srgb_curve_clipped():
    px = np.array([[[0.0, 0.18, 1.0], [4.0, -1.0, np.nan]]], dtype=np.float32)
    out = image_io.linear_to_srgb8(px)
    assert out.dtype == np.uint8
    # 0.18 linear is mid grey: 1.055 * 0.18 ** (1 / 2.4) - 0.055 = 0.4614 -> 118
    assert out[0, 0].tolist() == [0, 118, 255]
    assert out[0, 1].tolist() == [255, 0, 0]


@pytest.mark.skipif(not lens.has_exr_support(), reason="no EXR writer in this interpreter")
def test_an_exr_frame_reads_as_srgb_rgb8(tmp_path):
    img = np.zeros((6, 8, 3), dtype=np.float32)
    img[:, :, 0] = 1.0
    img[:, :, 1] = 0.18
    path, fmt = lens.write_exr(tmp_path / "f.0001.exr", img)
    if fmt != "exr":
        pytest.skip("EXR writer fell back to another format")
    rgb = image_io.read_rgb8(path)
    assert rgb.shape == (6, 8, 3) and rgb.dtype == np.uint8
    assert rgb[0, 0].tolist() == [255, 118, 0]
    assert image_io.image_size(path) == (8, 6)
    assert image_io.read_pil_rgb(path).size == (8, 6)


@pytest.mark.skipif(not lens.has_exr_support(), reason="no EXR writer in this interpreter")
def test_the_2d_loader_reads_an_exr_sequence(tmp_path):
    import cotracker_2d as c2d
    img = np.full((16, 24, 3), 0.5, dtype=np.float32)
    files = []
    for k in range(3):
        path, fmt = lens.write_exr(tmp_path / ("plate.%04d.exr" % (1001 + k)), img)
        if fmt != "exr":
            pytest.skip("EXR writer fell back to another format")
        files.append(Path(path))
    frames, size = c2d._load_resized(files, 0)
    assert frames.shape == (3, 16, 24, 3) and size == (24, 16)
    assert ".exr" in c2d.IMAGE_EXTS and ".dpx" in c2d.IMAGE_EXTS


@pytest.mark.skipif(not ffmpeg_exe().exists(), reason="no bundled FFmpeg (appears after SETUP)")
def test_a_dpx_frame_reads_through_ffmpeg(tmp_path):
    from core.proc import run_hidden
    dpx = tmp_path / "f.1001.dpx"
    res = run_hidden([str(ffmpeg_exe()), "-v", "error", "-y", "-f", "lavfi", "-i",
                      "color=c=red:s=32x16", "-frames:v", "1", "-pix_fmt", "gbrp10le", str(dpx)],
                     capture=True)
    if res.returncode != 0 or not dpx.exists():
        pytest.skip("this FFmpeg cannot write DPX")
    rgb = image_io.read_rgb8(dpx)
    assert rgb.shape == (16, 32, 3)
    assert rgb[8, 16, 0] > 200 and rgb[8, 16, 1] < 40
    assert image_io.image_size(dpx) == (32, 16)


def test_a_dpx_without_ffmpeg_says_what_to_do(tmp_path, monkeypatch):
    import core.app_paths as app_paths
    monkeypatch.setattr(app_paths, "ffmpeg_exe", lambda: tmp_path / "missing" / "ffmpeg.exe")
    dpx = tmp_path / "f.1001.dpx"
    dpx.write_bytes(b"SDPX")
    with pytest.raises(image_io.UnreadableImage, match="SETUP.bat"):
        image_io.read_rgb8(dpx)
    assert image_io.image_size(dpx) is None


def test_an_unreadable_frame_stops_the_2d_loader_with_a_sentence(tmp_path):
    import cotracker_2d as c2d
    bad = tmp_path / "f.1001.exr"
    bad.write_bytes(b"not an exr")
    with pytest.raises(ValueError, match="f.1001.exr"):
        c2d._load_resized([bad], 0)


# =============================================================================
# 2D _latest IS CLEARED LIKE THE 3D ONE
# =============================================================================
def test_sync_latest_folder_drops_what_the_new_run_did_not_write(tmp_path):
    import cotracker_2d as c2d
    latest = tmp_path / "_latest"
    (latest / "Old_Layer").mkdir(parents=True)
    (latest / "tracks_2d_cornerpin_nuke.nk").write_text("old")
    (latest / "tracks_2d_overlay.mp4").write_text("old overlay")
    (latest / "Old_Layer" / "tracks_2d.json").write_text("old")
    run = tmp_path / "2026-09-29_10-00-00"
    (run / "Wall").mkdir(parents=True)
    (run / "tracks_2d.json").write_text("new")
    (run / "Wall" / "tracks_2d.json").write_text("new")

    assert c2d.sync_latest_folder(run, latest, keep=("tracks_2d_overlay.mp4",))
    names = sorted(p.relative_to(latest).as_posix() for p in latest.rglob("*"))
    assert names == ["Wall", "Wall/tracks_2d.json", "tracks_2d.json", "tracks_2d_overlay.mp4"]

    # A real track that writes its own overlay replaces the kept one; without
    # `keep` nothing old survives.
    (run / "tracks_2d_overlay.mp4").write_text("new overlay")
    c2d.sync_latest_folder(run, latest)
    assert (latest / "tracks_2d_overlay.mp4").read_text() == "new overlay"
    (run / "tracks_2d_overlay.mp4").unlink()
    c2d.sync_latest_folder(run, latest)
    assert not (latest / "tracks_2d_overlay.mp4").exists()


# =============================================================================
# A GPU THE BUNDLED TORCH HAS NO KERNELS FOR
# =============================================================================
CU124_ARCHES = ["sm_50", "sm_60", "sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90"]


def test_arch_list_matching_follows_cuda_binary_compatibility():
    import cotracker_2d as c2d
    assert c2d._arch_runs_on("sm_86", 8, 9)          # RTX 40 runs sm_86 kernels
    assert c2d._arch_runs_on("sm_75", 7, 5)
    assert not c2d._arch_runs_on("sm_90", 12, 0)     # RTX 50 does not run sm_90
    assert not c2d._arch_runs_on("sm_86", 8, 0)      # newer minor never runs on older
    assert c2d._arch_runs_on("compute_90", 12, 0)    # PTX JITs forward
    assert not c2d._arch_runs_on("garbage", 8, 6)


def _fake_gpu(monkeypatch, c2d, capability, name):
    monkeypatch.setattr(c2d.torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(c2d.torch.cuda, "get_device_capability", lambda i=0: capability)
    monkeypatch.setattr(c2d.torch.cuda, "get_arch_list", lambda: list(CU124_ARCHES))
    monkeypatch.setattr(c2d.torch.cuda, "get_device_name", lambda i=0: name)
    monkeypatch.setattr(c2d, "_CUDA_PROBLEM", [])


def test_an_rtx_50_falls_back_to_the_cpu_with_a_plain_message(monkeypatch):
    import cotracker_2d as c2d
    _fake_gpu(monkeypatch, c2d, (12, 0), "NVIDIA GeForce RTX 5090")
    assert c2d.get_default_device() == "cpu"
    msg = c2d.cuda_unsupported_reason()
    assert "RTX 5090 (RTX 50-series) isn't supported by this version yet" in msg
    assert "run on the CPU, which is slower" in msg
    assert c2d.get_gpu_memory_info()[2] == "CPU Only"


def test_an_rtx_40_still_runs_on_the_gpu(monkeypatch):
    import cotracker_2d as c2d
    _fake_gpu(monkeypatch, c2d, (8, 9), "NVIDIA GeForce RTX 4080 SUPER")
    assert c2d.cuda_unsupported_reason() is None
    assert c2d.get_default_device() == "cuda"


# =============================================================================
# FINDING BLENDER
# =============================================================================
def test_the_newest_blender_wins_by_version_number(tmp_path):
    import export_tools as et
    root = tmp_path / "Program Files"
    for v in ("3.6", "4.9", "4.10", "4.2"):
        exe = root / "Blender Foundation" / ("Blender " + v) / "blender.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
    assert et.newest_installed_blender([root]).parent.name == "Blender 4.10"
    exe = root / "Blender Foundation" / "Blender 5.0" / "blender.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    assert et.newest_installed_blender([root]).parent.name == "Blender 5.0"
    assert et.newest_installed_blender([tmp_path / "nothing"]) is None


# =============================================================================
# WORKER HELPERS (core.workers needs PySide6 for its QThreads)
# =============================================================================
def _workers():
    pytest.importorskip("PySide6")
    from core import workers
    return workers


def test_only_the_stamped_every_frame_cache_is_scrubbed(tmp_path):
    workers = _workers()
    clip = tmp_path / "shot.mp4"
    clip.write_bytes(b"movie")
    img_dir = tmp_path / "shot" / "images"
    img_dir.mkdir(parents=True)
    for k in range(1, 4):
        (img_dir / ("frame_%06d.jpg" % k)).write_bytes(b"")
    assert workers.scrub_cache_frames(img_dir, clip) == []          # no stamp: unknown origin
    workers.write_images_stamp(img_dir, clip, 2, 3)
    assert workers.scrub_cache_frames(img_dir, clip) == []          # a 3D run at step 2
    workers.write_images_stamp(img_dir, clip, 1, 3, pixel_aspect=2.0)
    assert workers.scrub_cache_frames(img_dir, clip) == []          # de-squeezed for 3D
    workers.write_images_stamp(img_dir, clip, 1, 3)
    assert [f.name for f in workers.scrub_cache_frames(img_dir, clip)] == [
        "frame_000001.jpg", "frame_000002.jpg", "frame_000003.jpg"]


def test_a_square_dpx_sequence_is_converted_for_colmap(tmp_path):
    workers = _workers()
    src = tmp_path / "plate"
    src.mkdir()
    for n in range(1001, 1004):
        (src / ("shot_a_%d.dpx" % n)).write_bytes(b"")
    plan = workers.sequence_import_plan(src, tmp_path / "images", "ffmpeg.exe")
    assert plan["mode"] == "ffmpeg"
    cmd = plan["command"]
    assert cmd[cmd.index("-i") + 1].endswith("shot_a_%04d.dpx")
    assert cmd[-1].endswith("frame_%06d.jpg")


def test_roto_on_an_anamorphic_plate_is_stretched_with_the_frames(tmp_path):
    from PIL import Image
    from mask_animator import AnimatedMask, rasterize_masks_to_png
    m = AnimatedMask("m_1", "M", "exclusion")
    # Drawn on the squeezed 50x40 plate: x 10..20.
    m.set_keyframe(0, [(10, 5), (20, 5), (20, 35), (10, 35)], "poly")
    out = tmp_path / "masks"
    # The solve sees the plate de-squeezed 2x to 100x40.
    rasterize_masks_to_png([m], 100, 40, out, 1, ["frame_000001.jpg"], scale_x=2.0, scale_y=1.0)
    mask = np.asarray(Image.open(out / "frame_000001.jpg.png"))
    assert mask[20, 30] == 0          # inside 20..40 once stretched
    assert mask[20, 15] == 255        # where the unstretched shape would have been
    assert mask[20, 45] == 255
