"""
The --selftest report: what the app can and cannot find, written to
selftest.txt and printed when there is a console.

    Automated_Tracker.exe --selftest
    "00 PYTHON\\python.exe" "05 SCRIPT\\tracker_gui.py" --selftest

tracker_gui.main() calls run_selftest() before creating the QApplication, so
this module must not import Qt.
"""

import sys
from pathlib import Path


def run_selftest():
    """
    Report what the app can and cannot find, then exit.

    A windowed build has nowhere to print, so this writes selftest.txt next to
    the executable (falling back to the user cache if that folder is read-only).
    Useful for checking an install, and the only way to see inside a frozen
    build when it will not start.

        Automated_Tracker.exe --selftest
    """
    import platform
    from core.app_paths import (
        is_frozen, BASE_DIR, COLMAP_DIR, VIDEOS_DIR, FFMPEG_DIR, SCENES_DIR,
        COTRACKER_DIR, colmap_exe, ffmpeg_exe, ffprobe_exe, writable_cache_dir,
    )

    lines = []
    problems = []

    def row(label, ok, detail=""):
        lines.append("  [%s] %-26s %s" % ("ok " if ok else "FAIL", label, detail))
        if not ok:
            problems.append(label)

    lines.append("Automated Tracker self-test")
    lines.append("=" * 62)
    lines.append("  python      : %s" % sys.version.split()[0])
    lines.append("  platform    : %s" % platform.platform())
    lines.append("  frozen      : %s" % is_frozen())
    lines.append("  executable  : %s" % sys.executable)
    lines.append("  base dir    : %s" % BASE_DIR)
    lines.append("")
    lines.append("Folders")
    row("01 COLMAP", COLMAP_DIR.is_dir(), str(COLMAP_DIR))
    row("02 VIDEOS", VIDEOS_DIR.is_dir(), str(VIDEOS_DIR))
    row("03 FFMPEG", FFMPEG_DIR.is_dir(), str(FFMPEG_DIR))
    row("04 SCENES", SCENES_DIR.is_dir(), str(SCENES_DIR))
    row("06 COTRACKER", COTRACKER_DIR.is_dir(), str(COTRACKER_DIR))

    lines.append("")
    lines.append("Executables")
    row("colmap.exe", colmap_exe().exists(), str(colmap_exe()))
    row("ffmpeg.exe", ffmpeg_exe().exists(), str(ffmpeg_exe()))
    row("ffprobe.exe", ffprobe_exe().exists(), str(ffprobe_exe()))

    lines.append("")
    lines.append("CoTracker weights")
    for name in ("scaled_offline.pth", "scaled_online.pth"):
        p = COTRACKER_DIR / "checkpoints" / name
        row(name, p.exists(),
            ("%.0f MB" % (p.stat().st_size / 1024 ** 2)) if p.exists() else "missing")

    # Running from source, the vendored cotracker package is only importable once
    # 06 COTRACKER is on sys.path - which is what the engine module does on import.
    if not is_frozen() and str(COTRACKER_DIR) not in sys.path:
        sys.path.insert(0, str(COTRACKER_DIR))

    lines.append("")
    lines.append("Python packages")
    for mod in ("torch", "torchvision", "numpy", "PIL", "imageio", "PySide6", "cotracker"):
        try:
            __import__(mod)
            row(mod, True)
        except Exception as e:
            row(mod, False, str(e)[:60])
    try:
        import torch
        lines.append("  [ok ] torch %s  CUDA=%s  device=%s"
                     % (torch.__version__, torch.cuda.is_available(),
                        torch.cuda.get_device_name(0) if torch.cuda.is_available() else "-"))
    except Exception:
        pass

    lines.append("")
    lines.append("Launching the external tools")
    # This is the check that matters most in a frozen build: a windowed exe has no
    # standard handles to give a child process, so an unredirected subprocess just
    # fails to start. Importing the tools is not enough - they must actually run.
    from core.proc import run_hidden
    for label, cmd in (
        ("ffmpeg -version", [str(ffmpeg_exe()), "-version"]),
        ("ffprobe -version", [str(ffprobe_exe()), "-version"]),
        ("colmap help", [str(colmap_exe()), "help"]),
    ):
        try:
            r = run_hidden(cmd, capture=True, timeout=30)
            out = (r.stdout or b"")
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            first = out.strip().splitlines()[0][:48] if out.strip() else ""
            row(label, bool(first), first or "no output from the child process")
        except Exception as e:
            row(label, False, "%s: %s" % (type(e).__name__, str(e)[:48]))

    # Running a tool with --version proves the handles work. Actually writing files
    # proves the whole extraction path does, which is what the GUI depends on.
    lines.append("")
    lines.append("Real frame extraction")
    try:
        import tempfile
        vids = [f for f in VIDEOS_DIR.glob("*")
                if f.suffix.lower() in (".mp4", ".mov", ".avi", ".mkv", ".m4v")]
        if not vids:
            lines.append("  [skip] no clip in 02 VIDEOS to test with")
        else:
            tmp = Path(tempfile.mkdtemp(prefix="attrack_selftest_"))
            cmd = [str(ffmpeg_exe()), "-y", "-loglevel", "error",
                   "-i", str(vids[0]), "-vframes", "3", "-q:v", "2",
                   str(tmp / "f_%03d.jpg")]
            r = run_hidden(cmd, capture=True, timeout=120)
            got = len(list(tmp.glob("*.jpg")))
            err = (r.stdout or b"")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            row("extract 3 frames", got == 3,
                "%d written from %s%s" % (got, vids[0].name,
                                          ("  | " + err.strip()[:60]) if err.strip() else ""))
            import shutil as _sh
            _sh.rmtree(tmp, ignore_errors=True)
    except Exception as e:
        row("extract 3 frames", False, "%s: %s" % (type(e).__name__, str(e)[:60]))

    lines.append("")
    lines.append("Writable locations")
    for label, d in (("02 VIDEOS", VIDEOS_DIR), ("04 SCENES", SCENES_DIR),
                     ("cache", writable_cache_dir())):
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".write_test"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            row(label + " writable", True, str(d))
        except Exception as e:
            row(label + " writable", False, str(e)[:60])

    lines.append("")
    lines.append("=" * 62)
    lines.append("RESULT: %s" % ("ALL OK" if not problems
                                 else "%d problem(s): %s" % (len(problems), ", ".join(problems))))

    report = "\n".join(lines)

    # Write the file FIRST. A windowed PyInstaller build has sys.stdout set to
    # None, so printing would raise and the report would never be saved - which
    # is exactly the situation this self-test exists to diagnose.
    written = None
    for target in (BASE_DIR / "selftest.txt",
                   writable_cache_dir() / "selftest.txt"):
        try:
            target.write_text(report, encoding="utf-8")
            written = target
            break
        except Exception:
            continue

    try:
        if sys.stdout is not None:
            print(report)
            if written is not None:
                print("\nwritten to: %s" % written)
    except Exception:
        pass

    return 1 if problems else 0
