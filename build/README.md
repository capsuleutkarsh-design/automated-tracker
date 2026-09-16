# Building Automated Tracker

Produces a standalone `Automated_Tracker.exe` and a Windows installer.

## Quick start

Double-click **`BUILD.bat`**. It runs two steps:

1. **Python** — freezes `05 SCRIPT/tracker_gui.py` with PyInstaller into
   `build/dist/Automated_Tracker/`
2. **Inno Setup** — packages that, plus COLMAP, FFmpeg and the CoTracker
   weights, into `build/Output/AutomatedTracker_Setup_<version>.exe`

```
BUILD.bat              full build (exe + installer)
BUILD.bat clean        wipe dist/ and work/ first, then full build
BUILD.bat exe          stop after the executable
BUILD.bat installer    skip freezing, re-run Inno on the last build
BUILD.bat check        verify prerequisites only, build nothing
```

Run `BUILD.bat check` first if you are unsure — it reports every missing piece
at once instead of failing part way through.

## What you need

| | |
|---|---|
| Python | the bundled `00 PYTHON` (used automatically), or any 3.11+ on PATH |
| PyInstaller | installed automatically on first build |
| Inno Setup | 5 or 6, from <https://jrsoftware.org/isdl.php> |
| Disk | ~12 GB free during the build; the installer itself is several GB |
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

Set `APP_VERSION` near the top of `build_app.py`. It is written to
`build/version.txt`, which `installer.iss` reads, so both halves stay in step.

## Troubleshooting

**"no Python found"** — the build looks for `00 PYTHON\python.exe` first, then
`python` on PATH. Restore the bundled interpreter or install Python 3.11+.

**"Inno Setup compiler (ISCC.exe) not found"** — install Inno Setup, or compile
`installer.iss` by hand. The exe from step 1 is still usable.

**The built exe starts and immediately exits** — run it from a terminal to see
the error. The most common cause is a missing module that PyInstaller could not
discover; add it to `hidden` in `automated_tracker.spec` and rebuild.

**The exe runs but cannot find COLMAP or the checkpoints** — it is not sitting
at the root of the install folder. See the layout above.

**A module is missing only in the frozen build** — PyInstaller follows static
imports. Anything imported dynamically has to be listed in `hidden` in the spec.
`cotracker`, `timm` and `pxr` are already collected that way.
