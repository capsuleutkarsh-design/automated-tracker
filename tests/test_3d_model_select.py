"""
find_best_model (which sparse/N to export), detect_sequence_start, and the
images/ cache stamp used to decide whether extracted frames can be reused.
"""
import struct

import pytest

from core.colmap_model import find_best_model, model_stats
from core.media_info import detect_sequence_start


def write_txt_model(d, n_images, n_points):
    d.mkdir(parents=True, exist_ok=True)
    (d / "cameras.txt").write_text("1 SIMPLE_RADIAL 100 100 90 50 50 0\n", encoding="utf-8")
    lines = ["# header"]
    for i in range(1, n_images + 1):
        lines.append(f"{i} 1 0 0 0 0 0 0 1 frame_{i:06d}.jpg")
        lines.append("" if i % 2 else "1.0 2.0 1")
    (d / "images.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (d / "points3D.txt").write_text("".join(f"{i} 0 0 0 0 0 0 0\n" for i in range(n_points)), encoding="utf-8")


def test_find_best_model_prefers_more_registered_images(tmp_path):
    sparse = tmp_path / "sparse"
    write_txt_model(sparse / "0", 2, 50)
    write_txt_model(sparse / "1", 500, 20)
    assert model_stats(sparse / "0") == (2, 50)
    assert model_stats(sparse / "1") == (500, 20)
    logs = []
    assert find_best_model(sparse, log=logs.append) == sparse / "1"
    assert logs and "2 models" in logs[0]


def test_find_best_model_reads_bin_counts_and_breaks_ties_on_points(tmp_path):
    sparse = tmp_path / "sparse"
    for name, imgs, pts in (("0", 10, 5), ("1", 10, 900)):
        d = sparse / name
        d.mkdir(parents=True)
        (d / "cameras.bin").write_bytes(struct.pack("<Q", 1))
        (d / "images.bin").write_bytes(struct.pack("<Q", imgs))
        (d / "points3D.bin").write_bytes(struct.pack("<Q", pts))
    assert find_best_model(sparse) == sparse / "1"


def test_find_best_model_none_when_empty(tmp_path):
    assert find_best_model(tmp_path / "missing") is None
    (tmp_path / "sparse").mkdir()
    assert find_best_model(tmp_path / "sparse") is None
    write_txt_model(tmp_path / "sparse" / "0", 0, 0)
    assert find_best_model(tmp_path / "sparse") is None


def test_detect_sequence_start(tmp_path):
    seq = tmp_path / "plate"
    seq.mkdir()
    for f in range(1001, 1004):
        (seq / f"shot_a_v2_{f}.exr").write_bytes(b"")
    assert detect_sequence_start(seq) == 1001

    single = tmp_path / "single"
    single.mkdir()
    (single / "untitled.png").write_bytes(b"")
    assert detect_sequence_start(single, default=1) == 1

    gap = tmp_path / "gap"
    gap.mkdir()
    (gap / "a_0001.png").write_bytes(b"")
    (gap / "a_0500.png").write_bytes(b"")
    assert detect_sequence_start(gap, default=7) == 7

    assert detect_sequence_start(tmp_path / "nowhere", default=3) == 3
    assert detect_sequence_start(tmp_path / "plate" / "shot_a_v2_1001.exr", default=1) == 1


def test_images_cache_stamp(tmp_path):
    pytest.importorskip("PySide6")
    from core import workers as w

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x" * 10)
    img_dir = tmp_path / "shot" / "images"
    img_dir.mkdir(parents=True)
    for k in range(1, 4):
        (img_dir / f"frame_{k:06d}.jpg").write_bytes(b"")

    assert not w.images_stamp_matches(img_dir, src, 1)          # legacy folder: no stamp
    w.write_images_stamp(img_dir, src, 2, 3)
    assert w.images_stamp_path(img_dir) == tmp_path / "shot" / "images_cache.json"
    assert w.images_stamp_matches(img_dir, src, 2)
    assert not w.images_stamp_matches(img_dir, src, 1)          # different step
    src.write_bytes(b"y" * 11)                                  # source replaced
    assert not w.images_stamp_matches(img_dir, src, 2)

    assert len(w.list_extracted_frames(img_dir)) == 3
    w.clear_extracted_frames(img_dir)
    assert w.list_extracted_frames(img_dir) == []
    assert not w.images_stamp_path(img_dir).exists()
