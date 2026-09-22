"""
Multi-Format Photogrammetry Exporter (Enhanced with RANSAC Ground Plane Alignment)
Exports COLMAP sparse reconstruction to:
- import_to_blender.py (1-Click Blender script with camera, background sequence, point cloud & ground plane)
- camera_track.usda (Pixar Universal Scene Description)
- camera_track.chan (Nuke camera tracking channel)
- points3D.ply (Stanford Point Cloud with RGB colors)
- camera_track.json (Full structured matrices and intrinsics)
"""

import sys
import os
import re
import shutil
import subprocess
import math
import json
from pathlib import Path
import numpy as np

# Optional USD Python binding
try:
    from pxr import Usd, UsdGeom, Gf, Sdf
    HAS_USD = True
except ImportError:
    HAS_USD = False


def _ensure_script_dir_on_path():
    """
    Make `core.*` importable when this file runs as a script (CLI or
    batch_reconstruct.bat). Frozen builds already have everything on the path.
    """
    if getattr(sys, 'frozen', False):
        return
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)


IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.exr', '.tif', '.tiff')


# =============================================================================
# COORDINATE CONVENTIONS
# =============================================================================
# COLMAP: world is arbitrary but its camera has x right, y DOWN, z FORWARD; the
# stored pose is world-to-camera. Every DCC camera has x right, y UP and looks
# down -Z, so the camera axes flip with diag(1, -1, -1). The world basis is the
# only thing that differs per target:
#   blender  Z-up:  X_b = X_c, Y_b = Z_c, Z_b = -Y_c   (COLMAP -Y is "up")
#   nuke     Y-up:  diag(1, -1, -1)                     (camera looks down -Z)
#   usd      Y-up:  same as Nuke (USD's default stage up axis)
CAMERA_FLIP = np.diag([1.0, -1.0, -1.0])
WORLD_BASES = {
    "blender": np.array([[1.0, 0.0, 0.0],
                         [0.0, 0.0, 1.0],
                         [0.0, -1.0, 0.0]]),
    "nuke": np.diag([1.0, -1.0, -1.0]),
    "usd": np.diag([1.0, -1.0, -1.0]),
}


def colmap_pose_to(img, target):
    """
    Camera-to-world pose of a parsed COLMAP image in a target convention.

    Returns (C, R): the camera centre and a 3x3 camera-to-world rotation, both
    in the target's world basis, with the camera looking down its local -Z.
    Column-vector convention: X_world = R @ X_cam + C.
    """
    W = WORLD_BASES[target]
    # + 0.0 normalises -0.0 so a static axis never exports as "-0.000000"
    C = W @ np.asarray(img["center"], dtype=float) + 0.0
    R = W @ np.asarray(img["R_world"], dtype=float) @ CAMERA_FLIP + 0.0
    return C, R


def colmap_points_to(xyz, target):
    """(N, 3) COLMAP world points -> the target's world basis."""
    xyz = np.asarray(xyz, dtype=float).reshape(-1, 3)
    return xyz @ WORLD_BASES[target].T


def usd_matrix_rows(C, R):
    """
    The 16 row-major values for Gf.Matrix4d(...) of a camera-to-world pose.

    USD matrices are ROW-vector (p' = p @ M): the 3x3 block holds the transpose
    of the column-vector rotation and the translation sits in the last row.
    """
    R = np.asarray(R, dtype=float)
    C = np.asarray(C, dtype=float)
    M = np.eye(4)
    M[:3, :3] = R.T
    M[3, :3] = C
    return [float(v) for v in M.reshape(-1)]


def camera_intrinsics_mm(cam, sensor_width_mm=36.0):
    """
    Lens and aperture in millimetres for a parsed COLMAP camera, on a 36 mm wide
    sensor. The vertical aperture follows the pixel aspect implied by fx/fy, so
    a non-square solve still reprojects correctly. FOVs are in degrees.
    """
    width = float(cam["width"])
    height = float(cam["height"])
    fx = float(cam["focal_x"])
    fy = float(cam.get("focal_y", fx))
    lens_mm = fx * sensor_width_mm / width
    sensor_height_mm = lens_mm * height / fy
    return {
        "lens_mm": lens_mm,
        "sensor_width_mm": sensor_width_mm,
        "sensor_height_mm": sensor_height_mm,
        "hfov_deg": math.degrees(2.0 * math.atan(width / (2.0 * fx))),
        "vfov_deg": math.degrees(2.0 * math.atan(height / (2.0 * fy))),
    }


def nuke_win_translate(cam):
    """
    Nuke Camera win_translate for a COLMAP principal point (cx, cy).

    Nuke's window units are NDC: the full aperture spans -1..1, and the knob
    moves the projection WINDOW, so the image content moves the other way.
    COLMAP measures cy from the top, Nuke's image y is up, hence the flip:
        u = (w - 2*cx) / w
        v = (2*cy - h) / h
    """
    w = float(cam["width"])
    h = float(cam["height"])
    cx = float(cam.get("cx", w / 2.0))
    cy = float(cam.get("cy", h / 2.0))
    return (w - 2.0 * cx) / w, (2.0 * cy - h) / h


def rotation_from_z_to(n):
    """Rotation matrix that takes +Z onto the unit vector n (for ground planes)."""
    n = np.asarray(n, dtype=float)
    n = n / (np.linalg.norm(n) or 1.0)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(z, n)
    s = np.linalg.norm(v)
    c = float(np.dot(z, n))
    if s < 1e-9:
        if c > 0:
            return np.eye(3)
        # n == -Z: half turn about X
        return np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]],
                   [v[2], 0, -v[0]],
                   [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1.0 - c) / (s * s))


def apply_timeline_start(images, start_frame, frame_step=1):
    """
    Put every registered image on the shot's timeline.

    Extracted frames are always renumbered 1..M on disk, but at Frame step N
    they are every Nth frame of the shot: on-disk index k is source frame
    1 + (k - 1) * N, so it belongs on timeline_start + (k - 1) * N - regardless
    of whether COLMAP registered frame 1. The camera therefore carries a key
    every N frames across the plate's real range and the DCC interpolates
    between them; nothing is squeezed onto consecutive frames.

    Returns the offset applied to the first frame (timeline_start - 1).
    """
    step = max(1, int(frame_step or 1))
    offset = int(start_frame or 1) - 1
    for img in images.values():
        img["frame"] = offset + 1 + (int(img["index"]) - 1) * step
    return offset


def timeline_start_of(images, frame_step=1):
    """
    Recover timeline_start from any image, undoing the rule above:
    frame = timeline_start + (index - 1) * frame_step.
    """
    step = max(1, int(frame_step or 1))
    for img in images.values():
        return int(img["frame"]) - (int(img["index"]) - 1) * step
    return 1


def qvec2rotmat(qvec):
    """Convert COLMAP quaternion (qw, qx, qy, qz) to 3x3 rotation matrix."""
    w, x, y, z = qvec
    return np.array([
        [1 - 2*y*y - 2*z*z, 2*x*y - 2*w*z, 2*x*z + 2*w*y],
        [2*x*y + 2*w*z, 1 - 2*x*x - 2*z*z, 2*y*z - 2*w*x],
        [2*x*z - 2*w*y, 2*y*z + 2*w*x, 1 - 2*x*x - 2*y*y]
    ])


def rotmat2euler(R):
    """
    Convert 3x3 rotation matrix to Euler angles (XYZ order in radians) for Blender/Nuke.
    """
    sy = math.sqrt(R[0, 0] * R[0, 0] + R[1, 0] * R[1, 0])
    singular = sy < 1e-6

    if not singular:
        x = math.atan2(R[2, 1], R[2, 2])
        y = math.atan2(-R[2, 0], sy)
        z = math.atan2(R[1, 0], R[0, 0])
    else:
        x = math.atan2(-R[1, 2], R[1, 1])
        y = math.atan2(-R[2, 0], sy)
        z = 0.0

    # + 0.0 turns -0.0 into 0.0 so exported curves never read "-0.000000"
    return x + 0.0, y + 0.0, z + 0.0


