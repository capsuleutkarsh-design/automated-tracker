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

import struct
from pathlib import Path


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
    """(registered_images, points) for one sparse model folder."""
    model_dir = Path(model_dir)
    imgs = _count_from_bin(model_dir / "images.bin")
    pts = _count_from_bin(model_dir / "points3D.bin")
    if not imgs:
        imgs = _count_images_txt(model_dir / "images.txt")
    if not pts:
        pts = _count_from_txt(model_dir / "points3D.txt")
    return imgs, pts


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
