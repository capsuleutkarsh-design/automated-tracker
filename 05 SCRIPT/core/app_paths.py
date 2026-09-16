"""
One place that decides where the tracker's folders live.

Running from source, the layout is:

    <root>/00 PYTHON  01 COLMAP  02 VIDEOS  ...  05 SCRIPT/<this file's package>

so the root is three levels up from this module. Frozen with PyInstaller there is
no 05 SCRIPT folder at all and __file__ points inside the temporary extraction
directory, so the root has to come from the executable's own location instead -
the built exe is installed next to 01 COLMAP, 02 VIDEOS and the rest.

Everything that needs a path imports from here so the two cases can never drift.
"""

import os
import sys
import tempfile
from pathlib import Path


def is_frozen():
    """True when running from a PyInstaller build."""
    return bool(getattr(sys, "frozen", False))


def _resolve_base_dir():
    if is_frozen():
        # <install dir>/Automated_Tracker.exe  ->  <install dir>
        return Path(sys.executable).resolve().parent
    # <root>/05 SCRIPT/core/app_paths.py  ->  <root>
    return Path(__file__).resolve().parent.parent.parent


BASE_DIR = _resolve_base_dir()

PYTHON_DIR = BASE_DIR / "00 PYTHON"
COLMAP_DIR = BASE_DIR / "01 COLMAP"
VIDEOS_DIR = BASE_DIR / "02 VIDEOS"
FFMPEG_DIR = BASE_DIR / "03 FFMPEG"
SCENES_DIR = BASE_DIR / "04 SCENES"
COTRACKER_DIR = BASE_DIR / "06 COTRACKER"

# Where the source modules live. Frozen, there is no such folder - callers that
# only use it to extend sys.path should skip it when frozen.
SCRIPT_DIR = None if is_frozen() else (BASE_DIR / "05 SCRIPT")


def _first_existing(*candidates):
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


def colmap_exe():
    return _first_existing(COLMAP_DIR / "colmap.exe", COLMAP_DIR / "bin" / "colmap.exe")


def ffmpeg_exe():
    return _first_existing(FFMPEG_DIR / "ffmpeg.exe", FFMPEG_DIR / "bin" / "ffmpeg.exe")


def ffprobe_exe():
    return _first_existing(FFMPEG_DIR / "bin" / "ffprobe.exe", FFMPEG_DIR / "ffprobe.exe")


def bundled_dir():
    """
    Read-only folder holding data files bundled into the build (sys._MEIPASS),
    or the source tree when running unfrozen.
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def writable_cache_dir():
    """
    Somewhere we can always write small generated files. An installed build may
    sit in Program Files, which is not writable, so fall back to LOCALAPPDATA.
    """
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local:
        p = Path(local) / "AutomatedTracker"
    else:
        p = Path(tempfile.gettempdir()) / "AutomatedTracker"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        p = Path(tempfile.gettempdir())
    return p


def ensure_runtime_dirs():
    """Create the working folders a fresh install will not have yet."""
    for d in (VIDEOS_DIR, SCENES_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
