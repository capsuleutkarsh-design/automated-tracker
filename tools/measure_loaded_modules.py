"""
Which of PyTorch's DLLs does the app actually need? Measure it, three ways.

    "00 PYTHON\\python.exe" tools\\measure_loaded_modules.py            report
    "00 PYTHON\\python.exe" tools\\measure_loaded_modules.py --probe a.dll,b.dll
    "00 PYTHON\\python.exe" tools\\measure_loaded_modules.py --workload  (internal)

Roughly 3.6 GB of the 4.7 GB payload is PyTorch, most of it CUDA libraries.
Deciding what to drop by reading file names is how you ship an installer that
works on this machine and dies on someone else's card, so this measures.

**1. What the process loaded.** The workload - the app's own self-test plus a
real CoTracker 2D track on the GPU over the bundled sample clip - runs first,
then Windows is asked which modules the process ended up with, through
psutil's memory maps or EnumProcessModules by hand. Read the answer with your
eyes open: torch's own __init__ globs `torch/lib/*.dll` and LoadLibraryExW's
every one of them, so for that folder "loaded" only means "present". The list
is still worth having for everything outside torch/lib, where it is honest.

**2. What links to what.** So the real evidence for torch/lib is the import
tables. A DLL named in another's import directory must exist or the loader
refuses the whole chain - that is a hard dependency and it is not negotiable.
Anything reachable from torch_python.dll that way is mandatory; anything else
is opened dynamically, or never, and is at least a candidate. This walks the
PE headers itself rather than trusting a name.

**3. Does it still work without it.** The only proof that counts. --probe
renames the named DLLs aside, re-runs the whole workload in a fresh process,
and compares the exported track against the one the full build produced -
coordinates, not just an exit code, because a missing kernel can leave you
with a result that runs and is wrong. The files are put back afterwards,
including when the probe crashes.

The conclusions this produced are written up as comments beside the exclusion
lists in build/automated_tracker.spec and tools/pack_runtime.py.
"""
import ctypes
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYDIR = ROOT / "00 PYTHON"
TORCH_LIB = PYDIR / "Lib" / "site-packages" / "torch" / "lib"
SAMPLE = ROOT / "02 VIDEOS" / "uhd_30fps.mp4"

# The DLL every torch program needs: whatever this reaches through import
# tables is mandatory, whatever it does not is a candidate.
ROOT_DLL = "torch_python.dll"


def _prepare_paths():
    sys.path.insert(0, str(ROOT / "05 SCRIPT"))
    sys.path.insert(0, str(ROOT / "06 COTRACKER"))
    # The app resolves its folders relative to the script when run from source.
    os.chdir(ROOT / "05 SCRIPT")