# COLMAP camera models and their documented parameter order
# (src/colmap/sensor/models.h). Values are indices into PARAMS[]:
#   (fx, fy, cx, cy, k1, k2); None when the model has no such parameter.
COLMAP_CAMERA_MODELS = {
    "SIMPLE_PINHOLE":       (0, 0, 1, 2, None, None),  # f, cx, cy
    "PINHOLE":              (0, 1, 2, 3, None, None),  # fx, fy, cx, cy
    "SIMPLE_RADIAL":        (0, 0, 1, 2, 3, None),     # f, cx, cy, k
    "RADIAL":               (0, 0, 1, 2, 3, 4),        # f, cx, cy, k1, k2
    "OPENCV":               (0, 1, 2, 3, 4, 5),        # fx, fy, cx, cy, k1, k2, p1, p2
    "OPENCV_FISHEYE":       (0, 1, 2, 3, 4, 5),        # fx, fy, cx, cy, k1, k2, k3, k4
    "FULL_OPENCV":          (0, 1, 2, 3, 4, 5),        # fx, fy, cx, cy, k1, k2, p1, p2, k3..k6
    "FOV":                  (0, 1, 2, 3, None, None),  # fx, fy, cx, cy, omega
    "SIMPLE_RADIAL_FISHEYE": (0, 0, 1, 2, 3, None),    # f, cx, cy, k
    "RADIAL_FISHEYE":       (0, 0, 1, 2, 3, 4),        # f, cx, cy, k1, k2
    "THIN_PRISM_FISHEYE":   (0, 1, 2, 3, 4, 5),        # fx, fy, cx, cy, k1, k2, p1, p2, k3, k4, sx1, sy1
}
FISHEYE_MODELS = ("OPENCV_FISHEYE", "SIMPLE_RADIAL_FISHEYE", "RADIAL_FISHEYE",
                  "THIN_PRISM_FISHEYE", "FOV")


