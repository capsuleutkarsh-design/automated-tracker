"""find_latest_output, the media-pool scan and the thumbnail cache key."""
import os
import time
from pathlib import Path

import pytest

from core.media_pool import (
    find_latest_output, scan_media_pool, thumb_cache_key, thumb_path,
    prune_thumb_cache,
)


def _touch(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


# ---------------------------------------------------------------- lookup ----
def test_latest_wins_over_timestamp_dirs(tmp_path):
    shot = tmp_path / "shot"
    _touch(shot / "3D_CAMERA_TRACK" / "2026-01-01_00-00-00" / "camera_track_nuke.nk")
    latest = _touch(shot / "3D_CAMERA_TRACK" / "_latest" / "camera_track_nuke.nk")
    f, folder = find_latest_output(shot, "3D_CAMERA_TRACK", "camera_track_nuke.nk")
    assert f == latest
    assert folder == latest.parent


def test_newest_timestamp_dir_when_no_latest(tmp_path):
    shot = tmp_path / "shot"
    _touch(shot / "3D_CAMERA_TRACK" / "2026-01-01_00-00-00" / "camera_track_nuke.nk")
    newest = _touch(shot / "3D_CAMERA_TRACK" / "2026-03-05_12-00-00" / "camera_track_nuke.nk")
    # a newer folder that lacks the file must be skipped, not returned empty
    (shot / "3D_CAMERA_TRACK" / "2026-09-09_00-00-00").mkdir()
    f, folder = find_latest_output(shot, "3D_CAMERA_TRACK", "camera_track_nuke.nk")
    assert f == newest
    assert folder.name == "2026-03-05_12-00-00"


def test_legacy_layout_in_shot_dir(tmp_path):
    shot = tmp_path / "shot"
    legacy = _touch(shot / "sparse" / "cameras.txt")
    f, folder = find_latest_output(shot, "3D_CAMERA_TRACK", "sparse/cameras.txt",
                                   legacy_subdirs=("",))
    assert f == legacy
    assert folder == shot


def test_legacy_2d_subfolder(tmp_path):
    shot = tmp_path / "shot"
    legacy = _touch(shot / "cotracker_2d" / "tracks_2d_overlay.mp4")
    f, folder = find_latest_output(shot, "2D_POINT_TRACK", "tracks_2d_overlay.mp4",
                                   legacy_subdirs=("cotracker_2d",))
    assert f == legacy
    assert folder == shot / "cotracker_2d"


def test_filename_preference_order(tmp_path):
    shot = tmp_path / "shot"
    d = shot / "2D_POINT_TRACK" / "_latest"
    _touch(d / "tracks_2d_nuke.nk")
    pin = _touch(d / "tracks_2d_cornerpin_nuke.nk")
    f, _ = find_latest_output(shot, "2D_POINT_TRACK",
                              ["tracks_2d_cornerpin_nuke.nk", "tracks_2d_nuke.nk"])
    assert f == pin


def test_nothing_found(tmp_path):
    assert find_latest_output(tmp_path / "nope", "3D_CAMERA_TRACK", "x.nk") == (None, None)


# ------------------------------------------------------------------ scan ----
def test_scan_media_pool_files_and_sequence_dirs(tmp_path):
    _touch(tmp_path / "b_clip.MP4")
    _touch(tmp_path / "a_clip.mov")
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / "seq" / "frame_0001.exr")
    (tmp_path / "empty_dir").mkdir()
    names = [p.name for p in scan_media_pool(tmp_path)]
    assert names == ["a_clip.mov", "b_clip.MP4", "seq"]


def test_scan_missing_folder_is_empty(tmp_path):
    assert scan_media_pool(tmp_path / "missing") == []


# ----------------------------------------------------------- thumbnails ----
def test_thumb_key_changes_with_mtime(tmp_path):
    clip = _touch(tmp_path / "clip.mp4", b"abc")
    k1 = thumb_cache_key(clip)
    os.utime(clip, (time.time() + 100, time.time() + 100))
    assert thumb_cache_key(clip) != k1


def test_thumb_key_changes_with_path_and_size(tmp_path):
    a = _touch(tmp_path / "a" / "clip.mp4", b"abc")
    b = _touch(tmp_path / "b" / "clip.mp4", b"abc")
    st = a.stat()
    os.utime(b, (st.st_atime, st.st_mtime))
    assert thumb_cache_key(a) != thumb_cache_key(b), "same name in another folder must not collide"
    a.write_bytes(b"abcd")
    os.utime(a, (st.st_atime, st.st_mtime))
    assert thumb_cache_key(a) != thumb_cache_key(b)


def test_thumb_path_is_under_cache_dir(tmp_path):
    clip = _touch(tmp_path / "clip.mp4")
    p = thumb_path(tmp_path / "thumbs", clip, 7)
    assert p.parent == tmp_path / "thumbs"
    assert p.name.endswith("_000007.jpg")


def test_prune_removes_oldest_first(tmp_path):
    cache = tmp_path / "thumbs"
    old = _touch(cache / "old.jpg", b"0" * 100)
    new = _touch(cache / "new.jpg", b"0" * 100)
    os.utime(old, (1, 1))
    removed = prune_thumb_cache(cache, limit_bytes=150)
    assert removed == 1
    assert not old.exists() and new.exists()
