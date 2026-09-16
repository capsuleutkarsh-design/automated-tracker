"""
Automated Photogrammetry Tracker - CoTracker3 2D AI Point Tracker Engine (Enhanced)
Features:
- Auto VRAM sliding-window chunking & FP16 (Prevents OOM on long 4K videos)
- Bounding box inclusion / exclusion masking
- 4-Point CornerPin / Screen Replacement Solver (Nuke CornerPin2D, AE Corner Pin, Blender Quad)
- Confidence threshold & velocity outlier filtering
- Exports: JSON, Nuke Tracker4, Nuke CornerPin2D, After Effects JSX, Blender Empties/Quad, CSV, Overlay MP4
"""

import os
import sys
import json
import math
import shutil
import subprocess
import colorsys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

import torch
import torch.nn.functional as F

_here = Path(__file__).resolve().parent
if not getattr(sys, 'frozen', False) and str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from core.app_paths import BASE_DIR, COTRACKER_DIR, ffmpeg_exe
from core.proc import run_hidden

# The cotracker package is vendored in 06 COTRACKER when running from source;
# frozen, it is bundled into the executable and already importable.
if not getattr(sys, 'frozen', False) and str(COTRACKER_DIR) not in sys.path:
    sys.path.insert(0, str(COTRACKER_DIR))

try:
    from cotracker.predictor import CoTrackerPredictor
    HAS_COTRACKER = True
except ImportError:
    HAS_COTRACKER = False

FFMPEG_EXE = ffmpeg_exe()


def get_default_device():
    return "cuda" if torch.cuda.is_available() else "cpu"


def get_gpu_memory_info():
    """Returns (allocated_mb, total_mb, device_name)"""
    if torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated() / (1024 * 1024)
        total = torch.cuda.get_device_properties(0).total_memory / (1024 * 1024)
        name = torch.cuda.get_device_name(0)
        return allocated, total, name
    return 0, 0, "CPU Only"


