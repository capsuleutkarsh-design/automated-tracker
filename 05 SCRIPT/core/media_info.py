"""
Media probing helpers.

The frame rate used to be hard-coded (24 in the 2D path, 30 in the 3D exporters),
so every exported curve landed on the wrong timing for anything that was not
shot at that rate. Everything now asks this module instead.
"""

import re
import json
import subprocess
from pathlib import Path

from core.app_paths import ffprobe_exe as _resolved_ffprobe
from core.proc import hidden_kwargs

DEFAULT_FPS = 24.0


def _ffprobe_exe():
    exe = _resolved_ffprobe()
    return exe if exe.exists() else None


def _parse_rational(text):
    """Turns '30000/1001' or '25' into a float."""
    text = str(text).strip()
    if "/" in text:
        num, _, den = text.partition("/")
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            return None
        if den_f == 0:
            return None
        return num_f / den_f
    try:
        return float(text)
    except ValueError:
        return None


def probe_fps(video_path, default=DEFAULT_FPS):
    """
    Returns the frame rate of a video file as a float.

    Tries ffprobe from the bundled FFmpeg first (exact, handles 30000/1001),
    then imageio, then falls back to `default`.
    """
    video_path = Path(video_path)
    if video_path.is_dir() or not video_path.exists():
        return float(default)

    exe = _ffprobe_exe()
    if exe is not None:
        try:
            res = subprocess.run(
                [
                    str(exe), "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=r_frame_rate,avg_frame_rate",
                    "-of", "json", str(video_path),
                ],
                text=True, timeout=10, **hidden_kwargs(capture=True),
            )
            if res.returncode == 0 and res.stdout.strip():
                streams = json.loads(res.stdout).get("streams") or []
                if streams:
                    for key in ("avg_frame_rate", "r_frame_rate"):
                        val = _parse_rational(streams[0].get(key, ""))
                        if val and val > 0:
                            return float(val)
        except Exception:
            pass

    try:
        import imageio.v3 as iio
        meta = iio.immeta(str(video_path), plugin="FFMPEG")
        val = float(meta.get("fps", 0) or 0)
        if val > 0:
            return val
    except Exception:
        pass

    return float(default)


def probe_frame_count(video_path, fps=None, default=0):
    """Best-effort frame count; 0 when it cannot be determined."""
    video_path = Path(video_path)
    if video_path.is_dir() or not video_path.exists():
        return int(default)

    exe = _ffprobe_exe()
    if exe is not None:
        try:
            res = subprocess.run(
                [
                    str(exe), "-v", "error",
                    "-select_streams", "v:0",
                    "-count_packets",
                    "-show_entries", "stream=nb_read_packets,nb_frames,duration",
                    "-of", "json", str(video_path),
                ],
                text=True, timeout=20, **hidden_kwargs(capture=True),
            )
            if res.returncode == 0 and res.stdout.strip():
                streams = json.loads(res.stdout).get("streams") or []
                if streams:
                    st = streams[0]
                    for key in ("nb_read_packets", "nb_frames"):
                        try:
                            n = int(st.get(key, 0) or 0)
                        except (TypeError, ValueError):
                            n = 0
                        if n > 0:
                            return n
                    try:
                        dur = float(st.get("duration", 0) or 0)
                    except (TypeError, ValueError):
                        dur = 0.0
                    if dur > 0 and fps:
                        return max(1, int(round(dur * float(fps))))
        except Exception:
            pass

    return int(default)


SEQ_EXTS = {".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff", ".dpx"}


def detect_sequence_start(folder, default=1):
    """
    First frame number of an image sequence, read from its filenames.

    A plate delivered as shot_a_1001.exr .. shot_a_1200.exr belongs on frames
    1001-1200 of the timeline, but the pipeline renumbers everything to
    frame_000001 on import - so without this the exports would be keyed from 1
    and land nowhere near the plate.

    Returns `default` when the folder is not a numbered sequence.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return int(default)

    files = sorted(f for f in folder.iterdir()
                   if f.is_file() and f.suffix.lower() in SEQ_EXTS)
    if not files:
        return int(default)

    # the frame number is the last run of digits in the stem
    m = re.findall(r"(\d+)", files[0].stem)
    if not m:
        return int(default)
    try:
        first = int(m[-1])
    except ValueError:
        return int(default)

    # sanity: the second file should be the next frame (or close to it)
    if len(files) > 1:
        m2 = re.findall(r"(\d+)", files[1].stem)
        if m2:
            try:
                second = int(m2[-1])
            except ValueError:
                return first
            if not (0 < second - first <= 100):
                return int(default)

    return first
