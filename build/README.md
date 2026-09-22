# Building Automated Tracker

Produces a standalone `Automated_Tracker.exe` and a Windows installer.

## Quick start

Double-click **`BUILD.bat`**. It runs two steps:

1. **Python** — freezes `05 SCRIPT/tracker_gui.py` with PyInstaller into
   `build/dist/Automated_Tracker/`
2. **Inno Setup** — packages that, plus COLMAP, FFmpeg and the CoTracker
   weights, into `build/Output/AutomatedTracker_Setup_<version>.exe` **and**
   `AutomatedTracker_Setup_<version>-1.bin`, `-2.bin`, ...

The installer is the exe *plus* every `.bin` beside it. The payload (PyTorch,
the CoTracker model, COLMAP, FFmpeg) compresses to more than 2 GB, and Inno
Setup cannot write a single setup exe that large, so it splits the data into
`.bin` slices and leaves a small exe that reads them. Ship the whole `Output`
folder together — the exe on its own installs nothing.

```
BUILD.bat              full build (exe + installer)
BUILD.bat clean        wipe dist/ and work/ first, then full build
BUILD.bat exe          stop after the executable
BUILD.bat installer    skip freezing, re-run Inno on the last build
BUILD.bat check        verify prerequisites only, build nothing

extra words, combinable with the above in any order:
BUILD.bat ... console  debug exe with a console window (build_app.py --console)
BUILD.bat ... noverify skip the built exe's self-test     (build_app.py --no-verify)
```

Run `BUILD.bat check` first if you are unsure — it reports every missing piece
at once instead of failing part way through.

## What you need

| | |
|---|---|
| Python | the bundled `00 PYTHON` (used automatically), or any 3.11+ on PATH |
| PyInstaller | installed automatically on first build |
| Inno Setup | 5 or 6, from <https://jrsoftware.org/isdl.php> |
| Disk | ~12 GB free during the build; the installer (exe + `.bin` slices) is several GB |
| Time | 10–25 minutes for a clean build (PyTorch is ~3.6 GB) |

## Files here

| File | Purpose |
|---|---|
| `BUILD.bat` | the orchestrator — run this |
| `build_app.py` | the Python step: PyInstaller, icon, version stamp |
| `automated_tracker.spec` | PyInstaller configuration |
| `installer.iss` | Inno Setup configuration |
| `app_icon.ico` | generated on first build |
| `dist/`, `work/`, `Output/` | build products (git-ignored) |

## How the installed app is laid out

```
C:\Program Files\Automated Tracker\
    Automated_Tracker.exe        <- the frozen app
    _internal\                   <- PyInstaller runtime (PyTorch, Qt, ...)
    01 COLMAP\                   <- solver binaries
    02 VIDEOS\                   <- your footage goes here  (writable)
    03 FFMPEG\                   <- frame extraction
    04 SCENES\                   <- solved output          (writable)
    06 COTRACKER\checkpoints\    <- the two .pth model files
```

This matters. `05 SCRIPT/core/app_paths.py` resolves the tracker's folders from
`sys.executable` when frozen, so **the exe must sit at the root of that folder**,
beside `01 COLMAP` and the rest. Moving the exe on its own will break it.

COLMAP, FFmpeg and the `.pth` checkpoints stay as loose folders on purpose — the
app shells out to the binaries and loads the weights by path, so burying them
inside the exe would not work.

`02 VIDEOS` and `04 SCENES` are installed with user-modify permissions, because
the app writes into them and whoever runs it will not be an administrator.

## Building just the exe

```
"00 PYTHON\python.exe" build\build_app.py           # build
"00 PYTHON\python.exe" build\build_app.py --clean   # rebuild from scratch
"00 PYTHON\python.exe" build\build_app.py --check   # prerequisites only
```

The result is `build/dist/Automated_Tracker/Automated_Tracker.exe`. To test it
before making an installer, copy the whole `Automated_Tracker` folder's contents
to the tracker root so the exe sits beside `01 COLMAP`, then run it.

## Building just the installer

```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss
```

or `BUILD.bat installer`.

## Version number

The version lives in exactly one place: `APP_VERSION` in
`05 SCRIPT/core/version.py`. The app shows it in the title bar, status bar and
About box; `build_app.py` imports it and writes `build/version.txt`, which
`installer.iss` reads (and refuses to compile without); `tools/pack_runtime.py`
names the runtime zips with it; `SETUP.bat` parses the same line to pick the
matching GitHub release. Bump it there, build, then tag the release with the
bare number (`1.1.0`, no `v`).

## Logs and crash reports

The built app has no console. Everything it prints goes to
`%LOCALAPPDATA%\AutomatedTracker\logs\app.log` (rotating, 2 MB x 3), and an
unhandled exception writes `error_<timestamp>.log` in the same folder and shows
a dialog with that path. Ask users for those files. `--selftest` still writes
`selftest.txt` next to the exe.

## Troubleshooting

**"no Python found"** — the build looks for `00 PYTHON\python.exe` first, then
`python` on PATH. Restore the bundled interpreter or install Python 3.11+.

**"Inno Setup compiler (ISCC.exe) not found"** — install Inno Setup, or compile
`installer.iss` by hand. The exe from step 1 is still usable.

**Inno fails at the very end with a size error, or `Output` holds only a
2 GB exe and no `.bin` files** — disk spanning got switched off. Inno Setup's
hard limit for a single setup exe is 2,097,152,000 bytes and this payload is
above it. `installer.iss` must keep `DiskSpanning=yes`.

**Running the installer says a `.bin` file is missing** — the setup exe and its
`.bin` slices were separated. They have to sit in the same folder.

**The built exe starts and immediately exits** — run it from a terminal to see
the error. The most common cause is a missing module that PyInstaller could not
discover; add it to `hidden` in `automated_tracker.spec` and rebuild.

**The exe runs but cannot find COLMAP or the checkpoints** — it is not sitting
at the root of the install folder. See the layout above.

**A module is missing only in the frozen build** — PyInstaller follows static
imports. Anything imported dynamically has to be listed in `hidden` in the spec.
`cotracker`, `timm` and `pxr` are already collected that way.
