"""
Python half of the build.

  1. makes sure PyInstaller is available
  2. draws the application icon
  3. freezes 05 SCRIPT/tracker_gui.py into build/dist/Automated_Tracker/
  4. writes build/version.txt for the Inno script to read

BUILD.bat runs this, then hands over to Inno Setup. Run it directly for just the
exe:

    "00 PYTHON\\python.exe" build\\build_app.py
    "00 PYTHON\\python.exe" build\\build_app.py --clean
    "00 PYTHON\\python.exe" build\\build_app.py --check      (prerequisites only)
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SRC = ROOT / "05 SCRIPT"

# The version is defined once, in 05 SCRIPT/core/version.py; this file only
# stamps it into build/version.txt for the Inno Setup script.
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
from core.version import APP_VERSION  # noqa: E402

APP_NAME = "Automated_Tracker"
PUBLISHER = "Automated Tracker"

DIST = HERE / "dist"
WORK = HERE / "work"
ICON = HERE / "app_icon.ico"

# Folders shipped next to the exe. The app shells out to these or loads files
# from them by path, so they stay outside the frozen bundle.
PAYLOAD_DIRS = ["01 COLMAP", "03 FFMPEG"]
PAYLOAD_SUBDIRS = [("06 COTRACKER", "checkpoints")]
EMPTY_DIRS = ["02 VIDEOS", "04 SCENES"]


def log(msg):
    print("[build] %s" % msg, flush=True)


def fail(msg):
    print("\n[build] ERROR: %s\n" % msg, file=sys.stderr, flush=True)
    sys.exit(1)


# --------------------------------------------------------------------------
def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
        log("PyInstaller %s" % PyInstaller.__version__)
        return
    except ImportError:
        pass
    log("PyInstaller missing - installing it")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--no-input", "pyinstaller"])
    if r.returncode != 0:
        fail("could not install PyInstaller. Install it manually:\n"
             '    "00 PYTHON\\python.exe" -m pip install pyinstaller')
    import PyInstaller  # noqa: F401
    log("PyInstaller installed")


def make_icon():
    """
    Use the branded icon from branding/ when it is there (rendered from mark.svg
    by branding/render_assets.py); otherwise draw a plain crosshair so the build
    never depends on art assets.
    """
    branded = ROOT / "branding" / "app_icon.ico"
    if branded.exists():
        if not ICON.exists() or branded.stat().st_mtime > ICON.stat().st_mtime:
            shutil.copy2(branded, ICON)
            log("icon <- branding/app_icon.ico")
        else:
            log("icon already present (branding)")
        return
    if ICON.exists():
        log("icon already present")
        return
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        log("Pillow not available - building without a custom icon")
        return

    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = []
    for s in sizes:
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        pad = max(1, int(s * 0.06))
        d.rounded_rectangle([pad, pad, s - pad - 1, s - pad - 1],
                            radius=max(2, int(s * 0.18)), fill=(20, 23, 29, 255))
        cx = cy = s / 2.0
        r = s * 0.27
        w = max(1, int(s * 0.055))
        accent = (56, 189, 248, 255)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=accent, width=w)
        arm = s * 0.40
        d.line([cx, cy - arm, cx, cy - r * 0.55], fill=accent, width=w)
        d.line([cx, cy + r * 0.55, cx, cy + arm], fill=accent, width=w)
        d.line([cx - arm, cy, cx - r * 0.55, cy], fill=accent, width=w)
        d.line([cx + r * 0.55, cy, cx + arm, cy], fill=accent, width=w)
        dot = max(1, int(s * 0.055))
        d.ellipse([cx - dot, cy - dot, cx + dot, cy + dot], fill=(255, 255, 255, 255))
        frames.append(img)

    frames[-1].save(ICON, format="ICO", sizes=[(f.width, f.height) for f in frames])
    log("icon written -> %s" % ICON.name)


def check_prereqs():
    ok = True
    log("checking prerequisites")
    if not (SRC / "tracker_gui.py").exists():
        log("  MISSING  05 SCRIPT/tracker_gui.py"); ok = False
    else:
        log("  ok       05 SCRIPT/tracker_gui.py")
    for name in PAYLOAD_DIRS:
        p = ROOT / name
        log("  %s %s" % ("ok      " if p.is_dir() else "MISSING ", name))
        ok = ok and p.is_dir()
    for parent, sub in PAYLOAD_SUBDIRS:
        p = ROOT / parent / sub
        log("  %s %s/%s" % ("ok      " if p.is_dir() else "MISSING ", parent, sub))
        ok = ok and p.is_dir()
    for mod in ("torch", "PySide6", "numpy", "PIL", "imageio", "OpenEXR"):
        try:
            __import__(mod)
            log("  ok       %s" % mod)
        except ImportError:
            log("  MISSING  %s" % mod); ok = False
    try:
        import torch
        log("  ok       CUDA available: %s" % torch.cuda.is_available())
    except Exception:
        pass
    return ok


def clear_stray_links():
    """
    Remove any junctions left in the dist folder by an interrupted verify run.

    PyInstaller wipes its output directory, and a stale junction there makes
    that fail - or worse, would let the installer package COLMAP twice.
    """
    app_dir = DIST / APP_NAME
    if not app_dir.is_dir():
        return
    for name in PAYLOAD_DIRS + [p for p, _ in PAYLOAD_SUBDIRS] + EMPTY_DIRS:
        d = app_dir / name
        try:
            if d.is_symlink() or (d.exists() and not d.is_dir()):
                subprocess.run(["cmd", "/c", "rmdir", str(d)], capture_output=True)
                log("  removed stale link %s" % name)
            elif d.exists():
                # A junction that resolves still reports is_dir(); rmdir on a
                # junction removes the link only, and fails on a real folder
                # that has contents - which is exactly the behaviour we want.
                subprocess.run(["cmd", "/c", "rmdir", str(d)], capture_output=True)
        except OSError:
            # A broken junction raises on stat; remove it by name.
            subprocess.run(["cmd", "/c", "rmdir", str(d)], capture_output=True)
            log("  removed broken link %s" % name)


def freeze(clean):
    clear_stray_links()
    if clean:
        for d in (DIST, WORK):
            if d.exists():
                log("removing %s" % d.name)
                shutil.rmtree(d, ignore_errors=True)

    spec = HERE / "automated_tracker.spec"
    if not spec.exists():
        fail("missing %s" % spec)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        str(spec),
        "--distpath", str(DIST),
        "--workpath", str(WORK),
        "--noconfirm",
    ]
    log("freezing (this takes a while - torch is ~3.6 GB)")
    log("  " + " ".join('"%s"' % c if " " in c else c for c in cmd))
    t0 = time.time()
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        fail("PyInstaller failed with exit code %d" % r.returncode)

    exe = DIST / APP_NAME / (APP_NAME + ".exe")
    if not exe.exists():
        fail("PyInstaller reported success but %s is missing" % exe)

    size = sum(f.stat().st_size for f in (DIST / APP_NAME).rglob("*") if f.is_file())
    log("built %s  (%.2f GB, %.1f min)"
        % (exe.name, size / 1024 ** 3, (time.time() - t0) / 60.0))
    return exe


def verify(exe):
    """
    Run the frozen exe's own self-test in a stand-in of the installed layout.

    The tool folders are linked in as directory junctions rather than copied, so
    this costs nothing, and they are removed again afterwards - leaving them
    would make Inno package COLMAP and FFmpeg twice.
    """
    app_dir = exe.parent
    linked = []
    made = []

    log("verifying the frozen build")
    try:
        # 02 VIDEOS is linked as well so the self-test has a real clip to extract
        # frames from - that check is the one that catches broken subprocess
        # handles, and it silently skips itself if the folder is empty.
        for name in PAYLOAD_DIRS + [p for p, _ in PAYLOAD_SUBDIRS] + ["02 VIDEOS"]:
            src = ROOT / name
            dst = app_dir / name
            if dst.exists() or not src.is_dir():
                continue
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)],
                               capture_output=True, text=True)
            if r.returncode == 0:
                linked.append(dst)
            else:
                log("  could not link %s: %s" % (name, r.stderr.strip()[:80]))
        for name in EMPTY_DIRS:
            d = app_dir / name
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                made.append(d)

        r = subprocess.run([str(exe), "--selftest"], capture_output=True, text=True,
                           timeout=300, cwd=str(app_dir))
        report = (app_dir / "selftest.txt")
        text = report.read_text(encoding="utf-8") if report.exists() else (r.stdout or "")
        for line in text.splitlines():
            log("  " + line)
        ok = "RESULT: ALL OK" in text
        if not ok:
            log("  self-test reported problems (exit code %d)" % r.returncode)
        return ok
    except subprocess.TimeoutExpired:
        log("  self-test timed out")
        return False
    except Exception as e:
        log("  verification could not run: %s" % e)
        return False
    finally:
        for d in linked:
            subprocess.run(["cmd", "/c", "rmdir", str(d)], capture_output=True)
        for d in made:
            try:
                d.rmdir()
            except Exception:
                pass
        try:
            (app_dir / "selftest.txt").unlink()
        except Exception:
            pass


def write_version_file():
    (HERE / "version.txt").write_text(APP_VERSION, encoding="utf-8")
    log("version.txt -> %s" % APP_VERSION)


def main():
    ap = argparse.ArgumentParser(description="Build the Automated Tracker executable.")
    ap.add_argument("--clean", action="store_true", help="delete dist/ and work/ first")
    ap.add_argument("--check", action="store_true", help="check prerequisites and exit")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip running the built exe's self-test")
    ap.add_argument("--console", action="store_true",
                    help="build a debug exe with a console so crashes are visible")
    args = ap.parse_args()

    log("root: %s" % ROOT)
    if not check_prereqs():
        fail("prerequisites missing - see above")
    if args.check:
        log("prerequisite check only - nothing built")
        return

    if args.console:
        os.environ["ATRACK_CONSOLE"] = "1"
        log("console build - the exe will show a terminal window")

    ensure_pyinstaller()
    make_icon()
    exe = freeze(args.clean)
    write_version_file()

    if not args.no_verify:
        if not verify(exe):
            fail("the built executable failed its own self-test - "
                 "do not ship this build")
        log("self-test passed")

    log("")
    log("done. executable: %s" % exe)
    log("next: Inno Setup compiles build/installer.iss into build/Output/")
    log("      (a setup exe plus numbered .bin slices - ship them together)")


if __name__ == "__main__":
    main()
