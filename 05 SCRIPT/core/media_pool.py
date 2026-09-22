"""
The media pool (02 VIDEOS), the per-shot output tree (04 SCENES) and the
thumbnail cache. Pure Python - no Qt - so it can be unit-tested directly.

Output layout for a shot:

    04 SCENES/<shot>/3D_CAMERA_TRACK/_latest/...          synced copy of the newest solve
    04 SCENES/<shot>/3D_CAMERA_TRACK/<timestamp>/...      every solve, newest sorts last
    04 SCENES/<shot>/sparse/...                           legacy layout (very old solves)
    04 SCENES/<shot>/2D_POINT_TRACK/{_latest,<timestamp>}/...
    04 SCENES/<shot>/cotracker_2d/...                     legacy 2D layout

find_latest_output() is the one lookup for all of these.
"""

import hashlib
import os
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
SEQ_EXTS = {".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

LATEST_DIR = "_latest"


def is_sequence_dir(path):
    """A folder counts as a clip when it holds at least one image file."""
    try:
        return any(s.is_file() and s.suffix.lower() in SEQ_EXTS for s in Path(path).iterdir())
    except OSError:
        return False


def scan_media_pool(videos_dir):
    """
    Every clip in the media folder: video files plus folders holding an image
    sequence, sorted by name. Missing folder -> empty list.
    """
    videos_dir = Path(videos_dir)
    if not videos_dir.is_dir():
        return []
    items = []
    for f in videos_dir.iterdir():
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
            items.append(f)
        elif f.is_dir() and is_sequence_dir(f):
            items.append(f)
    return sorted(items, key=lambda x: x.name)


def find_latest_output(shot_dir, track_subdir, filenames, legacy_subdirs=("",)):
    """
    Locate the newest copy of an output file for a shot.

    Looks, in order, in  <shot_dir>/<track_subdir>/_latest, then in every
    timestamp folder under <track_subdir> newest first, then in each legacy
    folder ("" means shot_dir itself). Within one folder the filenames are
    tried in the order given, so put the preferred file first.

    Returns (file_path, folder) or (None, None).
    """
    shot_dir = Path(shot_dir)
    if isinstance(filenames, (str, Path)):
        filenames = [filenames]
    filenames = [Path(f) for f in filenames]

    def _first_in(folder):
        for name in filenames:
            p = folder / name
            if p.exists():
                return p
        return None

    track_root = shot_dir / track_subdir
    hit = _first_in(track_root / LATEST_DIR)
    if hit:
        return hit, track_root / LATEST_DIR

    if track_root.is_dir():
        subs = [d for d in track_root.iterdir() if d.is_dir() and d.name != LATEST_DIR]
        for d in sorted(subs, key=lambda x: x.name, reverse=True):
            hit = _first_in(d)
            if hit:
                return hit, d

    for legacy in legacy_subdirs:
        folder = shot_dir / legacy if legacy else shot_dir
        hit = _first_in(folder)
        if hit:
            return hit, folder

    return None, None


# ---------------------------------------------------------------------------
# Thumbnail cache
#
# Single frames pulled out of a clip for scrubbing before its frame cache
# exists. Keyed by the clip's full path, size and mtime, so a different clip
# with the same file name - or the same file re-exported - never shows stale
# frames. Lives under the per-user cache folder and is pruned at startup.
# ---------------------------------------------------------------------------
THUMB_CACHE_LIMIT = 300 * 1024 * 1024   # bytes kept after pruning


def thumb_cache_key(video_path):
    """Short stable id for a clip: sha1 of (absolute path, size, mtime_ns)."""
    p = Path(video_path)
    try:
        st = p.stat()
        size, mtime = st.st_size, st.st_mtime_ns
    except OSError:
        size, mtime = -1, -1
    raw = "%s|%d|%d" % (os.path.normcase(str(p.resolve())), size, mtime)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:20]


def thumb_path(thumbs_dir, video_path, frame_idx):
    """Where the cached still for frame_idx of video_path goes."""
    return Path(thumbs_dir) / ("%s_%06d.jpg" % (thumb_cache_key(video_path), int(frame_idx)))


def prune_thumb_cache(thumbs_dir, limit_bytes=THUMB_CACHE_LIMIT):
    """
    Delete the oldest thumbnails until the folder is under limit_bytes.
    Returns the number of files removed. Never raises.
    """
    thumbs_dir = Path(thumbs_dir)
    if not thumbs_dir.is_dir():
        return 0
    entries = []
    for f in thumbs_dir.glob("*.jpg"):
        try:
            st = f.stat()
            entries.append((st.st_mtime, st.st_size, f))
        except OSError:
            continue
    total = sum(e[1] for e in entries)
    removed = 0
    for mtime, size, f in sorted(entries):
        if total <= limit_bytes:
            break
        try:
            f.unlink()
            total -= size
            removed += 1
        except OSError:
            continue
    return removed
