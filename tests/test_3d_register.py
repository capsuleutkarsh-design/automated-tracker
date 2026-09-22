"""
Registering the frames the mapper skipped (1.3).

The COLMAP calls themselves need the binary; everything decided around them is
pure and lives here: reading model_analyzer's error line, listing which frames a
model actually holds, and the rule that says whether the registered model is
worth keeping.
"""
import struct
from pathlib import Path

import pytest

from core.colmap_model import (
    frame_index_from_name,
    model_error,
    parse_model_error,
    registered_indices,
)

DATA = Path(__file__).parent / "data"


# What `colmap model_analyzer --path sparse/0` prints for a healthy solve.
ANALYZER_OUTPUT = """\
Loading model...

Cameras: 1
Images: 60
Registered images: 60
Points: 12043
Observations: 55892
Mean track length: 4.64023
Mean observations per image: 931.533
Mean reprojection error: 0.71787px
"""

# A build that printed nothing useful: the header is there, the number is not.
MALFORMED_OUTPUT = """\
Loading model...

Cameras: 1
Images: 2
Registered images: 2
Points: 0
Observations: 0
Mean reprojection error: nanpx
"""


def test_parse_model_error_reads_the_pixel_value():
    assert parse_model_error(ANALYZER_OUTPUT) == pytest.approx(0.71787)


def test_parse_model_error_tolerates_anything_else():
    assert parse_model_error(MALFORMED_OUTPUT) is None
    assert parse_model_error("") is None
    assert parse_model_error(None) is None
    assert parse_model_error("Mean reprojection error: px") is None
    assert parse_model_error("COLMAP is not installed") is None


def test_model_error_returns_none_without_a_binary(tmp_path):
    # no model at all, and a model with a path that cannot be executed: neither
    # may raise, both mean "we do not know"
    assert model_error(tmp_path / "nothing", "colmap") is None
    (tmp_path / "images.txt").write_text("1 1 0 0 0 0 0 0 1 frame_000001.jpg\n", encoding="utf-8")
    assert model_error(tmp_path, tmp_path / "no_such_colmap.exe") is None


def test_frame_index_from_name_uses_the_last_digit_run():
    assert frame_index_from_name("frame_000042.jpg") == 42
    assert frame_index_from_name("shot2_000042.png") == 42
    assert frame_index_from_name("plate.exr") is None


def test_registered_indices_from_the_txt_fixture():
    # the fixture deliberately skips frame_000001 and has non-sequential ids
    assert registered_indices(DATA) == [2, 3]


def test_registered_indices_from_bin(tmp_path):
    blob = struct.pack("<Q", 2)
    for image_id, name in ((7, b"frame_000005.jpg"), (3, b"frame_000002.jpg")):
        blob += struct.pack("<I", image_id)
        blob += struct.pack("<7d", 1, 0, 0, 0, 0, 0, 0)
        blob += struct.pack("<I", 1)
        blob += name + b"\x00"
        blob += struct.pack("<Q", 1)
        blob += struct.pack("<ddq", 1.0, 2.0, -1)
    (tmp_path / "images.bin").write_bytes(blob)
    assert registered_indices(tmp_path) == [2, 5]


def test_registered_indices_empty_when_unreadable(tmp_path):
    assert registered_indices(tmp_path / "missing") == []
    (tmp_path / "images.bin").write_bytes(b"\x00\x01")
    assert registered_indices(tmp_path) == []


# -----------------------------------------------------------------------------
# the adopt / reject decision and the frame numbers the artist is told about
# -----------------------------------------------------------------------------
@pytest.fixture
def w():
    pytest.importorskip("PySide6")
    from core import workers
    return workers


def test_adopt_more_images_and_similar_error(w):
    adopt, reason = w._should_adopt((54, 9000), 0.71, (60, 9400), 0.78)
    assert adopt
    assert "60 frames instead of 54" in reason


def test_reject_when_the_error_doubled(w):
    adopt, reason = w._should_adopt((54, 9000), 0.71, (60, 9400), 1.42)
    assert not adopt
    assert "25%" in reason


def test_reject_when_the_error_is_over_the_absolute_cap(w):
    # only 10% worse, but 4.4 px is not a track anyone can use
    adopt, reason = w._should_adopt((54, 9000), 4.0, (60, 9400), 4.4)
    assert not adopt
    assert "4.0 px" in reason


def test_reject_when_no_frames_were_gained(w):
    assert w._should_adopt((60, 9000), 0.7, (60, 9400), 0.7)[0] is False
    assert w._should_adopt((60, 9000), 0.7, (54, 9400), 0.2)[0] is False


def test_unknown_error_falls_back_to_the_frame_count(w):
    adopt, reason = w._should_adopt((54, 9000), None, (60, 9400), 0.78)
    assert adopt and "unknown" in reason
    assert w._should_adopt((54, 9000), 0.71, (60, 9400), None)[0] is True
    assert w._should_adopt((54, 9000), None, (60, 9400), None)[0] is True
    # frame count still rules: an unknown error never rescues a model that
    # registered no more frames
    assert w._should_adopt((54, 9000), None, (54, 9400), None)[0] is False


def test_missing_frames_are_named_in_timeline_numbers(w, tmp_path):
    # frames 1 and 4 of 5 on disk are missing; with Timeline start 1001 the
    # artist is told about 1001 and 1004, never about COLMAP's image ids
    (tmp_path / "images.txt").write_text(
        "# header\n"
        "9 1 0 0 0 0 0 0 1 frame_000002.jpg\n"
        "1.0 2.0 1\n"
        "4 1 0 0 0 0 0 0 1 frame_000003.jpg\n"
        "\n"
        "7 1 0 0 0 0 0 0 1 frame_000005.jpg\n"
        "1.0 2.0 1\n", encoding="utf-8")

    worker = w.TrackerWorker([], {"timeline_start": 1001}, ".", ".", "colmap", ".", "ffmpeg", ".")
    assert worker._missing_timeline_frames(tmp_path, 5) == [1001, 1004]
    assert w.frame_ranges(worker._missing_timeline_frames(tmp_path, 5)) == "1001, 1004"

    # an unreadable model means "we cannot name them", not "none registered"
    assert worker._missing_timeline_frames(tmp_path / "gone", 5) == []

    # at frame step 3 the same gaps are three frames apart on the timeline:
    # on-disk 1 and 4 are source frames 1 and 10, so 1001 and 1010
    stepped = w.TrackerWorker([], {"timeline_start": 1001, "frame_step": 3},
                              ".", ".", "colmap", ".", "ffmpeg", ".")
    assert stepped._missing_timeline_frames(tmp_path, 5) == [1001, 1010]
    assert w.frame_ranges(stepped._missing_timeline_frames(tmp_path, 5)) == "1001, 1010"


def test_frame_ranges_reads_like_a_shot(w):
    assert w.frame_ranges([1017, 1018, 1019]) == "1017-1019"
    assert w.frame_ranges([1017, 1018, 1019, 1025]) == "1017-1019, 1025"
    assert w.frame_ranges([5, 5, 4]) == "4-5"
    assert w.frame_ranges([]) == ""
    long_list = w.frame_ranges(range(1, 60, 2), max_ranges=3)
    assert long_list.startswith("1, 3, 5,") and long_list.endswith("and 27 more")