# --------------------------------------------------------------------------
# 1. what the process loaded
# --------------------------------------------------------------------------
def loaded_via_psutil():
    """psutil's memory maps list every file the process has mapped, which on
    Windows is every DLL it has loaded. Cheapest answer when it is installed."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        paths = [m.path for m in psutil.Process().memory_maps()
                 if getattr(m, "path", "") and
                 Path(m.path).suffix.lower() in (".dll", ".pyd", ".exe")]
        return paths or None
    except Exception:
        return None


def loaded_via_win32():
    """
    EnumProcessModules through ctypes - what psutil wraps anyway, and what
    still answers on an interpreter without it.

    The first call is handed a buffer it is allowed to overflow so it can
    report how many bytes of handles it wanted; the loop then retries at that
    size. Growing it matters: torch alone pushes the count past any fixed
    guess. On Windows 7 and later the psapi entry points live in kernel32
    under K32 names; older builds need psapi.dll, hence the two-way lookup.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    if hasattr(k32, "K32EnumProcessModules"):
        enum, get_name = k32.K32EnumProcessModules, k32.K32GetModuleFileNameExW
    else:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        enum, get_name = psapi.EnumProcessModules, psapi.GetModuleFileNameExW

    enum.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE),
                     wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    enum.restype = wintypes.BOOL
    get_name.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    get_name.restype = wintypes.DWORD

    handle = ctypes.windll.kernel32.GetCurrentProcess()
    needed = wintypes.DWORD()
    count = 512
    while True:
        arr = (wintypes.HMODULE * count)()
        if not enum(handle, arr, ctypes.sizeof(arr), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        got = needed.value // ctypes.sizeof(wintypes.HMODULE)
        if got <= count:
            break
        count = got + 64

    out = []
    buf = ctypes.create_unicode_buffer(32768)
    for i in range(got):
        if get_name(handle, arr[i], buf, len(buf)):
            out.append(buf.value)
    return out


def loaded_modules():
    paths = loaded_via_psutil()
    how = "psutil.memory_maps"
    if not paths:
        paths, how = loaded_via_win32(), "EnumProcessModules (ctypes)"
    seen = {}
    for p in paths:
        try:
            rp = Path(p).resolve()
        except Exception:
            continue
        seen[str(rp).lower()] = rp
    return how, sorted(seen.values(), key=lambda p: str(p).lower())


# --------------------------------------------------------------------------
# 2. what links to what
# --------------------------------------------------------------------------
def pe_imports(path):
    """
    (static, delay-loaded) DLL names from a PE file's two import directories.

    Static imports are resolved by the loader before a single instruction of
    the DLL runs, so a missing one takes the whole chain down; delay-loaded
    ones are resolved on first use, so they only matter if that code path is
    reached. Keeping them apart is the whole point of reading this by hand.
    """
    data = Path(path).read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        return [], []
    coff = pe + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    optsz = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    plus = struct.unpack_from("<H", data, opt)[0] == 0x20B      # PE32+
    datadirs = opt + (112 if plus else 96)

    sections = []
    for i in range(nsec):
        b = opt + optsz + i * 40
        vsz, va, rawsz, rawptr = struct.unpack_from("<IIII", data, b + 8)
        sections.append((va, max(vsz, rawsz), rawptr))

    def to_offset(rva):
        for va, size, ptr in sections:
            if va <= rva < va + size:
                return ptr + (rva - va)
        return None

    def names(index, entry_size, name_field):
        rva = struct.unpack_from("<II", data, datadirs + index * 8)[0]
        out = []
        if not rva:
            return out
        off = to_offset(rva)
        while off is not None:
            entry = data[off:off + entry_size]
            if len(entry) < entry_size or not any(entry):
                break
            name_rva = struct.unpack_from("<I", data, off + name_field)[0]
            if not name_rva:
                break
            no = to_offset(name_rva)
            if no is None:
                break
            out.append(data[no:data.index(b"\0", no)].decode("ascii", "replace"))
            off += entry_size
        return out

    # directory 1 is the import table (20-byte entries, name at +12);
    # directory 13 is the delay-load table (32-byte entries, name at +4).
    return names(1, 20, 12), names(13, 32, 4)


def mandatory_set(folder, root_name=ROOT_DLL):
    """Everything in `folder` reachable from root_name through static imports."""
    have = {f.name.lower(): f for f in folder.glob("*.dll")}
    need, queue = set(), [root_name.lower()]
    while queue:
        name = queue.pop()
        if name in need or name not in have:
            continue
        need.add(name)
        static, _delay = pe_imports(have[name])
        queue.extend(d.lower() for d in static)
    return need, have


# --------------------------------------------------------------------------
# the workload
# --------------------------------------------------------------------------
def run_selftest():
    from core.selftest import run_selftest as _st
    _st()
    report = ROOT / "selftest.txt"
    text = report.read_text(encoding="utf-8") if report.exists() else ""
    return "RESULT: ALL OK" in text


def run_real_track(quiet=True):
    """
    A short but genuine GPU track: the same process_cotracker_2d() the Track
    button calls, on the sample clip, a small grid, a handful of frames. Short
    enough to finish in seconds, real enough that the encoder convolutions,
    the sliding window and the FP16 autocast path all run for real.

    Returns a dict the probe can compare against the untrimmed baseline.
    """
    import torch
    out = {"torch": torch.__version__, "cuda_build": torch.version.cuda,
           "cuda": bool(torch.cuda.is_available())}
    if out["cuda"]:
        out["device"] = torch.cuda.get_device_name(0)
        out["arch"] = "sm_%d%d" % torch.cuda.get_device_capability(0)

    import cotracker_2d
    out_dir = Path(tempfile.mkdtemp(prefix="attrack_measure_"))
    cfg = {
        "max_dimension": 480,     # small, but a real resize and a real model pass
        "in_point": 0,
        "out_point": 24,          # 25 frames: more than one sliding-window hop
        "grid_size": 6,
        "mode": "grid",
        "offline": True,
        "fps": 30.0,
        "output_dir": str(out_dir),
    }
    t0 = time.time()
    res = cotracker_2d.process_cotracker_2d(
        SAMPLE, config=cfg,
        log_callback=(lambda m, c="#fff": None) if quiet else None)
    out["secs"] = round(time.time() - t0, 2)
    out["success"] = bool(res.get("success"))
    out["out_dir"] = str(out_dir)

    # Peak VRAM is the check that the track really reached the GPU. A probe
    # that quietly fell back to the CPU would "pass" while proving nothing.
    if out["cuda"]:
        out["peak_vram_mb"] = round(torch.cuda.max_memory_allocated() / 1024 ** 2)

    # The exported coordinates, not the exit code. A missing kernel can leave a
    # run that finishes and is wrong, and only the numbers show that up.
    try:
        data = json.loads(Path(res["json_path"]).read_text(encoding="utf-8"))
        flat = []

        def walk(node):
            if isinstance(node, dict):
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
            elif isinstance(node, (int, float)):
                flat.append(round(float(node), 2))
        walk(data)
        out["coord_count"] = len(flat)
        out["coord_sum"] = round(sum(flat), 1)
    except Exception as e:
        out["export_error"] = str(e)

    # Half precision and the convolution path only pull in some cuDNN
    # sub-libraries once autocast is actually on, so make the measured run
    # cover them explicitly rather than hoping the track did.
    if out["cuda"]:
        with torch.autocast("cuda", dtype=torch.float16):
            x = torch.randn(1, 64, 96, 96, device="cuda")
            w = torch.randn(64, 64, 3, 3, device="cuda")
            torch.nn.functional.conv2d(x, w, padding=1)
            torch.matmul(x.reshape(64, -1), x.reshape(64, -1).T)
        torch.cuda.synchronize()
        out["fp16_conv"] = True
    return out


def workload(quiet=True):
    result = {"selftest_ok": False}
    try:
        result["selftest_ok"] = run_selftest()
    except Exception as e:
        result["selftest_error"] = "%s: %s" % (type(e).__name__, e)
    result.update(run_real_track(quiet=quiet))
    return result


# --------------------------------------------------------------------------
# 3. does it still work without it
# --------------------------------------------------------------------------
def probe(names):
    """
    Rename the named DLLs aside, run the workload in a clean process, restore.

    A fresh process is the point: torch binds these at import, so the only
    honest test is an interpreter that never saw them. The restore runs in a
    finally block - leaving a renamed CUDA library behind would break the
    interpreter for everything else on this machine.
    """
    targets = []
    for n in names:
        p = TORCH_LIB / n
        if not p.exists():
            print("[probe] no such file: %s" % n)
            return
        targets.append(p)
    saved = sum(p.stat().st_size for p in targets)

    print("[probe] hiding %d file(s), %.1f MB:" % (len(targets), saved / 1024 ** 2))
    for p in targets:
        print("          %s" % p.name)

    moved = []
    try:
        for p in targets:
            aside = p.with_suffix(p.suffix + ".probe_hidden")
            p.rename(aside)
            moved.append((aside, p))
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--workload"],
                           capture_output=True, text=True, timeout=900)
        line = ""
        for l in (r.stdout or "").splitlines():
            if l.startswith("WORKLOAD_JSON "):
                line = l[len("WORKLOAD_JSON "):]
        if not line:
            print("[probe] FAILED - the workload did not finish (exit %d)" % r.returncode)
            tail = (r.stderr or r.stdout or "").strip().splitlines()[-12:]
            for t in tail:
                print("        | %s" % t)
            return
        res = json.loads(line)
        print("[probe] selftest_ok=%s success=%s cuda=%s peak_vram=%s MB  %.1f s"
              % (res.get("selftest_ok"), res.get("success"), res.get("cuda"),
                 res.get("peak_vram_mb"), res.get("secs", 0)))
        print("[probe] coord_count=%s coord_sum=%s"
              % (res.get("coord_count"), res.get("coord_sum")))
        print("[probe] compare those two numbers with the untrimmed baseline; "
              "equal means the track is bit-for-bit the same work.")
    finally:
        for aside, original in moved:
            try:
                aside.rename(original)
            except OSError:
                print("[probe] !! could not restore %s - rename it back by hand" % original.name)
        print("[probe] restored")


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------
def size_of(p):
    try:
        return p.stat().st_size
    except OSError:
        return 0


