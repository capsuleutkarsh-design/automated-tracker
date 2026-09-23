"""
Pack the bundled runtime - the folders git ignores because they are too big for
GitHub - into zip parts that each stay under GitHub's 2 GB release-asset limit.

    "00 PYTHON\\python.exe" tools\\pack_runtime.py

Writes build/Runtime/:
    runtime_<version>_part01.zip, part02.zip, ...   independent zips, each
                                                    holding a slice of the files
    runtime.parts.txt                               one line per part:
                                                    <name> <sha256> <bytes>

Upload every part and runtime.parts.txt to the GitHub release. SETUP.bat on a
fresh clone downloads them and unpacks each one into the repository root.
"""
import hashlib
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "Runtime"

# Single source of the version: 05 SCRIPT/core/version.py
sys.path.insert(0, str(ROOT / "05 SCRIPT"))
from core.version import APP_VERSION  # noqa: E402

# Everything the app needs that is not in git. Paths are relative to the root
# and unpack to the same place.
FOLDERS = [
    "00 PYTHON",
    "01 COLMAP",
    "03 FFMPEG",
    "06 COTRACKER",
    "05 SCRIPT/gui/assets",
]
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "pip-cache", ".cache"}
SKIP_SUFFIX = {".pyc", ".pyo"}
SKIP_NAMES = {"README.md"}          # the placeholder READMEs live in git

# Build-only packages and unused binaries that would otherwise ship in the
# runtime: PyInstaller and its hooks are needed to *make* the exe, never to run
# the app, and ffplay.exe is a 228 MB player nothing calls.
SKIP_DIR_PREFIXES = ("PyInstaller", "_pyinstaller_hooks_contrib",
                     "pyinstaller-", "pyinstaller_hooks_contrib-")
SKIP_FILES = {"ffplay.exe",
                 # imageio ships its own FFmpeg; the app is pointed at the
                 # one in 03 FFMPEG instead (app_paths).
                 "ffmpeg-win-x86_64-v7.1.exe"}

# --- CUDA libraries the app never reaches ---------------------------------
# The same four the frozen build drops, for the same measured reasons. Keep
# this list and TRIM_BINARIES in build/automated_tracker.spec identical - the
# zips and the installer have to ship the same PyTorch or a bug reproduces in
# one and not the other. The reasoning is written out in full in the spec;
# the short version is that tools/measure_loaded_modules.py ran the self-test
# and a real GPU track with each of these renamed aside and got a byte-equal
# track back, while the roadmap's headline candidates - the 562 MB precompiled
# cuDNN engine library and cuBLASLt - both turned out to be load-bearing.
SKIP_TORCH_LIBS = {
    "cudnn_adv64_9.dll",            # 230 MB  cuDNN's RNN and fused-attention engines
    "cusolvermg64_11.dll",          #  75 MB  multi-GPU cuSOLVER; torch has no bindings
    "curand64_10.dll",              #  60 MB  host cuRAND; torch's GPU RNG is its own
    "cupti64_2024.1.0.dll",         #   4 MB  the profiler interface, torch.profiler only
    # Unreachable now that the app disables cuDNN (see the spec for the
    # measurements). The rest of cuDNN stays: torch_cuda imports the shim
    # statically and torch will not import without it.
    "cudnn_engines_precompiled64_9.dll",
    "cudnn_heuristic64_9.dll",
}

