"""
The background jobs the main window starts.

Four QThreads that exist for one reason: none of them may run on the GUI
thread. Copying a multi-GB plate, asking GitHub about a release, writing a full
set of exports and putting one tracked point back through CoTracker are all
things that used to freeze the window while they happened.

The window owns the wiring - it connects the signals, it decides when to start
one and what to do when it finishes - so what lives here is only the work
itself.
"""

import shutil
import logging
import datetime
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from core.version import APP_VERSION
from gui.theme import ACCENT, ERR, WARN

log = logging.getLogger("tracker_gui")


class MediaCopyWorker(QThread):
    """
    Copies clips into 02 VIDEOS off the GUI thread. A multi-GB plate used to
    freeze the window for the whole copy, and a permission error vanished.

    jobs: list of (src_path, overwrite)
    """
    progress_signal = Signal(str)            # status text
    finished_signal = Signal(list, list)     # copied names, "name: error" strings

    def __init__(self, jobs, dest_dir):
        super().__init__()
        self.jobs = [(Path(s), bool(o)) for s, o in jobs]
        self.dest_dir = Path(dest_dir)

    def run(self):
        copied, errors = [], []
        total = len(self.jobs)
        for i, (src, overwrite) in enumerate(self.jobs, start=1):
            dst = self.dest_dir / src.name
            try:
                self.dest_dir.mkdir(parents=True, exist_ok=True)
                if src.resolve() == dst.resolve():
                    continue
                if dst.exists() and not overwrite:
                    errors.append("%s: already in the media pool (not replaced)" % src.name)
                    continue
                size_mb = src.stat().st_size / (1024 * 1024)
                self.progress_signal.emit(
                    "Importing %d/%d: %s (%.0f MB)..." % (i, total, src.name, size_mb))
                shutil.copy2(src, dst)
                copied.append(src.name)
            except Exception as e:
                errors.append("%s: %s" % (src.name, e))
        self.finished_signal.emit(copied, errors)


class UpdateCheckWorker(QThread):
    """
    Asks GitHub whether a newer release exists (2.6).

    Installs are manual, so somebody can sit on an old build for months without
    knowing. Opt-in, off the GUI thread, and silent about every failure: a
    missing network is not worth a dialog.
    """
    found_signal = Signal(dict)

    def run(self):
        try:
            from core.update_check import check_for_update
            info = check_for_update(APP_VERSION)
        except Exception:
            info = None
        if info:
            self.found_signal.emit(info)


class ReExportWorker(QThread):
    """
    Write a fresh export folder from a solve that already exists (roadmap 1.4).

    Re-exporting is minutes of writing, not seconds, because the STMaps and the
    undistorted plate are full-resolution images - so it runs here rather than
    on the GUI thread, like every other job in this window.

    The existing sparse model is COPIED into a new timestamped folder rather
    than exported over: a scene transform or an overscan the artist regrets must
    never cost them the export they already handed to comp.
    """
    log_signal = Signal(str, str)
    finished_signal = Signal(bool, str)     # success, message

    def __init__(self, source_dir, shot_dir, options):
        super().__init__()
        self.source_dir = Path(source_dir)
        self.shot_dir = Path(shot_dir)
        self.options = dict(options)
        self.output_dir = None

    def run(self):
        try:
            from export_tools import export_all_formats, source_sequence_plate

            stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_dir = self.shot_dir / "3D_CAMERA_TRACK" / stamp
            src_sparse = self.source_dir / "sparse"
            if not src_sparse.is_dir():
                self.finished_signal.emit(
                    False, "The solve in %s has no sparse model to export." % self.source_dir.name)
                return
            out_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_sparse, out_dir / "sparse", dirs_exist_ok=True)
            self.output_dir = out_dir
            self.log_signal.emit("▶ Re-exporting into 3D_CAMERA_TRACK/%s ..." % stamp, ACCENT)

            video = self.options.get("video_path")
            plate = None
            if video is not None and Path(video).is_dir():
                plate = source_sequence_plate(Path(video))

            res = export_all_formats(
                out_dir,
                blender_path=self.options.get("blender_path"),
                log_callback=lambda m, c: self.log_signal.emit(m, c),
                fps=self.options.get("fps"),
                start_frame=self.options.get("start_frame"),
                colmap_exe=self.options.get("colmap_exe"),
                frame_step=self.options.get("frame_step", 1),
                source_sequence=plate,
                scene_transform=self.options.get("scene_transform"),
                overscan=self.options.get("overscan", 0.0),
                write_undistort=self.options.get("write_undistort", False),
                pixel_aspect=self.options.get("pixel_aspect", 1.0),
            )
            if not res.get("success"):
                self.finished_signal.emit(
                    False, "Re-export failed: %s" % res.get("error", "unknown error"))
                return

            # _latest is what every Open Output button and the media table read,
            # so a re-export that did not move it would look like it did nothing.
            try:
                latest_dir = self.shot_dir / "3D_CAMERA_TRACK" / "_latest"
                shutil.rmtree(latest_dir, ignore_errors=True)
                latest_dir.mkdir(parents=True, exist_ok=True)
                for f in out_dir.iterdir():
                    if f.is_file():
                        shutil.copy2(f, latest_dir / f.name)
                for sub in ("sparse", "undistorted"):
                    if (out_dir / sub).exists():
                        shutil.copytree(out_dir / sub, latest_dir / sub, dirs_exist_ok=True)
            except Exception as sync_err:
                self.log_signal.emit("Notice: could not refresh _latest: %s" % sync_err, WARN)

            self.finished_signal.emit(
                True, "✔ Re-exported into 3D_CAMERA_TRACK/%s" % stamp)
        except Exception as e:
            log.exception("re-export failed")
            for line in traceback.format_exc().rstrip().splitlines():
                self.log_signal.emit("   %s" % line, ERR)
            self.finished_signal.emit(False, "Re-export raised: %s" % e)