def parse_colmap_cameras(cameras_file):
    """Parses COLMAP cameras.txt"""
    cameras = {}
    if not Path(cameras_file).exists():
        return cameras

    with open(cameras_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            cam_id = int(parts[0])
            model = parts[1]
            width = int(parts[2])
            height = int(parts[3])
            params = [float(p) for p in parts[4:]]

            def _p(idx, default):
                return params[idx] if idx is not None and idx < len(params) else default

            # Unknown model: assume "f cx cy ..." like SIMPLE_RADIAL rather than
            # silently centring the principal point.
            ifx, ify, icx, icy, ik1, ik2 = COLMAP_CAMERA_MODELS.get(model, (0, 0, 1, 2, None, None))
            focal_x = _p(ifx, params[0])
            focal_y = _p(ify, focal_x)
            cx = _p(icx, width / 2.0)
            cy = _p(icy, height / 2.0)

            cameras[cam_id] = {
                "id": cam_id,
                "model": model,
                "width": width,
                "height": height,
                "focal_x": focal_x,
                "focal_y": focal_y,
                "cx": cx,
                "cy": cy,
                "k1": _p(ik1, 0.0),
                "k2": _p(ik2, 0.0),
                "fisheye": model in FISHEYE_MODELS,
                "params": params
            }
    return cameras


def parse_colmap_images(images_file):
    """Parses COLMAP images.txt"""
    images = {}
    if not Path(images_file).exists():
        return images

    with open(images_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith('#'):
            continue

        parts = line.split()
        if len(parts) >= 9:
            image_id = int(parts[0])
            qw, qx, qy, qz = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
            tx, ty, tz = float(parts[5]), float(parts[6]), float(parts[7])
            camera_id = int(parts[8])
            name = parts[9] if len(parts) > 9 else f"image_{image_id}"

            # On-disk frame index from the filename (frame_000042.jpg -> 42). The
            # last digit run of the stem, like detect_sequence_start, so a stem
            # such as shot2_000042 still gives 42.
            frame_num = image_id
            m = re.findall(r'(\d+)', Path(name).stem)
            if m:
                frame_num = int(m[-1])

            R_colmap = qvec2rotmat(np.array([qw, qx, qy, qz]))
            t_colmap = np.array([tx, ty, tz])

            # Camera center in COLMAP world coordinates: C = -R^T * t
            R_world = R_colmap.T
            C_world = -R_world @ t_colmap

            images[image_id] = {
                "id": image_id,
                "index": frame_num,   # 1-based position on disk, never shifted
                "frame": frame_num,   # timeline frame; see apply_timeline_start
                "name": name,
                "camera_id": camera_id,
                "qvec": [qw, qx, qy, qz],
                "tvec": [tx, ty, tz],
                "center": C_world.tolist(),
                "R_world": R_world.tolist()
            }

            if i < len(lines) and not lines[i].startswith('#'):
                i += 1
    return images


class PointCloud:
    """Sparse points as arrays: xyz (N, 3) float64 in COLMAP world, rgb (N, 3) uint8."""
    __slots__ = ("xyz", "rgb")

    def __init__(self, xyz=None, rgb=None):
        self.xyz = np.zeros((0, 3), dtype=float) if xyz is None else np.asarray(xyz, dtype=float).reshape(-1, 3)
        self.rgb = np.zeros((0, 3), dtype=np.uint8) if rgb is None else np.asarray(rgb, dtype=np.uint8).reshape(-1, 3)

    def __len__(self):
        return int(self.xyz.shape[0])


def parse_colmap_points3D(points_file):
    """Parses COLMAP points3D.txt into a PointCloud (parsed once, shared by every exporter)."""
    xyz = []
    rgb = []
    if not Path(points_file).exists():
        return PointCloud()

    with open(points_file, 'r', encoding='utf-8') as f:
        for line in f:
            if not line or line[0] == '#':
                continue
            parts = line.split(None, 7)
            if len(parts) >= 7:
                xyz.append((float(parts[1]), float(parts[2]), float(parts[3])))
                rgb.append((int(parts[4]), int(parts[5]), int(parts[6])))
    return PointCloud(xyz, rgb)


class GroundPlane:
    """RANSAC ground plane in COLMAP world: unit normal pointing 'up' (-Y side), plane offset d, inlier centroid."""
    __slots__ = ("normal", "d", "inlier_ratio", "centroid")

    def __init__(self, normal, d, inlier_ratio, centroid):
        self.normal = np.asarray(normal, dtype=float)
        self.d = float(d)
        self.inlier_ratio = float(inlier_ratio)
        self.centroid = np.asarray(centroid, dtype=float)

    def in_convention(self, target):
        """(location, unit normal) in the target world basis."""
        W = WORLD_BASES[target]
        return W @ self.centroid, W @ self.normal


def detect_ground_plane_ransac(points, max_iters=300, dist_thresh=0.08, seed=0):
    """
    RANSAC plane estimation on the sparse cloud to find the dominant ground plane.

    Seeded, so every exporter that is handed the result sees the same plane.
    Returns a GroundPlane, or None when there is no convincing plane.
    """
    xyz = points.xyz if isinstance(points, PointCloud) else np.asarray(points, dtype=float).reshape(-1, 3)
    num_pts = len(xyz)
    if num_pts < 20:
        return None

    rng = np.random.default_rng(seed)
    best_inliers = 0
    best_plane = None
    best_mask = None

    for _ in range(max_iters):
        # Sample 3 random points
        idx = rng.choice(num_pts, 3, replace=False)
        p1, p2, p3 = xyz[idx[0]], xyz[idx[1]], xyz[idx[2]]

        normal = np.cross(p2 - p1, p3 - p1)
        norm_len = np.linalg.norm(normal)
        if norm_len < 1e-6:
            continue

        normal = normal / norm_len
        d = -np.dot(normal, p1)

        # Distance from points to plane: |n . p + d|
        mask = np.abs(xyz @ normal + d) < dist_thresh
        inliers = int(mask.sum())

        if inliers > best_inliers:
            best_inliers = inliers
            best_plane = (normal, d)
            best_mask = mask

    inlier_ratio = best_inliers / num_pts
    if best_plane is None or inlier_ratio <= 0.15:
        return None

    normal, d = best_plane
    # COLMAP's camera y points down, so the sky is on the -Y side of the floor.
    if normal[1] > 0:
        normal, d = -normal, -d
    centroid = xyz[best_mask].mean(axis=0)
    return GroundPlane(normal, d, inlier_ratio, centroid)


def find_images_dir(scene_path):
    """The extracted-frames folder for a solve: <shot>/images next to 3D_CAMERA_TRACK/<stamp>."""
    scene_path = Path(scene_path)
    for cand in (scene_path / "images",
                 scene_path.parent.parent / "images",
                 scene_path.parent / "images"):
        if cand.exists():
            return cand
    return scene_path / "images"


def image_sequence_info(img_dir):
    """
    (extension, frame count) of the extracted sequence, from what is on disk.
    The extractor keeps the source suffix (.png / .exr / .tif are common plates),
    so nothing may assume .jpg. Returns ('.jpg', 0) for a missing folder.
    """
    img_dir = Path(img_dir)
    if not img_dir.is_dir():
        return ".jpg", 0
    counts = {}
    for f in img_dir.iterdir():
        ext = f.suffix.lower()
        if ext in IMAGE_EXTS and f.is_file():
            counts[ext] = counts.get(ext, 0) + 1
    if not counts:
        return ".jpg", 0
    ext = max(counts, key=counts.get)
    return ext, counts[ext]


def source_sequence_plate(folder):
    """
    The artist's own plate in a folder, as something a DCC can read directly.

    The extracted frames under images/ are an internal cache - renumbered from 1
    and, at Frame step N, only every Nth frame - so they are not what a Read node
    should point at when the source was a sequence to begin with. This turns
    shot_a_1001.exr .. shot_a_1060.exr into
    {'pattern': '.../shot_a_%04d.exr', 'first': 1001, 'last': 1060, ...},
    every frame, already numbered the way the timeline is.

    The frame number is the last run of digits in the stem, the same rule
    parse_colmap_images and detect_sequence_start use. Returns None when the
    folder is not a numbered image sequence.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return None
    files = sorted(f for f in folder.iterdir()
                   if f.is_file() and f.suffix.lower() in IMAGE_EXTS)
    if not files:
        return None

    def _digits(path):
        runs = list(re.finditer(r"\d+", path.stem))
        return runs[-1] if runs else None

    first_run = _digits(files[0])
    last_run = _digits(files[-1])
    if first_run is None or last_run is None:
        return None

    stem = files[0].stem
    digits = len(first_run.group())
    pattern_stem = stem[:first_run.start()] + f"%0{digits}d" + stem[first_run.end():]
    return {
        "pattern": str(folder / (pattern_stem + files[0].suffix)).replace("\\", "/"),
        "first": int(first_run.group()),
        "last": int(last_run.group()),
        "count": len(files),
        "ext": files[0].suffix.lower(),
        "digits": digits,
    }


def _sequence_range(images, img_dir, frame_step=1, source_sequence=None):
    """
    (timeline_start, timeline_end, n_frames, ext) covering the whole plate, not
    just the registered frames.

    n_frames is how many files the plate actually has; the timeline range is
    what the artist sees, so at Frame step N the M extracted frames still span
    (M - 1) * N frames of the shot. A source sequence is the real plate - every
    frame - so its range is simply its own length.
    """
    step = max(1, int(frame_step or 1))
    t0 = timeline_start_of(images, step)
    if source_sequence:
        n_frames = int(source_sequence["count"])
        return t0, t0 + n_frames - 1, n_frames, source_sequence["ext"]
    ext, n_on_disk = image_sequence_info(img_dir)
    n_frames = n_on_disk or max(int(i["index"]) for i in images.values())
    return t0, t0 + (n_frames - 1) * step, n_frames, ext


# =============================================================================
# EXPORTERS
# =============================================================================
def export_blender_script(scene_dir, cameras, images, points, output_script_path=None, fps=None,
                          ground=None, frame_step=1, source_sequence=None):
    """
    Generates a 1-Click Python script for Blender that sets up camera,
    animation, background image sequence, Geometry Nodes point cloud (EEVEE & Cycles renderable),
    RANSAC ground plane, and provides Alembic (.abc) export.

    The point cloud is read from points3D.ply next to the script (written by
    export_ply_pointcloud, Y-up) instead of being embedded as JSON.

    `source_sequence` (see source_sequence_plate) is the artist's own plate and
    is used as the background when there is one; otherwise the extracted frames
    are. The scene frame range is the plate's real timeline range, so at
    `frame_step` > 1 the camera is keyed every Nth frame inside it.
    """
    scene_path = Path(scene_dir).resolve()
    if output_script_path is None:
        output_script_path = scene_path / "import_to_blender.py"

    sorted_images = sorted(images.values(), key=lambda x: x["frame"])
    if not sorted_images or not cameras:
        return False

    first_cam = next(iter(cameras.values()))
    width = first_cam["width"]
    height = first_cam["height"]
    # Frame rate comes from the caller (probed off the source clip). The old code read a
    # "fps" key that parse_colmap_cameras never sets, so every export was silently 30.
    fps_val = float(fps) if fps else float(first_cam.get("fps", 0) or 0) or 24.0
    # Blender stores the rate as fps / fps_base, which is how NTSC rates such as
    # 29.97 (30000/1001) are represented exactly rather than rounded to 30.
    fps = int(round(fps_val))
    fps_base = round(fps / fps_val, 6) if fps_val > 0 else 1.0
    cx = first_cam.get("cx", width / 2.0)
    cy = first_cam.get("cy", height / 2.0)

    # Sensor and lens calculation
    lens = camera_intrinsics_mm(first_cam)
    sensor_width_mm = lens["sensor_width_mm"]
    lens_mm = lens["lens_mm"]
    sensor_height_mm = lens["sensor_height_mm"]

    # Principal Point Shift for Blender
    shift_x = float((width / 2.0 - cx) / float(max(width, height)))
    shift_y = -float((height / 2.0 - cy) / float(max(width, height)))

    step = max(1, int(frame_step or 1))
    img_dir = find_images_dir(scene_path)
    # The plate covers the whole sequence; the camera is keyed on the registered
    # frames only, every `step` frames.
    start_frame, end_frame, n_frames, img_ext = _sequence_range(images, img_dir, step, source_sequence)

    # Which plate the background points at. A source sequence is the artist's
    # own, every frame and already numbered like the timeline, so Blender only
    # has to be told where its numbering starts. The extracted frames are an
    # internal cache; at step > 1 they are every Nth frame and Blender's image
    # user has no step of its own, so the header says so in plain words rather
    # than showing a background that drifts away from the camera.
    if source_sequence:
        images_dir_str = str(Path(source_sequence["pattern"]).parent).replace('\\', '/')
        img_ext = source_sequence["ext"]
        frame_offset = int(source_sequence["first"]) - 1
        plate_note = (f"Background: your source sequence, frames "
                      f"{source_sequence['first']}-{source_sequence['last']}.")
    else:
        images_dir_str = str(img_dir).replace('\\', '/')
        frame_offset = 0
        plate_note = "Background: the extracted frames in images/."
        if step > 1:
            plate_note += (f"\nNOTE: Frame Step {step} was used, so those frames are every {step}th "
                           f"frame of the shot\nand do NOT line up with the timeline one to one. "
                           f"The camera is keyed every {step} frames\nfrom {start_frame}; for a "
                           f"frame-accurate background, load the original plate yourself.")

    frames_data = []
    for img in sorted_images:
        f_num = img["frame"]
        # COLMAP World -> Blender World: X_b = X_c, Y_b = Z_c, Z_b = -Y_c
        loc_blender, R_blender = colmap_pose_to(img, "blender")
        rx, ry, rz = rotmat2euler(R_blender)

        frames_data.append({
            "frame": f_num,
            "loc": [round(float(v), 6) for v in loc_blender],
            "rot": [round(v, 6) for v in [rx, ry, rz]]
        })

    # RANSAC Ground Plane (fitted once by the caller so Blender and Nuke agree)
    if ground is None and points is not None and len(points) >= 20:
        ground = detect_ground_plane_ransac(points)
    ground_data = None
    if ground is not None:
        loc_b, norm_b = ground.in_convention("blender")
        # a Blender plane faces +Z; rotate it onto the fitted normal
        rx, ry, rz = rotmat2euler(rotation_from_z_to(norm_b))
        ground_data = {
            "location": [round(float(v), 4) for v in loc_b],
            "normal": [round(float(v), 4) for v in norm_b],
            "rotation": [round(v, 6) for v in (rx, ry, rz)],
            "inlier_ratio": round(ground.inlier_ratio, 3)
        }

    output_script_name = Path(output_script_path).name
    output_script_dir_str = str(Path(output_script_path).parent).replace('\\', '/')
    ply_path_str = str(Path(output_script_path).parent / "points3D.ply").replace('\\', '/')
    mesh_path_str = str(scene_path / "environment_mesh.ply").replace('\\', '/')
    image_exts_repr = repr((img_ext,) if img_ext else IMAGE_EXTS)
    try:
        _ensure_script_dir_on_path()
        from core.version import display_version
        version_str = display_version()
    except Exception:
        version_str = ""

    script_content = f'''"""
=============================================================================
1-CLICK BLENDER PHOTOGRAMMETRY CAMERA TRACK & POINT CLOUD IMPORTER
Generated by Automated Tracker {version_str}

HOW TO USE IN BLENDER:
1. Open Blender.
2. Go to the 'Scripting' workspace at the top.
3. Click 'Open' and select this file ({output_script_name}).
4. Click 'Run Script' (or press Alt+P).
5. The Tracked Camera, Video Background, Point Cloud (Renderable), and Ground Plane load instantly!

{plate_note}
=============================================================================
"""

import bpy
import os
import sys
import math
import json
from mathutils import Vector, Euler, Matrix

def set_linear_interpolation(target_obj):
    """Safely sets linear interpolation across Blender 3.x, 4.x, and 5.x Slotted Actions."""
    anim_data = getattr(target_obj, "animation_data", None)
    if not anim_data or not anim_data.action:
        return
    action = anim_data.action
    fcurves = getattr(action, "fcurves", None)
    if fcurves is None:
        slot = getattr(anim_data, "action_slot", None) or (action.slots[0] if getattr(action, "slots", None) else None)
        if slot and getattr(action, "layers", None) and action.layers[0].strips:
            try:
                channelbag = action.layers[0].strips[0].channelbag(slot)
                fcurves = channelbag.fcurves
            except Exception:
                fcurves = []
    if fcurves:
        for fc in fcurves:
            for kf in fc.keyframe_points:
                kf.interpolation = 'LINEAR'

def setup_point_cloud_geometry_nodes(pc_obj):
    """Builds a Geometry Nodes modifier and material so point cloud is visible and renderable in EEVEE & Cycles."""
    try:
        mat_name = "Photogrammetry_PointCloud_Mat"
        mat = bpy.data.materials.get(mat_name)
        if not mat:
            mat = bpy.data.materials.new(name=mat_name)
            mat.use_nodes = True
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            nodes.clear()

            attr_node = nodes.new(type='ShaderNodeAttribute')
            attr_node.attribute_name = "Col"
            attr_node.location = (-300, 0)

            bsdf_node = nodes.new(type='ShaderNodeBsdfPrincipled')
            bsdf_node.location = (0, 0)
            if hasattr(bsdf_node.inputs.get('Roughness'), 'default_value'):
                bsdf_node.inputs['Roughness'].default_value = 0.5

            out_node = nodes.new(type='ShaderNodeOutputMaterial')
            out_node.location = (300, 0)

            links.new(attr_node.outputs['Color'], bsdf_node.inputs['Base Color'])
            links.new(bsdf_node.outputs['BSDF'], out_node.inputs['Surface'])

        if pc_obj.data.materials:
            pc_obj.data.materials[0] = mat
        else:
            pc_obj.data.materials.append(mat)

        mod_name = "PointCloud_Spheres"
        gn_mod = pc_obj.modifiers.get(mod_name) or pc_obj.modifiers.new(name=mod_name, type='NODES')

        gn_group_name = "Photogrammetry_PointsToSpheres"
        node_group = bpy.data.node_groups.get(gn_group_name)
        if not node_group:
            node_group = bpy.data.node_groups.new(name=gn_group_name, type='GeometryNodeTree')
            if hasattr(node_group, 'interface'):
                node_group.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
                node_group.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
            elif hasattr(node_group, 'inputs'):
                node_group.inputs.new('NodeSocketGeometry', 'Geometry')
                node_group.outputs.new('NodeSocketGeometry', 'Geometry')

            gn_nodes = node_group.nodes
            gn_links = node_group.links
            gn_nodes.clear()

            in_n = gn_nodes.new('NodeGroupInput')
            in_n.location = (-400, 0)

            m2p_n = gn_nodes.new('GeometryNodeMeshToPoints')
            m2p_n.location = (-150, 0)
            if 'Radius' in m2p_n.inputs:
                m2p_n.inputs['Radius'].default_value = 0.025

            set_mat_n = gn_nodes.new('GeometryNodeSetMaterial')
            set_mat_n.location = (150, 0)
            set_mat_n.inputs['Material'].default_value = mat

            out_n = gn_nodes.new('NodeGroupOutput')
            out_n.location = (400, 0)

            gn_links.new(in_n.outputs['Geometry'], m2p_n.inputs['Mesh'])
            gn_links.new(m2p_n.outputs['Points'], set_mat_n.inputs['Geometry'])
            gn_links.new(set_mat_n.outputs['Geometry'], out_n.inputs['Geometry'])

        gn_mod.node_group = node_group
        print("✔ Point cloud equipped with Geometry Nodes & Vertex Color Shader.")
    except Exception as e:
        print(f"Notice on Geometry Nodes setup: {{e}}")

def setup_tracked_scene():
    print("🎬 Setting up Photogrammetry Camera Track in Blender...")

    scene = bpy.context.scene
    scene.render.resolution_x = {width}
    scene.render.resolution_y = {height}
    scene.render.fps = {fps}
    scene.render.fps_base = {fps_base}
    scene.frame_start = {start_frame}
    scene.frame_end = {end_frame}

    # 1. Create Collection
    col_name = "Photogrammetry_Track"
    col = bpy.data.collections.get(col_name)
    if not col:
        col = bpy.data.collections.new(col_name)
        scene.collection.children.link(col)

    # 2. Create Tracked Camera
    cam_data_name = "Tracked_Camera_Data"
    cam_obj_name = "Tracked_Camera"

    cam_data = bpy.data.cameras.get(cam_data_name) or bpy.data.cameras.new(cam_data_name)
    cam_data.sensor_fit = 'HORIZONTAL'
    cam_data.sensor_width = {sensor_width_mm:.4f}
    cam_data.sensor_height = {sensor_height_mm:.4f}
    cam_data.lens = {lens_mm:.4f}
    cam_data.shift_x = {shift_x:.6f}
    cam_data.shift_y = {shift_y:.6f}
    cam_data.display_size = 0.5
    cam_data.show_background_images = True

    # Setup Background Video Frame Sequence
    images_folder = r"{images_dir_str}"
    if os.path.exists(images_folder):
        frame_files = sorted([f for f in os.listdir(images_folder) if f.lower().endswith({image_exts_repr})])
        if frame_files:
            first_frame_path = os.path.join(images_folder, frame_files[0])
            bg_img = cam_data.background_images.new()
            try:
                img_data = bpy.data.images.load(first_frame_path, check_existing=True)
                img_data.source = 'SEQUENCE'
                bg_img.image = img_data
                bg_img.image_user.frame_duration = len(frame_files)
                bg_img.image_user.frame_start = {start_frame}
                # the plate's own numbering: file number = scene frame - frame_start + 1 + frame_offset
                bg_img.image_user.frame_offset = {frame_offset}
                bg_img.image_user.use_auto_refresh = True
                bg_img.alpha = 1.0
                bg_img.display_depth = 'BACK'
                print(f"✔ Attached background image sequence ({{len(frame_files)}} frames).")
            except Exception as e:
                print(f"Notice: Background image sequence load: {{e}}")

    cam_obj = bpy.data.objects.get(cam_obj_name)
    if not cam_obj:
        cam_obj = bpy.data.objects.new(cam_obj_name, cam_data)
        col.objects.link(cam_obj)

    scene.camera = cam_obj

    # 3. Animate Camera Location and Rotation Keyframes
    cam_obj.animation_data_clear()
    cam_obj.rotation_mode = 'XYZ'

    frames_data = {json.dumps(frames_data)}

    for fd in frames_data:
        f = fd["frame"]
        loc = fd["loc"]
        rot = fd["rot"]

        cam_obj.location = Vector(loc)
        cam_obj.rotation_euler = Euler(rot, 'XYZ')

        cam_obj.keyframe_insert(data_path="location", frame=f)
        cam_obj.keyframe_insert(data_path="rotation_euler", frame=f)

    # Set interpolation to Linear
    set_linear_interpolation(cam_obj)
    print(f"✔ Keyframed camera over {{len(frames_data)}} frames.")

    # 4. Sparse Point Cloud: read points3D.ply (written next to this script, with
    #    vertex colours) instead of embedding every point in this file. The PLY is
    #    Y-up like the Nuke/USD exports; +90 deg about X turns Y-up into Blender's Z-up.
    ply_path = r"{ply_path_str}"
    pc_obj = None
    if os.path.exists(ply_path):
        try:
            pc_obj_name = "Sparse_PointCloud"
            stale = bpy.data.objects.get(pc_obj_name)
            if stale:
                bpy.data.objects.remove(stale, do_unlink=True)
            for o in list(bpy.context.selected_objects):
                o.select_set(False)
            if hasattr(bpy.ops.wm, "ply_import"):
                bpy.ops.wm.ply_import(filepath=ply_path, forward_axis='Y', up_axis='Z')
            elif hasattr(bpy.ops.import_mesh, "ply"):
                bpy.ops.import_mesh.ply(filepath=ply_path)
            pc_obj = bpy.context.selected_objects[0] if bpy.context.selected_objects else None
            if pc_obj:
                pc_obj.name = pc_obj_name
                pc_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
                if pc_obj.name not in col.objects:
                    col.objects.link(pc_obj)
                if pc_obj.name in scene.collection.objects:
                    scene.collection.objects.unlink(pc_obj)
                setup_point_cloud_geometry_nodes(pc_obj)
                print(f"✔ Loaded sparse point cloud with {{len(pc_obj.data.vertices)}} renderable points.")
        except Exception as e:
            print(f"Notice on point cloud import: {{e}}")

    # 5. RANSAC Ground Plane Object (placed at the inlier centroid, rotated onto the fitted normal)
    ground_info = {json.dumps(ground_data)}
    if ground_info:
        try:
            gp_name = "Ground_Plane"
            gp_obj = bpy.data.objects.get(gp_name)
            if not gp_obj:
                bpy.ops.mesh.primitive_plane_add(size=10.0, location=ground_info["location"],
                                                 rotation=ground_info["rotation"])
                gp_obj = bpy.context.active_object
                gp_obj.name = gp_name
                gp_obj.display_type = 'WIRE'
                if gp_obj.name not in col.objects:
                    col.objects.link(gp_obj)
                if gp_obj.name in scene.collection.objects:
                    scene.collection.objects.unlink(gp_obj)
            print("✔ Created RANSAC Ground Plane reference.")
        except Exception as e:
            print(f"Notice on ground plane: {{e}}")

    # 6. 3D Environment Surface Mesh (if available). COLMAP writes it in its own
    #    world (y down), so -90 deg about X brings it into Blender's Z-up.
    mesh_path = r"{mesh_path_str}"
    if os.path.exists(mesh_path):
        try:
            for o in list(bpy.context.selected_objects):
                o.select_set(False)
            if hasattr(bpy.ops.wm, "ply_import"):
                bpy.ops.wm.ply_import(filepath=mesh_path, forward_axis='Y', up_axis='Z')
            elif hasattr(bpy.ops.import_mesh, "ply"):
                bpy.ops.import_mesh.ply(filepath=mesh_path)
            imported_mesh = bpy.context.selected_objects[0] if bpy.context.selected_objects else None
            if imported_mesh:
                imported_mesh.name = "Environment_Mesh"
                imported_mesh.display_type = 'WIRE'
                imported_mesh.rotation_euler = (math.radians(-90.0), 0.0, 0.0)
                if imported_mesh.name not in col.objects:
                    col.objects.link(imported_mesh)
                if imported_mesh.name in scene.collection.objects:
                    scene.collection.objects.unlink(imported_mesh)
                print("✔ Loaded 3D Environment Surface Mesh.")
        except Exception as e:
            print(f"Notice on 3D Mesh import: {{e}}")

    print("\\n=======================================================")
    print("🎉 SUCCESS: Camera Track & Point Cloud loaded in Blender!")
    print("=======================================================\\n")

def export_to_alembic(filepath=None):
    if filepath is None:
        filepath = os.path.join(r"{output_script_dir_str}", "camera_track.abc")
    
    try:
        bpy.ops.wm.alembic_export(
            filepath=filepath,
            start={start_frame},
            end={end_frame},
            selected=False,
            visible_objects_only=False,
            evaluation_mode='VIEWPORT'
        )
        print(f"✔ Exported Alembic (.abc) file to: {{filepath}}")
    except Exception as e:
        print(f"Notice: Alembic export exception: {{e}}")

def save_blend_file(filepath=None):
    if filepath is None:
        filepath = os.path.join(r"{output_script_dir_str}", "camera_track.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        print(f"✔ Saved Blender (.blend) project file to: {{filepath}}")
    except Exception as e:
        pass

if __name__ == "__main__":
    setup_tracked_scene()
    export_to_alembic()
    save_blend_file()
    if getattr(bpy.app, "background", False):
        sys.exit(0)
'''

    with open(output_script_path, 'w', encoding='utf-8') as f:
        f.write(script_content)

    return True


def export_usd_scene(scene_dir, cameras, images, points, output_usd_path=None, fps=None):
    if not HAS_USD:
        return False

    scene_path = Path(scene_dir).resolve()
    if output_usd_path is None:
        output_usd_path = scene_path / "camera_track.usda"

    sorted_images = sorted(images.values(), key=lambda x: x["frame"])
    if not sorted_images or not cameras:
        return False

    first_cam = next(iter(cameras.values()))
    lens = camera_intrinsics_mm(first_cam)

    # Real timeline frames, so the stage spans the shot's own range; at a frame
    # step the samples inside it simply sit every Nth frame and USD interpolates.
    start_frame = sorted_images[0]["frame"]
    end_frame = sorted_images[-1]["frame"]

    stage = Usd.Stage.CreateNew(str(output_usd_path))
    # Y-up, USD's default: the same world as the Nuke export.
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetStartTimeCode(start_frame)
    stage.SetEndTimeCode(end_frame)
    usd_fps = float(fps) if fps else 24.0
    stage.SetTimeCodesPerSecond(usd_fps)
    stage.SetFramesPerSecond(usd_fps)

    # 1. Create Camera
    cam_prim_path = Sdf.Path("/World/Tracked_Camera")
    usd_cam = UsdGeom.Camera.Define(stage, cam_prim_path)
    usd_cam.CreateFocalLengthAttr(lens["lens_mm"])
    usd_cam.CreateHorizontalApertureAttr(lens["sensor_width_mm"])
    usd_cam.CreateVerticalApertureAttr(lens["sensor_height_mm"])
    usd_cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 10000.0))

    xform_op = usd_cam.AddTransformOp()

    for img in sorted_images:
        f = img["frame"]
        loc_usd, R_usd = colmap_pose_to(img, "usd")
        # Gf.Matrix4d is row-vector: rotation block transposed, translation in the last row.
        mat = Gf.Matrix4d(*usd_matrix_rows(loc_usd, R_usd))
        xform_op.Set(mat, Usd.TimeCode(f))

    # 2. Create 3D Points
    if points is not None and len(points):
        pts_path = Sdf.Path("/World/Sparse_PointCloud")
        usd_pts = UsdGeom.Points.Define(stage, pts_path)

        xyz_usd = colmap_points_to(points.xyz, "usd")
        rgb = points.rgb.astype(float) / 255.0
        pts_vec = [Gf.Vec3f(float(p[0]), float(p[1]), float(p[2])) for p in xyz_usd]
        colors_vec = [Gf.Vec3f(float(c[0]), float(c[1]), float(c[2])) for c in rgb]
        widths = [0.02] * len(points)

        usd_pts.CreatePointsAttr(pts_vec)
        usd_pts.CreateDisplayColorAttr(colors_vec)
        usd_pts.CreateWidthsAttr(widths)

    stage.GetRootLayer().Save()
    return True


def export_nuke_chan(scene_dir, cameras, images, output_chan_path=None, frame_step=1):
    """
    Exports a clean, standard 8-column ASCII .chan camera tracking file for Nuke:
    Columns: frame  tx  ty  tz  rx  ry  rz  vfov
    Nuke reads column 8 as the VERTICAL field of view in degrees; the angles are
    XYZ-order Eulers in degrees (set rot_order XYZ on the Camera before importing).

    Frame numbers are the shot's own: at Frame step N the keys land every N
    frames across the real range, and a third comment line says so, because a
    .chan with gaps in it otherwise looks like a failed solve.
    """
    scene_path = Path(scene_dir).resolve()
    if output_chan_path is None:
        output_chan_path = scene_path / "camera_track.chan"

    sorted_images = sorted(images.values(), key=lambda x: x["frame"])
    if not sorted_images or not cameras:
        return False

    first_cam = next(iter(cameras.values()))
    vfov_deg = camera_intrinsics_mm(first_cam)["vfov_deg"]

    lines = [
        "# Nuke .chan camera: frame tx ty tz rx ry rz vfov\n",
        "# Y-up world, camera looks down -Z. Rotations in degrees, rot_order XYZ "
        "(set the Camera's rot_order to XYZ before importing). vfov is the vertical FOV.\n",
    ]
    step = max(1, int(frame_step or 1))
    if step > 1:
        lines.append(
            "# Solved at frame step %d: one key every %d frames over %d-%d, "
            "interpolated in between.\n"
            % (step, step, sorted_images[0]["frame"], sorted_images[-1]["frame"]))
    for img in sorted_images:
        f = img["frame"]
        loc, R_nuke = colmap_pose_to(img, "nuke")
        rx, ry, rz = rotmat2euler(R_nuke)

        rx_deg = math.degrees(rx)
        ry_deg = math.degrees(ry)
        rz_deg = math.degrees(rz)

        lines.append(f"{f}\t{loc[0]:.6f}\t{loc[1]:.6f}\t{loc[2]:.6f}\t{rx_deg:.6f}\t{ry_deg:.6f}\t{rz_deg:.6f}\t{vfov_deg:.4f}\n")

    with open(output_chan_path, 'w', encoding='utf-8', newline='\n') as f:
        f.writelines(lines)

    return True


def export_nuke_camera_script(scene_dir, cameras, images, points, output_nk_path=None, fps=None,
                              ground=None, frame_step=1, source_sequence=None):
    """
    Generates a full 1-Click VFX Node Graph (.nk) for Foundry Nuke:
    - Read node (Footage Sequence, offset onto the timeline)
    - TimeWarp (only when the extracted frames are stepped; see below)
    - Camera3 node (Keyframed poses, lens, sensor aperture, win_translate)
    - ReadGeo2 node (3D Point Cloud PLY)
    - Card2 node (RANSAC Ground Plane)
    - Scene node (3D stage)
    - ScanlineRender node (Pre-connected 3D projection comp)
    - Lens distortion annotations for exact matching

    World is Nuke's Y-up with the camera looking down -Z (see WORLD_BASES).

    The Read points at `source_sequence` (the artist's own plate) when there is
    one, and at the extracted frames otherwise. Stepped extracted frames do not
    sit on timeline frames at all, so they get a TimeWarp that maps the timeline
    back onto them instead.
    """
    scene_path = Path(scene_dir).resolve()
    if output_nk_path is None:
        output_nk_path = scene_path / "camera_track_nuke.nk"

    sorted_images = sorted(images.values(), key=lambda x: x["frame"])
    if not sorted_images or not cameras:
        return False

    first_cam = next(iter(cameras.values()))
    width = first_cam["width"]
    height = first_cam["height"]

    lens = camera_intrinsics_mm(first_cam)
    sensor_width_mm = lens["sensor_width_mm"]
    lens_mm = lens["lens_mm"]
    sensor_height_mm = lens["sensor_height_mm"]

    # Window Translate (Principal Point Offset) in Nuke NDC units, see nuke_win_translate
    win_u, win_v = nuke_win_translate(first_cam)

    # Camera keys cover the registered frames, every `step` of them; the plate
    # covers the whole sequence.
    step = max(1, int(frame_step or 1))
    start_frame = sorted_images[0]["frame"]
    end_frame = sorted_images[-1]["frame"]
    img_dir = find_images_dir(scene_path)
    timeline_start, timeline_end, n_frames, img_ext = _sequence_range(images, img_dir, step, source_sequence)

    # Distortion parameters, read per model by parse_colmap_cameras
    k1 = float(first_cam.get("k1", 0.0))
    k2 = float(first_cam.get("k2", 0.0))

    tx_parts, ty_parts, tz_parts = [], [], []
    rx_parts, ry_parts, rz_parts = [], [], []

    for img in sorted_images:
        f = img["frame"]
        loc, R_nuke = colmap_pose_to(img, "nuke")
        rx, ry, rz = rotmat2euler(R_nuke)

        rx_deg = math.degrees(rx)
        ry_deg = math.degrees(ry)
        rz_deg = math.degrees(rz)

        tx_parts.append(f"x{f} {loc[0]:.6f}")
        ty_parts.append(f"x{f} {loc[1]:.6f}")
        tz_parts.append(f"x{f} {loc[2]:.6f}")

        rx_parts.append(f"x{f} {rx_deg:.6f}")
        ry_parts.append(f"x{f} {ry_deg:.6f}")
        rz_parts.append(f"x{f} {rz_deg:.6f}")

    tx_curve = " ".join(tx_parts)
    ty_curve = " ".join(ty_parts)
    tz_curve = " ".join(tz_parts)
    rx_curve = " ".join(rx_parts)
    ry_curve = " ".join(ry_parts)
    rz_curve = " ".join(rz_parts)

    ply_name = "points3D.ply"
    ply_path_str = str((Path(output_nk_path).parent / ply_name)).replace('\\', '/')

    # Which plate the Read points at, and what has to happen for it to line up
    # with the camera:
    #  - a source sequence is the artist's own plate: every frame, numbered as
    #    the timeline is, so it needs an offset only when the two disagree;
    #  - the extracted frames are numbered 1..M, so at step 1 the "offset" frame
    #    mode puts file 1 on timeline_start;
    #  - stepped extracted frames are every Nth frame of the shot and no offset
    #    can fix that, so a TimeWarp maps the timeline onto them instead and
    #    holds the ends.
    timewarp_node_str = ""
    plate_input = "Plate_Footage"
    if source_sequence:
        img_seq_path = source_sequence["pattern"]
        read_first = int(source_sequence["first"])
        read_last = int(source_sequence["last"])
        read_offset = timeline_start - read_first
    else:
        img_dir_str = str(img_dir).replace('\\', '/')
        img_seq_path = f"{img_dir_str}/frame_%06d{img_ext}"
        read_first, read_last = 1, n_frames
        read_offset = 0 if step > 1 else timeline_start - 1

    frame_knobs = f" frame_mode offset\n frame {read_offset}\n" if read_offset else ""

    if step > 1 and not source_sequence:
        plate_input = "Plate_Timewarp"
        timewarp_node_str = f'''TimeWarp {{
 inputs 1
 lookup {{{{clamp((frame - {timeline_start}) / {step} + 1, {read_first}, {read_last})}}}}
 name Plate_Timewarp
 label "frame step {step}: timeline {timeline_start} -> extracted frame 1"
 selected false
 xpos -180
 ypos 70
}}
'''

    # RANSAC ground plane for Nuke Card (fitted once by the caller so Blender and Nuke agree)
    if ground is None and points is not None and len(points) >= 20:
        ground = detect_ground_plane_ransac(points)
    card_node_str = ""
    extra_inputs = 0
    if ground is not None:
        loc_nuke, norm_nuke = ground.in_convention("nuke")
        # a Card faces +Z; rotate it onto the fitted normal (degrees, XYZ order)
        crx, cry, crz = (math.degrees(a) for a in rotmat2euler(rotation_from_z_to(norm_nuke)))
        extra_inputs += 1
        card_node_str = f'''push $cut_paste_input
Card2 {{
 inputs 0
 rot_order XYZ
 translate {{{loc_nuke[0]:.4f} {loc_nuke[1]:.4f} {loc_nuke[2]:.4f}}}
 rotate {{{crx:.4f} {cry:.4f} {crz:.4f}}}
 rows 10
 columns 10
 name Ground_Plane
 selected false
 xpos 320
 ypos 0
}}
'''

    # 3D Environment Mesh node. COLMAP writes the mesh in its own world (y down),
    # so a half turn about X puts it in the Y-up world the camera lives in.
    mesh_node_str = ""
    mesh_file = scene_path / "environment_mesh.ply"
    if mesh_file.exists():
        extra_inputs += 1
        mesh_ply_str = str(mesh_file).replace('\\', '/')
        mesh_node_str = f'''push $cut_paste_input
ReadGeo2 {{
 inputs 0
 file "{mesh_ply_str}"
 name Environment_Mesh_Geo
 selected false
 xpos 480
 ypos 0
}}
TransformGeo {{
 inputs 1
 rot_order XYZ
 rotate {{180 0 0}}
 name Environment_Mesh
 selected false
 xpos 480
 ypos 70
}}
'''

    scene_inputs = str(2 + extra_inputs)
    nk_fps = float(fps) if fps else 24.0
    # The label is the first thing an artist reads: say that the camera is keyed
    # every Nth frame rather than letting the gaps look like a failed solve.
    keys_note = f" (key every {step})" if step > 1 else ""

    # Read node: first/last and origfirst/origlast are the plate's own file
    # numbering, and frame_knobs carries the "offset" frame mode when the plate
    # has to be moved onto the timeline (A3).
    script = f'''set cut_paste_input [stack 0]
version 14.0 v1
BackdropNode {{
 inputs 0
 name Tracker_3D_Rig
 tile_color 0x243044ff
 gl_color 0x243044ff
 label "<b>Photogrammetry 3D Tracking Rig</b>\\n\\nCamera: {lens_mm:.2f}mm | Sensor: {sensor_width_mm:.1f}x{sensor_height_mm:.1f}mm | Shift: ({win_u:.4f}, {win_v:.4f})\\nDistortion: k1={k1:.6f}, k2={k2:.6f} | Points: {len(points):,} | Frames: {start_frame}-{end_frame}{keys_note} @ {nk_fps:.3f} fps | Plate: {timeline_start}-{timeline_end}"
 note_font_size 14
 xpos -220
 ypos -120
 bdwidth 820
 bdheight 480
 z_order 0
}}
push $cut_paste_input
Read {{
 inputs 0
 file "{img_seq_path}"
 format "{width} {height} 0 0 {width} {height} 1.0 {width}x{height}"
 first {read_first}
 last {read_last}
 origfirst {read_first}
 origlast {read_last}
{frame_knobs} frame_rate {nk_fps:.4f}
 name Plate_Footage
 selected false
 xpos -180
 ypos 0
}}
{timewarp_node_str}push $cut_paste_input
Camera3 {{
 inputs 0
 rot_order XYZ
 translate {{{{curve {tx_curve}}}}} {{{{curve {ty_curve}}}}} {{{{curve {tz_curve}}}}}
 rotate {{{{curve {rx_curve}}}}} {{{{curve {ry_curve}}}}} {{{{curve {rz_curve}}}}}
 focal {lens_mm:.4f}
 haperture {sensor_width_mm:.4f}
 vaperture {sensor_height_mm:.4f}
 win_translate {{{win_u:.6f} {win_v:.6f}}}
 name Solved_Camera
 selected true
 xpos 0
 ypos 100
}}
push $cut_paste_input
ReadGeo2 {{
 inputs 0
 file "{ply_path_str}"
 name Sparse_PointCloud
 selected false
 xpos 160
 ypos 0
}}
{card_node_str}{mesh_node_str}Scene {{
 inputs {scene_inputs}
 name Scene3D
 selected false
 xpos 160
 ypos 140
}}
ScanlineRender {{
 inputs 3
 bg {plate_input}
 obj Scene3D
 cam Solved_Camera
 output_motion_vectors false
 name ScanlineRender_Comp
 selected false
 xpos 0
 ypos 260
}}
'''
    with open(output_nk_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def export_ply_pointcloud(scene_dir, points, output_ply_path=None):
    scene_path = Path(scene_dir).resolve()
    if output_ply_path is None:
        output_ply_path = scene_path / "points3D.ply"

    if points is None or not len(points):
        return False

    header = f"""ply
format ascii 1.0
comment Y-up world, same as the Nuke and USD exports (Blender rotates it +90 deg about X)
element vertex {len(points)}
property float x
property float y
property float z
property uchar red
property uchar green
property uchar blue
end_header
"""
    xyz = colmap_points_to(points.xyz, "nuke")
    # newline='\n' matters: opened in default text mode on Windows, Python turns every
    # \n into \r\n, and strict PLY readers reject carriage returns in the header.
    with open(output_ply_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(header)
        rows = np.column_stack([xyz, points.rgb.astype(np.int64)])
        np.savetxt(f, rows, fmt="%.6f %.6f %.6f %d %d %d", newline="\n")

    return True


def find_blender_executable(custom_path=None):
    """
    Fast, instant check for Blender executable without slow recursive drive scanning.
    """
    if custom_path:
        p = Path(custom_path)
        if p.is_file() and p.name.lower().startswith("blender"):
            return p
        if p.is_dir() and (p / "blender.exe").exists():
            return p / "blender.exe"

    # Check PATH first (fastest)
    which_b = shutil.which("blender") or shutil.which("blender.exe")
    if which_b:
        return Path(which_b)

    # Check common standard locations directly (no slow recursive drive scanning)
    common_paths = [
        r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.5\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.4\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.3\blender.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
        r"D:\SteamLibrary\steamapps\common\Blender\blender.exe",
        r"D:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"D:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"D:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
    ]

    for p_str in common_paths:
        p = Path(p_str)
        if p.exists() and p.is_file():
            return p

    # Check subfolders in Blender Foundation directly (non-recursive, depth 1)
    bf_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
    if bf_dir.exists() and bf_dir.is_dir():
        try:
            for sub in bf_dir.iterdir():
                if sub.is_dir() and (sub / "blender.exe").exists():
                    return sub / "blender.exe"
        except Exception:
            pass

    return None


def auto_export_alembic_via_blender(scene_dir, blender_path=None, log_callback=None):
    """
    Runs headless Blender in the background to automatically bake camera_track.abc
    and camera_track.blend from import_to_blender.py.
    """
    scene_path = Path(scene_dir).resolve()
    blender_script = scene_path / "import_to_blender.py"
    if not blender_script.exists():
        return False

    blender_exe = find_blender_executable(blender_path)
    if not blender_exe:
        if log_callback:
            log_callback("💡 Tip: Run 'import_to_blender.py' in Blender or specify Blender Path in UI to auto-bake Alembic (.abc)!", "#a0a0b0")
        return False

    if log_callback:
        log_callback(f"▶ Auto-baking Alembic (.abc) & Blender (.blend) via {blender_exe.name}...", "#00d2ff")

    # Large clouds take a while to bake; stderr is folded into stdout by
    # hidden_kwargs, so the log tail has to come from stdout.
    timeout_s = 600
    try:
        cmd = [str(blender_exe), "--background", "--python", str(blender_script)]
        _ensure_script_dir_on_path()
        from core.proc import hidden_kwargs
        proc = subprocess.run(cmd, text=True, timeout=timeout_s, encoding="utf-8",
                              errors="replace", **hidden_kwargs(capture=True))
        abc_path = scene_path / "camera_track.abc"
        if abc_path.exists():
            if log_callback:
                log_callback(f"✔ Successfully generated Alembic: {abc_path.name}", "#00ff88")
            return True
        else:
            if log_callback:
                tail = [l for l in (proc.stdout or "").splitlines() if l.strip()][-8:]
                log_callback(f"Notice: Headless Blender finished with code {proc.returncode} but wrote no camera_track.abc.", "#e0a000")
                for l in tail:
                    log_callback(f"   {l[:200]}", "#a0a0b0")
    except subprocess.TimeoutExpired:
        if log_callback:
            log_callback(f"Notice: Headless Blender timed out after {timeout_s}s.", "#e0a000")
    except Exception as e:
        if log_callback:
            log_callback(f"Notice: Auto Alembic export failed: {e}", "#e0a000")

    return False


def _probe_scene_fps(scene_path):
    """Best-effort frame rate for a 3D_CAMERA_TRACK folder, via its source clip."""
    try:
        _ensure_script_dir_on_path()
        from core.media_info import probe_fps
    except Exception:
        return None

    shot_dir = None
    for parent in scene_path.parents:
        if parent.name == "3D_CAMERA_TRACK":
            shot_dir = parent.parent
            break
    if shot_dir is None:
        return None

    videos_dir = shot_dir.parent.parent / "02 VIDEOS"
    if not videos_dir.is_dir():
        return None
    for ext in (".mp4", ".mov", ".avi", ".mkv", ".m4v"):
        candidate = videos_dir / (shot_dir.name + ext)
        if candidate.exists():
            return probe_fps(candidate)
    return None


def _ensure_txt_model(model_dir, colmap_exe=None, log_callback=None):
    """
    Make sure model_dir holds cameras.txt / images.txt. A sparse/N that only has
    the .bin files is converted in place with `colmap model_converter` (C12).
    Returns True when the TXT files are there afterwards.
    """
    model_dir = Path(model_dir)
    if (model_dir / "cameras.txt").exists() and (model_dir / "images.txt").exists():
        return True
    if not ((model_dir / "cameras.bin").exists() and (model_dir / "images.bin").exists()):
        return False

    exe = Path(colmap_exe) if colmap_exe else None
    if exe is None or not exe.exists():
        try:
            _ensure_script_dir_on_path()
            from core.app_paths import colmap_exe as _resolve_colmap
            exe = _resolve_colmap()
        except Exception:
            exe = None
    if exe is None or not Path(exe).exists():
        if log_callback:
            log_callback(f"Notice: {model_dir.name} only has binary model files and colmap.exe was not found to convert them.", "#e0a000")
        return False

    if log_callback:
        log_callback(f"   Converting binary model {model_dir.name} to TXT for export...", "#a0a0b0")
    try:
        _ensure_script_dir_on_path()
        from core.proc import run_hidden
        res = run_hidden(
            [str(exe), "model_converter",
             "--input_path", str(model_dir),
             "--output_path", str(model_dir),
             "--output_type", "TXT"],
            capture=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if res.returncode != 0 and log_callback:
            tail = [l for l in (res.stdout or "").splitlines() if l.strip()][-4:]
            log_callback(f"Notice: model_converter exited with {res.returncode}: " + " | ".join(tail), "#e0a000")
    except Exception as e:
        if log_callback:
            log_callback(f"Notice: model_converter failed: {e}", "#e0a000")
    return (model_dir / "cameras.txt").exists() and (model_dir / "images.txt").exists()


def export_all_formats(scene_dir, blender_path=None, log_callback=None, fps=None,
                       start_frame=None, colmap_exe=None, frame_step=1, source_sequence=None):
    """
    Parses a COLMAP scene folder and automatically generates all export formats:
    - import_to_blender.py
    - camera_track_nuke.nk (1-Click Nuke Script)
    - camera_track.chan (Nuke)
    - camera_track.abc (Alembic)
    - camera_track.blend (Blender Scene)
    - camera_track.usda (USD)
    - points3D.ply (Point Cloud)
    - camera_track.json

    `frame_step` is the Frame step the solve was extracted at: the camera gets a
    key every N frames across the plate's real range, always at the plate's own
    frame rate. `source_sequence` (see source_sequence_plate) is the original
    plate when the shot came in as an image sequence, and is what the Nuke Read
    and the Blender background point at.
    """
    scene_path = Path(scene_dir).resolve()
    step = max(1, int(frame_step or 1))
    sparse_dir = scene_path / "sparse"

    # When no rate is supplied (batch_reconstruct.bat / direct CLI use), work the shot
    # name back out of 04 SCENES/<shot>/3D_CAMERA_TRACK/<stamp> and probe its source clip.
    if not fps:
        fps = _probe_scene_fps(scene_path)

    model_dir = sparse_dir
    if not ((sparse_dir / "cameras.txt").exists() and (sparse_dir / "images.txt").exists()):
        # Fall back to whichever sparse/<n> model actually holds a solve, rather
        # than assuming sparse/0 - COLMAP numbers them in the order it tried, not
        # by quality.
        try:
            _ensure_script_dir_on_path()
            from core.colmap_model import find_best_model
            best = find_best_model(sparse_dir)
        except Exception:
            best = None
        if best is not None:
            model_dir = Path(best)

    if not _ensure_txt_model(model_dir, colmap_exe=colmap_exe, log_callback=log_callback):
        return {
            "success": False,
            "error": f"No readable COLMAP model (cameras/images .txt or .bin) found in {sparse_dir}."
        }

    cameras_file = model_dir / "cameras.txt"
    images_file = model_dir / "images.txt"
    points3D_file = model_dir / "points3D.txt"

    cameras = parse_colmap_cameras(cameras_file)
    images = parse_colmap_images(images_file)
    if not cameras or not images:
        return {
            "success": False,
            "error": f"COLMAP model in {model_dir} has no cameras or registered images."
        }
    # Parsed once into numpy arrays; PLY, USD and the ground plane all reuse it (C8).
    points = parse_colmap_points3D(points3D_file)

    # Frames are numbered from the extracted filenames, which the pipeline always
    # renumbers from 1, so image k belongs on timeline_start + (k - 1) * step
    # whether or not COLMAP registered frame 1 (A1). Every exporter below reads
    # img['frame'], so doing it once here covers all of them.
    offset = apply_timeline_start(images, start_frame, step)
    if offset and log_callback:
        log_callback("   Timeline start %d - camera keys shifted by %+d frames."
                     % (int(start_frame), offset), "#a0a0b0")
    if step > 1 and log_callback:
        keyed = sorted(img["frame"] for img in images.values())
        log_callback("   Frame step %d - camera keys land every %d frames, %d..%d."
                     % (step, step, keyed[0], keyed[-1]), "#a0a0b0")

    # One seeded ground-plane fit shared by Blender and Nuke (C9).
    ground = detect_ground_plane_ransac(points) if len(points) >= 20 else None

    exported_files = []

    # 1. PLY Point Cloud (first: the Blender script and the Nuke ReadGeo load it)
    ply_file = scene_path / "points3D.ply"
    if export_ply_pointcloud(scene_path, points, ply_file):
        exported_files.append(str(ply_file))

    # 2. Blender 1-Click Script
    blender_script = scene_path / "import_to_blender.py"
    if export_blender_script(scene_path, cameras, images, points, blender_script, fps=fps, ground=ground,
                             frame_step=step, source_sequence=source_sequence):
        exported_files.append(str(blender_script))

    # 3. Nuke 1-Click Script (.nk)
    nuke_nk_file = scene_path / "camera_track_nuke.nk"
    if export_nuke_camera_script(scene_path, cameras, images, points, nuke_nk_file, fps=fps, ground=ground,
                                 frame_step=step, source_sequence=source_sequence):
        exported_files.append(str(nuke_nk_file))

    # 4. Nuke .chan Camera File
    chan_file = scene_path / "camera_track.chan"
    # .chan carries no frame-rate field, so fps is not passed here.
    if export_nuke_chan(scene_path, cameras, images, chan_file, frame_step=step):
        exported_files.append(str(chan_file))

    # 5. Universal Scene Description (.usda)
    if HAS_USD:
        usd_file = scene_path / "camera_track.usda"
        if export_usd_scene(scene_path, cameras, images, points, usd_file, fps=fps):
            exported_files.append(str(usd_file))

    # 6. Structured JSON
    json_file = scene_path / "camera_track.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump({
            "fps": float(fps) if fps else None,
            "timeline_start": int(start_frame or 1),
            # image k on disk is timeline frame timeline_start + (k - 1) * frame_step;
            # a downstream tool needs the step to know that and to read the gaps
            # between keys as intended rather than as missing frames.
            "frame_step": step,
            "cameras": cameras,
            "images": images,
            "points_count": len(points)
        }, f, indent=2)
    exported_files.append(str(json_file))

    # 7. Auto-bake Alembic (.abc) via background Blender if available
    auto_export_alembic_via_blender(scene_path, blender_path=blender_path, log_callback=log_callback)
    abc_file = scene_path / "camera_track.abc"
    if abc_file.exists():
        exported_files.append(str(abc_file))
    blend_file = scene_path / "camera_track.blend"
    if blend_file.exists():
        exported_files.append(str(blend_file))

    return {
        "success": True,
        "exported_files": exported_files,
        "frames_count": len(images),
        "points_count": len(points),
        "alembic_path": str(abc_file) if abc_file.exists() else None,
        "blend_path": str(blend_file) if blend_file.exists() else None
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_scene = sys.argv[1]
        cli_fps = float(sys.argv[2]) if len(sys.argv) > 2 else None
        res = export_all_formats(target_scene, fps=cli_fps)
        print(json.dumps(res, indent=2))
    else:
        print("Usage: python export_tools.py <path_to_scene_directory>")