# --- Qt that only the source runtime carries ------------------------------
# The zips ship all 640 MB of PySide6 where the frozen build ships 92 MB,
# because PyInstaller follows the imports and the packer just walks the folder.
# The app imports QtCore, QtGui and QtWidgets and nothing else, so the whole
# Chromium-based QtWebEngine stack - the DLL, its 101 MB of .pak resources and
# its 44 MB of locale files - is dead weight, as are Qt's designer and
# translation executables and the header/typesystem data shiboken uses to
# *generate* bindings. The frozen build is the second opinion: PyInstaller's
# own dependency analysis of this same source shipped none of it either.
#
# This list has no counterpart in the spec on purpose: PyInstaller already
# drops all of it.
def _skip_pyside(path):
    rel = path.as_posix()
    if "/PySide6/" not in rel:
        return False
    name = path.name.lower()
    if "webengine" in name or "/PySide6/resources/" in rel:
        return True
    if "/PySide6/translations/qtwebengine_locales/" in rel:
        return True
    # Qt's own tools: designer, linguist, assistant, the qml utilities. They
    # build and translate Qt apps; they never run one. QtWebEngineProcess.exe
    # is caught by the webengine test above.
    if path.suffix.lower() == ".exe" and path.parent.name == "PySide6":
        return True
    # Binding-generator input, not runtime files.
    if any(("/PySide6/%s/" % d) in rel for d in ("include", "doc", "glue", "typesystems", "metatypes")):
        return True
    return False


def _skip_dir(name):
    return name in SKIP_DIRS or name.lower().startswith(tuple(p.lower() for p in SKIP_DIR_PREFIXES))


def _skip_file(p):
    if p.name.lower() in SKIP_FILES:
        return True
    if (p.name.lower() in SKIP_TORCH_LIBS
            and p.parent.name.lower() == "lib" and p.parent.parent.name.lower() == "torch"):
        return True
    # torch/bin is the wheel's build tools - a duplicate fbgemm.dll and
    # protoc.exe. Nothing loads them.
    if p.parent.name.lower() == "bin" and p.parent.parent.name.lower() == "torch":
        return True
    return _skip_pyside(p)

# GitHub refuses release assets over 2 GB. Roll to a new part before a file
# could push the current one past this, assuming the worst case (no compression).
PART_LIMIT = 1_900_000_000


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect():
    files = []
    for folder in FOLDERS:
        base = ROOT / folder
        if not base.is_dir():
            sys.exit("missing folder: %s" % folder)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not _skip_dir(d)]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix in SKIP_SUFFIX or (name in SKIP_NAMES and p.parent == base):
                    continue
                if _skip_file(p):
                    continue
                files.append(p)
    return files


def main():
    files = collect()
    total = sum(f.stat().st_size for f in files)
    print("packing %d files, %.2f GB uncompressed" % (len(files), total / 1024 ** 3))
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("runtime_*_part*.zip"):
        old.unlink()

    parts = []
    state = {"zf": None, "n": 0}
    t0 = time.time()

    def open_part():
        state["n"] += 1
        name = OUT / ("runtime_%s_part%02d.zip" % (APP_VERSION, state["n"]))
        state["zf"] = zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED, compresslevel=6)
        parts.append(name)
        print("  -> %s" % name.name, flush=True)

    open_part()
    done = 0
    for f in files:
        size = f.stat().st_size
        zf = state["zf"]
        if zf.fp.tell() > 0 and zf.fp.tell() + size > PART_LIMIT:
            zf.close()
            open_part()
            zf = state["zf"]
        zf.write(f, f.relative_to(ROOT).as_posix())
        prev = done
        done += size
        if int(done * 20 / total) != int(prev * 20 / total):
            print("     %3d%%  %.1f min" % (done * 100 / total, (time.time() - t0) / 60), flush=True)
    state["zf"].close()

    lines = []
    for p in parts:
        n = p.stat().st_size
        if n >= 2_000_000_000:
            sys.exit("%s is %d bytes - over GitHub's 2 GB limit, lower PART_LIMIT" % (p.name, n))
        lines.append("%s %s %d" % (p.name, sha256(p), n))
        print("  %-32s %6.2f GB" % (p.name, n / 1024 ** 3))
    (OUT / "runtime.parts.txt").write_text("\n".join(lines) + "\n", encoding="ascii")
    print("wrote runtime.parts.txt  (%.1f min)" % ((time.time() - t0) / 60))
    print("upload every file in %s to the GitHub release" % OUT)


if __name__ == "__main__":
    main()