class TrackCorrectionWorker(QThread):
    """
    Fix a drifting 2D track without re-solving the shot (roadmap 2.1).

    Two jobs, because both are the wrong thing to do on the GUI thread: a
    re-track puts one point back through CoTracker on the GPU, and a re-export
    writes every delivery format again (the Blender script for a dense grid is
    megabytes of it).

    The result is worked on as a COPY and handed back on finish. The canvas
    paints straight out of those arrays, and a worker writing into them while
    the window repaints is how a track flickers half-updated on screen.
    """
    log_signal = Signal(str, str)
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, str)

    def __init__(self, job):
        super().__init__()
        self.job = dict(job)
        self.is_cancelled = False
        self.result = None
        self.output_dir = None

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        import copy as _copy
        try:
            import cotracker_2d as c2d
        except ImportError as e:
            self.finished_signal.emit(False, "CoTracker engine failed to import: %s" % e)
            return

        job = self.job
        result = _copy.deepcopy(job["result"])
        try:
            if job["mode"] == "retrack":
                info = c2d.retrack_correction(
                    job["video_path"], result, job["layer_key"], job["point_index"],
                    job["frame_t"], job["x"], job["y"],
                    config=job.get("config"), backwards=job.get("backwards", False),
                    log_callback=lambda m, c: self.log_signal.emit(m, c),
                    progress_callback=lambda v, t: self.progress_signal.emit(int(v), t),
                    cancel_check=lambda: self.is_cancelled,
                )
                self.result = result
                parts = ", ".join("%s %d frame(s)" % (word, n)
                                  for word, n in info["spliced"].items())
                self.finished_signal.emit(
                    True, "✔ Re-tracked point #%d from frame %d (%s)."
                    % (int(job["point_index"]) + 1, info["frame"], parts))
                return

            stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            out_dir = Path(job["track_root"]) / stamp
            self.progress_signal.emit(20, "Writing the corrected exports...")
            paths = c2d.export_corrected_result(
                result, job["layers_meta"], out_dir,
                fps=job.get("fps", 24.0), images_dir=job.get("images_dir"),
                timeline_start=job.get("timeline_start", 1),
                source_name=job.get("source_name", ""),
                latest_dir=Path(job["track_root"]) / "_latest",
                log=lambda m, c: self.log_signal.emit(m, c),
            )
            self.result = result
            self.output_dir = out_dir
            self.progress_signal.emit(100, "Corrected exports written")
            self.finished_signal.emit(
                True, "✔ Re-exported the corrected tracks into 2D_POINT_TRACK/%s "
                      "(%s)." % (stamp, Path(paths["json_path"]).name))
        except c2d.TrackingCancelled:
            self.finished_signal.emit(False, "⏹ Correction cancelled.")
        except Exception as e:
            log.exception("track correction failed")
            for line in traceback.format_exc().rstrip().splitlines():
                self.log_signal.emit("   %s" % line, ERR)
            self.finished_signal.emit(False, "✖ %s" % e)
