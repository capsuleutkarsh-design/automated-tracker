"""
A way to make the frozen build prove it can still track.

PyInstaller runs this before tracker_gui.py. It does nothing at all unless
ATRACK_VERIFY_TRACK is set in the environment, so the shipped app is unchanged;
build_app.py sets it to run one real GPU track inside the frozen process, which
then exits before a window is ever created.

This exists because the self-test only proves torch imports and reports a CUDA
device. Trimming CUDA libraries out of the bundle can leave exactly that
passing while the first convolution of a real track raises - which is what
happened when the precompiled cuDNN engine library was tried as an exclusion.
Nothing goes into the spec's TRIM_BINARIES without this run passing too.

A windowed build has nowhere to print, the same problem the self-test has, so
the report is written to verify_track.txt beside the exe.
"""
import os
import sys

if os.environ.get("ATRACK_VERIFY_TRACK"):
    import tempfile
    import time
    import traceback
    from pathlib import Path

    _lines = []

    def say(msg):
        _lines.append(msg)

    def _run():
        import torch
        say("frozen     : %s" % getattr(sys, "frozen", False))
        say("torch      : %s (CUDA %s)" % (torch.__version__, torch.version.cuda))
        if not torch.cuda.is_available():
            say("FAIL: no CUDA device in the frozen build")
            return 1
        say("device     : %s  sm_%d%d"
            % ((torch.cuda.get_device_name(0),) + torch.cuda.get_device_capability(0)))

        from core.app_paths import BASE_DIR
        videos = Path(BASE_DIR) / "02 VIDEOS"
        clips = sorted(videos.glob("*.mp4")) if videos.is_dir() else []
        if not clips:
            say("FAIL: no clip in 02 VIDEOS to track")
            return 1
        clip = clips[0]
        say("clip       : %s" % clip.name)

        import cotracker_2d
        out_dir = Path(tempfile.mkdtemp(prefix="attrack_verify_"))
        t0 = time.time()
        res = cotracker_2d.process_cotracker_2d(
            clip,
            config={"max_dimension": 480, "in_point": 0, "out_point": 24,
                    "grid_size": 6, "mode": "grid", "offline": True,
                    "fps": 30.0, "output_dir": str(out_dir)},
            log_callback=lambda m, c="#fff": None)
        say("track      : success=%s in %.1f s" % (res.get("success"), time.time() - t0))

        # Peak VRAM is the check that this really ran on the GPU: a silent
        # fallback to the CPU would report success and prove nothing.
        peak = torch.cuda.max_memory_allocated() / 1024 ** 2
        say("peak VRAM  : %.0f MB" % peak)
        if not res.get("success") or peak <= 0:
            say("FAIL: the track did not run on the GPU")
            return 1

        written = sorted(p.name for p in out_dir.rglob("*") if p.is_file())
        say("wrote      : %d file(s) in %s" % (len(written), out_dir))
        for n in written:
            say("             %s" % n)
        if not written:
            say("FAIL: the track wrote nothing")
            return 1
        say("RESULT: TRACK OK")
        return 0

    try:
        code = _run()
    except Exception:
        say(traceback.format_exc())
        say("RESULT: TRACK FAILED")
        code = 1

    text = "\n".join(_lines) + "\n"
    try:
        from core.app_paths import BASE_DIR
        (Path(BASE_DIR) / "verify_track.txt").write_text(text, encoding="utf-8")
    except Exception:
        pass
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(code)
