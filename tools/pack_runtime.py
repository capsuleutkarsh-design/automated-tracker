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

sys.path.insert(0, str(ROOT / "build"))
try:
    from build_app import APP_VERSION
except Exception:
    APP_VERSION = "1.1.0"

# Everything the app needs that is not in git. Paths are relative to the root
# and unpack to the same place.
FOLDERS = [
    "00 PYTHON",
    "01 COLMAP",
    "03 FFMPEG",
    "06 COTRACKER",
    "05 SCRIPT/gui/assets",
]
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache"}
SKIP_SUFFIX = {".pyc", ".pyo"}
SKIP_NAMES = {"README.md"}          # the placeholder READMEs live in git

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
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                p = Path(dirpath) / name
                if p.suffix in SKIP_SUFFIX or (name in SKIP_NAMES and p.parent == base):
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