def load_video_frames(video_path, max_dimension=720, frame_step=1, in_point=0, out_point=-1):
    """
    Loads video frames as a NumPy array [T, H, W, 3] and returns original (W, H).
    Supports image sequence folders, extracted frames in 04 SCENES/<name>/images, or FFmpeg extraction.
    """
    video_path = Path(video_path)
    base_name = video_path.stem
    scene_images_dir = BASE_DIR / "04 SCENES" / base_name / "images"

    frames = []
    if video_path.is_dir():
        seq_files = sorted([f for f in video_path.iterdir() if f.is_file() and f.suffix.lower() in {'.jpg', '.jpeg', '.png', '.exr', '.tif', '.tiff'}])
        if out_point >= 0:
            seq_files = seq_files[in_point:out_point + 1]
        elif in_point > 0:
            seq_files = seq_files[in_point:]
        for i, fpath in enumerate(seq_files):
            if i % frame_step == 0:
                img = Image.open(fpath).convert("RGB")
                frames.append(np.array(img))
    elif scene_images_dir.exists():
        jpg_files = sorted(list(scene_images_dir.glob("*.jpg")) + list(scene_images_dir.glob("*.png")))
        if jpg_files:
            if out_point >= 0:
                jpg_files = jpg_files[in_point:out_point + 1]
            elif in_point > 0:
                jpg_files = jpg_files[in_point:]
            for i, fpath in enumerate(jpg_files):
                if i % frame_step == 0:
                    img = Image.open(fpath).convert("RGB")
                    frames.append(np.array(img))

    if not frames:
        import tempfile, shutil
        tmp_dir = Path(tempfile.mkdtemp(prefix="cotracker_frames_"))
        try:
            cmd = [
                str(FFMPEG_EXE), "-loglevel", "error",
                "-i", str(video_path),
                "-qscale:v", "2",
                str(tmp_dir / "f_%06d.jpg")
            ]
            run_hidden(cmd, check=True)
            jpgs = sorted(list(tmp_dir.glob("*.jpg")))
            if out_point >= 0:
                jpgs = jpgs[in_point:out_point + 1]
            elif in_point > 0:
                jpgs = jpgs[in_point:]
            for i, jp in enumerate(jpgs):
                if i % frame_step == 0:
                    frames.append(np.array(Image.open(jp).convert("RGB")))
        except Exception:
            pass
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    if not frames:
        raise ValueError(f"Could not extract or read any video frames from {video_path}")

    frames_np = np.stack(frames) # [T, H, W, 3]
    orig_h, orig_w = frames_np.shape[1], frames_np.shape[2]

    # Resize if max_dimension is specified and smaller than original
    if max_dimension and max(orig_h, orig_w) > max_dimension:
        scale = max_dimension / max(orig_h, orig_w)
        new_w = int(orig_w * scale)
        new_h = int(orig_h * scale)
        new_w = (new_w // 8) * 8
        new_h = (new_h // 8) * 8
        resized_frames = [np.array(Image.fromarray(f).resize((new_w, new_h), Image.BILINEAR)) for f in frames_np]
        proc_frames = np.stack(resized_frames)
    else:
        new_w = (orig_w // 8) * 8
        new_h = (orig_h // 8) * 8
        if new_w != orig_w or new_h != orig_h:
            resized_frames = [np.array(Image.fromarray(f).resize((new_w, new_h), Image.BILINEAR)) for f in frames_np]
            proc_frames = np.stack(resized_frames)
        else:
            proc_frames = frames_np

    return proc_frames, (orig_w, orig_h)


def point_in_poly(x, y, poly):
    """
    Ray-casting point-in-polygon test.
    poly: list of [px, py] or (px, py) vertices.
    """
    n = len(poly)
    if n < 3:
        return False
    inside = False
    p1x, p1y = poly[0]
    for i in range(n + 1):
        p2x, p2y = poly[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def is_point_in_mask(x, y, mask):
    """
    Checks if coordinate (x, y) is inside mask dict or bounding box list.
    Supports rectangle: {"type": "rect", "coords": [x1, y1, x2, y2]}
    Supports polygon:   {"type": "poly", "points": [(x1, y1), (x2, y2), ...]}
    Supports legacy:    [x1, y1, x2, y2]
    """
    if isinstance(mask, (list, tuple)):
        if len(mask) == 4:
            x1, y1, x2, y2 = mask
            return (min(x1, x2) <= x <= max(x1, x2)) and (min(y1, y2) <= y <= max(y1, y2))
        return False
    elif isinstance(mask, dict):
        m_type = mask.get("type", "rect")
        if m_type == "rect":
            coords = mask.get("coords", [])
            if len(coords) == 4:
                x1, y1, x2, y2 = coords
                return (min(x1, x2) <= x <= max(x1, x2)) and (min(y1, y2) <= y <= max(y1, y2))
        elif m_type == "poly":
            pts = mask.get("points", [])
            return point_in_poly(x, y, pts)
    return False


def scale_mask(mask, scale_x, scale_y):
    """Scales mask coordinates from original resolution to processing resolution."""
    if isinstance(mask, (list, tuple)) and len(mask) == 4:
        return [mask[0] / scale_x, mask[1] / scale_y, mask[2] / scale_x, mask[3] / scale_y]
    elif isinstance(mask, dict):
        m_type = mask.get("type", "rect")
        if m_type == "rect":
            c = mask.get("coords", [0, 0, 0, 0])
            return {
                "type": "rect",
                "coords": [c[0] / scale_x, c[1] / scale_y, c[2] / scale_x, c[3] / scale_y]
            }
        elif m_type == "poly":
            pts = mask.get("points", [])
            scaled_pts = [[p[0] / scale_x, p[1] / scale_y] for p in pts]
            return {
                "type": "poly",
                "points": scaled_pts
            }
    return mask


def generate_grid_points_with_masks(H, W, grid_size=10, inclusion_masks=None, exclusion_masks=None, inclusion_box=None, exclusion_box=None):
    """
    Generates [N, 3] grid query points (t, x, y) at t=0, filtered by inclusion and exclusion masks.
    Supports multi-masks (rectangles and freehand polygons).
    """
    inc_list = []
    if inclusion_masks:
        inc_list.extend(inclusion_masks if isinstance(inclusion_masks, list) else [inclusion_masks])
    if inclusion_box:
        inc_list.append({"type": "rect", "coords": inclusion_box})

    exc_list = []
    if exclusion_masks:
        exc_list.extend(exclusion_masks if isinstance(exclusion_masks, list) else [exclusion_masks])
    if exclusion_box:
        exc_list.append({"type": "rect", "coords": exclusion_box})

    xs = np.linspace(10, W - 10, grid_size)
    ys = np.linspace(10, H - 10, grid_size)
    grid_x, grid_y = np.meshgrid(xs, ys)
    pts = np.stack([grid_x.flatten(), grid_y.flatten()], axis=1) # [N, 2]

    filtered_pts = []
    for x, y in pts:
        # Check inclusion masks: if any inclusion mask exists, point MUST be inside at least one
        if inc_list:
            if not any(is_point_in_mask(x, y, m) for m in inc_list):
                continue

        # Check exclusion masks: point MUST NOT be inside ANY exclusion mask
        if exc_list:
            if any(is_point_in_mask(x, y, m) for m in exc_list):
                continue

        filtered_pts.append([0.0, float(x), float(y)])

    if not filtered_pts:
        # Fallback to center point if masks filtered everything
        filtered_pts.append([0.0, float(W / 2.0), float(H / 2.0)])

    return filtered_pts


def filter_trajectories_by_animated_masks(tracks, vis, animated_masks):
    """
    Evaluates tracks of shape [T, N, 2] against animated exclusion/inclusion masks at each frame t.
    If a tracked point moves inside an exclusion mask on frame t, vis[t, n] is set to False (occluded).
    """
    if not animated_masks:
        return tracks, vis

    from mask_animator import AnimatedMask
    T, N, _ = tracks.shape
    vis_updated = vis.copy()

    # Pre-parse AnimatedMask instances
    mask_objs = [m if isinstance(m, AnimatedMask) else AnimatedMask.from_dict(m) for m in animated_masks]

    for t in range(T):
        inc_geoms = []
        exc_geoms = []
        for m_obj in mask_objs:
            geom = m_obj.get_interpolated_geometry(t)
            if geom:
                if m_obj.category == "inclusion":
                    inc_geoms.append(geom)
                else:
                    exc_geoms.append(geom)

        for n in range(N):
            if not vis_updated[t, n]:
                continue
            px = tracks[t, n, 0]
            py = tracks[t, n, 1]

            if inc_geoms and not any(is_point_in_mask(px, py, g) for g in inc_geoms):
                vis_updated[t, n] = False
                continue

            if exc_geoms and any(is_point_in_mask(px, py, g) for g in exc_geoms):
                vis_updated[t, n] = False

    return tracks, vis_updated


def is_fp16_supported():
    """
    Checks if active GPU supports native FP16 Tensor Core autocast without CUDA launch failures.
    Pascal (sm_60+), Turing (sm_70+), Ampere (sm_80+), Ada (sm_89+) support fast native FP16.
    Maxwell (sm_50/sm_52, e.g. Quadro M4000, GTX 970/980) runs full precision FP32.
    """
    if not torch.cuda.is_available():
        return False
    try:
        cap = torch.cuda.get_device_capability(0)
        return cap[0] >= 6
    except Exception:
        return False


class _RawVisibilityCapture:
    """
    CoTrackerPredictor hard-codes `visibilities = visibilities > 0.9` before returning,
    which throws away the raw probabilities the Confidence control needs.

    A forward hook cannot see them: the predictor calls `self.model.forward(...)`
    directly, and nn.Module only dispatches hooks through __call__. So wrap the bound
    forward method instead, and restore it afterwards.
    """

    def __init__(self, predictor):
        self.raw = None
        self._inner = getattr(predictor, "model", None)
        self._orig = None
        if self._inner is None:
            return
        try:
            orig = self._inner.forward

            def _wrapped(*args, **kwargs):
                out = orig(*args, **kwargs)
                try:
                    if isinstance(out, (tuple, list)) and len(out) >= 2 and out[1] is not None:
                        self.raw = out[1].detach()
                    else:
                        self.raw = None
                except Exception:
                    self.raw = None
                return out

            self._inner.forward = _wrapped
            self._orig = orig
        except Exception:
            self._orig = None

    def remove(self):
        if self._orig is not None:
            try:
                self._inner.forward = self._orig
            except Exception:
                pass
            self._orig = None


# Measured ratio of peak VRAM to raw frame bytes for CoTracker3 offline.
ACTIVATION_OVERHEAD = 24
# Smallest window worth running; below this the tracker has too little temporal context.
MIN_CHUNK = 8


def auto_chunk_size(T, C, H, W, device, requested=120, enabled=True, log=None):
    """
    Picks how many frames may sit on the GPU at once.

    The raw frames alone cost C*H*W*4 bytes each once converted to float32, and the
    model needs several times that again for its own activations, so only a slice of
    free VRAM is budgeted for the frames themselves.
    """
    if not enabled:
        return T
    if device != "cuda":
        return min(requested, T)
    try:
        free_b, _total_b = torch.cuda.mem_get_info()
    except Exception:
        return min(requested, T)

    # The frames themselves are the small part: CoTracker's activations measured about
    # 24x the raw frame bytes (120 frames at 720x400 peaked near 10 GB against 0.41 GB of
    # frames). Budget against that, and leave 30% of free VRAM as headroom.
    bytes_per_frame = C * H * W * 4 * ACTIVATION_OVERHEAD
    budget = free_b * 0.70
    n = int(budget // max(1, bytes_per_frame))
    n = max(MIN_CHUNK, min(requested, n, T))
    if log and n < min(requested, T):
        log(f"   Auto VRAM Chunking: {free_b / 1024**3:.1f} GB free - "
            f"processing {n} frames at a time (instead of {min(requested, T)}).", "#e0a000")
        if n <= MIN_CHUNK:
            log(f"   ! {W}x{H} is large for this GPU. Tracking will still run, but a lower "
                f"Resolution setting gives better results.", "#e0a000")
    return n


def run_cotracker_chunked(model, video_tensor, queries_tensor, chunk_size=120, overlap=30,
                          device=None, auto_chunk=True, log=None):
    """
    Memory-safe chunked inference for long videos.

    video_tensor: [1, T, 3, H, W] uint8 **on the CPU** - only one chunk at a time is
                  uploaded to the GPU and converted to float32.
    queries_tensor: [1, N, 3] (t, x, y) on the CPU.

    Returns (tracks [1,T,N,2], visible [1,T,N] bool, confidence [1,T,N] float).
    """
    if device is None:
        device = get_default_device()

    T = video_tensor.shape[1]
    C, H, W = video_tensor.shape[2], video_tensor.shape[3], video_tensor.shape[4]
    N = queries_tensor.shape[1]
    use_fp16 = (device == "cuda") and is_fp16_supported()

    chunk = auto_chunk_size(T, C, H, W, device, requested=chunk_size, enabled=auto_chunk, log=log)
    overlap = max(0, min(overlap, chunk // 2))

    capture = _RawVisibilityCapture(model)

    def _is_oom(exc):
        return isinstance(exc, torch.cuda.OutOfMemoryError) or "out of memory" in str(exc).lower()

    def _infer_single(v_chunk_u8, q_chunk):
        v_gpu = v_chunk_u8.to(device).float()
        q_gpu = q_chunk.to(device)
        capture.raw = None
        with torch.no_grad():
            if use_fp16:
                try:
                    with torch.amp.autocast(device_type="cuda", dtype=torch.float16):
                        tracks, vis = model(v_gpu, queries=q_gpu)
                except Exception:
                    tracks, vis = model(v_gpu, queries=q_gpu)
            else:
                tracks, vis = model(v_gpu, queries=q_gpu)

        n_out = tracks.shape[2]
        raw = capture.raw
        if raw is not None and raw.ndim == 3 and raw.shape[2] >= n_out:
            conf = raw[:, :, :n_out].float().cpu().numpy()
        else:
            # Fall back to the thresholded boolean if the hook could not fire.
            conf = vis.float().cpu().numpy()

        t_np = tracks.cpu().numpy()
        v_np = vis.cpu().numpy().astype(bool)
        del v_gpu, q_gpu, tracks, vis
        if device == "cuda":
            torch.cuda.empty_cache()
        return t_np, v_np, conf

    try:
        # Single pass when the whole clip is allowed on the GPU at once.
        if T <= chunk:
            try:
                return _infer_single(video_tensor, queries_tensor)
            except Exception as exc:
                if not _is_oom(exc):
                    raise
                if device == "cuda":
                    torch.cuda.empty_cache()
                chunk = max(MIN_CHUNK, T // 2)
                overlap = min(overlap, chunk // 2)
                if log:
                    log(f"   ! GPU ran out of memory on a single pass - switching to "
                        f"{chunk}-frame blocks.", "#e0a000")

        max_qt = float(queries_tensor[0, :, 0].max().item()) if N else 0.0
        if max_qt > 0:
            if log:
                log(f"   ! Manual points sit as late as frame {int(max_qt) + 1}, but this clip is "
                    f"processed in {chunk}-frame blocks, so they are re-anchored to the start of "
                    f"the tracking range. Place manual points on the first frame of the range "
                    f"for exact results.", "#e0a000")
            queries_tensor = queries_tensor.clone()
            queries_tensor[0, :, 0] = queries_tensor[0, :, 0].clamp(0, chunk - 1)

        full_tracks = np.zeros((1, T, N, 2), dtype=np.float32)
        full_vis = np.zeros((1, T, N), dtype=bool)
        full_conf = np.zeros((1, T, N), dtype=np.float32)

        current_queries = queries_tensor
        start_idx = 0
        prev_end = 0

        while start_idx < T:
            # If the GPU cannot hold this window, halve it and try the same frames again
            # rather than aborting the whole job.
            while True:
                end_idx = min(start_idx + chunk, T)
                try:
                    c_tracks, c_vis, c_conf = _infer_single(
                        video_tensor[:, start_idx:end_idx], current_queries
                    )
                    break
                except Exception as exc:
                    if not _is_oom(exc) or chunk <= MIN_CHUNK:
                        raise
                    if device == "cuda":
                        torch.cuda.empty_cache()
                    chunk = max(MIN_CHUNK, chunk // 2)
                    overlap = min(overlap, chunk // 2)
                    if log:
                        log(f"   ! GPU ran out of memory - retrying with {chunk} frames "
                            f"per block.", "#e0a000")

            # Frames [start_idx, prev_end) were already written by the previous chunk and
            # only served as run-up context for the model, so skip them here.
            w_from = max(prev_end - start_idx, 0)
            abs_from = start_idx + w_from
            full_tracks[:, abs_from:end_idx] = c_tracks[:, w_from:end_idx - start_idx]
            full_vis[:, abs_from:end_idx] = c_vis[:, w_from:end_idx - start_idx]
            full_conf[:, abs_from:end_idx] = c_conf[:, w_from:end_idx - start_idx]

            prev_end = end_idx
            if end_idx >= T:
                break

            # Re-seed the next window `overlap` frames back, so the model has real context
            # before it reaches frames it has not seen yet.
            next_start = max(end_idx - overlap, start_idx + 1)
            seed_idx = next_start - start_idx
            seed_pos = c_tracks[0, seed_idx]  # [N, 2]
            new_q = np.zeros((1, N, 3), dtype=np.float32)
            new_q[0, :, 0] = 0.0  # relative to the new chunk start
            new_q[0, :, 1:] = seed_pos
            current_queries = torch.from_numpy(new_q)

            start_idx = next_start

        return full_tracks, full_vis, full_conf
    finally:
        capture.remove()


def filter_tracks_confidence(tracks, vis, conf=None, min_confidence=0.7, max_jump_distance=150.0):
    """
    Cleans up tracks: drops low-confidence samples, then suppresses jumping coordinates
    when a point is lost.

    tracks: [T, N, 2]
    vis:    [T, N] boolean visibility from the model
    conf:   [T, N] raw visibility probability in 0..1 (optional)
    """
    T, N, _ = tracks.shape
    cleaned_tracks = tracks.copy()
    cleaned_vis = np.asarray(vis).astype(bool).copy()

    # Apply the user's confidence threshold to the raw probabilities. Note this REPLACES
    # the model's own 0.9 cut rather than narrowing it, so lowering the setting really
    # does keep more samples and raising it keeps fewer.
    if conf is not None:
        conf_arr = np.asarray(conf, dtype=np.float32)
        if conf_arr.shape == cleaned_vis.shape and conf_arr.dtype.kind == "f":
            cleaned_vis = conf_arr >= float(min_confidence)

    for n in range(N):
        last_valid_x = cleaned_tracks[0, n, 0]
        last_valid_y = cleaned_tracks[0, n, 1]

        for t in range(1, T):
            if not cleaned_vis[t, n]:
                # Hold last valid position when point is occluded/lost
                cleaned_tracks[t, n, 0] = last_valid_x
                cleaned_tracks[t, n, 1] = last_valid_y
                continue

            dx = cleaned_tracks[t, n, 0] - last_valid_x
            dy = cleaned_tracks[t, n, 1] - last_valid_y
            dist = math.sqrt(dx * dx + dy * dy)

            # Detect impossible sudden velocity jumps
            if dist > max_jump_distance:
                cleaned_vis[t, n] = False
                cleaned_tracks[t, n, 0] = last_valid_x
                cleaned_tracks[t, n, 1] = last_valid_y
            else:
                last_valid_x = cleaned_tracks[t, n, 0]
                last_valid_y = cleaned_tracks[t, n, 1]

    return cleaned_tracks, cleaned_vis


# =============================================================================
# EXPORTERS: JSON, CSV, NUKE TRACKER, AFTER EFFECTS, BLENDER
# =============================================================================
def frame_number(t, start_frame=1, frame_step=1):
    """
    Maps a tracked sample index to the real timeline frame number.

    `start_frame` is the 1-based source frame the tracking range began on (the In
    point), and `frame_step` is the frame-skip used while loading. Without this the
    exports always claimed to start at frame 1, which shifted every track when an
    In point or a frame step was in use.
    """
    return int(start_frame) + int(t) * max(1, int(frame_step))


def export_2d_json(tracks_rescaled, vis, orig_w, orig_h, out_path, start_frame=1, frame_step=1):
    T, N, _ = tracks_rescaled.shape
    tracks_data = []

    for n in range(N):
        pt_track = {
            "track_id": n + 1,
            "total_frames": T,
            "frames": []
        }
        for t in range(T):
            x = float(tracks_rescaled[t, n, 0])
            y = float(tracks_rescaled[t, n, 1])
            v = bool(vis[t, n])
            pt_track["frames"].append({
                "frame": frame_number(t, start_frame, frame_step),
                "x": round(x, 2),
                "y": round(y, 2),
                "norm_x": round(x / orig_w, 5),
                "norm_y": round(y / orig_h, 5),
                "visible": v
            })
        tracks_data.append(pt_track)

    output = {
        "resolution": {"width": orig_w, "height": orig_h},
        "start_frame": int(start_frame),
        "frame_step": max(1, int(frame_step)),
        "frame_count": T,
        "track_count": N,
        "tracks": tracks_data
    }

    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2)


def export_2d_csv(tracks_rescaled, vis, orig_w, orig_h, out_path, start_frame=1, frame_step=1):
    T, N, _ = tracks_rescaled.shape
    lines = ["frame,track_id,x,y,norm_x,norm_y,visible\n"]
    for t in range(T):
        for n in range(N):
            x = float(tracks_rescaled[t, n, 0])
            y = float(tracks_rescaled[t, n, 1])
            v = 1 if vis[t, n] else 0
            lines.append(f"{frame_number(t, start_frame, frame_step)},{n+1},{x:.2f},{y:.2f},{x/orig_w:.5f},{y/orig_h:.5f},{v}\n")

    with open(out_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)


def export_2d_nuke_tracker(tracks_rescaled, vis, orig_w, orig_h, fps, out_path, node_name="CoTracker2D_Tracker", xpos=0, ypos=0, start_frame=1, frame_step=1):
    """
    Generates a native Nuke Tracker4 node with all tracks keyframed.
    
    Nuke Tracker4 tracks knob column order (31 total per track):
      Col 0: enable        — {curve K x<start> 1} or just 1
      Col 1: name          — "track_name"
      Col 2: track_x       — {curve x1 val1 x2 val2 ...}
      Col 3: track_y       — {curve x1 val1 x2 val2 ...}
      Col 4: offset_x      — {curve K x<start> 0} or 0
      Col 5: offset_y      — {curve K x<start> 0} or 0
      Col 6: T             — 1 (use translate)
      Col 7: R             — 0 (use rotate)
      Col 8: S             — 0 (use scale)
      Col 9: error         — {curve x<start> 0} or 0
      Col 10: error_min    — 0
      Col 11: error_max    — 0
      Col 12-15: pattern   — -15 -15 15 15  (pattern bbox)
      Col 16-19: search    — -25 -25 25 25  (search bbox)
      Col 20-30: reserved  — {} {} {} {} {} {} {} {} {} {} {}  (11 empty)
    """
    T, N, _ = tracks_rescaled.shape
    start_frame = int(start_frame)

    header = f'''set cut_paste_input [stack 0]
version 14.0 v1
push $cut_paste_input
Tracker4 {{
 tracks {{ {{ 1 31 {N} }}
  {{ '''

    rows = []
    for n in range(N):
        track_name = f"track_{n+1:03d}"
        x_curve_parts = []
        y_curve_parts = []
        for t in range(T):
            x = tracks_rescaled[t, n, 0]
            y = orig_h - tracks_rescaled[t, n, 1]  # Nuke Y is bottom-up
            fno = frame_number(t, start_frame, frame_step)
            x_curve_parts.append(f"x{fno} {x:.2f}")
            y_curve_parts.append(f"x{fno} {y:.2f}")

        x_curve = " ".join(x_curve_parts)
        y_curve = " ".join(y_curve_parts)
        empty_slots = " ".join(["{}"] * 11)
        # Correct Nuke Tracker4 row: 31 fields in exact order
        row = (
            f'{{ {{curve K x{start_frame} 1}} '     # Col 0: enable
            f'"{track_name}" '                        # Col 1: name
            f'{{curve {x_curve}}} '                   # Col 2: track_x
            f'{{curve {y_curve}}} '                   # Col 3: track_y
            f'{{curve K x{start_frame} 0}} '          # Col 4: offset_x
            f'{{curve K x{start_frame} 0}} '          # Col 5: offset_y
            f'1 0 0 '                                 # Col 6,7,8: T R S
            f'{{curve x{start_frame} 0}} '            # Col 9: error
            f'0 0 '                                   # Col 10,11: error_min/max
            f'-15 -15 15 15 '                         # Col 12-15: pattern bbox
            f'-25 -25 25 25 '                         # Col 16-19: search bbox
            f'{empty_slots} }}'                       # Col 20-30: 11 reserved
        )
        rows.append(row)

    body = "\n    ".join(rows)
    footer = f''' }}
 }}
 name {node_name}
 selected true
 xpos {xpos}
 ypos {ypos}
}}
'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(header + body + footer)
    return True


def export_multi_layer_nuke_tracker(layers_data, orig_w, orig_h, fps, out_path, start_frame=1, frame_step=1):
    """
    Generates a master Nuke script containing individual Tracker4 nodes for each layer.
    layers_data: list of dicts {"name": str, "tracks": np.ndarray, "vis": np.ndarray}
    """
    script = '''set cut_paste_input [stack 0]
version 14.0 v1
'''
    x_offset = 0
    start_frame = int(start_frame)
    for idx, ldata in enumerate(layers_data):
        l_name = ldata.get("name", f"Layer_{idx+1}").replace(" ", "_")
        tracks = ldata["tracks"]
        T, N, _ = tracks.shape
        script += f'''push $cut_paste_input
Tracker4 {{
 tracks {{ {{ 1 31 {N} }}
  {{ '''
        rows = []
        for n in range(N):
            track_name = f"{l_name}_{n+1:03d}"
            x_curve_parts = []
            y_curve_parts = []
            for t in range(T):
                x = tracks[t, n, 0]
                y = orig_h - tracks[t, n, 1]
                fno = frame_number(t, start_frame, frame_step)
                x_curve_parts.append(f"x{fno} {x:.2f}")
                y_curve_parts.append(f"x{fno} {y:.2f}")
            x_curve = " ".join(x_curve_parts)
            y_curve = " ".join(y_curve_parts)
            empty_slots = " ".join(["{}"] * 11)
            row = (
                f'{{ {{curve K x{start_frame} 1}} '
                f'"{track_name}" '
                f'{{curve {x_curve}}} '
                f'{{curve {y_curve}}} '
                f'{{curve K x{start_frame} 0}} '
                f'{{curve K x{start_frame} 0}} '
                f'1 0 0 '
                f'{{curve x{start_frame} 0}} '
                f'0 0 '
                f'-15 -15 15 15 '
                f'-25 -25 25 25 '
                f'{empty_slots} }}'
            )
            rows.append(row)
        script += "\n    ".join(rows)
        script += f''' }}
 }}
 name Tracker_{l_name}
 label "Layer: {l_name}"
 selected true
 xpos {x_offset}
 ypos 0
}}
'''
        x_offset += 150

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


# =============================================================================
# 4-POINT CORNERPIN SOLVERS: NUKE, AFTER EFFECTS, BLENDER
# =============================================================================
def export_2d_cornerpin_nuke(tracks_rescaled, orig_w, orig_h, fps, out_path, start_frame=1, frame_step=1):
    """
    Generates a 1-click Nuke CornerPin2D node.
    Corners: 1=Top-Left, 2=Top-Right, 3=Bottom-Right, 4=Bottom-Left
    """
    T, N, _ = tracks_rescaled.shape
    if N < 4:
        return False

    def get_curve(corner_idx):
        x_parts = []
        y_parts = []
        for t in range(T):
            x = tracks_rescaled[t, corner_idx, 0]
            y = orig_h - tracks_rescaled[t, corner_idx, 1]
            fno = frame_number(t, start_frame, frame_step)
            x_parts.append(f"x{fno} {x:.2f}")
            y_parts.append(f"x{fno} {y:.2f}")
        return " ".join(x_parts), " ".join(y_parts)

    # Standard Nuke CornerPin2D pin layout:
    # 1 = Bottom-Left (pt 3), 2 = Bottom-Right (pt 2), 3 = Top-Right (pt 1), 4 = Top-Left (pt 0)
    to1_x, to1_y = get_curve(3)
    to2_x, to2_y = get_curve(2)
    to3_x, to3_y = get_curve(1)
    to4_x, to4_y = get_curve(0)

    script = f'''set cut_paste_input [stack 0]
version 14.0 v1
push $cut_paste_input
CornerPin2D {{
 to1 {{{{curve {to1_x}}}}} {{{{curve {to1_y}}}}}
 to2 {{{{curve {to2_x}}}}} {{{{curve {to2_y}}}}}
 to3 {{{{curve {to3_x}}}}} {{{{curve {to3_y}}}}}
 to4 {{{{curve {to4_x}}}}} {{{{curve {to4_y}}}}}
 from1 {{0 0}}
 from2 {{{orig_w} 0}}
 from3 {{{orig_w} {orig_h}}}
 from4 {{0 {orig_h}}}
 name CoTracker_CornerPin2D
 selected true
 xpos 0
 ypos 0
}}
'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def export_2d_cornerpin_ae(tracks_rescaled, orig_w, orig_h, fps, out_path, start_frame=1, frame_step=1):
    """Generates an After Effects ExtendScript applying Corner Pin to an insert layer."""
    T, N, _ = tracks_rescaled.shape
    if N < 4:
        return False

    script = f'''// Adobe After Effects ExtendScript - CoTracker 4-Point Corner Pin
(function() {{
    app.beginUndoGroup("Apply CoTracker Corner Pin");
    var comp = app.project.activeItem;
    if (!comp || !(comp instanceof CompItem)) {{
        comp = app.project.items.addComp("CoTracker_Screen_Replacement", {orig_w}, {orig_h}, 1.0, {T/fps:.2f}, {fps});
    }}

    var solid = comp.layers.addSolid([0.2, 0.6, 1.0], "Screen_Insert", {orig_w}, {orig_h}, 1.0);
    var cornerPin = solid.property("Effects").addProperty("Corner Pin");

    var to1 = cornerPin.property("Upper Left");
    var to2 = cornerPin.property("Upper Right");
    var to3 = cornerPin.property("Lower Right");
    var to4 = cornerPin.property("Lower Left");

'''
    for t in range(T):
        time_sec = (frame_number(t, start_frame, frame_step) - 1) / fps
        x1, y1 = tracks_rescaled[t, 0, 0], tracks_rescaled[t, 0, 1]
        x2, y2 = tracks_rescaled[t, 1, 0], tracks_rescaled[t, 1, 1]
        x3, y3 = tracks_rescaled[t, 2, 0], tracks_rescaled[t, 2, 1]
        x4, y4 = tracks_rescaled[t, 3, 0], tracks_rescaled[t, 3, 1]
        script += f'    to1.setValueAtTime({time_sec:.4f}, [{x1:.2f}, {y1:.2f}]);\n'
        script += f'    to2.setValueAtTime({time_sec:.4f}, [{x2:.2f}, {y2:.2f}]);\n'
        script += f'    to3.setValueAtTime({time_sec:.4f}, [{x3:.2f}, {y3:.2f}]);\n'
        script += f'    to4.setValueAtTime({time_sec:.4f}, [{x4:.2f}, {y4:.2f}]);\n'

    script += '''
    app.endUndoGroup();
    alert("✔ Successfully created animated Corner Pin screen replacement layer in After Effects!");
})();
'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def export_2d_cornerpin_blender(tracks_rescaled, orig_w, orig_h, fps, out_path, start_frame=1, frame_step=1):
    """Generates a Blender script creating an animated 4-point Quad mesh."""
    T, N, _ = tracks_rescaled.shape
    if N < 4:
        return False

    script = f'''"""
1-CLICK BLENDER 4-POINT CORNERPIN / SCREEN REPLACEMENT QUAD
Creates a 4-vertex Quad plane mesh animated to match the tracked screen corners.
"""
import bpy

def setup_cornerpin_mesh():
    scene = bpy.context.scene
    scene.frame_start = {frame_number(0, start_frame, frame_step)}
    scene.frame_end = {frame_number(T - 1, start_frame, frame_step)}
    scene.render.fps = {int(round(fps))}
    scene.render.fps_base = {round(int(round(fps)) / fps, 6) if fps else 1.0}

    mesh_name = "Tracked_Screen_Mesh"
    obj_name = "Tracked_Screen_Surface"

    mesh = bpy.data.meshes.get(mesh_name) or bpy.data.meshes.new(mesh_name)
    verts = [(-1, 1, 0), (1, 1, 0), (1, -1, 0), (-1, -1, 0)]
    faces = [(0, 1, 2, 3)]
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    obj = bpy.data.objects.get(obj_name)
    if not obj:
        obj = bpy.data.objects.new(obj_name, mesh)
        scene.collection.objects.link(obj)

    # Animate 4 vertices as shape keys per frame
    sk_basis = obj.shape_key_add(name="Basis")
'''
    step_n = max(1, int(frame_step))
    for t in range(T):
        f = frame_number(t, start_frame, frame_step)
        coords = []
        for idx in range(4):
            x_norm = (tracks_rescaled[t, idx, 0] / orig_w - 0.5) * 2.0
            y_norm = -(tracks_rescaled[t, idx, 1] / orig_h - 0.5) * 2.0 * (orig_h / orig_w)
            coords.append([x_norm, y_norm, 0.0])

        script += f'''
    sk_{f} = obj.shape_key_add(name="Frame_{f}")
    for v_i, pt in enumerate({coords}):
        sk_{f}.data[v_i].co = pt
    sk_{f}.value = 1.0
    sk_{f}.keyframe_insert(data_path="value", frame={f})
    if {t} > 0:
        sk_{f}.value = 0.0
        sk_{f}.keyframe_insert(data_path="value", frame={f - step_n})
    if {t} < {T - 1}:
        sk_{f}.value = 0.0
        sk_{f}.keyframe_insert(data_path="value", frame={f + step_n})
'''

    script += '''
    print("✔ Tracked screen replacement quad mesh created successfully in Blender!")

if __name__ == "__main__":
    setup_cornerpin_mesh()
'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def export_2d_after_effects_jsx(tracks_rescaled, vis, orig_w, orig_h, fps, out_path, start_frame=1, frame_step=1):
    T, N, _ = tracks_rescaled.shape
    script = f'''// Adobe After Effects ExtendScript - CoTracker 2D Tracks Importer
(function() {{
    app.beginUndoGroup("Import CoTracker 2D Tracks");
    var comp = app.project.activeItem;
    if (!comp || !(comp instanceof CompItem)) {{
        comp = app.project.items.addComp("CoTracker_2D_Comp", {orig_w}, {orig_h}, 1.0, {T/fps:.2f}, {fps});
    }}

    var trackCount = {N};
    var frameCount = {T};
    var fps = {fps};

'''
    for n in range(N):
        track_name = f"Track_{n+1:03d}"
        script += f'''    var nullLayer_{n} = comp.layers.addNull();
    nullLayer_{n}.name = "{track_name}";
    var posProp_{n} = nullLayer_{n}.property("Position");
'''
        for t in range(T):
            x = float(tracks_rescaled[t, n, 0])
            y = float(tracks_rescaled[t, n, 1])
            script += f'    posProp_{n}.setValueAtTime({frame_number(t, start_frame, frame_step) - 1}/fps, [{x:.2f}, {y:.2f}, 0]);\n'

    script += '''
    app.endUndoGroup();
    alert("✔ Successfully created " + trackCount + " tracked Null layers in After Effects!");
})();
'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)


def export_2d_blender_empties(tracks_rescaled, vis, orig_w, orig_h, fps, out_path, images_dir=None, collection_name="CoTracker_2D_Tracks", start_frame=1, frame_step=1):
    T, N, _ = tracks_rescaled.shape
    images_dir_str = str(images_dir).replace('\\', '/') if images_dir else ""

    script = f'''"""
=============================================================================
1-CLICK BLENDER 2D POINT TRACKS & REFERENCE CAMERA IMPORTER
Creates an aligned Reference Camera + Video Background + Keyframed Track Empties.

HOW TO USE:
1. Open Blender -> Go to Scripting tab (Text Editor).
2. Open this script and click Run Script (Alt+P).
3. Press Numpad 0 to look through the Camera!
=============================================================================
"""
import bpy
import os

def import_2d_tracks():
    scene = bpy.context.scene
    scene.render.resolution_x = {orig_w}
    scene.render.resolution_y = {orig_h}
    scene.frame_start = {frame_number(0, start_frame, frame_step)}
    scene.frame_end = {frame_number(T - 1, start_frame, frame_step)}
    scene.render.fps = {int(round(fps))}
    scene.render.fps_base = {round(int(round(fps)) / fps, 6) if fps else 1.0}

    col_name = "{collection_name}"
    col = bpy.data.collections.get(col_name) or bpy.data.collections.new(col_name)
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)

    # 1. Create Reference Camera matching footage aspect ratio
    cam_name = "Camera_2D_Viewer"
    cam_data = bpy.data.cameras.get("Camera_2D_Data") or bpy.data.cameras.new("Camera_2D_Data")
    cam_data.sensor_width = 36.0
    cam_data.lens = 50.0
    cam_data.display_size = 0.5
    cam_data.show_background_images = True

    images_folder = r"{images_dir_str}"
    if images_folder and os.path.exists(images_folder):
        frame_files = sorted([f for f in os.listdir(images_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
        if frame_files:
            bg = cam_data.background_images.new()
            try:
                img_data = bpy.data.images.load(os.path.join(images_folder, frame_files[0]), check_existing=True)
                img_data.source = 'SEQUENCE'
                bg.image = img_data
                bg.image_user.frame_duration = len(frame_files)
                bg.image_user.frame_start = 1
                bg.image_user.use_auto_refresh = True
                bg.alpha = 0.85
                bg.display_depth = 'BACK'
            except Exception:
                pass

    cam_obj = bpy.data.objects.get(cam_name)
    if not cam_obj:
        cam_obj = bpy.data.objects.new(cam_name, cam_data)
        scene.collection.objects.link(cam_obj)

    cam_obj.location = (0.0, 0.0, 2.7475)
    cam_obj.rotation_euler = (0.0, 0.0, 0.0)
    scene.camera = cam_obj

    # 2. Create Keyframed 2D Track Markers
    aspect = {orig_h} / {orig_w}
    for n in range({N}):
        track_name = f"Track_2D_{{n+1:03d}}"
        empty_obj = bpy.data.objects.get(track_name)
        if not empty_obj:
            empty_obj = bpy.data.objects.new(track_name, None)
            empty_obj.empty_display_type = 'PLAIN_AXES'
            empty_obj.empty_display_size = 0.1
            col.objects.link(empty_obj)

'''
    for n in range(N):
        script += f'''        if n == {n}:
'''
        for t in range(T):
            x_norm = (tracks_rescaled[t, n, 0] / orig_w - 0.5) * 2.0
            y_norm = -(tracks_rescaled[t, n, 1] / orig_h - 0.5) * 2.0 * (orig_h / orig_w)
            script += f'''            empty_obj.location = ({x_norm:.4f}, {y_norm:.4f}, 0.0)
            empty_obj.keyframe_insert(data_path="location", frame={frame_number(t, start_frame, frame_step)})
'''

    script += '''
    print("✔ 2D Tracks & Reference Camera created successfully in Blender! Press Numpad 0 for camera view.")

if __name__ == "__main__":
    import_2d_tracks()
'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def export_multi_layer_blender(layers_data, orig_w, orig_h, fps, out_path, images_dir=None, start_frame=1, frame_step=1):
    """
    Generates a master Blender script creating separate collections for each tracking layer.
    """
    images_dir_str = str(images_dir).replace('\\', '/') if images_dir else ""
    T_max = max(ldata["tracks"].shape[0] for ldata in layers_data) if layers_data else 1

    script = f'''"""
=============================================================================
1-CLICK BLENDER MULTI-LAYER 2D POINT TRACKS & REFERENCE CAMERA IMPORTER
Creates Reference Camera + Video Background + Layer Collections with Empties.

HOW TO USE:
1. Open Blender -> Go to Scripting tab (Text Editor).
2. Open this script and click Run Script (Alt+P).
3. Press Numpad 0 to look through the Camera!
=============================================================================
"""
import bpy
import os

def import_multi_layer_tracks():
    scene = bpy.context.scene
    scene.render.resolution_x = {orig_w}
    scene.render.resolution_y = {orig_h}
    scene.frame_start = {frame_number(0, start_frame, frame_step)}
    scene.frame_end = {frame_number(T_max - 1, start_frame, frame_step)}
    scene.render.fps = {int(round(fps))}
    scene.render.fps_base = {round(int(round(fps)) / fps, 6) if fps else 1.0}

    # 1. Setup Reference Camera
    cam_name = "Camera_2D_Viewer"
    cam_data = bpy.data.cameras.get("Camera_2D_Data") or bpy.data.cameras.new("Camera_2D_Data")
    cam_data.sensor_width = 36.0
    cam_data.lens = 50.0
    cam_data.display_size = 0.5
    cam_data.show_background_images = True

    images_folder = r"{images_dir_str}"
    if images_folder and os.path.exists(images_folder):
        frame_files = sorted([f for f in os.listdir(images_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
        if frame_files:
            bg = cam_data.background_images.new()
            try:
                img_data = bpy.data.images.load(os.path.join(images_folder, frame_files[0]), check_existing=True)
                img_data.source = 'SEQUENCE'
                bg.image = img_data
                bg.image_user.frame_duration = len(frame_files)
                bg.image_user.frame_start = 1
                bg.image_user.use_auto_refresh = True
                bg.alpha = 0.85
                bg.display_depth = 'BACK'
            except Exception:
                pass

    cam_obj = bpy.data.objects.get(cam_name)
    if not cam_obj:
        cam_obj = bpy.data.objects.new(cam_name, cam_data)
        scene.collection.objects.link(cam_obj)

    cam_obj.location = (0.0, 0.0, 2.7475)
    cam_obj.rotation_euler = (0.0, 0.0, 0.0)
    scene.camera = cam_obj

    # 2. Populate Collections for each Layer
'''
    for l_idx, ldata in enumerate(layers_data):
        l_name = ldata.get("name", f"Layer_{l_idx+1}").replace(" ", "_")
        tracks = ldata["tracks"]
        T, N, _ = tracks.shape
        script += f'''
    # --- Layer: {l_name} ---
    col_{l_idx} = bpy.data.collections.get("Layer_{l_name}") or bpy.data.collections.new("Layer_{l_name}")
    if col_{l_idx}.name not in scene.collection.children:
        scene.collection.children.link(col_{l_idx})

'''
        for n in range(N):
            track_name = f"{l_name}_Track_{n+1:03d}"
            script += f'''    empty_{l_idx}_{n} = bpy.data.objects.get("{track_name}") or bpy.data.objects.new("{track_name}", None)
    empty_{l_idx}_{n}.empty_display_type = 'PLAIN_AXES'
    empty_{l_idx}_{n}.empty_display_size = 0.1
    if empty_{l_idx}_{n}.name not in col_{l_idx}.objects:
        col_{l_idx}.objects.link(empty_{l_idx}_{n})
'''
            for t in range(T):
                x_norm = (tracks[t, n, 0] / orig_w - 0.5) * 2.0
                y_norm = -(tracks[t, n, 1] / orig_h - 0.5) * 2.0 * (orig_h / orig_w)
                script += f'''    empty_{l_idx}_{n}.location = ({x_norm:.4f}, {y_norm:.4f}, 0.0)
    empty_{l_idx}_{n}.keyframe_insert(data_path="location", frame={frame_number(t, start_frame, frame_step)})
'''

    script += '''
    print("✔ All tracking layers & Reference Camera loaded into Blender successfully! Press Numpad 0.")

if __name__ == "__main__":
    import_multi_layer_tracks()
'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def render_track_overlay_video(proc_frames_np, tracks_proc, vis, out_mp4_path, fps=24, trail_len=15):
    """Renders an MP4 video with glowing colorful trails showing the 2D point trajectories."""
    import imageio

    T, H, W, C = proc_frames_np.shape
    N = tracks_proc.shape[1]

    colors = [
        tuple(int(c * 255) for c in colorsys.hsv_to_rgb(i / max(1, N), 0.9, 1.0))
        for i in range(N)
    ]

    rendered_frames = []
    for t in range(T):
        base_img = Image.fromarray(proc_frames_np[t]).copy()
        draw = ImageDraw.Draw(base_img)

        for n in range(N):
            col = colors[n]
            tail_start = max(0, t - trail_len)
            pts = []
            for prev_t in range(tail_start, t + 1):
                if vis[prev_t, n]:
                    px = int(tracks_proc[prev_t, n, 0])
                    py = int(tracks_proc[prev_t, n, 1])
                    pts.append((px, py))

            if len(pts) > 1:
                draw.line(pts, fill=col, width=2)

            if vis[t, n]:
                cur_x = int(tracks_proc[t, n, 0])
                cur_y = int(tracks_proc[t, n, 1])
                draw.ellipse([cur_x-3, cur_y-3, cur_x+3, cur_y+3], fill=col, outline=(255, 255, 255))

        rendered_frames.append(np.array(base_img))

    imageio.mimsave(str(out_mp4_path), rendered_frames, fps=fps, quality=8)
    return True


def render_multi_layer_overlay_video(proc_frames_np, layer_results, out_mp4_path, fps=24, trail_len=15):
    """
    Renders video with colorful trails for each layer using that layer's distinct theme color.
    """
    import imageio

    T, H, W, C = proc_frames_np.shape
    rendered_frames = []

    def hex_to_rgb(h):
        if isinstance(h, (tuple, list)):
            return tuple(int(x) for x in h[:3])
        h = str(h).lstrip('#')
        if len(h) == 6:
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
        return (0, 210, 255)

    for t in range(T):
        base_img = Image.fromarray(proc_frames_np[t]).copy()
        draw = ImageDraw.Draw(base_img)

        for ldata in layer_results:
            tracks = ldata["tracks_proc"]
            vis = ldata["vis"]
            N = tracks.shape[1]
            l_col = ldata.get("color", "#00d2ff")
            base_rgb = hex_to_rgb(l_col)

            for n in range(N):
                tail_start = max(0, t - trail_len)
                pts = []
                for prev_t in range(tail_start, t + 1):
                    if vis[prev_t, n]:
                        px = int(tracks[prev_t, n, 0])
                        py = int(tracks[prev_t, n, 1])
                        pts.append((px, py))

                if len(pts) > 1:
                    draw.line(pts, fill=base_rgb, width=2)

                if vis[t, n]:
                    cur_x = int(tracks[t, n, 0])
                    cur_y = int(tracks[t, n, 1])
                    draw.ellipse([cur_x-3, cur_y-3, cur_x+3, cur_y+3], fill=base_rgb, outline=(255, 255, 255))

        rendered_frames.append(np.array(base_img))

    imageio.mimsave(str(out_mp4_path), rendered_frames, fps=fps, quality=8)
    return True


# =============================================================================
# MAIN ORCHESTRATION PIPELINE
# =============================================================================
def process_cotracker_2d(video_path, config=None, progress_callback=None, log_callback=None):
    """
    Full end-to-end 2D tracking pipeline with VRAM chunking, masking, and CornerPin support.
    """
    if config is None:
        config = {}

    def log(msg, color="#ffffff"):
        if log_callback:
            log_callback(msg, color)
        else:
            try:
                print(msg)
            except UnicodeEncodeError:
                safe_msg = msg.replace("▶", ">").replace("✔", "[OK]").replace("✖", "[ERROR]").replace("🎉", "[SUCCESS]").replace("🎬", "")
                print(safe_msg.encode('ascii', errors='replace').decode('ascii'))

    def prog(val, text):
        if progress_callback:
            progress_callback(val, text)

    import datetime
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    video_path = Path(video_path)
    base_name = video_path.stem
    scene_dir = BASE_DIR / "04 SCENES" / base_name
    if config.get("output_dir"):
        out_dir = Path(config["output_dir"])
    else:
        out_dir = scene_dir / "2D_POINT_TRACK" / timestamp
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"▶ [1/4] Loading and preparing video frames for '{video_path.name}'...", "#00d2ff")
    prog(10, "Loading video frames...")

    max_dim = config.get("max_dimension", 720)
    step = config.get("frame_step", 1)
    in_pt = config.get("in_point", 0)
    out_pt = config.get("out_point", -1)
    step = max(1, int(step))
    in_pt = max(0, int(in_pt))
    # Frame number the exported curves start on. timeline_start is the frame the
    # clip's first frame sits on in the edit (1 unless the shot is numbered from
    # something else, e.g. a 1001-1200 plate); the In point is then an offset
    # within it.
    timeline_start = int(config.get("timeline_start", 1) or 1)
    export_start_frame = timeline_start + in_pt
    frames_np, (orig_w, orig_h) = load_video_frames(video_path, max_dimension=max_dim, frame_step=step, in_point=in_pt, out_point=out_pt)

    T, proc_h, proc_w, _ = frames_np.shape
    scale_x = orig_w / proc_w
    scale_y = orig_h / proc_h

    log(f"✔ Loaded {T} frames ({orig_w}x{orig_h} resized to {proc_w}x{proc_h} for AI processing).", "#00ff88")

    device = get_default_device()
    alloc_mb, total_mb, gpu_name = get_gpu_memory_info()
    fp_mode = "FP16 Autocast Active" if (device == "cuda" and is_fp16_supported()) else "FP32 Precision"
    if device == "cuda":
        log(f"⚡ GPU: {gpu_name} (VRAM: {alloc_mb:.0f}/{total_mb:.0f} MB, {fp_mode})", "#00d2ff")

    # The clip stays on the CPU as uint8. run_cotracker_chunked() uploads one chunk at a
    # time and converts it to float32 there. Uploading the whole clip as float32 (the old
    # behaviour) needed ~7.3 GB of VRAM for a 708-frame 720p shot and blew up on 'Original'.
    video_tensor = torch.from_numpy(frames_np).permute(0, 3, 1, 2)[None].contiguous()
    auto_chunk = bool(config.get("auto_chunk", True))
    est_gb = (T * 3 * proc_h * proc_w * 4) / (1024 ** 3)
    if device == "cuda":
        if auto_chunk:
            log(f"   Auto VRAM Chunking ON (full clip would need {est_gb:.1f} GB of VRAM in one pass).", "#a0a0b0")
        else:
            log(f"   ! Auto VRAM Chunking is OFF - the whole clip ({est_gb:.1f} GB) is sent to the "
                f"GPU at once and may run out of memory.", "#e0a000")

    mode = config.get("mode", "grid")
    offline = config.get("offline", True)
    fps = config.get("fps", 24.0)

    # Normalize to layers list
    raw_layers = config.get("layers")
    if not raw_layers:
        raw_layers = [{
            "name": "Layer_01",
            "mode": config.get("mode", "grid"),
            "grid_size": config.get("grid_size", 10),
            "query_points": config.get("query_points"),
            "inclusion_masks": config.get("inclusion_masks"),
            "exclusion_masks": config.get("exclusion_masks"),
            "inclusion_box": config.get("inclusion_box"),
            "exclusion_box": config.get("exclusion_box"),
            "min_confidence": config.get("min_confidence", 0.7),
            "export_cornerpin": config.get("export_cornerpin", False),
            "color": config.get("color", "#00d2ff")
        }]

    is_multi_layer = len(raw_layers) > 1
    log(f"▶ [2/4] Initializing CoTracker3 AI on GPU for {len(raw_layers)} tracking layer(s)...", "#00d2ff")
    prog(30, "Loading AI model...")

    ckpt_name = "scaled_offline.pth" if offline else "scaled_online.pth"
    ckpt_path = COTRACKER_DIR / "checkpoints" / ckpt_name

    model = CoTrackerPredictor(
        checkpoint=str(ckpt_path),
        offline=offline,
        window_len=60 if offline else 16
    ).to(device)

    # Frame-numbering bundle handed to every exporter.
    fr = {"start_frame": export_start_frame, "frame_step": step}

    layers_results = []
    total_points = 0

    for l_idx, lconf in enumerate(raw_layers):
        l_name = lconf.get("name", f"Layer_{l_idx+1}").replace(" ", "_")
        l_mode = lconf.get("mode", "grid")
        l_min_conf = lconf.get("min_confidence", 0.7)
        l_col = lconf.get("color", "#00d2ff")

        # Read once, for every mode. This used to live inside the grid branch only, so a
        # 'points' or 'cornerpin' layer hit an unbound local further down and the run died.
        anim_masks = lconf.get("animated_masks")

        # Build queries for this layer
        if (l_mode == "points" or l_mode == "cornerpin") and lconf.get("query_points"):
            raw_queries = lconf["query_points"]
            proc_queries = []
            dropped = 0
            for q in raw_queries:
                t, x, y = q
                # Points carry the absolute timeline frame they were clicked on, but the
                # loaded clip starts at the In point and may skip frames - rebase them.
                t_local = (int(t) - in_pt) / float(step)
                if t_local < 0 or t_local > (T - 1):
                    dropped += 1
                    continue
                proc_queries.append([int(round(t_local)), x / scale_x, y / scale_y])
            if dropped:
                log(f"   ! [{l_name}] {dropped} manual point(s) sit outside the In/Out range "
                    f"and were skipped.", "#e0a000")
            if not proc_queries:
                raise ValueError(
                    f"Layer '{l_name}' has no manual points inside the tracking range "
                    f"(In point frame {in_pt + 1}). Move the In point or place points inside it."
                )
            queries_list = proc_queries
        else:
            g_size = lconf.get("grid_size", 10)

            if anim_masks:
                from mask_animator import AnimatedMask
                inc_f0 = []
                exc_f0 = []
                for m in anim_masks:
                    m_obj = m if isinstance(m, AnimatedMask) else AnimatedMask.from_dict(m)
                    g0 = m_obj.get_interpolated_geometry(0)
                    if g0:
                        if m_obj.category == "inclusion":
                            inc_f0.append(scale_mask(g0, scale_x, scale_y))
                        else:
                            exc_f0.append(scale_mask(g0, scale_x, scale_y))
                queries_list = generate_grid_points_with_masks(
                    proc_h, proc_w,
                    grid_size=g_size,
                    inclusion_masks=inc_f0 if inc_f0 else None,
                    exclusion_masks=exc_f0 if exc_f0 else None
                )
            else:
                inc_masks = lconf.get("inclusion_masks")
                exc_masks = lconf.get("exclusion_masks")
                inc_box = lconf.get("inclusion_box")
                exc_box = lconf.get("exclusion_box")

                p_inc_masks = [scale_mask(m, scale_x, scale_y) for m in inc_masks] if inc_masks else None
                p_exc_masks = [scale_mask(m, scale_x, scale_y) for m in exc_masks] if exc_masks else None
                p_inc_box = scale_mask(inc_box, scale_x, scale_y) if inc_box else None
                p_exc_box = scale_mask(exc_box, scale_x, scale_y) if exc_box else None

                queries_list = generate_grid_points_with_masks(
                    proc_h, proc_w,
                    grid_size=g_size,
                    inclusion_masks=p_inc_masks,
                    exclusion_masks=p_exc_masks,
                    inclusion_box=p_inc_box,
                    exclusion_box=p_exc_box
                )

        q_tensor = torch.tensor(queries_list, dtype=torch.float32)[None]
        N_layer = q_tensor.shape[1]
        total_points += N_layer

        log(f"   ▶ Tracking Layer [{l_name}] ({N_layer} points, mode: {l_mode})...", "#00d2ff")
        prog(35 + int((l_idx / len(raw_layers)) * 35), f"Tracking Layer {l_name} ({N_layer} pts)...")

        pred_tracks, pred_vis, pred_conf = run_cotracker_chunked(
            model, video_tensor, q_tensor, chunk_size=120, overlap=30,
            device=device, auto_chunk=auto_chunk, log=log
        )
        l_tracks_proc = pred_tracks[0]
        l_vis = pred_vis[0]
        l_conf = pred_conf[0]

        l_tracks_proc, l_vis = filter_tracks_confidence(
            l_tracks_proc, l_vis, conf=l_conf, min_confidence=l_min_conf
        )
        log(f"   [{l_name}] Confidence >= {l_min_conf:.2f}: "
            f"{int(l_vis.sum())}/{l_vis.size} samples kept.", "#a0a0b0")

        l_tracks_rescaled = l_tracks_proc.copy()
        l_tracks_rescaled[:, :, 0] *= scale_x
        l_tracks_rescaled[:, :, 1] *= scale_y

        # Dynamic trajectory culling against animated masks over time
        if anim_masks:
            l_tracks_rescaled, l_vis = filter_trajectories_by_animated_masks(l_tracks_rescaled, l_vis, anim_masks)

        # Per-layer export directory
        if is_multi_layer:
            layer_dir = out_dir / l_name
            layer_dir.mkdir(parents=True, exist_ok=True)
            export_2d_json(l_tracks_rescaled, l_vis, orig_w, orig_h, layer_dir / "tracks_2d.json", **fr)
            export_2d_csv(l_tracks_rescaled, l_vis, orig_w, orig_h, layer_dir / "tracks_2d_csv.csv", **fr)
            export_2d_nuke_tracker(l_tracks_rescaled, l_vis, orig_w, orig_h, fps, layer_dir / "tracks_2d_nuke.nk", node_name=f"Tracker_{l_name}", **fr)
            export_2d_after_effects_jsx(l_tracks_rescaled, l_vis, orig_w, orig_h, fps, layer_dir / "tracks_2d_ae.jsx", **fr)
            export_2d_blender_empties(l_tracks_rescaled, l_vis, orig_w, orig_h, fps, layer_dir / "tracks_2d_blender.py", images_dir=scene_dir / "images", collection_name=f"Layer_{l_name}", **fr)

            if N_layer == 4 or lconf.get("export_cornerpin", False):
                export_2d_cornerpin_nuke(l_tracks_rescaled, orig_w, orig_h, fps, layer_dir / "tracks_2d_cornerpin_nuke.nk", **fr)
                export_2d_cornerpin_ae(l_tracks_rescaled, orig_w, orig_h, fps, layer_dir / "tracks_2d_cornerpin_ae.jsx", **fr)
                export_2d_cornerpin_blender(l_tracks_rescaled, orig_w, orig_h, fps, layer_dir / "tracks_2d_cornerpin_blender.py", **fr)

        layers_results.append({
            "name": l_name,
            "tracks": l_tracks_rescaled,
            "tracks_proc": l_tracks_proc,
            "vis": l_vis,
            "color": l_col,
            "point_count": N_layer,
            "export_cornerpin": (N_layer == 4) or lconf.get("export_cornerpin", False)
        })

    log(f"✔ Solved and filtered trajectories for all {total_points} points across {T} frames!", "#00ff88")
    prog(75, "Generating Master VFX Exporters...")
    log("▶ [3/4] Generating VFX 2D Tracker Formats (Nuke, AE, Blender, JSON, CSV)...", "#00d2ff")

    # Primary / Master Combined Exports
    json_path = out_dir / "tracks_2d.json"
    csv_path = out_dir / "tracks_2d_csv.csv"
    nuke_path = out_dir / "tracks_2d_nuke.nk"
    ae_path = out_dir / "tracks_2d_ae.jsx"
    blender_path = out_dir / "tracks_2d_blender.py"
    cornerpin_nuke_path = None
    cornerpin_ae_path = None
    cornerpin_blender_path = None

    if is_multi_layer:
        # Multi-layer combined files
        export_multi_layer_nuke_tracker(layers_results, orig_w, orig_h, fps, nuke_path, **fr)
        log(f"   ✔ Generated Multi-Layer Nuke Tracker: {nuke_path.name}", "#00ff88")

        export_multi_layer_blender(layers_results, orig_w, orig_h, fps, blender_path, images_dir=scene_dir / "images", **fr)
        log(f"   ✔ Generated Multi-Layer Blender Script: {blender_path.name}", "#00ff88")

        # Master combined JSON
        all_tracks_dict = {
            "metadata": {"video": video_path.name, "width": orig_w, "height": orig_h, "frames": T, "fps": fps, "start_frame": export_start_frame, "frame_step": step, "layers": len(layers_results)},
            "layers": {}
        }
        for ldata in layers_results:
            l_name = ldata["name"]
            tr = ldata["tracks"]
            vi = ldata["vis"]
            all_tracks_dict["layers"][l_name] = {
                "point_count": ldata["point_count"],
                "points": {
                    f"track_{n+1:03d}": [
                        {"frame": frame_number(t, export_start_frame, step), "x": round(float(tr[t, n, 0]), 2), "y": round(float(tr[t, n, 1]), 2), "visible": bool(vi[t, n])}
                        for t in range(T)
                    ]
                    for n in range(ldata["point_count"])
                }
            }
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(all_tracks_dict, f, indent=2)
        log(f"   ✔ Generated Multi-Layer JSON: {json_path.name}", "#00ff88")

    else:
        # Single layer flat files
        single_l = layers_results[0]
        export_2d_json(single_l["tracks"], single_l["vis"], orig_w, orig_h, json_path, **fr)
        log(f"   ✔ Generated JSON: {json_path.name}", "#00ff88")

        export_2d_csv(single_l["tracks"], single_l["vis"], orig_w, orig_h, csv_path, **fr)
        log(f"   ✔ Generated CSV: {csv_path.name}", "#00ff88")

        export_2d_nuke_tracker(single_l["tracks"], single_l["vis"], orig_w, orig_h, fps, nuke_path, **fr)
        log(f"   ✔ Generated Nuke Tracker Node: {nuke_path.name}", "#00ff88")

        export_2d_after_effects_jsx(single_l["tracks"], single_l["vis"], orig_w, orig_h, fps, ae_path, **fr)
        log(f"   ✔ Generated After Effects Script: {ae_path.name}", "#00ff88")

        export_2d_blender_empties(single_l["tracks"], single_l["vis"], orig_w, orig_h, fps, blender_path, images_dir=scene_dir / "images", **fr)
        log(f"   ✔ Generated Blender 2D Script: {blender_path.name}", "#00ff88")

        if single_l["export_cornerpin"]:
            cornerpin_nuke_path = out_dir / "tracks_2d_cornerpin_nuke.nk"
            export_2d_cornerpin_nuke(single_l["tracks"], orig_w, orig_h, fps, cornerpin_nuke_path, **fr)
            log(f"   ✔ Generated Nuke CornerPin2D Node: {cornerpin_nuke_path.name}", "#00d2ff")

            cornerpin_ae_path = out_dir / "tracks_2d_cornerpin_ae.jsx"
            export_2d_cornerpin_ae(single_l["tracks"], orig_w, orig_h, fps, cornerpin_ae_path, **fr)
            log(f"   ✔ Generated After Effects Corner Pin Script: {cornerpin_ae_path.name}", "#00d2ff")

            cornerpin_blender_path = out_dir / "tracks_2d_cornerpin_blender.py"
            export_2d_cornerpin_blender(single_l["tracks"], orig_w, orig_h, fps, cornerpin_blender_path, **fr)
            log(f"   ✔ Generated Blender Corner Pin Quad Surface: {cornerpin_blender_path.name}", "#00d2ff")

    # Render Overlay Video
    log("▶ [4/4] Rendering 2D Motion Trails Preview Video...", "#00d2ff")
    prog(85, "Rendering Overlay Video...")
    overlay_path = out_dir / "tracks_2d_overlay.mp4"
    try:
        if is_multi_layer:
            render_multi_layer_overlay_video(frames_np, layers_results, overlay_path, fps=fps)
        else:
            render_track_overlay_video(frames_np, layers_results[0]["tracks_proc"], layers_results[0]["vis"], overlay_path, fps=fps)
        log(f"   ✔ Rendered Motion Trail Overlay Video: {overlay_path.name}", "#00ff88")
    except Exception as e:
        log(f"Notice: Overlay render exception: {e}", "#e0a000")

    # Update _latest folder for instant 1-click access
    try:
        latest_dir = scene_dir / "2D_POINT_TRACK" / "_latest"
        latest_dir.mkdir(parents=True, exist_ok=True)
        for f in out_dir.iterdir():
            if f.is_file():
                shutil.copy2(f, latest_dir / f.name)
            elif f.is_dir() and f.name != "_latest":
                sub_dest = latest_dir / f.name
                if sub_dest.exists():
                    shutil.rmtree(sub_dest)
                shutil.copytree(f, sub_dest)
    except Exception:
        pass

    prog(100, "2D Point Tracking Complete")
    log(f"🎉 SUCCESS: 2D Point Tracking complete for '{base_name}'! Results in 04 SCENES/{base_name}/2D_POINT_TRACK/{timestamp}/", "#00ff88")

    return {
        "success": True,
        "out_dir": str(out_dir),
        "track_count": total_points,
        "frame_count": T,
        "start_frame": export_start_frame,
        "timeline_start": timeline_start,
        "frame_step": step,
        "layers_count": len(layers_results),
        "json_path": str(json_path),
        "nuke_path": str(nuke_path),
        "ae_path": str(ae_path),
        "blender_path": str(blender_path),
        "cornerpin_nuke_path": str(cornerpin_nuke_path) if cornerpin_nuke_path else None,
        "cornerpin_ae_path": str(cornerpin_ae_path) if cornerpin_ae_path else None,
        "cornerpin_blender_path": str(cornerpin_blender_path) if cornerpin_blender_path else None,
        "overlay_path": str(overlay_path) if overlay_path.exists() else None
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = sys.argv[1]
        res = process_cotracker_2d(target)
        print(json.dumps(res, indent=2))
    else:
        print("Usage: python cotracker_2d.py <video_path>")
