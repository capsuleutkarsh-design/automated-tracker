"""
Automated Photogrammetry Tracker - CoTracker3 2D AI Point Tracker Engine (Enhanced)
Features:
- Auto VRAM sliding-window chunking & FP16 (Prevents OOM on long 4K videos)
- Bounding box inclusion / exclusion masking
- 4-Point CornerPin / Screen Replacement Solver (Nuke CornerPin2D, AE Corner Pin, Blender Quad)
- Confidence threshold & velocity outlier filtering
- Exports: JSON, Nuke Tracker4, Nuke CornerPin2D, After Effects JSX, Blender Empties/Quad, CSV, Overlay MP4
"""

import sys
import json
import math
import zlib
import base64
import shutil
import colorsys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

import torch

_here = Path(__file__).resolve().parent
if not getattr(sys, 'frozen', False) and str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from core.app_paths import BASE_DIR, COTRACKER_DIR, ffmpeg_exe, ffprobe_exe
from core.proc import run_hidden
from core.media_info import probe_frame_count
from core.tracking_layer import point_in_poly, is_point_in_mask  # noqa: F401 (re-exported)

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


IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.exr', '.tif', '.tiff'}


class TrackingCancelled(Exception):
    """Raised inside the tracker when the caller's cancel_check() returns True."""