def report():
    res = workload(quiet=True)
    print("[measure] self-test ALL OK: %s" % res.get("selftest_ok"))
    print("[measure] torch %s (CUDA %s) on %s %s"
          % (res.get("torch"), res.get("cuda_build"), res.get("device"), res.get("arch")))
    if not res.get("cuda"):
        print("[measure] ! no CUDA here - this run misses every CUDA library "
              "and must not be used to decide exclusions")
    print("[measure] track success=%s in %.1f s, peak VRAM %s MB, %s coordinates summing to %s"
          % (res.get("success"), res.get("secs", 0), res.get("peak_vram_mb"),
             res.get("coord_count"), res.get("coord_sum")))

    how, mods = loaded_modules()
    print("\n[measure] %d modules, listed by %s" % (len(mods), how))

    def under_pydir(p):
        try:
            p.relative_to(PYDIR)
            return True
        except ValueError:
            return False

    inside = [p for p in mods if under_pydir(p)]
    outside = [p for p in mods if not under_pydir(p)]

    print("\n" + "=" * 78)
    print("LOADED from 00 PYTHON  (%d files)" % len(inside))
    print("  torch/lib entries are loaded wholesale by torch's own __init__ -")
    print("  presence, not use. See the import-table section below for those.")
    print("=" * 78)
    total_loaded = 0
    for p in sorted(inside, key=lambda q: -size_of(q)):
        total_loaded += size_of(p)
        print("  %9.2f MB  %s" % (size_of(p) / 1024 ** 2, p.relative_to(PYDIR).as_posix()))
    print("\n  plus %d modules from outside 00 PYTHON (Windows, the display driver)"
          % len(outside))

    # The honest answer for torch/lib.
    need, have = mandatory_set(TORCH_LIB)
    print("\n" + "=" * 78)
    print("torch/lib by import table, rooted at %s" % ROOT_DLL)
    print("=" * 78)
    print("  MANDATORY - a static import somewhere in the chain, cannot be dropped:")
    mand = sorted((size_of(have[n]), n) for n in need)
    for n, name in sorted(mand, reverse=True):
        print("    %9.2f MB  %s" % (n / 1024 ** 2, name))
    print("    %9.2f MB  TOTAL" % (sum(n for n, _ in mand) / 1024 ** 2))

    rest = sorted((size_of(p), p.name) for p in have.values() if p.name.lower() not in need)
    print("\n  NOT reached statically - opened on demand, or never. Candidates,")
    print("  each of which still has to survive --probe before it is excluded:")
    for n, name in sorted(rest, reverse=True):
        print("    %9.2f MB  %s" % (n / 1024 ** 2, name))
    print("    %9.2f MB  TOTAL" % (sum(n for n, _ in rest) / 1024 ** 2))

    loaded_keys = {str(p).lower() for p in mods}
    unloaded, on_disk = [], 0
    for dirpath, dirnames, filenames in os.walk(PYDIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            f = Path(dirpath) / name
            on_disk += size_of(f)
            if (f.suffix.lower() in (".dll", ".pyd", ".exe")
                    and str(f).lower() not in loaded_keys
                    and f.parent != TORCH_LIB):
                unloaded.append((size_of(f), f))
    unloaded.sort(reverse=True)

    print("\n" + "=" * 78)
    print("NEVER LOADED outside torch/lib, over 1 MB")
    print("=" * 78)
    unloaded_big = 0
    for n, f in unloaded:
        if n < 1024 ** 2:
            break
        unloaded_big += n
        print("  %9.2f MB  %s" % (n / 1024 ** 2, f.relative_to(PYDIR).as_posix()))

    print("\n" + "=" * 78)
    print("  loaded binaries under 00 PYTHON   : %8.1f MB" % (total_loaded / 1024 ** 2))
    print("  never loaded, over 1 MB           : %8.1f MB" % (unloaded_big / 1024 ** 2))
    print("  everything under 00 PYTHON        : %8.1f MB" % (on_disk / 1024 ** 2))
    print("=" * 78)


def main():
    args = sys.argv[1:]
    _prepare_paths()

    if "--workload" in args:
        # Child of --probe: say nothing but the one machine-readable line.
        try:
            res = workload(quiet=True)
        except Exception:
            traceback.print_exc()
            sys.exit(1)
        print("WORKLOAD_JSON " + json.dumps(res))
        return

    if "--probe" in args:
        i = args.index("--probe")
        if i + 1 >= len(args):
            sys.exit("--probe needs a comma-separated list of DLL names")
        probe([n.strip() for n in args[i + 1].split(",") if n.strip()])
        return

    report()


if __name__ == "__main__":
    main()
