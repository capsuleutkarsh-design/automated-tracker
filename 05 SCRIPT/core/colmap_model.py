"""
Picking the right COLMAP model out of a sparse/ folder.

COLMAP's incremental mapper writes one folder per reconstruction it manages to
build: sparse/0, sparse/1, ... If its first initialisation attempt produces a
degenerate pair and it then starts over, the *good* solve ends up in sparse/1
while sparse/0 holds a throwaway two-image model.

The pipeline used to hard-code sparse/0, so a run could register all 708 frames
and still export a two-camera track. That is the "it ran but the track was
unusable" failure: the solve was fine, the export read the wrong folder.
"""

import re
import struct
from pathlib import Path

from core.proc import run_hidden


def _count_from_bin(path):
    """COLMAP .bin files start with a uint64 element count."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        if len(head) < 8:
            return 0
        return struct.unpack("<Q", head)[0]
    except Exception:
        return 0


def _count_from_txt(path):
    """One entry per non-comment line - points3D.txt."""
    try:
        n = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    n += 1
        return n
    except Exception:
        return 0


def _count_images_txt(path):
    """
    images.txt alternates a pose line and a POINTS2D line per image, but the
    POINTS2D line is empty when nothing was triangulated - so counting
    non-blank lines and halving undercounts. Match the pose lines instead:
    IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME = 10 fields, starting with an int.
    """
    try:
        n = 0
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 10 and parts[0].isdigit() and parts[8].isdigit():
                    n += 1
        return n
    except Exception:
        return 0


def model_stats(model_dir):
    """
    (registered_images, points) for one sparse model folder.

    Callers sort on this tuple, so the order and the meaning of the two numbers
    are fixed: more registered images wins, points break the tie. Mean
    reprojection error is deliberately not in here - it needs COLMAP itself and
    is asked for separately through model_error().
    """
    model_dir = Path(model_dir)
    imgs = _count_from_bin(model_dir / "images.bin")
    pts = _count_from_bin(model_dir / "points3D.bin")
    if not imgs:
        imgs = _count_images_txt(model_dir / "images.txt")
    if not pts:
        pts = _count_from_txt(model_dir / "points3D.txt")
    return imgs, pts


# -----------------------------------------------------------------------------
# Mean reprojection error
#
# Frame count alone cannot say whether a model got better: image_registrator can
# add frames by forcing poses that pull the whole solve apart. COLMAP prints the
# one number that catches that - the mean reprojection error - from
# `colmap model_analyzer --path <model>`, in a block like:
#
#   Cameras: 1
#   Images: 60
#   Registered images: 60
#   Points: 12043
#   Observations: 55892
#   Mean track length: 4.6402
#   Mean observations per image: 931.533
#   Mean reprojection error: 0.71787px
#
# Everything here is best-effort: a missing binary, a COLMAP build that words
# the line differently or a crash all mean "we do not know", never an exception.
# -----------------------------------------------------------------------------
_ERROR_LINE = re.compile(r"mean\s+reprojection\s+error\s*[:=]\s*([-+0-9.eE]+)", re.I)


def parse_model_error(text):
    """Mean reprojection error in pixels from model_analyzer output, or None."""
    if not text:
        return None
    m = _ERROR_LINE.search(text)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    # A model with no observations reports nan or nothing usable; that is not a
    # number a threshold can be compared against.
    if value != value or value < 0:
        return None
    return value


def model_error(model_dir, colmap_exe):
    """
    Mean reprojection error in pixels for a sparse model, or None if unknown.

    Runs COLMAP's own model_analyzer rather than re-deriving the number, so the
    threshold in the worker is compared against exactly what COLMAP reports.
    """
    model_dir = Path(model_dir)
    if not is_model_dir(model_dir):
        return None
    try:
        res = run_hidden(
            [str(colmap_exe), "model_analyzer", "--path", str(model_dir)],
            capture=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except Exception:
        return None
    return parse_model_error(res.stdout or "")


# -----------------------------------------------------------------------------
# Which frames a model actually holds
#
# The artist thinks in timeline frames, COLMAP in image ids, and the two have
# nothing to do with each other: ids are handed out in matching order, not in
# frame order, and unregistered frames simply have no id. The link back is the
# image NAME (frame_000042.jpg), read with the same last-digit-run rule as
# export_tools.parse_colmap_images so shot2_000042 still gives 42.
# -----------------------------------------------------------------------------
def frame_index_from_name(name):
    """On-disk frame index from a COLMAP image name, or None."""
    runs = re.findall(r"(\d+)", Path(str(name)).stem)
    if not runs:
        return None
    try:
        return int(runs[-1])
    except ValueError:
        return None


def _indices_from_txt(path):
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                # the pose line, same shape model_stats counts; the POINTS2D line
                # that follows it never matches
                if len(parts) < 10 or not parts[0].isdigit() or not parts[8].isdigit():
                    continue
                idx = frame_index_from_name(parts[9])
                if idx is not None:
                    out.append(idx)
    except OSError:
        return []
    return out


def _indices_from_bin(path):
    """
    images.bin: a uint64 count, then per image an id, a quaternion, a
    translation, a camera id, a NUL-terminated name and its 2D points.
    """
    out = []
    try:
        with open(path, "rb") as f:
            head = f.read(8)
            if len(head) < 8:
                return []
            count = struct.unpack("<Q", head)[0]
            for _ in range(count):
                fixed = f.read(64)   # uint32 id + 7 doubles + uint32 camera id
                if len(fixed) < 64:
                    break
                name = bytearray()
                while True:
                    ch = f.read(1)
                    if not ch or ch == b"\x00":
                        break
                    name += ch
                n_pts = f.read(8)
                if len(n_pts) < 8:
                    break
                f.seek(struct.unpack("<Q", n_pts)[0] * 24, 1)
                idx = frame_index_from_name(name.decode("utf-8", "replace"))
                if idx is not None:
                    out.append(idx)
    except (OSError, struct.error):
        return []
    return out


def registered_indices(model_dir):
    """
    Sorted on-disk frame indices (1-based) present in a sparse model.

    Empty when the model cannot be read - callers treat that as "we cannot name
    the missing frames", not as "no frame is registered".
    """
    model_dir = Path(model_dir)
    idx = _indices_from_bin(model_dir / "images.bin")
    if not idx:
        idx = _indices_from_txt(model_dir / "images.txt")
    return sorted(set(idx))


def is_model_dir(d):
    d = Path(d)
    return d.is_dir() and any(
        (d / n).exists() for n in ("images.bin", "images.txt")
    )


def find_best_model(sparse_dir, log=None):
    """
    The reconstruction with the most registered images, or None.

    Ties break on point count, so a model with real structure wins over one that
    registered the same number of frames but triangulated nothing.
    """
    sparse_dir = Path(sparse_dir)
    if not sparse_dir.is_dir():
        return None

    candidates = []

    # sparse/<n>/ - the usual layout
    for d in sorted(sparse_dir.iterdir()):
        if is_model_dir(d):
            imgs, pts = model_stats(d)
            candidates.append((imgs, pts, d))

    # some tools write the model straight into sparse/
    if is_model_dir(sparse_dir):
        imgs, pts = model_stats(sparse_dir)
        candidates.append((imgs, pts, sparse_dir))

    if not candidates:
        return None

    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    best_imgs, best_pts, best = candidates[0]

    if log and len(candidates) > 1:
        summary = ", ".join("%s: %d images/%d points" % (c[2].name, c[0], c[1])
                            for c in candidates)
        log("   COLMAP produced %d models (%s) - exporting '%s' with %d images "
            "and %d points." % (len(candidates), summary, best.name, best_imgs, best_pts))

    if best_imgs == 0:
        return None
    return best