def _processing_size(orig_w, orig_h, max_dimension):
    """Working (w, h): longer side scaled to max_dimension (never up), then floored to /8."""
    if max_dimension and max(orig_h, orig_w) > max_dimension:
        scale = max_dimension / max(orig_h, orig_w)
        new_w, new_h = int(orig_w * scale), int(orig_h * scale)
    else:
        new_w, new_h = int(orig_w), int(orig_h)
    return max(8, (new_w // 8) * 8), max(8, (new_h // 8) * 8)


def _select_range(files, frame_step, in_point, out_point):
    """The In..Out slice of a sorted frame list, every frame_step-th file."""
    if out_point >= 0:
        files = files[in_point:out_point + 1]
    elif in_point > 0:
        files = files[in_point:]
    return files[::max(1, int(frame_step))]


def _load_resized(files, max_dimension, orig_size=None):
    """
    Reads image files into one [T, H, W, 3] uint8 array at the working resolution.

    Every frame is resized as it is read, so the full-resolution stack is never held
    (700 frames of 4K was ~17 GB before the resize even started). Returns
    (frames, (orig_w, orig_h)); `orig_size` overrides what the first file reports
    when the files were already scaled down on extraction.
    """
    frames = []
    target = None
    for fpath in files:
        img = Image.open(fpath).convert("RGB")
        if target is None:
            if orig_size is None:
                orig_size = img.size
            target = _processing_size(orig_size[0], orig_size[1], max_dimension)
        if img.size != target:
            img = img.resize(target, Image.BILINEAR)
        frames.append(np.asarray(img))
    if not frames:
        return None, None
    return np.stack(frames), (int(orig_size[0]), int(orig_size[1]))


def _scene_images_match_clip(video_path, image_files):
    """
    04 SCENES/<clip>/images may have been extracted by the 3D pipeline with a frame
    step, or from an earlier clip of the same name. Use it only when its frame count
    matches the clip's; anything else falls through to FFmpeg.
    """
    if video_path.is_dir() or not video_path.exists():
        return False
    n_clip = probe_frame_count(video_path)
    return n_clip > 0 and abs(len(image_files) - n_clip) <= 1


def _probe_dimensions(video_path):
    """(width, height) of the first video stream, or None."""
    exe = ffprobe_exe()
    if not exe.exists():
        return None
    try:
        res = run_hidden(
            [str(exe), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video_path)],
            capture=True, text=True, timeout=15,
        )
        w, h = (int(v) for v in res.stdout.strip().split(",")[:2])
        return (w, h) if w > 0 and h > 0 else None
    except Exception:
        return None


def _extract_with_ffmpeg(video_path, max_dimension, frame_step, in_point, out_point):
    """
    Decodes only the In..Out range at every frame_step-th frame, scaled to the working
    resolution on the way out, so a 4K clip never lands on disk at full size. Raises
    with FFmpeg's own message on failure instead of hiding it.
    """
    import tempfile
    if not video_path.exists():
        raise ValueError(f"Video not found: {video_path}")
    orig_size = _probe_dimensions(video_path)
    last = int(out_point) if out_point >= 0 else 2 ** 31 - 1
    # Commas inside a filter argument are escaped for the filtergraph parser.
    filters = [f"select=between(n\\,{in_point}\\,{last})*not(mod(n-{in_point}\\,{frame_step}))"]
    if max_dimension and orig_size and max(orig_size) > max_dimension:
        m = int(max_dimension)
        filters.append(f"scale=w=min(iw\\,{m}):h=min(ih\\,{m}):force_original_aspect_ratio=decrease")
    tmp_dir = Path(tempfile.mkdtemp(prefix="cotracker_frames_"))
    try:
        cmd = [
            str(FFMPEG_EXE), "-loglevel", "error", "-nostdin",
            "-i", str(video_path),
            "-vf", ",".join(filters), "-fps_mode", "vfr",
            "-qscale:v", "2",
            str(tmp_dir / "f_%06d.jpg")
        ]
        res = run_hidden(cmd, capture=True, text=True)
        if res.returncode != 0:
            raise ValueError(f"FFmpeg could not decode '{video_path.name}': "
                             f"{(res.stdout or '').strip()[-800:] or 'no error output'}")
        return _load_resized(sorted(tmp_dir.glob("*.jpg")), max_dimension, orig_size=orig_size)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def load_video_frames(video_path, max_dimension=720, frame_step=1, in_point=0, out_point=-1):
    """
    Loads video frames as a NumPy array [T, H, W, 3] at the working resolution and
    returns the original (W, H).
    Supports image sequence folders, extracted frames in 04 SCENES/<name>/images, or FFmpeg extraction.
    """
    video_path = Path(video_path)
    base_name = video_path.stem
    scene_images_dir = BASE_DIR / "04 SCENES" / base_name / "images"
    frame_step = max(1, int(frame_step))
    in_point = max(0, int(in_point))

    frames_np, orig_size = None, None
    if video_path.is_dir():
        seq_files = sorted(f for f in video_path.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTS)
        frames_np, orig_size = _load_resized(_select_range(seq_files, frame_step, in_point, out_point), max_dimension)
    elif scene_images_dir.exists():
        jpg_files = sorted(list(scene_images_dir.glob("*.jpg")) + list(scene_images_dir.glob("*.png")))
        if jpg_files and _scene_images_match_clip(video_path, jpg_files):
            frames_np, orig_size = _load_resized(_select_range(jpg_files, frame_step, in_point, out_point), max_dimension)

    if frames_np is None and not video_path.is_dir():
        frames_np, orig_size = _extract_with_ffmpeg(video_path, max_dimension, frame_step, in_point, out_point)

    if frames_np is None:
        raise ValueError(f"Could not extract or read any video frames from {video_path}")

    return frames_np, orig_size


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


def generate_grid_points_with_masks(H, W, grid_size=10, inclusion_masks=None, exclusion_masks=None):
    """
    Generates [N, 3] grid query points (t, x, y) at t=0, filtered by inclusion and exclusion masks.
    Supports multi-masks (rectangles and freehand polygons).
    """
    inc_list = []
    if inclusion_masks:
        inc_list.extend(inclusion_masks if isinstance(inclusion_masks, list) else [inclusion_masks])

    exc_list = []
    if exclusion_masks:
        exc_list.extend(exclusion_masks if isinstance(exclusion_masks, list) else [exclusion_masks])

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


def rasterize_polys(geoms, width, height):
    """Union of polygon masks as a boolean [H, W] array (True inside), or None if none drew."""
    img = Image.new("1", (int(width), int(height)), 0)
    draw = ImageDraw.Draw(img)
    drawn = False
    for g in geoms:
        pts = [(float(p[0]), float(p[1])) for p in g.get("points", [])]
        if len(pts) >= 3:
            draw.polygon(pts, fill=1)
            drawn = True
    if not drawn:
        return None
    return np.array(img, dtype=bool)


def filter_trajectories_by_animated_masks(tracks, vis, animated_masks, in_point=0, frame_step=1,
                                          width=None, height=None):
    """
    Evaluates tracks of shape [T, N, 2] against animated exclusion/inclusion masks at each frame.
    A sample inside an exclusion mask (or outside every inclusion mask) gets vis[t, n] = False.

    Mask keyframes sit on absolute source frames while t indexes the loaded clip, which
    starts at the In point and may skip frames: local t is source frame in_point + t*frame_step.
    Each frame's masks are rasterised once and the samples index into them, instead of a
    Python point-in-polygon test per sample.
    """
    if not animated_masks:
        return tracks, vis

    from mask_animator import AnimatedMask
    T, N, _ = tracks.shape
    vis_updated = np.asarray(vis).astype(bool).copy()
    step = max(1, int(frame_step))
    in_point = max(0, int(in_point))

    if width is None or height is None:
        # No frame size given: rasterise just large enough to hold every sample.
        width = int(np.ceil(tracks[:, :, 0].max())) + 2 if N else 1
        height = int(np.ceil(tracks[:, :, 1].max())) + 2 if N else 1
    width, height = max(1, int(width)), max(1, int(height))

    # Pre-parse AnimatedMask instances
    mask_objs = [m if isinstance(m, AnimatedMask) else AnimatedMask.from_dict(m) for m in animated_masks]

    xs = np.clip(np.rint(tracks[:, :, 0]), 0, width - 1).astype(np.intp)
    ys = np.clip(np.rint(tracks[:, :, 1]), 0, height - 1).astype(np.intp)
    # A sample outside the frame is never inside a mask.
    in_frame = ((tracks[:, :, 0] >= 0) & (tracks[:, :, 0] < width) &
                (tracks[:, :, 1] >= 0) & (tracks[:, :, 1] < height))

    for t in range(T):
        src_frame = in_point + t * step
        inc_geoms = []
        exc_geoms = []
        for m_obj in mask_objs:
            geom = m_obj.get_interpolated_geometry(src_frame)
            if geom:
                if m_obj.category == "inclusion":
                    inc_geoms.append(geom)
                else:
                    exc_geoms.append(geom)
        if not inc_geoms and not exc_geoms:
            continue

        keep = vis_updated[t]
        if inc_geoms:
            inc_arr = rasterize_polys(inc_geoms, width, height)
            if inc_arr is None:
                keep = np.zeros(N, dtype=bool)
            else:
                keep = keep & in_frame[t] & inc_arr[ys[t], xs[t]]
        if exc_geoms:
            exc_arr = rasterize_polys(exc_geoms, width, height)
            if exc_arr is not None:
                keep = keep & ~(in_frame[t] & exc_arr[ys[t], xs[t]])
        vis_updated[t] = keep

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


# CoTracker3 resamples every chunk to this fixed (H, W) before the network runs,
# whatever the input size (see cotracker/predictor.py _compute_sparse_tracks).
MODEL_RESOLUTION = (384, 512)
# Measured ratio of peak VRAM to float32 frame bytes *at the model resolution* for
# CoTracker3 offline: 120 frames peaked near 10 GB against 0.28 GB of 384x512 frames.
ACTIVATION_OVERHEAD = 35
# Smallest window worth running; below this the tracker has too little temporal context.
MIN_CHUNK = 8


def auto_chunk_size(T, C, H, W, device, requested=120, enabled=True, log=None, model_hw=MODEL_RESOLUTION):
    """
    Picks how many frames may sit on the GPU at once.

    CoTracker's activations cost the same per frame at 512p and 4K because it resamples
    every chunk to `model_hw` first. What does scale with the input is the float32
    upload of the chunk itself (C*H*W*4 bytes per frame), so the budget is the sum of
    the two. Budgeting the activations from the input size, as this used to, collapsed
    the chunk to MIN_CHUNK on any GPU for a 4K clip.
    """
    if not enabled:
        return T
    if device != "cuda":
        return min(requested, T)
    try:
        free_b, _total_b = torch.cuda.mem_get_info()
    except Exception:
        return min(requested, T)

    mh, mw = model_hw if model_hw else (H, W)
    input_bytes = C * H * W * 4
    model_bytes = C * mh * mw * 4 * ACTIVATION_OVERHEAD
    bytes_per_frame = input_bytes + model_bytes
    # Leave 30% of free VRAM as headroom.
    budget = free_b * 0.70
    n = int(budget // max(1, bytes_per_frame))
    n = max(MIN_CHUNK, min(requested, n, T))
    if log and n < min(requested, T):
        log(f"   Auto VRAM Chunking: {free_b / 1024**3:.1f} GB free - "
            f"processing {n} frames at a time (instead of {min(requested, T)}).", "#e0a000")
        if n <= MIN_CHUNK:
            log(f"   ! Very little free VRAM: only {n} frames fit per block. Close other GPU "
                f"applications, or choose a lower Resolution to shrink the upload.", "#e0a000")
    return n


def _clamp_query_frames(queries, n_frames):
    """Copy of [1, N, 3] queries with every frame index inside [0, n_frames-1]."""
    q = queries.clone()
    q[0, :, 0] = q[0, :, 0].clamp(0, max(0, int(n_frames) - 1))
    return q


def run_cotracker_chunked(model, video_tensor, queries_tensor, chunk_size=120, overlap=30,
                          device=None, auto_chunk=True, log=None, cancel_check=None):
    """
    Memory-safe chunked inference for long videos.

    video_tensor: [1, T, 3, H, W] uint8 **on the CPU** - only one chunk at a time is
                  uploaded to the GPU and converted to float32.
    queries_tensor: [1, N, 3] (t, x, y) on the CPU.
    cancel_check: optional callable; polled between chunks, raises TrackingCancelled.

    Returns (tracks [1,T,N,2], visible [1,T,N] bool, confidence [1,T,N] float).
    """
    if device is None:
        device = get_default_device()

    T = video_tensor.shape[1]
    C, H, W = video_tensor.shape[2], video_tensor.shape[3], video_tensor.shape[4]
    N = queries_tensor.shape[1]
    use_fp16 = (device == "cuda") and is_fp16_supported()

    model_hw = getattr(model, "interp_shape", None) or MODEL_RESOLUTION
    chunk = auto_chunk_size(T, C, H, W, device, requested=chunk_size, enabled=auto_chunk, log=log,
                            model_hw=tuple(int(v) for v in model_hw))
    overlap = max(0, min(overlap, chunk // 2))

    def _check_cancel():
        if cancel_check is not None and cancel_check():
            raise TrackingCancelled()

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
        if max_qt > chunk - 1 and log:
            log(f"   ! Manual points sit as late as frame {int(max_qt) + 1}, but this clip is "
                f"processed in {chunk}-frame blocks, so they are re-anchored to the last frame "
                f"of the first block. Place manual points earlier in the range for exact "
                f"results.", "#e0a000")

        full_tracks = np.zeros((1, T, N, 2), dtype=np.float32)
        full_vis = np.zeros((1, T, N), dtype=bool)
        full_conf = np.zeros((1, T, N), dtype=np.float32)

        current_queries = queries_tensor
        start_idx = 0
        prev_end = 0

        while start_idx < T:
            _check_cancel()
            # If the GPU cannot hold this window, halve it and try the same frames again
            # rather than aborting the whole job.
            while True:
                end_idx = min(start_idx + chunk, T)
                # A query frame must sit inside the window it is run on. Re-clamp on
                # every attempt: the window may have been halved since the last one,
                # and an index past the end is a device-side assert that poisons the
                # CUDA context for the rest of the session.
                q_try = _clamp_query_frames(current_queries, end_idx - start_idx)
                try:
                    c_tracks, c_vis, c_conf = _infer_single(
                        video_tensor[:, start_idx:end_idx], q_try
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
            # before it reaches frames it has not seen yet. Each point is seeded from the
            # later of that overlap frame and its own query frame: before its query frame
            # a manual point has no real track yet, and seeding from there sent the rest
            # of the track off from a bogus position.
            next_start = max(end_idx - overlap, start_idx + 1)
            seed_idx = next_start - start_idx
            q_rel = q_try[0, :, 0].round().long().clamp(0, end_idx - start_idx - 1).numpy()
            seed_per_pt = np.maximum(seed_idx, q_rel)
            new_q = np.zeros((1, N, 3), dtype=np.float32)
            new_q[0, :, 0] = (seed_per_pt - seed_idx).astype(np.float32)  # relative to the new chunk start
            new_q[0, :, 1:] = c_tracks[0, seed_per_pt, np.arange(N)]
            current_queries = torch.from_numpy(new_q)

            start_idx = next_start

        return full_tracks, full_vis, full_conf
    finally:
        capture.remove()


# Default jump threshold as a fraction of the frame diagonal at processing resolution.
# 0.18 of the diagonal is what the old fixed 150 px meant at 720x400; as a fraction it
# now means the same thing at 512p and at 4K.
MAX_JUMP_FRACTION = 0.18


def filter_tracks_confidence(tracks, vis, conf=None, min_confidence=0.7, max_jump_distance=None,
                             frame_size=None, max_jump_fraction=MAX_JUMP_FRACTION):
    """
    Cleans up tracks: drops low-confidence samples, then suppresses jumping coordinates
    when a point is lost.

    tracks: [T, N, 2]
    vis:    [T, N] boolean visibility from the model
    conf:   [T, N] raw visibility probability in 0..1 (optional)
    frame_size: (W, H) the tracks are expressed in. The jump threshold is
                max_jump_fraction of that frame's diagonal unless max_jump_distance
                (in the same pixels as `tracks`) is given explicitly.
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

    if max_jump_distance is None:
        if frame_size:
            max_jump_distance = float(max_jump_fraction) * math.hypot(float(frame_size[0]), float(frame_size[1]))
        else:
            max_jump_distance = 150.0

    # One pass over the frames with every track handled at once. Each track is anchored
    # on its first *visible* frame: a manual point placed on frame 200 is not visible on
    # frame 0, and anchoring there read its first real sample as a jump and culled it.
    last = np.zeros((N, 2), dtype=cleaned_tracks.dtype)
    anchored = np.zeros(N, dtype=bool)
    for t in range(T):
        cur = cleaned_tracks[t]
        visible = cleaned_vis[t]
        dist = np.hypot(cur[:, 0] - last[:, 0], cur[:, 1] - last[:, 1])
        # Detect impossible sudden velocity jumps
        jump = visible & anchored & (dist > max_jump_distance)
        keep = visible & ~jump
        cleaned_vis[t, jump] = False
        # Hold the last valid position while the point is occluded, lost or jumping.
        hold = anchored & ~keep
        cleaned_tracks[t, hold] = last[hold]
        last[keep] = cur[keep]
        anchored |= keep

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


def _tracker4_rows(tracks, orig_h, name_prefix, start_frame=1, frame_step=1):
    """
    One Tracker4 `tracks` row per point.

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

    Y is flipped (orig_h - y): CoTracker's origin is top-left, Nuke's is bottom-left.
    """
    T, N, _ = tracks.shape
    start_frame = int(start_frame)
    empty_slots = " ".join(["{}"] * 11)
    rows = []
    for n in range(N):
        track_name = f"{name_prefix}{n+1:03d}"
        x_curve_parts = []
        y_curve_parts = []
        for t in range(T):
            x = tracks[t, n, 0]
            y = orig_h - tracks[t, n, 1]  # Nuke Y is bottom-up
            fno = frame_number(t, start_frame, frame_step)
            x_curve_parts.append(f"x{fno} {x:.2f}")
            y_curve_parts.append(f"x{fno} {y:.2f}")

        x_curve = " ".join(x_curve_parts)
        y_curve = " ".join(y_curve_parts)
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
    return rows


def _tracker4_node(tracks, orig_h, name_prefix, node_name, start_frame=1, frame_step=1,
                   xpos=0, ypos=0, label=None):
    """A complete Tracker4 node (with its `push`), ready to append to a .nk script."""
    T, N, _ = tracks.shape
    rows = _tracker4_rows(tracks, orig_h, name_prefix, start_frame, frame_step)
    label_line = f' label "{label}"\n' if label else ""
    return (
        f'push $cut_paste_input\n'
        f'Tracker4 {{\n'
        f' tracks {{ {{ 1 31 {N} }}\n'
        f'  {{ ' + "\n    ".join(rows) + f' }}\n'
        f' }}\n'
        f' name {node_name}\n'
        f'{label_line}'
        f' selected true\n'
        f' xpos {xpos}\n'
        f' ypos {ypos}\n'
        f'}}\n'
    )


NK_HEADER = "set cut_paste_input [stack 0]\nversion 14.0 v1\n"


def export_2d_nuke_tracker(tracks_rescaled, vis, orig_w, orig_h, fps, out_path, node_name="CoTracker2D_Tracker", xpos=0, ypos=0, start_frame=1, frame_step=1):
    """Generates a native Nuke Tracker4 node with all tracks keyframed."""
    script = NK_HEADER + _tracker4_node(tracks_rescaled, orig_h, "track_", node_name,
                                        start_frame, frame_step, xpos=xpos, ypos=ypos)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def export_multi_layer_nuke_tracker(layers_data, orig_w, orig_h, fps, out_path, start_frame=1, frame_step=1):
    """
    Generates a master Nuke script containing individual Tracker4 nodes for each layer.
    layers_data: list of dicts {"name": str, "tracks": np.ndarray, "vis": np.ndarray}
    """
    script = NK_HEADER
    x_offset = 0
    for idx, ldata in enumerate(layers_data):
        l_name = ldata.get("name", f"Layer_{idx+1}").replace(" ", "_")
        script += _tracker4_node(ldata["tracks"], orig_h, f"{l_name}_", f"Tracker_{l_name}",
                                 start_frame, frame_step, xpos=x_offset, ypos=0,
                                 label=f"Layer: {l_name}")
        x_offset += 150

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


# =============================================================================
# 4-POINT CORNERPIN SOLVERS: NUKE, AFTER EFFECTS, BLENDER
# =============================================================================
def order_corners_tl_tr_br_bl(tracks):
    """
    Indices of the four corner tracks in the order [TL, TR, BR, BL], decided from the
    first frame in screen coordinates (y down).

    Every corner-pin writer relies on this order. Trusting the point order as given
    (a 2x2 grid is TL, TR, BL, BR; a user may click anticlockwise) produced bow-tie
    quads.
    """
    pts = np.asarray(tracks[0, :4, :], dtype=np.float64)
    by_y = sorted(range(4), key=lambda i: pts[i, 1])
    top = sorted(by_y[:2], key=lambda i: pts[i, 0])
    bottom = sorted(by_y[2:], key=lambda i: pts[i, 0])
    return [top[0], top[1], bottom[1], bottom[0]]


def ae_time_expr(frame, timeline_start):
    """
    After Effects time of a timeline frame, as a JS expression over the script's `fps`.

    AE comp time starts at 0 whatever the comp's displayed start frame is, so frame
    1001 on a 1001-based plate is t=0, not 1001/fps (~41 s).
    """
    return f"{int(frame) - int(timeline_start)}/fps"


def _ae_comp_duration(T, fps, start_frame, frame_step, timeline_start):
    """Seconds from the comp start to just past the last exported frame, step included."""
    last = frame_number(T - 1, start_frame, frame_step)
    return max(1, last - int(timeline_start) + 1) / float(fps)


def export_2d_cornerpin_nuke(tracks_rescaled, orig_w, orig_h, fps, out_path, start_frame=1, frame_step=1):
    """
    Generates a 1-click Nuke CornerPin2D node.
    Nuke pins: to1 = Bottom-Left, to2 = Bottom-Right, to3 = Top-Right, to4 = Top-Left
    (Nuke's origin is bottom-left, so Y is flipped: y_nuke = orig_h - y).
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

    tl, tr, br, bl = order_corners_tl_tr_br_bl(tracks_rescaled)
    to1_x, to1_y = get_curve(bl)
    to2_x, to2_y = get_curve(br)
    to3_x, to3_y = get_curve(tr)
    to4_x, to4_y = get_curve(tl)

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


def export_2d_cornerpin_ae(tracks_rescaled, orig_w, orig_h, fps, out_path, start_frame=1, frame_step=1, timeline_start=1):
    """Generates an After Effects ExtendScript applying Corner Pin to an insert layer."""
    T, N, _ = tracks_rescaled.shape
    if N < 4:
        return False

    duration = _ae_comp_duration(T, fps, start_frame, frame_step, timeline_start)
    script = f'''// Adobe After Effects ExtendScript - CoTracker 4-Point Corner Pin
(function() {{
    app.beginUndoGroup("Apply CoTracker Corner Pin");
    var fps = {fps};
    var comp = app.project.activeItem;
    if (!comp || !(comp instanceof CompItem)) {{
        comp = app.project.items.addComp("CoTracker_Screen_Replacement", {orig_w}, {orig_h}, 1.0, {duration:.2f}, {fps});
    }}

    var solid = comp.layers.addSolid([0.2, 0.6, 1.0], "Screen_Insert", {orig_w}, {orig_h}, 1.0);
    var cornerPin = solid.property("Effects").addProperty("Corner Pin");

    var to1 = cornerPin.property("Upper Left");
    var to2 = cornerPin.property("Upper Right");
    var to3 = cornerPin.property("Lower Right");
    var to4 = cornerPin.property("Lower Left");

'''
    corners = order_corners_tl_tr_br_bl(tracks_rescaled)
    for t in range(T):
        time_expr = ae_time_expr(frame_number(t, start_frame, frame_step), timeline_start)
        for pin, idx in enumerate(corners, start=1):
            x, y = tracks_rescaled[t, idx, 0], tracks_rescaled[t, idx, 1]
            script += f'    to{pin}.setValueAtTime({time_expr}, [{x:.2f}, {y:.2f}]);\n'

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

    step_n = max(1, int(frame_step))
    corners = order_corners_tl_tr_br_bl(tracks_rescaled)
    # One record per frame: [frame, [[x, y] * 4 in TL, TR, BR, BL order]] in Blender's
    # normalised quad space, looped over inside Blender rather than unrolled here.
    keys = []
    for t in range(T):
        coords = []
        for idx in corners:
            x_norm = (tracks_rescaled[t, idx, 0] / orig_w - 0.5) * 2.0
            y_norm = -(tracks_rescaled[t, idx, 1] / orig_h - 0.5) * 2.0 * (orig_h / orig_w)
            coords.append([round(float(x_norm), 5), round(float(y_norm), 5)])
        keys.append([frame_number(t, start_frame, frame_step), coords])

    script = f'''"""
1-CLICK BLENDER 4-POINT CORNERPIN / SCREEN REPLACEMENT QUAD
Creates a 4-vertex Quad plane mesh animated to match the tracked screen corners.
"""
import bpy
import json

STEP = {step_n}
# [[frame, [[x, y], [x, y], [x, y], [x, y]]], ...]  corners in TL, TR, BR, BL order
KEYS = json.loads(r"""{json.dumps(keys, separators=(",", ":"))}""")

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
    obj.shape_key_add(name="Basis")
    last = len(KEYS) - 1
    for i, (f, coords) in enumerate(KEYS):
        sk = obj.shape_key_add(name=f"Frame_{{f}}")
        for v_i, (x, y) in enumerate(coords):
            sk.data[v_i].co = (x, y, 0.0)
        sk.value = 1.0
        sk.keyframe_insert(data_path="value", frame=f)
        if i > 0:
            sk.value = 0.0
            sk.keyframe_insert(data_path="value", frame=f - STEP)
        if i < last:
            sk.value = 0.0
            sk.keyframe_insert(data_path="value", frame=f + STEP)

    print("✔ Tracked screen replacement quad mesh created successfully in Blender!")

if __name__ == "__main__":
    setup_cornerpin_mesh()
'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def export_2d_after_effects_jsx(tracks_rescaled, vis, orig_w, orig_h, fps, out_path, start_frame=1, frame_step=1, timeline_start=1):
    T, N, _ = tracks_rescaled.shape
    duration = _ae_comp_duration(T, fps, start_frame, frame_step, timeline_start)
    script = f'''// Adobe After Effects ExtendScript - CoTracker 2D Tracks Importer
(function() {{
    app.beginUndoGroup("Import CoTracker 2D Tracks");
    var comp = app.project.activeItem;
    if (!comp || !(comp instanceof CompItem)) {{
        comp = app.project.items.addComp("CoTracker_2D_Comp", {orig_w}, {orig_h}, 1.0, {duration:.2f}, {fps});
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
            time_expr = ae_time_expr(frame_number(t, start_frame, frame_step), timeline_start)
            script += f'    posProp_{n}.setValueAtTime({time_expr}, [{x:.2f}, {y:.2f}, 0]);\n'

    script += '''
    app.endUndoGroup();
    alert("✔ Successfully created " + trackCount + " tracked Null layers in After Effects!");
})();
'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)


def pack_tracks_blob(tracks):
    """
    Track coordinates as compact base64 strings for embedding in a script.

    Positions are quantised to 0.1 px; frame 0 is stored absolute (int32) and the
    rest as per-frame deltas laid out track by track (int16 where they fit, int32
    otherwise), zlib-compressed. A 50x50 grid over 700 frames packs to a few MB
    instead of 3.5 million script lines. unpack_tracks_blob() reverses it and is
    also the code the Blender script runs.
    """
    arr = np.asarray(tracks, dtype=np.float64)
    T, N, _ = arr.shape
    q = np.rint(np.transpose(arr, (1, 0, 2)) * 10.0).astype(np.int64)  # [N, T, 2]
    first = np.ascontiguousarray(q[:, 0, :].astype("<i4"))
    d = np.diff(q, axis=1)  # [N, T-1, 2]
    dtype = "<i2" if (d.size == 0 or np.abs(d).max() < 32767) else "<i4"
    raw = np.ascontiguousarray(d.astype(dtype)).tobytes()
    return {
        "dtype": dtype,
        "shape": [int(T), int(N), 2],
        "scale": 0.1,
        "first": base64.b64encode(first.tobytes()).decode("ascii"),
        "b64": base64.b64encode(zlib.compress(raw, 6)).decode("ascii"),
    }


_BLENDER_UNPACK_SRC = '''
def unpack_tracks_blob(blob):
    """Inverse of pack_tracks_blob(): [T, N, 2] float64 pixel coordinates."""
    T, N, _ = blob["shape"]
    first = np.frombuffer(base64.b64decode(blob["first"]), dtype="<i4").reshape(N, 1, 2).astype(np.int64)
    raw = zlib.decompress(base64.b64decode(blob["b64"]))
    d = np.frombuffer(raw, dtype=np.dtype(blob["dtype"])).reshape(N, max(0, T - 1), 2).astype(np.int64)
    q = np.concatenate([first, first + np.cumsum(d, axis=1)], axis=1)  # [N, T, 2]
    return np.transpose(q, (1, 0, 2)) * float(blob["scale"])
'''
exec(_BLENDER_UNPACK_SRC)  # defines unpack_tracks_blob here from the same source Blender runs


def _blender_tracks_script(layers, orig_w, orig_h, fps, images_dir, start_frame, frame_step, title, func_name):
    """
    One Blender script for any number of layers: reference camera + background sequence
    preamble, then every layer's tracks embedded as a compressed blob and keyframed in a
    loop inside Blender. layers: list of dicts {"collection", "prefix", "tracks"}.
    """
    images_dir_str = str(images_dir).replace('\\', '/') if images_dir else ""
    T_max = max(int(l["tracks"].shape[0]) for l in layers) if layers else 1
    data = {
        "width": int(orig_w),
        "height": int(orig_h),
        "layers": [
            {
                "collection": l["collection"],
                "prefix": l["prefix"],
                "frames": [frame_number(t, start_frame, frame_step) for t in range(int(l["tracks"].shape[0]))],
                "tracks": pack_tracks_blob(l["tracks"]),
            }
            for l in layers
        ],
    }
    blob = json.dumps(data, separators=(",", ":"))

    return f'''"""
=============================================================================
{title}
Creates an aligned Reference Camera + Video Background + Keyframed Track Empties.

HOW TO USE:
1. Open Blender -> Go to Scripting tab (Text Editor).
2. Open this script and click Run Script (Alt+P).
3. Press Numpad 0 to look through the Camera!
=============================================================================
"""
import bpy
import os
import json
import zlib
import base64
import numpy as np

# All track data, one blob per layer (0.1 px deltas, zlib, base64) - see unpack_tracks_blob().
DATA = json.loads(r"""{blob}""")

{_BLENDER_UNPACK_SRC}

def {func_name}():
    scene = bpy.context.scene
    scene.render.resolution_x = {orig_w}
    scene.render.resolution_y = {orig_h}
    scene.frame_start = {frame_number(0, start_frame, frame_step)}
    scene.frame_end = {frame_number(T_max - 1, start_frame, frame_step)}
    scene.render.fps = {int(round(fps))}
    scene.render.fps_base = {round(int(round(fps)) / fps, 6) if fps else 1.0}

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

    # 2. Keyframed 2D Track Markers, one collection per layer
    W = float(DATA["width"])
    H = float(DATA["height"])
    for layer in DATA["layers"]:
        col = bpy.data.collections.get(layer["collection"]) or bpy.data.collections.new(layer["collection"])
        if col.name not in scene.collection.children:
            scene.collection.children.link(col)

        tracks = unpack_tracks_blob(layer["tracks"])  # [T, N, 2] pixels, y down
        frames = layer["frames"]
        T, N = int(tracks.shape[0]), int(tracks.shape[1])
        for n in range(N):
            track_name = f"{{layer['prefix']}}{{n+1:03d}}"
            empty_obj = bpy.data.objects.get(track_name)
            if not empty_obj:
                empty_obj = bpy.data.objects.new(track_name, None)
                empty_obj.empty_display_type = 'PLAIN_AXES'
                empty_obj.empty_display_size = 0.1
                col.objects.link(empty_obj)
            for t in range(T):
                x_norm = (tracks[t, n, 0] / W - 0.5) * 2.0
                y_norm = -(tracks[t, n, 1] / H - 0.5) * 2.0 * (H / W)
                empty_obj.location = (float(x_norm), float(y_norm), 0.0)
                empty_obj.keyframe_insert(data_path="location", frame=int(frames[t]))

    print("✔ 2D Tracks & Reference Camera created successfully in Blender! Press Numpad 0 for camera view.")

if __name__ == "__main__":
    {func_name}()
'''


def export_2d_blender_empties(tracks_rescaled, vis, orig_w, orig_h, fps, out_path, images_dir=None, collection_name="CoTracker_2D_Tracks", start_frame=1, frame_step=1):
    layers = [{"collection": collection_name, "prefix": "Track_2D_", "tracks": tracks_rescaled}]
    script = _blender_tracks_script(layers, orig_w, orig_h, fps, images_dir, start_frame, frame_step,
                                    "1-CLICK BLENDER 2D POINT TRACKS & REFERENCE CAMERA IMPORTER",
                                    "import_2d_tracks")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def export_multi_layer_blender(layers_data, orig_w, orig_h, fps, out_path, images_dir=None, start_frame=1, frame_step=1):
    """
    Generates a master Blender script creating separate collections for each tracking layer.
    """
    layers = []
    for l_idx, ldata in enumerate(layers_data):
        l_name = ldata.get("name", f"Layer_{l_idx+1}").replace(" ", "_")
        layers.append({"collection": f"Layer_{l_name}", "prefix": f"{l_name}_Track_", "tracks": ldata["tracks"]})
    script = _blender_tracks_script(layers, orig_w, orig_h, fps, images_dir, start_frame, frame_step,
                                    "1-CLICK BLENDER MULTI-LAYER 2D POINT TRACKS & REFERENCE CAMERA IMPORTER",
                                    "import_multi_layer_tracks")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def _hex_to_rgb(h):
    if isinstance(h, (tuple, list)):
        return tuple(int(x) for x in h[:3])
    h = str(h).lstrip('#')
    if len(h) == 6:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    return (0, 210, 255)


def _render_overlay(proc_frames_np, layers, out_mp4_path, fps=24, trail_len=15):
    """
    Draws glowing trails over the frames and streams them to an MP4 one frame at a time.

    layers: list of (tracks [T, N, 2] in processing pixels, vis [T, N], colors [N] RGB).
    Only the trail window [t - trail_len, t] is touched per frame, and the points are
    picked out of the window with numpy rather than a Python loop per sample.
    """
    import imageio

    T = proc_frames_np.shape[0]
    prepared = []
    for tracks, vis, colors in layers:
        pts = np.rint(np.asarray(tracks, dtype=np.float64)).astype(np.int32)
        prepared.append((pts, np.asarray(vis).astype(bool), list(colors)))

    writer = imageio.get_writer(str(out_mp4_path), fps=fps, quality=8)
    try:
        for t in range(T):
            base_img = Image.fromarray(proc_frames_np[t]).copy()
            draw = ImageDraw.Draw(base_img)
            tail_start = max(0, t - trail_len)

            for pts, vis, colors in prepared:
                win = pts[tail_start:t + 1]
                vwin = vis[tail_start:t + 1]
                for n in np.flatnonzero(vwin.any(axis=0)):
                    seg = win[vwin[:, n], n]
                    col = colors[n]
                    if len(seg) > 1:
                        draw.line([tuple(p) for p in seg.tolist()], fill=col, width=2)
                    if vwin[-1, n]:
                        cur_x, cur_y = int(seg[-1, 0]), int(seg[-1, 1])
                        draw.ellipse([cur_x-3, cur_y-3, cur_x+3, cur_y+3], fill=col, outline=(255, 255, 255))

            writer.append_data(np.asarray(base_img))
    finally:
        writer.close()
    return True


def render_track_overlay_video(proc_frames_np, tracks_proc, vis, out_mp4_path, fps=24, trail_len=15):
    """Renders an MP4 video with glowing colorful trails showing the 2D point trajectories."""
    N = tracks_proc.shape[1]
    colors = [
        tuple(int(c * 255) for c in colorsys.hsv_to_rgb(i / max(1, N), 0.9, 1.0))
        for i in range(N)
    ]
    return _render_overlay(proc_frames_np, [(tracks_proc, vis, colors)], out_mp4_path, fps=fps, trail_len=trail_len)


def render_multi_layer_overlay_video(proc_frames_np, layer_results, out_mp4_path, fps=24, trail_len=15):
    """
    Renders video with colorful trails for each layer using that layer's distinct theme color.
    """
    layers = []
    for ldata in layer_results:
        tracks = ldata["tracks_proc"]
        base_rgb = _hex_to_rgb(ldata.get("color", "#00d2ff"))
        layers.append((tracks, ldata["vis"], [base_rgb] * tracks.shape[1]))
    return _render_overlay(proc_frames_np, layers, out_mp4_path, fps=fps, trail_len=trail_len)


# =============================================================================
# MAIN ORCHESTRATION PIPELINE
# =============================================================================
def _export_layer_files(ldata, out_dir, orig_w, orig_h, fps, images_dir, fr, timeline_start, node_name, collection_name, log):
    """
    Writes one layer's full export set (JSON, CSV, Nuke Tracker4, AE nulls, Blender
    empties, and the three corner-pin files when the layer asked for them) into out_dir.
    Used for the single-layer flat output and for every per-layer subfolder alike.
    Returns the corner-pin paths (or None) as (nuke, ae, blender).
    """
    tracks, vis = ldata["tracks"], ldata["vis"]
    ae_fr = dict(fr, timeline_start=timeline_start)

    export_2d_json(tracks, vis, orig_w, orig_h, out_dir / "tracks_2d.json", **fr)
    export_2d_csv(tracks, vis, orig_w, orig_h, out_dir / "tracks_2d_csv.csv", **fr)
    export_2d_nuke_tracker(tracks, vis, orig_w, orig_h, fps, out_dir / "tracks_2d_nuke.nk", node_name=node_name, **fr)
    export_2d_after_effects_jsx(tracks, vis, orig_w, orig_h, fps, out_dir / "tracks_2d_ae.jsx", **ae_fr)
    export_2d_blender_empties(tracks, vis, orig_w, orig_h, fps, out_dir / "tracks_2d_blender.py",
                              images_dir=images_dir, collection_name=collection_name, **fr)
    log(f"   ✔ [{ldata['name']}] JSON, CSV, Nuke Tracker4, After Effects and Blender scripts written.", "#00ff88")

    if not ldata.get("export_cornerpin"):
        return None, None, None
    if tracks.shape[1] != 4:
        log(f"   ! [{ldata['name']}] Corner-pin export needs exactly 4 points, this layer has "
            f"{tracks.shape[1]} - skipped.", "#e0a000")
        return None, None, None

    cp_nuke = out_dir / "tracks_2d_cornerpin_nuke.nk"
    cp_ae = out_dir / "tracks_2d_cornerpin_ae.jsx"
    cp_blender = out_dir / "tracks_2d_cornerpin_blender.py"
    export_2d_cornerpin_nuke(tracks, orig_w, orig_h, fps, cp_nuke, **fr)
    export_2d_cornerpin_ae(tracks, orig_w, orig_h, fps, cp_ae, **ae_fr)
    export_2d_cornerpin_blender(tracks, orig_w, orig_h, fps, cp_blender, **fr)
    log(f"   ✔ [{ldata['name']}] Nuke CornerPin2D, After Effects Corner Pin and Blender quad written.", "#00d2ff")
    return cp_nuke, cp_ae, cp_blender


def process_cotracker_2d(video_path, config=None, progress_callback=None, log_callback=None, cancel_check=None):
    """
    Full end-to-end 2D tracking pipeline with VRAM chunking, masking, and CornerPin support.

    cancel_check: optional callable returning True once the user pressed Cancel. It is
    polled between chunks and between layers; the result then has "cancelled": True.
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

    def cancelled():
        return bool(cancel_check and cancel_check())

    if not HAS_COTRACKER:
        raise ImportError(
            "The CoTracker package could not be imported. Check that '06 COTRACKER' is present "
            "next to the app (from source) or was bundled into the build."
        )

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

    def cancelled_result():
        log("⏹ 2D tracking cancelled.", "#ff4b4b")
        prog(100, "2D Point Tracking Cancelled")
        return {"success": False, "cancelled": True, "out_dir": str(out_dir)}

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
    if cancelled():
        return cancelled_result()

    device = get_default_device()
    alloc_mb, total_mb, gpu_name = get_gpu_memory_info()
    fp_mode = "FP16 Autocast Active" if (device == "cuda" and is_fp16_supported()) else "FP32 Precision"
    if device == "cuda":
        log(f"⚡ GPU: {gpu_name} (VRAM: {alloc_mb:.0f}/{total_mb:.0f} MB, {fp_mode})", "#00d2ff")

    # The clip stays on the CPU as uint8. run_cotracker_chunked() uploads one chunk at a
    # time and converts it to float32 there. Uploading the whole clip as float32 (the old
    # behaviour) needed ~7.3 GB of VRAM for a 708-frame 720p shot and blew up on 'Original'.
    # The permute is a view: no second copy of the clip in RAM.
    video_tensor = torch.from_numpy(frames_np).permute(0, 3, 1, 2)[None]
    auto_chunk = bool(config.get("auto_chunk", True))
    est_gb = (T * 3 * proc_h * proc_w * 4) / (1024 ** 3)
    if device == "cuda":
        if auto_chunk:
            log(f"   Auto VRAM Chunking ON (full clip would need {est_gb:.1f} GB of VRAM in one pass).", "#a0a0b0")
        else:
            log(f"   ! Auto VRAM Chunking is OFF - the whole clip ({est_gb:.1f} GB) is sent to the "
                f"GPU at once and may run out of memory.", "#e0a000")

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
    images_dir = scene_dir / "images"

    layers_results = []
    total_points = 0

    for l_idx, lconf in enumerate(raw_layers):
        if cancelled():
            return cancelled_result()

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
                    # Keyframes sit on absolute source frames; local frame 0 is the In point.
                    g0 = m_obj.get_interpolated_geometry(in_pt)
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

                p_inc_masks = [scale_mask(m, scale_x, scale_y) for m in inc_masks] if inc_masks else None
                p_exc_masks = [scale_mask(m, scale_x, scale_y) for m in exc_masks] if exc_masks else None

                queries_list = generate_grid_points_with_masks(
                    proc_h, proc_w,
                    grid_size=g_size,
                    inclusion_masks=p_inc_masks,
                    exclusion_masks=p_exc_masks
                )

        q_tensor = torch.tensor(queries_list, dtype=torch.float32)[None]
        N_layer = q_tensor.shape[1]
        total_points += N_layer

        log(f"   ▶ Tracking Layer [{l_name}] ({N_layer} points, mode: {l_mode})...", "#00d2ff")
        prog(35 + int((l_idx / len(raw_layers)) * 35), f"Tracking Layer {l_name} ({N_layer} pts)...")

        try:
            pred_tracks, pred_vis, pred_conf = run_cotracker_chunked(
                model, video_tensor, q_tensor, chunk_size=120, overlap=30,
                device=device, auto_chunk=auto_chunk, log=log, cancel_check=cancel_check
            )
        except TrackingCancelled:
            return cancelled_result()
        l_tracks_proc = pred_tracks[0]
        l_vis = pred_vis[0]
        l_conf = pred_conf[0]

        l_tracks_proc, l_vis = filter_tracks_confidence(
            l_tracks_proc, l_vis, conf=l_conf, min_confidence=l_min_conf,
            frame_size=(proc_w, proc_h)
        )
        log(f"   [{l_name}] Confidence >= {l_min_conf:.2f}: "
            f"{int(l_vis.sum())}/{l_vis.size} samples kept.", "#a0a0b0")

        l_tracks_rescaled = l_tracks_proc.copy()
        l_tracks_rescaled[:, :, 0] *= scale_x
        l_tracks_rescaled[:, :, 1] *= scale_y

        # Dynamic trajectory culling against animated masks over time
        if anim_masks:
            l_tracks_rescaled, l_vis = filter_trajectories_by_animated_masks(
                l_tracks_rescaled, l_vis, anim_masks,
                in_point=in_pt, frame_step=step, width=orig_w, height=orig_h
            )

        layers_results.append({
            "name": l_name,
            "tracks": l_tracks_rescaled,
            "tracks_proc": l_tracks_proc,
            "vis": l_vis,
            "color": l_col,
            "point_count": N_layer,
            # Only a layer explicitly set up as a corner pin - never "has 4 points".
            "export_cornerpin": bool(lconf.get("export_cornerpin", False))
        })

    log(f"✔ Solved and filtered trajectories for all {total_points} points across {T} frames!", "#00ff88")
    prog(75, "Generating Master VFX Exporters...")
    log("▶ [3/4] Generating VFX 2D Tracker Formats (Nuke, AE, Blender, JSON, CSV)...", "#00d2ff")

    # Every layer gets the same file set: in out_dir itself for a single layer, in a
    # subfolder per layer otherwise (with combined master files alongside).
    json_path = out_dir / "tracks_2d.json"
    nuke_path = out_dir / "tracks_2d_nuke.nk"
    ae_path = out_dir / "tracks_2d_ae.jsx"
    blender_path = out_dir / "tracks_2d_blender.py"
    cornerpin_nuke_path = cornerpin_ae_path = cornerpin_blender_path = None

    for ldata in layers_results:
        l_name = ldata["name"]
        layer_dir = out_dir / l_name if is_multi_layer else out_dir
        layer_dir.mkdir(parents=True, exist_ok=True)
        cp_paths = _export_layer_files(
            ldata, layer_dir, orig_w, orig_h, fps, images_dir, fr, timeline_start,
            node_name=f"Tracker_{l_name}" if is_multi_layer else "CoTracker2D_Tracker",
            collection_name=f"Layer_{l_name}" if is_multi_layer else "CoTracker_2D_Tracks",
            log=log,
        )
        if not is_multi_layer:
            cornerpin_nuke_path, cornerpin_ae_path, cornerpin_blender_path = cp_paths

    if is_multi_layer:
        # Multi-layer combined files
        export_multi_layer_nuke_tracker(layers_results, orig_w, orig_h, fps, nuke_path, **fr)
        log(f"   ✔ Generated Multi-Layer Nuke Tracker: {nuke_path.name}", "#00ff88")

        export_multi_layer_blender(layers_results, orig_w, orig_h, fps, blender_path, images_dir=images_dir, **fr)
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

    if cancelled():
        return cancelled_result()

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
        "cancelled": False,
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
