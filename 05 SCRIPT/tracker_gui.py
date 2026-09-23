"""
Automated Photogrammetry & 2D AI Motion Tracker — VFX Studio Edition
Modular Main Coordinator & GUI Application Window
"""

import sys
import os
import shutil
import random
import logging
import logging.handlers
import threading
import traceback
import datetime
from pathlib import Path

import numpy as np

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QTabWidget, QMessageBox, QFileDialog, QTableWidgetItem,
        QInputDialog, QColorDialog, QListWidgetItem, QStackedLayout,
        QGraphicsOpacityEffect, QDoubleSpinBox, QPushButton
    )
    from PySide6.QtCore import Qt, QTimer, QSettings, QThread, QObject, Signal
    from PySide6.QtGui import (
        QColor, QImage, QPixmap, QDragEnterEvent, QDropEvent
    )
except ImportError:
    print("[ERROR] PySide6 is not installed. Please run LAUNCH_UI.bat (it uses the bundled "
          "00 PYTHON interpreter) or install it via: pip install PySide6")
    sys.exit(1)

# Running from source, the package folder has to be importable. Frozen, the
# modules are already inside the executable.
_here = Path(__file__).resolve().parent
if not getattr(sys, 'frozen', False) and str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from core.app_paths import (
    BASE_DIR, COLMAP_DIR, VIDEOS_DIR, FFMPEG_DIR, SCENES_DIR,
    colmap_exe, ffmpeg_exe, colmap_bat, colmap_plugins_dir, ensure_runtime_dirs,
    logs_dir, thumbs_dir,
)
from core.version import APP_VERSION, display_version

# The self-test exists to diagnose an install that will not start, so it runs
# before the GUI package is imported - a broken gui module must not hide it.
if __name__ == "__main__" and "--selftest" in sys.argv:
    from core.selftest import run_selftest
    sys.exit(run_selftest())

COLMAP_EXE = colmap_exe()
FFMPEG_EXE = ffmpeg_exe()
COLMAP_BAT = colmap_bat()

# A fresh install ships without these; the app writes into them.
ensure_runtime_dirs()

log = logging.getLogger("tracker_gui")

# Import Modular GUI & Core Components
from gui.theme import (
    DARK_STUDIO_QSS, OK, ERR, WARN, ACCENT, TEXT_DIM, apply_dark_palette,
)
from gui.tab_3d import build_3d_tab
from gui.tab_2d import build_2d_tab
from core.tracking_layer import TrackingLayer
from core.workers import TrackerWorker, CoTrackerWorker, FrameExtractorWorker
from core.hardware import gpu_monitor
from core.proc import run_hidden, popen_gui
from core import media_info
from core.media_info import probe_fps, probe_frame_count, detect_sequence_start, sequence_files
from core.presets import PRESETS, DEFAULT_PRESET
from core import project as project_file
from core import scene_transform
from gui.canvas import project_solved_points
from core.media_pool import (
    VIDEO_EXTS, scan_media_pool, find_latest_output,
    thumb_path, prune_thumb_cache,
)

# QSettings identity - registry key on Windows.
SETTINGS_ORG = "AutomatedTracker"
SETTINGS_APP = "AutomatedTracker"


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


class TrackerMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Automated Tracker %s — VFX Studio (3D SfM & 2D AI Motion Tracker)"
                            % display_version())
        self.resize(1280, 850)
        self.setMinimumSize(1000, 680)
        self.setAcceptDrops(True)
        self.worker_3d = None
        self.worker_2d = None
        # Exactly one frame extractor at a time. A request that arrives while
        # one is running is parked here and started when it finishes.
        self.frame_extractor = None
        self._pending_extract = None
        self.copy_worker = None
        self.reexport_worker = None
        self.update_worker = None
        self._check_updates = False
        self.correction_worker = None
        self._pending_imports = []
        self._closing = False

        # The last 2D result for the shot in view (roadmap 2.1), loaded back
        # from tracks_2d.json so a correction still works after the app has
        # been closed and reopened. `_track_layer_map` says which layer in the
        # window owns which block of the file, `_track_problem` is the sentence
        # shown when the file does not match the layers, and `_last_correction`
        # is what the Re-track buttons act on when the playhead is not sitting
        # on a corrected frame.
        self._track_result = None
        self._track_layer_map = None
        self._track_problem = ""
        self._last_correction = None

        # Scene setup (1.4). `_solve` is the newest solve for the shot in view -
        # its camera_track.json, its points in COLMAP's own world and a lookup
        # from timeline frame to solved camera - loaded only when a solve
        # exists. `_scene_picks` holds indices into those points, per purpose.
        self._solve = None
        self._scene_transform = None
        self._scene_picks = {"scale": [], "ground": [], "origin": []}
        self._scene_pick_mode = None

        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self._last_clip = self.settings.value("media/last_clip", "", type=str)
        # Settings are written a moment after the last change, not on every tick.
        self._settings_timer = QTimer(self)
        self._settings_timer.setSingleShot(True)
        self._settings_timer.setInterval(1500)
        self._settings_timer.timeout.connect(self._save_settings)

        # The shot whose project file the window is currently holding, and the
        # same debounce for it: the artist should never have to press save, but
        # nor should a mask drag write the file on every mouse move.
        self._project_shot = None
        self._restoring_project = False
        self._project_save_failed = False
        # True once a shot with a project file on disk is loaded, so the
        # auto-detected timeline start does not overwrite the saved one.
        self._project_loaded = False
        self._project_timer = QTimer(self)
        self._project_timer.setSingleShot(True)
        self._project_timer.setInterval(1500)
        self._project_timer.timeout.connect(self._save_project)

        # Video Player State for 2D Tab
        # Real frame rate of the selected clip. Used for playback speed, the timecode
        # readout and every exported curve - it used to be hard-coded to 24.
        self.current_fps = 24.0
        # A sequence carries no rate, so the last one the artist typed is the
        # best guess for the next sequence they open. App-wide, not per shot.
        self._last_user_fps = 24.0
        self.overlay_frames = None
        self.loaded_video_frames = None
        self.current_play_frame = 0
        self.is_playing = False
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._on_play_timer_tick)

        # Live Real-Time Hardware Timer (1s)
        self.vram_timer = QTimer(self)
        self.vram_timer.timeout.connect(self._update_hardware_monitor)
        self.vram_timer.start(1000)

        self._setup_style()
        self._init_ui()
        self._on_preset_changed(self.preset_combo.currentText())
        self._load_settings()
        self._wire_settings_autosave()

        # Deferred init for fast UI display
        QTimer.singleShot(20, self._deferred_init)

    @staticmethod
    def _pick_random_bg_image():
        """Pick a random .jpg/.png from gui/assets/ for atmospheric background."""
        from core.app_paths import bundled_dir
        assets_dir = bundled_dir() / "gui" / "assets"
        if not assets_dir.is_dir():
            return None
        images = [f for f in assets_dir.iterdir()
                  if f.suffix.lower() in ('.jpg', '.jpeg', '.png', '.webp')]
        if not images:
            return None
        chosen = random.choice(images)
        px = QPixmap(str(chosen))
        return px if not px.isNull() else None

    def _deferred_init(self):
        # A saved Blender path wins over auto-detection.
        if not self.txt_blender_path.text().strip():
            try:
                from export_tools import find_blender_executable
                detected_b = find_blender_executable()
                if detected_b:
                    self.txt_blender_path.setText(str(detected_b))
            except Exception as e:
                self._status("Blender auto-detect failed: %s" % e, error=True)

        # Correction works on a loaded result, and there is none until a clip
        # is picked - so the buttons start off rather than looking available.
        self._refresh_correction_ui()

        # Reading a saved 2D result back needs the tracker module, and importing
        # it pulls in torch (about a second). Warm it here, off the GUI thread,
        # so selecting a clip does not stall on it.
        threading.Thread(target=self._warm_tracker_import, daemon=True).start()

        # Old thumbnails cost disk for nothing; keep the cache bounded.
        try:
            removed = prune_thumb_cache(thumbs_dir())
            if removed:
                log.info("pruned %d thumbnail(s) from %s", removed, thumbs_dir())
        except Exception as e:
            log.warning("thumbnail cache prune failed: %s", e)

        # A frozen build has no console, so an exception here would vanish and
        # leave an empty media list with no explanation.
        try:
            self._refresh_videos()
        except Exception as e:
            log.exception("media folder unreadable")
            self._append_log_3d(
                f"✖ Could not read the media folder: {e}", ERR)
            self._append_log_2d(
                f"✖ Could not read the media folder: {e}", ERR)
            QMessageBox.warning(
                self, "Media Folder Unreadable",
                "Could not read:\n%s\n\n%s\n\n"
                "Check the folder exists and you have permission to read it."
                % (VIDEOS_DIR, e))
        self._update_hardware_monitor()
        self._start_update_check()

    @staticmethod
    def _warm_tracker_import():
        """Import the tracker module in the background; failure is not fatal here."""
        try:
            import cotracker_2d  # noqa: F401
        except Exception as e:
            log.info("tracker module could not be pre-imported: %s", e)

    def _status(self, text, error=False):
        """Status-bar message that is also written to app.log (C19)."""
        try:
            self.status_msg.setText(text)
        except Exception:
            pass
        (log.error if error else log.info)(text)

    @staticmethod
    def _set_chip_state(widget, state):
        """Chips are styled by the theme; handlers only set the state."""
        if widget.property("state") == state:
            return
        widget.setProperty("state", state)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _setup_style(self):
        self.setStyleSheet(DARK_STUDIO_QSS)

    def _init_ui(self):
        # ---------------- Native Desktop Menu Bar ----------------
        menubar = self.menuBar()

        # File Menu
        file_menu = menubar.addMenu("&File")
        act_add_media = file_menu.addAction("Import Media / Video...")
        act_add_media.triggered.connect(self._add_videos)
        act_open_media = file_menu.addAction("Open Media Folder")
        act_open_media.triggered.connect(self._open_videos_folder)
        act_open_scenes = file_menu.addAction("Open Output Scenes Folder")
        act_open_scenes.triggered.connect(self._open_3d_output_folder)
        file_menu.addSeparator()
        act_exit = file_menu.addAction("Exit")
        act_exit.triggered.connect(self.close)

        # 3D Solver Menu
        solver_menu = menubar.addMenu("&3D Solver")
        act_start_3d = solver_menu.addAction("Start 3D Camera Tracking")
        act_start_3d.triggered.connect(self._start_tracking_3d)
        act_stop_3d = solver_menu.addAction("Cancel 3D Solver")
        act_stop_3d.triggered.connect(self._stop_tracking_3d)
        solver_menu.addSeparator()
        act_clear_3d_log = solver_menu.addAction("Clear Diagnostics Log")
        act_clear_3d_log.triggered.connect(lambda: self.log_text.clear() if hasattr(self, 'log_text') else None)

        # 2D Tracker Menu
        tracker_menu = menubar.addMenu("&2D Tracker")
        act_start_2d = tracker_menu.addAction("Run 2D Point Tracking")
        act_start_2d.triggered.connect(self._start_tracking_2d)
        act_stop_2d = tracker_menu.addAction("Cancel 2D Tracker")
        act_stop_2d.triggered.connect(self._stop_tracking_2d)
        tracker_menu.addSeparator()
        act_unpack = tracker_menu.addAction("Unpack Frame Cache")
        act_unpack.triggered.connect(self._extract_frames_for_current_video)
        act_clear_masks = tracker_menu.addAction("Clear Active Layer Masks")
        act_clear_masks.triggered.connect(self._clear_active_layer_masks)
        act_clear_pts = tracker_menu.addAction("Clear Active Layer Points")
        act_clear_pts.triggered.connect(self._clear_manual_points)

        # Export Menu
        export_menu = menubar.addMenu("&Export")
        act_exp_blender = export_menu.addAction("Export 3D Camera for Blender (.abc)")
        act_exp_blender.triggered.connect(self._export_3d_for_blender)
        act_exp_nuke_3d = export_menu.addAction("Export 3D Camera for Nuke (.nk / .abc)")
        act_exp_nuke_3d.triggered.connect(self._export_3d_for_nuke)
        export_menu.addSeparator()
        act_exp_nuke_2d = export_menu.addAction("Export 2D Tracker Node for Nuke (.nk)")
        act_exp_nuke_2d.triggered.connect(self._export_2d_for_nuke)
        act_exp_nuke_roto = export_menu.addAction("Export Animated Roto Masks for Nuke (.nk)")
        act_exp_nuke_roto.triggered.connect(self._export_active_masks_to_nuke_roto)

        # View Menu
        view_menu = menubar.addMenu("&View")
        act_colmap_gui = view_menu.addAction("Open COLMAP 3D Viewport")
        act_colmap_gui.triggered.connect(self._open_colmap_gui)

        # Help Menu
        help_menu = menubar.addMenu("&Help")
        act_about = help_menu.addAction("About Automated Tracker")
        act_about.triggered.connect(self._show_about_dialog)
        self.act_updates = help_menu.addAction("Check for Updates on Start")
        self.act_updates.setCheckable(True)
        self.act_updates.toggled.connect(self._on_update_pref)

        # ================================================================
        # ATMOSPHERIC BACKGROUND via QStackedLayout(StackAll)
        # This is the ONLY reliable way in Qt to composite a low-opacity
        # image behind child widgets (QSS rgba doesn't actually alpha-blend).
        # ================================================================
        central = QWidget()
        self.setCentralWidget(central)

        stacked = QStackedLayout(central)
        stacked.setStackingMode(QStackedLayout.StackAll)

        # --- Layer 0 (bottom): Background image at very low opacity ---
        self._bg_label = QLabel()
        self._bg_label.setObjectName("bgPlate")
        self._bg_label.setScaledContents(True)

        bg_pixmap = self._pick_random_bg_image()
        if bg_pixmap:
            self._bg_label.setPixmap(bg_pixmap)
            opacity_fx = QGraphicsOpacityEffect(self._bg_label)
            # Low enough to read as texture. At 0.25 it showed through only in the
            # gaps between panels, which looked like a rendering fault.
            opacity_fx.setOpacity(0.10)
            self._bg_label.setGraphicsEffect(opacity_fx)

        stacked.addWidget(self._bg_label)

        # --- Layer 1 (top): Actual workspace content ---
        content = QWidget()
        content.setObjectName("workspaceRoot")
        content.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 4, 10, 4)
        content_layout.setSpacing(0)

        # Main Workspace Tab Widget
        self.tabs = QTabWidget()
        self.tab_3d = QWidget()
        self.tab_2d = QWidget()

        build_3d_tab(self, self.tab_3d, PRESETS)
        build_2d_tab(self, self.tab_2d)

        self.tabs.addTab(self.tab_3d, "3D Camera Tracking (COLMAP)")
        self.tabs.addTab(self.tab_2d, "2D Point Tracking (CoTracker3)")
        # A newer release is worth one quiet line above the tabs, never a dialog.
        self.update_banner = QWidget()
        self.update_banner.setVisible(False)
        _ub = QHBoxLayout(self.update_banner)
        _ub.setContentsMargins(0, 4, 0, 4)
        self.lbl_update = QLabel("")
        self.lbl_update.setObjectName("statusChip")
        self.lbl_update.setOpenExternalLinks(True)
        _ub.addWidget(self.lbl_update, 1)
        _dismiss = QPushButton("Dismiss")
        _dismiss.setToolTip("Hide this until the next launch.")
        _dismiss.clicked.connect(lambda: self.update_banner.setVisible(False))
        _ub.addWidget(_dismiss)
        content_layout.addWidget(self.update_banner)

        content_layout.addWidget(self.tabs, 1)

        stacked.addWidget(content)
        stacked.setCurrentWidget(content)

        # ---------------- Bottom Status Bar ----------------
        statusbar = self.statusBar()
        statusbar.setSizeGripEnabled(False)

        self.status_msg = QLabel("Ready")
        self.status_msg.setObjectName("hint")
        self.status_msg.setStyleSheet("padding-left: 6px;")
        statusbar.addWidget(self.status_msg, 1)

        self.status_gpu = QLabel("GPU: Checking...")
        self.status_gpu.setObjectName("statusChip")

        self.status_blender = QLabel("Blender: Auto")
        self.status_blender.setObjectName("statusChip")

        self.status_colmap = QLabel("COLMAP: Ready")
        self.status_colmap.setObjectName("statusChip")

        self.status_ver = QLabel(display_version())
        self.status_ver.setObjectName("statusChip")

        statusbar.addPermanentWidget(self.status_gpu)
        statusbar.addPermanentWidget(self.status_blender)
        statusbar.addPermanentWidget(self.status_colmap)
        statusbar.addPermanentWidget(self.status_ver)

    def _open_colmap_gui(self):
        if COLMAP_BAT.exists():
            # Use the .bat wrapper which sets up Qt plugin paths
            popen_gui([str(COLMAP_BAT), "gui"])
        elif COLMAP_EXE.exists():
            # Set QT_PLUGIN_PATH for COLMAP's own Qt libraries
            env = os.environ.copy()
            plugins_dir = colmap_plugins_dir()
            if plugins_dir.exists():
                env["QT_PLUGIN_PATH"] = str(plugins_dir)
            popen_gui([str(COLMAP_EXE), "gui"], env=env)
        else:
            QMessageBox.warning(self, "COLMAP Missing", f"COLMAP executable not found at:\n{COLMAP_EXE}")

    def _show_about_dialog(self):
        QMessageBox.information(
            self,
            "About Automated Tracker",
            "AUTOMATED TRACKER %s\n\n"
            "VFX Studio Camera Tracking & 2D Motion Tracking System\n"
            "Engines: COLMAP (3D SfM) & Meta CoTracker3 (2D Point Tracking)\n"
            "Pipeline Integrations: Blender (.abc) & Foundry Nuke (.nk / .abc)\n\n"
            "Logs: %s" % (display_version(), logs_dir())
        )

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if Path(url.toLocalFile()).suffix.lower() in VIDEO_EXTS:
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                filepath = url.toLocalFile()
                if Path(filepath).suffix.lower() in VIDEO_EXTS:
                    event.acceptProposedAction()
                    self._import_dropped_video(filepath)
                    return

    def _import_dropped_video(self, filepath):
        # A dropped clip replaces one of the same name, as it always did.
        self._import_media_files([filepath], overwrite=True)

    def _import_media_files(self, files, overwrite=False):
        """
        Copy clips into 02 VIDEOS on a worker thread (B14). One copy runs at a
        time; further requests queue behind it.
        """
        jobs = [(f, overwrite) for f in files]
        if self.copy_worker is not None and self.copy_worker.isRunning():
            self._pending_imports.extend(jobs)
            self._status("Import queued (%d file(s) waiting)" % len(self._pending_imports))
            return
        self.copy_worker = MediaCopyWorker(jobs, VIDEOS_DIR)
        self.copy_worker.progress_signal.connect(self._status)
        self.copy_worker.finished_signal.connect(self._on_copy_finished)
        self._status("Importing %d file(s)..." % len(jobs))
        self.copy_worker.start()

    def _on_copy_finished(self, copied, errors):
        for name in copied:
            self._append_log_3d(f"✔ Imported {name} into 02 VIDEOS.", OK)
        for err in errors:
            self._append_log_3d(f"✖ Import failed - {err}", ERR)
            log.error("import failed: %s", err)
        if errors:
            QMessageBox.warning(
                self, "Import Problems",
                "%d file(s) could not be imported:\n\n%s" % (len(errors), "\n".join(errors[:12])))
        self._status("Imported %d file(s)%s" % (
            len(copied), (", %d failed" % len(errors)) if errors else ""))
        if copied:
            self._last_clip = copied[-1]
        self._refresh_videos()
        if self._pending_imports and not self._closing:
            jobs, self._pending_imports = self._pending_imports, []
            self.copy_worker = MediaCopyWorker(jobs, VIDEOS_DIR)
            self.copy_worker.progress_signal.connect(self._status)
            self.copy_worker.finished_signal.connect(self._on_copy_finished)
            self.copy_worker.start()

    def _update_hardware_monitor(self):
        try:
            info = gpu_monitor.query()
            if info['available']:
                name = info['name']
                load = info['load_pct']
                used_mb = info['used_mb']
                total_mb = info['total_mb']
                used_gb = used_mb / 1024.0
                total_gb = total_mb / 1024.0

                self.status_gpu.setText(f"GPU: {name} ({load}%) | {used_gb:.1f}/{total_gb:.1f} GB")
                self._set_chip_state(self.status_gpu, "busy" if load >= 50 else "idle")
            else:
                self.status_gpu.setText("Device: CPU Mode")
                self._set_chip_state(self.status_gpu, "idle")
        except Exception as e:
            self.status_gpu.setText("GPU: Unknown")
            self._set_chip_state(self.status_gpu, "bad")
            if not getattr(self, "_gpu_error_logged", False):
                self._gpu_error_logged = True
                log.warning("GPU monitor query failed: %s", e)

        b_path = self.txt_blender_path.text().strip() if hasattr(self, 'txt_blender_path') else None
        if b_path:
            self.status_blender.setText("Blender: Linked")
            self._set_chip_state(self.status_blender, "busy")
        else:
            self.status_blender.setText("Blender: Auto")
            self._set_chip_state(self.status_blender, "idle")

        if COLMAP_EXE.exists():
            self.status_colmap.setText("COLMAP: Ready")
            self._set_chip_state(self.status_colmap, "idle")
        else:
            self.status_colmap.setText("COLMAP: Missing")
            self._set_chip_state(self.status_colmap, "bad")

    # =========================================================================
    # EVENT HANDLERS: 3D TAB
    # =========================================================================
    def _on_preset_changed(self, preset_name):
        data = PRESETS.get(preset_name, PRESETS["Custom (Manual Tuning)"])
        self.preset_desc.setText(data["description"])
        self.spin_tri.setValue(data["tri_angle"])
        self.spin_overlap.setValue(data["overlap"])
        self.spin_inliers.setValue(data["inliers"])
        
        # Match camera model prefix
        target_cam = data.get("camera_model", "SIMPLE_RADIAL")
        for i in range(self.combo_cam.count()):
            if self.combo_cam.itemText(i).startswith(target_cam):
                self.combo_cam.setCurrentIndex(i)
                break

        # Match solver engine prefix
        target_solver = data.get("solver_engine", "Incremental")
        for i in range(self.combo_solver_engine.count()):
            if self.combo_solver_engine.itemText(i).startswith(target_solver):
                self.combo_solver_engine.setCurrentIndex(i)
                break

        self.spin_step.setValue(data["frame_step"])
        self.chk_single_cam.setChecked(data["single_camera"])
        self.chk_gpu.setChecked(data["use_gpu"])

    def _browse_blender_exe(self):
        fpath, _ = QFileDialog.getOpenFileName(
            self, "Select blender.exe", "C:\\Program Files", "Blender Executable (blender.exe);;All Files (*.*)"
        )
        if fpath:
            self.txt_blender_path.setText(fpath)
            self._append_log_3d(f"✔ Set Blender executable path to: {fpath}", OK)
            self._schedule_settings_save()

    def _add_videos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Video or Image Sequence Files", "", "Video & Image Files (*.mp4 *.mov *.avi *.mkv *.m4v *.exr *.png *.jpg *.jpeg *.tif *.tiff);;All Files (*.*)"
        )
        if files:
            self._import_media_files(files, overwrite=False)

    def _refresh_videos(self):
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        self.table.setRowCount(0)

        # Repopulating the combo fires currentTextChanged for every addItem,
        # and each one used to start a frame extractor (B1). Fill it silently
        # and select the clip once afterwards.
        previous = self.combo_2d_video.currentText() or self._last_clip
        self.combo_2d_video.blockSignals(True)
        self.combo_2d_video.clear()

        items = scan_media_pool(VIDEOS_DIR)

        for v in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(v.name))

            if v.is_file():
                size_mb = v.stat().st_size / (1024 * 1024)
                self.table.setItem(row, 1, QTableWidgetItem(f"{size_mb:.1f} MB"))
            else:
                count = len([s for s in v.iterdir() if s.is_file()])
                self.table.setItem(row, 1, QTableWidgetItem(f"{count} Frames"))

            scene_dir = SCENES_DIR / v.stem
            solved, _ = find_latest_output(
                scene_dir, "3D_CAMERA_TRACK", "sparse/cameras.txt", legacy_subdirs=("",))
            if solved:
                status_item = QTableWidgetItem("● Solved (3D Ready)")
                status_item.setForeground(QColor(OK))
                status_item.setTextAlignment(Qt.AlignCenter)
            else:
                status_item = QTableWidgetItem("○ Ready to Track")
                status_item.setForeground(QColor(ACCENT))
                status_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, status_item)

            self.combo_2d_video.addItem(v.name)

        idx = self.combo_2d_video.findText(previous) if previous else -1
        if idx >= 0:
            self.combo_2d_video.setCurrentIndex(idx)
        self.combo_2d_video.blockSignals(False)

        if items:
            self._on_2d_video_selected(self.combo_2d_video.currentText())

        self._refresh_layer_list()

    def _open_videos_folder(self):
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(VIDEOS_DIR)

    def _open_scenes_folder(self):
        SCENES_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(SCENES_DIR)

    def _open_3d_output_folder(self):
        row = self.table.currentRow()
        if row >= 0:
            v_name = self.table.item(row, 0).text()
            shot_dir = SCENES_DIR / Path(v_name).stem
            track_dir = shot_dir / "3D_CAMERA_TRACK"
            if (track_dir / "_latest").exists():
                os.startfile(track_dir / "_latest")
            elif track_dir.exists():
                os.startfile(track_dir)
            elif (shot_dir / "sparse").exists():
                os.startfile(shot_dir)
            else:
                track_dir.mkdir(parents=True, exist_ok=True)
                os.startfile(track_dir)
        else:
            self._open_scenes_folder()

    def _on_table_row_selected(self):
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row, 0):
            v_name = self.table.item(row, 0).text()
            idx = self.combo_2d_video.findText(v_name)
            if idx >= 0 and self.combo_2d_video.currentIndex() != idx:
                self.combo_2d_video.setCurrentIndex(idx)

            # A saved project already holds the start the artist settled on;
            # only guess from the file numbering when there is nothing saved.
            if self._project_loaded and self._project_shot == Path(v_name).stem:
                return

            # An image sequence carries its own frame numbering; use it so the
            # exported camera lands on the same frames as the plate.
            detected = detect_sequence_start(VIDEOS_DIR / v_name, default=1)
            if detected != self.spin_start_frame_3d.value():
                self.spin_start_frame_3d.setValue(detected)
                if detected != 1:
                    self._append_log_3d(
                        f"Timeline start set to {detected} from the sequence numbering "
                        f"of '{v_name}'.", ACCENT)

    def _on_table_row_double_clicked(self, item):
        row = item.row()
        if row >= 0 and self.table.item(row, 0):
            v_name = self.table.item(row, 0).text()
            shot_dir = SCENES_DIR / Path(v_name).stem
            track_dir = shot_dir / "3D_CAMERA_TRACK" / "_latest"
            if track_dir.exists():
                os.startfile(track_dir)
            elif shot_dir.exists():
                os.startfile(shot_dir)
            else:
                self._open_scenes_folder()

    def _start_tracking_3d(self):
        if self.worker_3d is not None and self.worker_3d.isRunning():
            self._append_log_3d("A 3D solve is already running.", WARN)
            return
        videos = scan_media_pool(VIDEOS_DIR)

        if not videos:
            QMessageBox.warning(self, "No Videos Found", f"Please add at least one video or image sequence into:\n{VIDEOS_DIR}")
            return

        # Only solve what is selected in the media table. Selecting nothing means
        # 'all of them', which is what the button used to do unconditionally.
        selected_names = set()
        for item in self.table.selectedItems():
            name_item = self.table.item(item.row(), 0)
            if name_item and name_item.text().strip():
                selected_names.add(name_item.text().strip())
        if selected_names:
            picked = [v for v in videos if v.name in selected_names]
            if picked:
                videos = picked
                self._append_log_3d(
                    f"Solving {len(videos)} selected shot(s): "
                    f"{', '.join(v.name for v in videos)}", ACCENT)
        else:
            self._append_log_3d(
                f"No row selected - solving all {len(videos)} shot(s) in 02 VIDEOS.", TEXT_DIM)

        # Masks were drawn against whatever clip the 2D tab has loaded, so they must only
        # be applied to that clip - not to every video in the batch.
        all_masks = []
        for l in self.canvas_2d.layers:
            for m in l.animated_masks:
                all_masks.append(m)
        mask_shot = Path(self.combo_2d_video.currentText()).stem if self.combo_2d_video.currentText() else None
        if all_masks and mask_shot:
            self._append_log_3d(
                f"{len(all_masks)} roto mask(s) will be applied to '{mask_shot}' only.", ACCENT)

        cam_raw = self.combo_cam.currentText().split()[0].strip()
        preset_data = PRESETS.get(self.preset_combo.currentText(), PRESETS[DEFAULT_PRESET])
        config = {
            "solver_engine": self.combo_solver_engine.currentText(),
            "tri_angle": self.spin_tri.value(),
            "overlap": self.spin_overlap.value(),
            "inliers": self.spin_inliers.value(),
            "camera_model": cam_raw,
            "single_camera": self.chk_single_cam.isChecked(),
            "use_gpu": self.chk_gpu.isChecked(),
            "generate_mesh": self.chk_mesh_gen.isChecked(),
            "enable_caspar_ba": self.chk_caspar_ba.isChecked(),
            "max_image_size": 4096,
            "init_max_forward_motion": preset_data.get("init_max_forward_motion", 1.0),
            # The rate the artist set on the 2D tab, so a sequence exports on
            # the right times instead of whatever the probe guessed.
            "fps": self.current_fps if self.current_fps and self.current_fps > 0 else None,
            "timeline_start": self.spin_start_frame_3d.value(),
            "frame_step": self.spin_step.value(),
            "blender_path": self.txt_blender_path.text().strip() or None,
            "animated_masks": all_masks if all_masks else None,
            "mask_shot": mask_shot,
            "ba_refine_distortion": self.chk_ba_refine.isChecked(),
            # Handoff settings the exporters read (1.4, 1.5, 1.6). The scene
            # transform belongs to the shot in view, so a batch of other shots
            # gets None rather than this one's scale and floor.
            "scene_transform": (self._scene_transform
                                if len(videos) == 1 and Path(videos[0]).stem == self._current_shot_name()
                                else None),
            "overscan": self._overscan_value(),
            "write_undistort": bool(self.chk_write_undistort.isChecked()),
            "pixel_aspect": float(self.spin_pixel_aspect.value()),
        }

        self._pause_playback()
        self.btn_start_3d.setEnabled(False)
        self.btn_stop_3d.setEnabled(True)
        self.log_text.clear()

        self.worker_3d = TrackerWorker(
            videos, config,
            base_dir=BASE_DIR,
            colmap_dir=COLMAP_DIR,
            colmap_exe=COLMAP_EXE,
            ffmpeg_dir=FFMPEG_DIR,
            ffmpeg_exe=FFMPEG_EXE,
            scenes_dir=SCENES_DIR
        )
        self.worker_3d.log_signal.connect(self._append_log_3d)
        self.worker_3d.progress_signal.connect(self._update_progress_3d)
        self.worker_3d.video_status_signal.connect(self._update_video_status)
        self.worker_3d.finished_signal.connect(self._on_worker_3d_finished)
        self.worker_3d.start()

    def _stop_tracking_3d(self):
        if self.worker_3d and self.worker_3d.isRunning():
            self._append_log_3d("⏹ Stopping 3D tracking process...", ERR)
            self.worker_3d.cancel()
            self.btn_stop_3d.setEnabled(False)

    def _on_worker_3d_finished(self, success, message):
        self.btn_start_3d.setEnabled(True)
        self.btn_stop_3d.setEnabled(False)
        self._append_log_3d(f"\n{message}", OK if success else ERR)
        self._refresh_videos()
        # A fresh solve is a fresh point cloud, so the picks that pointed into
        # the old one mean nothing now.
        self._clear_scene_picks()
        self._load_solve_for_shot(self._current_shot_name())
        self._refresh_scene_setup()

    def _append_log_3d(self, text, color=TEXT_DIM):
        self.log_text.append(f'<span style="color: {color};">{text}</span>')
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _update_progress_3d(self, val, stage_text):
        self.progress_bar.setValue(val)
        self.progress_bar.setFormat(f"{val}% — {stage_text}")

    def _export_3d_for_nuke(self):
        row = self.table.currentRow()
        if row < 0 and self.table.rowCount() > 0:
            row = 0
        if row < 0:
            QMessageBox.warning(self, "No Video Selected", "Please select a video from the table first.")
            return

        v_name = self.table.item(row, 0).text()
        shot_name = Path(v_name).stem
        shot_dir = SCENES_DIR / shot_name
        nk_file, target_scene = find_latest_output(
            shot_dir, "3D_CAMERA_TRACK", "camera_track_nuke.nk", legacy_subdirs=("",))

        if nk_file and nk_file.exists():
            with open(nk_file, "r", encoding="utf-8") as f:
                content = f.read()
            QApplication.clipboard().setText(content)
            self._flash_button_feedback(self.btn_export_3d_nuke, "🎥 Export for Nuke (.abc / .nk)", "✔ Copied to Clipboard!")
            self._append_log_3d(f"📋 Copied Nuke 3D Camera node ({nk_file.name}) to clipboard! Press Ctrl+V inside Nuke.", WARN)
            os.startfile(target_scene)
        else:
            QMessageBox.information(
                self, "No Nuke File Found",
                f"No 3D Nuke Camera script found for '{shot_name}'.\n\nRun 3D Camera Tracking first."
            )

    def _export_3d_for_blender(self):
        row = self.table.currentRow()
        if row < 0 and self.table.rowCount() > 0:
            row = 0
        if row < 0:
            QMessageBox.warning(self, "No Video Selected", "Please select a video from the table first.")
            return

        v_name = self.table.item(row, 0).text()
        shot_dir = SCENES_DIR / Path(v_name).stem
        found, target_scene = find_latest_output(
            shot_dir, "3D_CAMERA_TRACK", ["import_to_blender.py", "camera_track.abc"],
            legacy_subdirs=("",))

        if not found:
            QMessageBox.warning(self, "3D Track Not Found", f"No 3D camera track found for '{v_name}'. Run 3D Camera Tracking first.")
            return

        blender_path = self.txt_blender_path.text().strip() or None
        self._append_log_3d(f"▶ Exporting 3D Track for Blender...", WARN)

        try:
            from export_tools import auto_export_alembic_via_blender
            auto_export_alembic_via_blender(
                target_scene,
                blender_path=blender_path,
                log_callback=lambda m, c: self._append_log_3d(m, c)
            )
        except Exception as e:
            self._append_log_3d(f"Notice: Alembic export check: {e}", WARN)

        abc_file = target_scene / "camera_track.abc"
        if abc_file.exists():
            self._flash_button_feedback(self.btn_export_3d_blender, "🎬 Export for Blender (.abc)", "✔ Alembic Ready!")
            self._append_log_3d(f"🎉 Blender Alembic (.abc) ready: {abc_file.name}", OK)
        else:
            self._flash_button_feedback(self.btn_export_3d_blender, "🎬 Export for Blender (.abc)", "✔ Script Ready!")
            self._append_log_3d(f"✔ Blender 1-Click Script ready: import_to_blender.py", OK)

        os.startfile(target_scene)

    def _flash_button_feedback(self, btn, orig_text, success_text="✔ Copied to Clipboard!", duration_ms=1800):
        btn.setText(success_text)
        prev_style = btn.styleSheet()
        btn.setStyleSheet(
            "background-color: #11291f; border: 1px solid %s; color: %s; font-weight: 700;"
            % (OK, OK)
        )
        def restore():
            btn.setText(orig_text)
            btn.setStyleSheet(prev_style)
        QTimer.singleShot(duration_ms, restore)

    # =========================================================================
    # EVENT HANDLERS: 2D TAB & TIMELINE
    # =========================================================================
    def _refresh_layer_list(self):
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for idx, layer in enumerate(self.canvas_2d.layers):
            inc_cnt = len(layer.inclusion_masks)
            exc_cnt = len(layer.exclusion_masks)
            pt_cnt = len(layer.points) if layer.mode != "grid" else (layer.grid_size ** 2)
            mask_summary = f"{exc_cnt} exc, {inc_cnt} inc" if (exc_cnt or inc_cnt) else "No masks"
            item_text = f"■ {layer.name} ({layer.mode.upper()}: {pt_cnt} pts | {mask_summary})"
            item = QListWidgetItem(item_text)
            item.setForeground(QColor(layer.color))
            self.layer_list.addItem(item)
        if 0 <= self.canvas_2d.active_layer_idx < self.layer_list.count():
            self.layer_list.setCurrentRow(self.canvas_2d.active_layer_idx)
        self.layer_list.blockSignals(False)
        # Every handler that adds, deletes, renames, recolours or re-points a
        # layer ends here, so this is the one place the project needs marking.
        self._schedule_project_save()

    def _on_layer_selected(self, row):
        if row < 0 or row >= len(self.canvas_2d.layers):
            return
        self.canvas_2d.active_layer_idx = row
        cur_l = self.canvas_2d.active_layer
        if not cur_l:
            return

        self.radio_grid.blockSignals(True)
        self.radio_points.blockSignals(True)
        self.radio_cornerpin.blockSignals(True)
        if cur_l.mode == "grid":
            self.radio_grid.setChecked(True)
            self.grid_settings_widget.setVisible(True)
        elif cur_l.mode == "points":
            self.radio_points.setChecked(True)
            self.grid_settings_widget.setVisible(False)
        elif cur_l.mode == "cornerpin":
            self.radio_cornerpin.setChecked(True)
            self.grid_settings_widget.setVisible(False)
        self.radio_grid.blockSignals(False)
        self.radio_points.blockSignals(False)
        self.radio_cornerpin.blockSignals(False)

        self.spin_grid_size.blockSignals(True)
        self.spin_grid_size.setValue(cur_l.grid_size)
        self.lbl_total_pts.setText(f"{cur_l.grid_size ** 2} pts")
        self.spin_grid_size.blockSignals(False)

        self.spin_min_conf.blockSignals(True)
        self.spin_min_conf.setValue(cur_l.min_confidence)
        self.spin_min_conf.blockSignals(False)

        self.canvas_2d.update()
        self._append_log_2d(f"Switched active layer to: [{cur_l.name}]", cur_l.color)

    def _add_new_layer(self):
        palette = ["#00d2ff", "#ffaa00", "#ff3388", "#00ff88", "#aa55ff", "#ffdd00", "#00e5ff"]
        idx = len(self.canvas_2d.layers) + 1
        col = palette[(idx - 1) % len(palette)]
        name, ok = QInputDialog.getText(self, "Add Tracking Layer", "Layer Name:", text=f"Layer {idx}")
        if ok and name.strip():
            new_l = TrackingLayer(name.strip(), col, "grid")
            self.canvas_2d.layers.append(new_l)
            self.canvas_2d.active_layer_idx = len(self.canvas_2d.layers) - 1
            self._refresh_layer_list()
            self._on_layer_selected(self.canvas_2d.active_layer_idx)
            self._append_log_2d(f"➕ Added new tracking layer: [{name.strip()}]", OK)

    def _delete_active_layer(self):
        if len(self.canvas_2d.layers) <= 1:
            QMessageBox.information(self, "Cannot Delete", "At least one tracking layer must remain.")
            return
        cur_idx = self.canvas_2d.active_layer_idx
        del_name = self.canvas_2d.layers[cur_idx].name
        del self.canvas_2d.layers[cur_idx]
        self.canvas_2d.active_layer_idx = max(0, cur_idx - 1)
        self._refresh_layer_list()
        self._on_layer_selected(self.canvas_2d.active_layer_idx)
        self._append_log_2d(f"🗑 Deleted layer [{del_name}].", ERR)

    def _rename_active_layer(self):
        cur_l = self.canvas_2d.active_layer
        if not cur_l:
            return
        name, ok = QInputDialog.getText(self, "Rename Layer", "New Name:", text=cur_l.name)
        if ok and name.strip():
            cur_l.name = name.strip()
            self._refresh_layer_list()
            self._append_log_2d(f"✏️ Renamed layer to [{cur_l.name}].", ACCENT)

    def _change_layer_color(self):
        cur_l = self.canvas_2d.active_layer
        if not cur_l:
            return
        c = QColorDialog.getColor(QColor(cur_l.color), self, "Select Layer Color")
        if c.isValid():
            cur_l.color = c.name()
            self._refresh_layer_list()
            self.canvas_2d.update()
            self._append_log_2d(f"🎨 Updated layer color for [{cur_l.name}] to {cur_l.color}.", cur_l.color)

    def _on_min_conf_changed(self, val):
        lay = self.canvas_2d.active_layer
        if lay:
            lay.min_confidence = float(val)
            self._refresh_layer_list()

    def _on_grid_size_changed(self, val):
        self.lbl_total_pts.setText(f"{val*val} pts")
        if self.canvas_2d.active_layer:
            self.canvas_2d.active_layer.grid_size = val
            self._refresh_layer_list()

    def _on_2d_mode_toggled(self):
        cur_l = self.canvas_2d.active_layer
        if not cur_l:
            return
        if self.radio_grid.isChecked():
            cur_l.mode = "grid"
            self.grid_settings_widget.setVisible(True)
        elif self.radio_points.isChecked():
            cur_l.mode = "points"
            self.grid_settings_widget.setVisible(False)
        elif self.radio_cornerpin.isChecked():
            cur_l.mode = "cornerpin"
            self.grid_settings_widget.setVisible(False)
        self._refresh_layer_list()

    def _on_canvas_tool_changed(self, idx):
        tools = ["select", "point", "inclusion_box", "inclusion_poly", "exclusion_box", "exclusion_poly"]
        if 0 <= idx < len(tools):
            self.canvas_2d.interaction_mode = tools[idx]
            self.canvas_2d.current_poly.clear()
            self.canvas_2d.update()

    def _on_point_added_on_canvas(self, frame_idx, x, y):
        if not self.radio_cornerpin.isChecked():
            self.radio_points.setChecked(True)
        count = len(self.canvas_2d.points)
        l_name = self.canvas_2d.active_layer.name if self.canvas_2d.active_layer else "Layer"
        self._append_log_2d(f"✔ [{l_name}] Added point #{count} at ({x:.1f}, {y:.1f}) on frame {frame_idx+1}", ACCENT)
        self._refresh_layer_list()

    def _jump_to_point_keyframe(self):
        if self.canvas_2d.points:
            target_f = int(self.canvas_2d.points[0][0])
            self.slider_2d_frame.setValue(target_f)
            self._append_log_2d(f"⏮ Jumped timeline to point keyframe {target_f+1}.", ACCENT)
        else:
            self._append_log_2d("No manual points placed on active layer yet.", TEXT_DIM)

    def _on_view_layer_changed(self, idx):
        if idx == 0:
            self.canvas_2d.is_overlay_active = False
            self.overlay_frames = None
            cur = self.slider_2d_frame.value()
            self._on_2d_frame_slider_changed(cur)
            self._append_log_2d("👁️ Switched view to Clean Raw Video.", ACCENT)
        elif idx == 1:
            self._load_overlay_into_player()

    def _clear_manual_points(self):
        self.canvas_2d.clear_active_layer_points()
        self._refresh_layer_list()
        self.overlay_frames = None
        self.canvas_2d.is_overlay_active = False
        self.combo_view_layer.blockSignals(True)
        self.combo_view_layer.setCurrentIndex(0)
        self.combo_view_layer.blockSignals(False)
        cur = self.slider_2d_frame.value()
        self._on_2d_frame_slider_changed(cur)
        l_name = self.canvas_2d.active_layer.name if self.canvas_2d.active_layer else "Active Layer"
        self._append_log_2d(f"🗑 Cleared tracking points on [{l_name}].", OK)

    def _clear_active_layer_masks(self):
        self.canvas_2d.clear_active_layer_masks()
        self._refresh_layer_list()
        cur = self.slider_2d_frame.value()
        self._on_2d_frame_slider_changed(cur)
        l_name = self.canvas_2d.active_layer.name if self.canvas_2d.active_layer else "Active Layer"
        self._append_log_2d(f"🗑 Cleared inclusion && exclusion masks on [{l_name}].", OK)

    def _on_2d_video_selected(self, video_name):
        if not video_name:
            return
        video_path = VIDEOS_DIR / video_name
        if not video_path.exists():
            return

        # The shot being left keeps its work: the debounce may not have fired yet.
        self._flush_project_save()

        self._pause_playback()
        self.overlay_frames = None
        self.loaded_video_frames = None
        # A different clip with the same size and length would otherwise show
        # the previous clip's cached, scaled frames.
        self.canvas_2d.invalidate_frame_cache()

        # A numbered sequence tells us where it belongs on the timeline.
        detected_start = detect_sequence_start(video_path, default=1)
        if detected_start != self.spin_start_frame_2d.value():
            self._set_timeline_start(detected_start)
            if detected_start != 1:
                self._append_log_2d(
                    f"Timeline start set to {detected_start} from the sequence numbering.",
                    ACCENT)

        # The saved project comes last, so anything the artist settled on wins
        # over what the file numbering and the probe guessed a moment ago.
        self._project_shot = video_path.stem
        self._project_save_failed = False
        # The previous shot's picks and transform must not follow the artist to
        # the next one - they index into a point cloud that is no longer loaded.
        self._scene_transform = None
        for bucket in self._scene_picks.values():
            del bucket[:]
        saved = project_file.load_project(project_file.project_path(SCENES_DIR, video_path.stem))
        self._project_loaded = saved is not None
        self._apply_fps_for_clip(video_path, saved.get("fps") if saved else None)
        self._apply_pixel_aspect_for_clip(
            video_path, (saved.get("settings_3d") or {}).get("pixel_aspect") if saved else None)
        if saved:
            self._apply_project(saved)
            self._append_log_2d(
                f"Restored the saved project for '{video_path.stem}': "
                f"{len(self.canvas_2d.layers)} layer(s), timeline start "
                f"{self.spin_start_frame_2d.value()}.", OK)
        else:
            # A shot that has never been saved starts from the defaults. Leaving
            # the previous shot's state in the window is how masks drawn on one
            # plate end up culling points on another, or a Frame Step of 3 set
            # for a long take silently follows you onto a short one.
            fresh = project_file.default_project()
            fresh["timeline_start"] = self.spin_start_frame_2d.value()
            self._apply_project(fresh)
            self._append_log_2d(
                f"'{video_path.stem}' has no saved project yet - starting from the "
                f"default settings.", TEXT_DIM)

        # The Scene setup panel follows the clip the canvas is showing, because
        # that is the plate its points are picked on.
        self._set_scene_pick_mode(None)
        self._load_solve_for_shot(video_path.stem)
        self._refresh_scene_setup()

        # After the project, because the result is matched to the layers it was
        # tracked from and those have only just come back.
        self._load_track_result_for_shot(video_path.stem)

        self._last_clip = video_name
        self._schedule_settings_save()

        # An image sequence is its own frame cache: preview it straight from the
        # files, no FFmpeg thumbnail and no extraction pass.
        if video_path.is_dir():
            files = sequence_files(video_path)
            if not files:
                self._status(f"'{video_path.name}' holds no image sequence.")
                self.slider_2d_frame.setRange(0, 0)
                return
            self.slider_2d_frame.setRange(0, len(files) - 1)
            self.slider_2d_frame.setValue(0)
            self._load_frame_preview(files[0], 0, len(files))
            self._append_log_2d(
                f"Image sequence: {len(files)} frames ({files[0].suffix.lower()}), "
                f"timeline {self.spin_start_frame_2d.value()}-"
                f"{self.spin_start_frame_2d.value() + len(files) - 1}.", TEXT_DIM)
            return

        scene_images_dir = SCENES_DIR / video_path.stem / "images"
        if scene_images_dir.exists() and list(scene_images_dir.glob("*.jpg")):
            jpgs = sorted(list(scene_images_dir.glob("*.jpg")))
            self.slider_2d_frame.setRange(0, len(jpgs) - 1)
            self.slider_2d_frame.setValue(0)
            self._load_frame_preview(jpgs[0], 0, len(jpgs))
            return

        tmp_img = self._thumbnail_for(video_path, 0, "-ss", "0.0")
        total_frames = probe_frame_count(video_path, self.current_fps, default=100) or 100

        if tmp_img is not None:
            self.slider_2d_frame.setRange(0, total_frames - 1)
            self.slider_2d_frame.setValue(0)
            self._load_frame_preview(tmp_img, 0, total_frames)
        else:
            self.slider_2d_frame.setRange(0, 0)
            self.slider_2d_frame.setValue(0)

        self._append_log_2d(f"⏳ Unpacking frame sequence for '{video_path.name}' in background for smooth 60 FPS scrubbing...", ACCENT)
        self._start_frame_extractor(video_path, scene_images_dir)

    def _thumbnail_for(self, video_path, frame_idx, *seek_args):
        if Path(video_path).is_dir():
            return None
        """
        Cached single frame of a clip for scrubbing before its frame cache
        exists (B12). Keyed by path + size + mtime, so a re-imported or renamed
        clip never shows another clip's frames. Returns the path or None, and
        reports an ffmpeg failure instead of hiding it (C19).
        """
        tmp_img = thumb_path(thumbs_dir(), video_path, frame_idx)
        if tmp_img.exists():
            return tmp_img
        cmd = [str(FFMPEG_EXE), "-y", "-loglevel", "error"]
        cmd += list(seek_args)
        cmd += ["-i", str(video_path), "-vframes", "1", "-q:v", "3", "-threads", "4", str(tmp_img)]
        try:
            r = run_hidden(cmd, capture=True, timeout=60)
            if not tmp_img.exists():
                err = (r.stdout or b"")
                if isinstance(err, bytes):
                    err = err.decode("utf-8", "replace")
                self._status("Could not read frame %d of %s: %s" % (
                    frame_idx + 1, video_path.name, err.strip()[:120] or "ffmpeg wrote nothing"),
                    error=True)
                return None
            return tmp_img
        except Exception as e:
            self._status("ffmpeg failed on %s: %s" % (video_path.name, e), error=True)
            return None

    def _start_frame_extractor(self, video_path, scene_images_dir):
        """
        Run exactly one FrameExtractorWorker at a time (B1). If one is still
        running for another clip, cancel it when the worker supports it and
        wait briefly; otherwise park the request until it finishes.
        """
        video_path = Path(video_path)
        ex = self.frame_extractor
        if ex is not None and ex.isRunning():
            if Path(ex.video_path) == video_path:
                return  # already extracting this clip
            if hasattr(ex, "cancel"):
                ex.cancel()
                ex.wait(5000)
            if ex.isRunning():
                self._pending_extract = (video_path, Path(scene_images_dir))
                self._append_log_2d(
                    f"Frame extraction for '{video_path.name}' will start after "
                    f"'{Path(ex.video_path).name}' finishes.", TEXT_DIM)
                return
        self._pending_extract = None
        self.frame_extractor = FrameExtractorWorker(video_path, scene_images_dir, FFMPEG_EXE)
        self.frame_extractor.finished_signal.connect(self._on_frames_extracted)
        self.frame_extractor.start()

    def _on_frames_extracted(self, shot_name, count):
        if count > 0:
            self._append_log_2d(f"✔ Extracted {count} frames for '{shot_name}' into 04 SCENES/{shot_name}/images/! Scrubbing is now instant.", OK)
            v_name = self.combo_2d_video.currentText()
            if Path(v_name).stem == shot_name:
                scene_images_dir = SCENES_DIR / shot_name / "images"
                jpgs = sorted(list(scene_images_dir.glob("*.jpg")))
                if jpgs:
                    cur_val = min(self.slider_2d_frame.value(), len(jpgs) - 1)
                    self.slider_2d_frame.setRange(0, len(jpgs) - 1)
                    self.slider_2d_frame.setValue(cur_val)
                    self._load_frame_preview(jpgs[cur_val], cur_val, len(jpgs))
        else:
            self._append_log_2d(
                f"✖ Frame extraction produced nothing for '{shot_name}'. "
                f"Check the clip plays and that 04 SCENES is writable.", ERR)
        self._update_keyframe_status()

        if self._pending_extract and not self._closing:
            video_path, images_dir = self._pending_extract
            self._pending_extract = None
            self._append_log_2d(f"⏳ Unpacking frame sequence for '{video_path.name}'...", ACCENT)
            self._start_frame_extractor(video_path, images_dir)

    def _extract_frames_for_current_video(self):
        v_name = self.combo_2d_video.currentText()
        if not v_name:
            return
        video_path = VIDEOS_DIR / v_name
        scene_images_dir = SCENES_DIR / video_path.stem / "images"
        self._append_log_2d(f"⏳ Extracting frame sequence for '{video_path.name}'...", ACCENT)
        self._start_frame_extractor(video_path, scene_images_dir)

    def _load_frame_preview(self, img_path_or_array, frame_idx, total_frames):
        from PIL import Image
        try:
            if isinstance(img_path_or_array, (str, Path)):
                im = Image.open(img_path_or_array).convert("RGB")
            else:
                im = Image.fromarray(img_path_or_array).convert("RGB")
            w, h = im.size
            qim = QImage(im.tobytes(), w, h, w * 3, QImage.Format_RGB888)
            fps = self.current_fps if self.current_fps and self.current_fps > 0 else 24.0
            self.canvas_2d.set_frame_image(qim, frame_idx, total_frames, w, h, fps=fps)

            fps_int = max(1, int(round(fps)))
            total_sec = frame_idx / fps
            hrs = int(total_sec // 3600)
            mins = int((total_sec % 3600) // 60)
            secs = int(total_sec % 60)
            fr = int(frame_idx % fps_int)
            self.lbl_frame_idx.setText(f"{hrs:02d}:{mins:02d}:{secs:02d}:{fr:02d} ({frame_idx+1}/{total_frames})")
        except Exception as e:
            log.exception("frame preview failed")
            self._status("Could not display frame %d: %s" % (frame_idx + 1, e), error=True)

    def _on_2d_frame_slider_changed(self, val):
        if self.overlay_frames is not None and len(self.overlay_frames) > 0 and 0 <= val < len(self.overlay_frames):
            self._load_frame_preview(self.overlay_frames[val], val, len(self.overlay_frames))
            return

        if self.loaded_video_frames is not None and len(self.loaded_video_frames) > 0 and 0 <= val < len(self.loaded_video_frames):
            self._load_frame_preview(self.loaded_video_frames[val], val, len(self.loaded_video_frames))
            return

        video_name = self.combo_2d_video.currentText()
        if not video_name:
            return
        video_path = VIDEOS_DIR / video_name
        if video_path.is_dir():
            files = sequence_files(video_path)
            if 0 <= val < len(files):
                self._load_frame_preview(files[val], val, len(files))
                self._update_keyframe_status()
            return
        scene_images_dir = SCENES_DIR / video_path.stem / "images"
        if scene_images_dir.exists():
            jpgs = sorted(list(scene_images_dir.glob("*.jpg")))
            if 0 <= val < len(jpgs):
                self._load_frame_preview(jpgs[val], val, len(jpgs))
                self._update_keyframe_status()
                return

        # The clip's real rate, not 24 (B13): on a 30 fps clip the old maths
        # showed the wrong frame while the cache was still being built.
        fps = self.current_fps if self.current_fps and self.current_fps > 0 else 24.0
        sec = val / fps
        tmp_img = self._thumbnail_for(video_path, val, "-ss", f"{sec:.3f}", "-noaccurate_seek")
        if tmp_img is not None:
            total_f = max(1, self.slider_2d_frame.maximum() + 1)
            self._load_frame_preview(tmp_img, val, total_f)

        self._update_keyframe_status()

    def _jump_prev_keyframe(self):
        cur_f = self.slider_2d_frame.value()
        all_keys = self.canvas_2d.active_layer.get_all_keyframe_frames() if self.canvas_2d.active_layer else []
        prev_keys = [k for k in all_keys if k < cur_f]
        if prev_keys:
            self.slider_2d_frame.setValue(max(prev_keys))
        elif all_keys:
            self.slider_2d_frame.setValue(all_keys[0])

    def _jump_next_keyframe(self):
        cur_f = self.slider_2d_frame.value()
        all_keys = self.canvas_2d.active_layer.get_all_keyframe_frames() if self.canvas_2d.active_layer else []
        next_keys = [k for k in all_keys if k > cur_f]
        if next_keys:
            self.slider_2d_frame.setValue(min(next_keys))
        elif all_keys:
            self.slider_2d_frame.setValue(all_keys[-1])

    def _set_mask_keyframe_on_current(self):
        layer = self.canvas_2d.active_layer
        if not layer or not layer.animated_masks:
            QMessageBox.information(self, "No Masks", "Draw an Exclusion or Inclusion mask first, then set keyframes as objects move!")
            return
        cur_f = self.slider_2d_frame.value()
        target_masks = [m for m in layer.animated_masks if m.id == self.canvas_2d.selected_mask_id] if self.canvas_2d.selected_mask_id else layer.animated_masks
        for m in target_masks:
            geom = m.get_interpolated_geometry(cur_f)
            if geom:
                m.set_keyframe(cur_f, geom["points"], "poly")
        self.canvas_2d.masks_changed.emit()
        self.canvas_2d.update()
        self._update_keyframe_status()
        self._append_log_2d(f"🔷 Keyframe created/updated at Frame {cur_f+1} on [{layer.name}].", OK)

    def _delete_mask_keyframe_on_current(self):
        layer = self.canvas_2d.active_layer
        if not layer or not layer.animated_masks:
            return
        cur_f = self.slider_2d_frame.value()
        target_masks = [m for m in layer.animated_masks if m.id == self.canvas_2d.selected_mask_id] if self.canvas_2d.selected_mask_id else layer.animated_masks
        deleted = False
        for m in target_masks:
            if m.delete_keyframe(cur_f):
                deleted = True
        if deleted:
            self.canvas_2d.masks_changed.emit()
            self.canvas_2d.update()
            self._update_keyframe_status()
            self._append_log_2d(f"🗑 Deleted keyframe at Frame {cur_f+1}.", WARN)

    def _update_keyframe_status(self):
        cur_f = self.slider_2d_frame.value()
        layer = self.canvas_2d.active_layer
        all_keys = layer.get_all_keyframe_frames() if layer else []
        is_kf = cur_f in all_keys
        if is_kf:
            self.lbl_key_status.setText(f"◆ f{cur_f+1}")
            self._set_chip_state(self.lbl_key_status, "key")
        elif all_keys:
            self.lbl_key_status.setText(f"~ f{cur_f+1}")
            self._set_chip_state(self.lbl_key_status, "interp")
        else:
            self.lbl_key_status.setText("No masks")
            self._set_chip_state(self.lbl_key_status, "idle")

    def _toggle_playback(self):
        if self.is_playing:
            self._pause_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        self.is_playing = True
        self.btn_play_pause.setText("⏸ Pause")
        fps = self.current_fps if self.current_fps and self.current_fps > 0 else 24.0
        self.play_timer.start(max(10, int(round(1000.0 / fps))))

    def _pause_playback(self):
        self.is_playing = False
        self.btn_play_pause.setText("▶ Play")
        self.play_timer.stop()

    def _step_back_frame(self):
        self._pause_playback()
        cur = self.slider_2d_frame.value()
        if cur > 0:
            self.slider_2d_frame.setValue(cur - 1)

    def _step_fwd_frame(self):
        self._pause_playback()
        cur = self.slider_2d_frame.value()
        if cur < self.slider_2d_frame.maximum():
            self.slider_2d_frame.setValue(cur + 1)

    def _on_play_timer_tick(self):
        max_f = self.slider_2d_frame.maximum()
        if max_f <= 0:
            self._pause_playback()
            return

        cur = self.slider_2d_frame.value()
        if cur >= max_f:
            if self.chk_loop.isChecked():
                self.slider_2d_frame.setValue(0)
            else:
                self._pause_playback()
        else:
            self.slider_2d_frame.setValue(cur + 1)

    def _step_frame_by_offset(self, offset):
        if offset > 0:
            self._step_fwd_frame()
        else:
            self._step_back_frame()

    def _nav_keyframe_by_offset(self, offset):
        if offset > 0:
            self._jump_next_keyframe()
        else:
            self._jump_prev_keyframe()

    def _set_in_point(self, frame_idx):
        self.canvas_2d.in_point = int(frame_idx)
        out_p = self.canvas_2d.out_point if self.canvas_2d.out_point >= 0 else self.slider_2d_frame.maximum()
        self.lbl_range_status.setText(f"{self.canvas_2d.in_point+1} – {out_p+1}")
        self._set_chip_state(self.lbl_range_status, "key")
        self._append_log_2d(f"📍 Set Tracking In-Point to Frame {self.canvas_2d.in_point+1}.", ACCENT)

    def _set_out_point(self, frame_idx):
        self.canvas_2d.out_point = int(frame_idx)
        in_p = self.canvas_2d.in_point
        self.lbl_range_status.setText(f"{in_p+1} – {self.canvas_2d.out_point+1}")
        self._set_chip_state(self.lbl_range_status, "key")
        self._append_log_2d(f"📍 Set Tracking Out-Point to Frame {self.canvas_2d.out_point+1}.", ACCENT)

    def _reset_tracking_range(self):
        self.canvas_2d.in_point = 0
        self.canvas_2d.out_point = -1
        self.lbl_range_status.setText("Full")
        self._set_chip_state(self.lbl_range_status, "idle")
        self._append_log_2d("↺ Reset Tracking Range to full sequence.", ACCENT)
        self._schedule_project_save()

    def _toggle_canvas_matte_overlay(self, checked):
        self.canvas_2d.show_mask_overlay = checked
        self.canvas_2d.update()

    def _toggle_canvas_alpha_mode(self, checked):
        self.canvas_2d.view_alpha_mode = checked
        self.canvas_2d.update()

    def _toggle_canvas_loupe(self, checked):
        self.canvas_2d.show_loupe = checked
        self.canvas_2d.update()

    def _export_active_masks_to_nuke_roto(self):
        layer = self.canvas_2d.active_layer
        if not layer or not layer.animated_masks:
            QMessageBox.information(self, "No Masks", "Draw and keyframe at least one rotomask first before exporting to Nuke!")
            return

        v_name = self.combo_2d_video.currentText()
        shot_name = Path(v_name).stem if v_name else "shot"
        out_dir = SCENES_DIR / shot_name / "2D_POINT_TRACK"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"roto_{shot_name}.nk"

        w = self.canvas_2d.orig_w
        h = self.canvas_2d.orig_h
        tot_f = max(1, self.canvas_2d.total_frames)

        from mask_animator import export_to_nuke_roto_script
        success = export_to_nuke_roto_script(layer.animated_masks, w, h, tot_f, out_file,
                                             timeline_start=self.spin_start_frame_2d.value())
        if success:
            nk_text = out_file.read_text(encoding="utf-8")
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(nk_text)
            self._append_log_2d(f"✔ Exported Nuke Roto node to: {out_file.name}", OK)
            self._append_log_2d("📋 Copied Roto node to Clipboard! Press Ctrl+V directly in Nuke Node Graph.", ACCENT)
            QMessageBox.information(self, "Nuke Roto Exported", f"Successfully exported Nuke Roto node!\n\nSaved to: {out_file}\n\nCopied to Clipboard! You can paste (Ctrl+V) directly into Nuke's Node Graph.")

    def _find_latest_overlay(self, shot_name):
        overlay, _ = find_latest_output(
            SCENES_DIR / shot_name, "2D_POINT_TRACK", "tracks_2d_overlay.mp4",
            legacy_subdirs=("cotracker_2d",))
        return overlay

    def _load_overlay_into_player(self):
        v_name = self.combo_2d_video.currentText()
        if not v_name:
            return
        overlay_file = self._find_latest_overlay(Path(v_name).stem)
        if not overlay_file or not overlay_file.exists():
            QMessageBox.information(self, "Overlay Not Found", "Run 2D Point Tracking first to generate the motion overlay video!")
            self.combo_view_layer.blockSignals(True)
            self.combo_view_layer.setCurrentIndex(0)
            self.combo_view_layer.blockSignals(False)
            return

        self.canvas_2d.is_overlay_active = True
        self.combo_view_layer.blockSignals(True)
        self.combo_view_layer.setCurrentIndex(1)
        self.combo_view_layer.blockSignals(False)

        self._append_log_2d("Loading motion overlay video into built-in player...", ACCENT)
        try:
            import imageio.v3 as iio
            self.overlay_frames = iio.imread(str(overlay_file), plugin="FFMPEG")
            self.slider_2d_frame.setRange(0, len(self.overlay_frames) - 1)
            self.slider_2d_frame.setValue(0)
            self._load_frame_preview(self.overlay_frames[0], 0, len(self.overlay_frames))
            self._start_playback()
            self._append_log_2d(f"✔ Loaded {len(self.overlay_frames)} overlay frames into player.", OK)
        except Exception as e:
            self._append_log_2d(f"Notice: Loading overlay failed: {e}", WARN)

    def _open_2d_output_folder(self):
        v_name = self.combo_2d_video.currentText()
        if v_name:
            shot_dir = SCENES_DIR / Path(v_name).stem
            point_dir = shot_dir / "2D_POINT_TRACK"
            if (point_dir / "_latest").exists():
                os.startfile(point_dir / "_latest")
            elif point_dir.exists():
                os.startfile(point_dir)
            elif (shot_dir / "cotracker_2d").exists():
                os.startfile(shot_dir / "cotracker_2d")
            else:
                point_dir.mkdir(parents=True, exist_ok=True)
                os.startfile(point_dir)
        else:
            self._open_scenes_folder()

    def _export_2d_for_nuke(self):
        v_name = self.combo_2d_video.currentText()
        if not v_name:
            QMessageBox.warning(self, "No Video Selected", "Please select a video first.")
            return
        shot_name = Path(v_name).stem
        shot_dir = SCENES_DIR / shot_name

        nk_file, target_scene = find_latest_output(
            shot_dir, "2D_POINT_TRACK", ["tracks_2d_cornerpin_nuke.nk", "tracks_2d_nuke.nk"],
            legacy_subdirs=("cotracker_2d",))

        if nk_file and nk_file.exists():
            with open(nk_file, "r", encoding="utf-8") as f:
                content = f.read()
            QApplication.clipboard().setText(content)
            self._flash_button_feedback(self.btn_export_2d_nuke, "📋 Export for Nuke (Tracker Node)", "✔ Copied to Clipboard!")
            self._append_log_2d(f"📋 Copied Nuke 2D Tracker node ({nk_file.name}) to clipboard! Press Ctrl+V inside Nuke.", OK)
            os.startfile(target_scene)
        else:
            QMessageBox.information(
                self, "No Nuke File Found",
                f"No Nuke 2D Tracker node found for '{shot_name}'.\n\nRun 2D Point Tracking first."
            )

    def _start_tracking_2d(self):
        if self.worker_2d is not None and self.worker_2d.isRunning():
            self._append_log_2d("A 2D track is already running.", WARN)
            return
        v_name = self.combo_2d_video.currentText()
        if not v_name:
            QMessageBox.warning(self, "No Video Selected", "Please select a video from the dropdown.")
            return

        video_path = VIDEOS_DIR / v_name
        max_dim = self._max_dimension_2d()

        offline = "Offline" in self.combo_2d_model.currentText()

        layers_config = []
        for l in self.canvas_2d.layers:
            if l.mode == "cornerpin" and len(l.points) != 4:
                QMessageBox.warning(
                    self, f"Need 4 Points for CornerPin on [{l.name}]",
                    f"Layer [{l.name}] is set to CornerPin mode, but has {len(l.points)} points!\nClick 4 corners (TL -> TR -> BR -> BL) on the preview image."
                )
                return
            if l.mode == "points" and not l.points:
                QMessageBox.warning(
                    self, f"No Points Clicked on [{l.name}]",
                    f"Layer [{l.name}] is set to Manual Points mode, but has no points placed!\nClick anywhere on the preview frame to add tracking points, or switch to Grid mode."
                )
                return

            # Manual points carry the frame they were clicked on. If the tracking range
            # was trimmed afterwards they can fall outside it, so say so here rather than
            # letting the engine raise half way through the run.
            if l.mode in ("points", "cornerpin") and l.points:
                in_pt = max(0, self.canvas_2d.in_point)
                out_pt = self.canvas_2d.out_point
                last = out_pt if out_pt >= 0 else (self.canvas_2d.total_frames - 1)
                outside = [int(p[0]) for p in l.points if not (in_pt <= int(p[0]) <= last)]
                if len(outside) == len(l.points):
                    QMessageBox.warning(
                        self, f"Points Outside the Tracking Range on [{l.name}]",
                        f"Every point on layer [{l.name}] sits outside the current range "
                        f"(frames {in_pt + 1}–{last + 1}).\n\n"
                        f"Points were placed on frame(s): "
                        f"{', '.join(str(f + 1) for f in sorted(set(outside))[:8])}\n\n"
                        f"Either press Reset to track the whole clip, or re-place the points "
                        f"inside the range."
                    )
                    return
                if outside:
                    self._append_log_2d(
                        f"! [{l.name}] {len(outside)} of {len(l.points)} points sit outside "
                        f"frames {in_pt + 1}–{last + 1} and will be skipped.", WARN)

            layers_config.append(l.to_config_dict())

        config = {
            "layers": layers_config,
            "max_dimension": max_dim,
            "offline": offline,
            "fps": self.current_fps if self.current_fps and self.current_fps > 0 else 24.0,
            "auto_chunk": self.chk_vram_chunk.isChecked(),
            "timeline_start": self.spin_start_frame_2d.value(),
            "in_point": self.canvas_2d.in_point,
            "out_point": self.canvas_2d.out_point,
            # Whole-layer backwards tracking, for a shot whose good reference
            # is at the end (roadmap 2.1).
            "backwards": bool(self.chk_track_backwards.isChecked()),
        }
        if config["backwards"]:
            self._append_log_2d(
                "Tracking backwards: the clip runs last frame first and the result is "
                "flipped back, so the exports still start at the head.", ACCENT)

        self._pause_playback()
        self.btn_start_2d.setEnabled(False)
        self.btn_stop_2d.setEnabled(True)
        self.log_2d_text.clear()

        self.worker_2d = CoTrackerWorker([video_path], config)
        self.worker_2d.log_signal.connect(self._append_log_2d)
        self.worker_2d.progress_signal.connect(self._update_progress_2d)
        self.worker_2d.video_status_signal.connect(lambda v, s: None)
        self.worker_2d.finished_signal.connect(self._on_worker_2d_finished)
        self.worker_2d.start()

    def _stop_tracking_2d(self):
        # Cancel means "stop the 2D job", whichever of the two is running - the
        # correction pass goes through the same tracker and honours the same flag.
        if self.worker_2d and self.worker_2d.isRunning():
            self._append_log_2d("⏹ Stopping 2D tracking process...", ERR)
            self.worker_2d.cancel()
            self.btn_stop_2d.setEnabled(False)
        if self.correction_worker and self.correction_worker.isRunning():
            self._append_log_2d("⏹ Stopping the correction...", ERR)
            self.correction_worker.cancel()
            self.btn_stop_2d.setEnabled(False)

    def _append_log_2d(self, text, color=TEXT_DIM):
        self.log_2d_text.append(f'<span style="color: {color};">{text}</span>')
        sb = self.log_2d_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _update_progress_2d(self, val, stage_text):
        self.progress_2d.setValue(val)
        self.progress_2d.setFormat(f"{val}% — {stage_text}")

    def _on_worker_2d_finished(self, success, message):
        self.btn_start_2d.setEnabled(True)
        self.btn_stop_2d.setEnabled(False)
        self._append_log_2d(f"\n{message}", OK if success else ERR)
        if success:
            # A fresh solve replaces whatever was loaded for correcting, and
            # the corrections that belonged to the old one go with it.
            for layer in self.canvas_2d.layers:
                layer.corrections = []
            self._load_track_result_for_shot(self._current_shot_name())
            self._schedule_project_save()
            self._load_overlay_into_player()

    # =========================================================================
    # FIXING A DRIFTING 2D TRACK  (roadmap 2.1)
    #
    # The result of the last solve is loaded back from tracks_2d.json, drawn on
    # the plate, and corrected by dragging a marker onto the feature it slid
    # off. "Re-track from here" then puts that ONE point back through CoTracker
    # from that frame, and the new positions are spliced into the stored result
    # from the correction onward - everything the artist already accepted in
    # front of it is left alone.
    # =========================================================================
    def _track_root_for(self, shot_name):
        return SCENES_DIR / shot_name / "2D_POINT_TRACK"

    def _load_track_result_for_shot(self, shot_name):
        """
        Load the shot's last 2D result and attach it to the current layers.

        Called when the clip is picked and after a track finishes, so the
        correction tools work on a shot solved days ago just as well as on one
        solved a minute ago. A result that does not line up with the layers in
        the window leaves correction switched off with the reason on screen -
        guessing which stored track belongs to which layer would have the
        artist correcting the wrong point.
        """
        self._track_result = None
        self._track_layer_map = None
        self._track_problem = ""
        self._last_correction = None
        self.canvas_2d.clear_tracked_result()

        if shot_name:
            json_path, _folder = find_latest_output(
                SCENES_DIR / shot_name, "2D_POINT_TRACK", "tracks_2d.json",
                legacy_subdirs=("cotracker_2d",))
            if json_path:
                try:
                    import cotracker_2d as c2d
                    result = c2d.load_tracks_2d(json_path)
                except Exception as e:
                    log.warning("could not read %s: %s", json_path, e)
                    result = None
                    self._track_problem = "The saved 2D result could not be read: %s" % e
                if result:
                    names = [l.name for l in self.canvas_2d.layers]
                    mapping, problem = c2d.match_result_to_layers(result, names)
                    if mapping:
                        self._track_result = result
                        self._track_layer_map = mapping
                        self._append_log_2d(
                            "Loaded the last 2D result for '%s': %d frame(s), %s. Switch "
                            "Result on to see it and drag a point to correct it."
                            % (shot_name, result["frame_count"],
                               ", ".join("%s %d point(s)"
                                         % (n, result["layers"][k]["tracks"].shape[1])
                                         for n, k in mapping.items())), TEXT_DIM)
                    else:
                        self._track_problem = problem
                        self._append_log_2d("! %s" % problem, WARN)
                elif not self._track_problem:
                    self._track_problem = "The saved 2D result could not be read."

        self._push_result_to_canvas()
        self._refresh_correction_ui()

    def _result_in_point(self):
        """
        The clip frame the loaded result starts on.

        The file records the timeline frame its first sample sits on, so the
        clip-relative index is that minus the shot's timeline start - the same
        arithmetic the exporters did on the way out.
        """
        if not self._track_result:
            return 0
        return max(0, int(self._track_result["start_frame"])
                   - int(self.spin_start_frame_2d.value()))

    def _push_result_to_canvas(self):
        """Hand the loaded result to the canvas, keyed by the layer it belongs to."""
        canvas = self.canvas_2d
        if not (self._track_result and self._track_layer_map):
            canvas.clear_tracked_result()
            return
        layers = {name: self._track_result["layers"][key]
                  for name, key in self._track_layer_map.items()
                  if key in self._track_result["layers"]}
        canvas.set_tracked_result(
            layers,
            in_point=self._result_in_point(),
            frame_step=self._track_result["frame_step"],
            show=bool(self.btn_show_result.isChecked()))

    def _all_corrections(self):
        """Every correction on every layer, as (layer, correction dict) pairs."""
        out = []
        for layer in self.canvas_2d.layers:
            for c in layer.corrections:
                out.append((layer, c))
        return out

    def _refresh_correction_ui(self):
        """Chip, timeline ticks and button states, from whatever is loaded now."""
        has_result = bool(self._track_result and self._track_layer_map)
        busy = self._correction_busy()
        marks = sorted({int(c["frame"]) for _l, c in self._all_corrections()})

        self.btn_show_result.setEnabled(has_result)
        if not has_result and self.btn_show_result.isChecked():
            # A shot with no result must not sit there claiming to show one.
            self.btn_show_result.blockSignals(True)
            self.btn_show_result.setChecked(False)
            self.btn_show_result.blockSignals(False)
        for btn in (self.btn_prev_fix, self.btn_next_fix):
            btn.setEnabled(has_result and bool(marks))
        for btn in (self.btn_retrack_fwd, self.btn_retrack_both):
            btn.setEnabled(has_result and not busy)
        self.btn_reexport_2d.setEnabled(has_result and not busy)

        if has_result:
            text = "%d fix%s" % (len(marks), "" if len(marks) == 1 else "es")
            self._set_chip_state(self.lbl_corrections, "key" if marks else "idle")
            self.lbl_corrections.setToolTip(
                "Corrected frames: %s" % (", ".join(str(f + 1) for f in marks[:12]) or "none yet")
                + ("\nRe-export to write these into the delivered files." if marks else ""))
        else:
            text = "No result"
            self._set_chip_state(self.lbl_corrections, "idle")
            self.lbl_corrections.setToolTip(
                self._track_problem or "Run a 2D track to get a result you can correct.")
        self.lbl_corrections.setText(text)

        # Only the frames this shot's result actually covers get a tick; a
        # correction left over from another range would point at nothing.
        try:
            self.slider_2d_frame.set_marks(marks)
        except AttributeError:
            pass

    def _toggle_tracked_result(self, checked):
        """The Result toggle: draw the last solve over the plate, or stop."""
        if checked and not (self._track_result and self._track_layer_map):
            self.btn_show_result.setChecked(False)
            QMessageBox.information(
                self, "No 2D Result",
                self._track_problem or
                "There is no 2D result for this shot yet.\n\nRun 2D Point Tracking first.")
            return
        self.canvas_2d.show_tracked_points = bool(checked)
        self._push_result_to_canvas()
        self.canvas_2d.update()
        if checked:
            self._append_log_2d(
                "Showing the last 2D result. Drag a marker to correct it on this frame, "
                "then Re-track ▶ (or right-click the marker).", ACCENT)

    def _layer_named(self, name):
        return next((l for l in self.canvas_2d.layers if l.name == name), None)

    def _result_block(self, layer_name):
        """The loaded arrays for a layer name, or None."""
        if not (self._track_result and self._track_layer_map):
            return None
        key = self._track_layer_map.get(layer_name)
        if key not in self._track_result["layers"]:
            return None
        return self._track_result["layers"][key]

    def _on_tracked_point_moved(self, layer_name, point_index, frame, x, y):
        """A tracked marker was dragged: that frame becomes what the artist set."""
        block = self._result_block(layer_name)
        layer = self._layer_named(layer_name)
        t = self.canvas_2d.tracked_index_for_frame(frame)
        if block is None or layer is None or t is None:
            self._append_log_2d(
                "The result does not cover frame %d, so there is nothing to correct there."
                % (int(frame) + 1), WARN)
            return
        import cotracker_2d as c2d
        layer.set_correction(point_index, frame, x, y)
        c2d.set_corrected_sample(block, point_index, t, x, y)
        self._last_correction = (layer_name, int(point_index), int(frame))
        self._append_log_2d(
            "◈ Corrected [%s] point #%d on frame %d to (%.1f, %.1f). Re-track ▶ to carry "
            "it forward." % (layer_name, int(point_index) + 1, int(frame) + 1, x, y), OK)
        self.canvas_2d.update()
        self._refresh_correction_ui()
        self._schedule_project_save()

    def _on_correction_cleared(self, layer_name, point_index, frame):
        """Forget one correction. The spliced positions stay - only the mark goes."""
        layer = self._layer_named(layer_name)
        if layer is not None and layer.clear_correction(point_index, frame):
            self._append_log_2d(
                "Forgot the correction on [%s] point #%d, frame %d."
                % (layer_name, int(point_index) + 1, int(frame) + 1), TEXT_DIM)
            if self._last_correction == (layer_name, int(point_index), int(frame)):
                self._last_correction = None
            self.canvas_2d.update()
            self._refresh_correction_ui()
            self._schedule_project_save()

    def _on_retrack_requested(self, layer_name, point_index, frame, backwards):
        self._run_retrack(layer_name, int(point_index), int(frame), bool(backwards))

    def _retrack_correction(self, backwards=False):
        """
        The Re-track buttons: work on the correction under the playhead.

        With nothing corrected on this frame the last correction is used, so
        scrubbing away to look at the fix and then pressing the button still
        does what the artist means.
        """
        frame = int(self.canvas_2d.current_frame)
        target = None
        for layer, c in self._all_corrections():
            if int(c["frame"]) == frame:
                target = (layer.name, int(c["point"]), frame)
                break
        target = target or self._last_correction
        if not target:
            QMessageBox.information(
                self, "Nothing to Re-track",
                "Switch Result on, drag a tracked point onto the feature it slid off, "
                "and then re-track from that frame.\n\n"
                "You can also right-click any marker to re-track it from the frame in view.")
            return
        self._run_retrack(target[0], target[1], target[2], backwards)

    def _run_retrack(self, layer_name, point_index, frame, backwards):
        """Start the re-track worker for one point, from one frame."""
        if self._correction_busy():
            self._append_log_2d("A 2D job is already running.", WARN)
            return
        block = self._result_block(layer_name)
        layer = self._layer_named(layer_name)
        t = self.canvas_2d.tracked_index_for_frame(frame)
        if block is None or layer is None or t is None:
            QMessageBox.warning(
                self, "Frame Not in the Result",
                "The saved result does not cover frame %d, so it cannot be re-tracked from "
                "there." % (int(frame) + 1))
            return

        # A marker re-tracked without being dragged starts from where the solve
        # left it - which is exactly what "re-track from here" means when the
        # track is right on this frame and wrong after it.
        c = layer.correction_at(frame, point_index)
        if c:
            x, y = float(c["x"]), float(c["y"])
        else:
            x, y = float(block["tracks"][t, point_index, 0]), float(block["tracks"][t, point_index, 1])

        v_name = self.combo_2d_video.currentText()
        if not v_name:
            return
        step = int(self._track_result["frame_step"])
        in_pt = self._result_in_point()
        config = {
            "max_dimension": self._max_dimension_2d(),
            "frame_step": step,
            "in_point": in_pt,
            # Exactly the range the result covers, whatever the In/Out chips
            # say now: the spliced frames have to line up with the stored ones.
            "out_point": in_pt + (int(self._track_result["frame_count"]) - 1) * step,
            "offline": "Offline" in self.combo_2d_model.currentText(),
            "auto_chunk": self.chk_vram_chunk.isChecked(),
        }
        self._pause_playback()
        self._append_log_2d(
            "▶ Re-tracking [%s] point #%d from frame %d%s — one point only, not the grid."
            % (layer_name, int(point_index) + 1, int(frame) + 1,
               " (and backwards)" if backwards else ""), ACCENT)
        self._start_correction_worker({
            "mode": "retrack",
            "result": self._track_result,
            "video_path": VIDEOS_DIR / v_name,
            "layer_key": self._track_layer_map[layer_name],
            "point_index": int(point_index),
            "frame_t": int(t),
            "x": x, "y": y,
            "backwards": bool(backwards),
            "config": config,
        })

    def _reexport_2d_result(self):
        """Write every 2D format again from the corrected result, without re-tracking."""
        if self._correction_busy():
            self._append_log_2d("A 2D job is already running.", WARN)
            return
        if not (self._track_result and self._track_layer_map):
            QMessageBox.information(
                self, "No 2D Result",
                self._track_problem or
                "There is no 2D result to export yet.\n\nRun 2D Point Tracking first.")
            return
        shot = self._current_shot_name()
        if not shot:
            return
        layers_meta = []
        for layer in self.canvas_2d.layers:
            key = self._track_layer_map.get(layer.name)
            if key in self._track_result["layers"]:
                layers_meta.append({
                    "name": layer.name,
                    "key": key,
                    "export_cornerpin": bool(layer.export_cornerpin or layer.mode == "cornerpin"),
                })
        self._append_log_2d(
            "▶ Re-exporting the corrected 2D tracks (no re-tracking, the overlay video "
            "stays as the last real track rendered it).", ACCENT)
        self._start_correction_worker({
            "mode": "export",
            "result": self._track_result,
            "track_root": self._track_root_for(shot),
            "layers_meta": layers_meta,
            "fps": self.current_fps if self.current_fps and self.current_fps > 0 else 24.0,
            "images_dir": SCENES_DIR / shot / "images",
            "timeline_start": int(self.spin_start_frame_2d.value()),
            "source_name": self.combo_2d_video.currentText(),
        })

    def _jump_correction(self, direction):
        """Move the playhead to the next or previous corrected frame."""
        marks = sorted({int(c["frame"]) for _l, c in self._all_corrections()})
        if not marks:
            return
        here = int(self.canvas_2d.current_frame)
        later = [f for f in marks if f > here]
        earlier = [f for f in marks if f < here]
        target = (later[0] if later else marks[0]) if direction > 0 else \
                 (earlier[-1] if earlier else marks[-1])
        self.slider_2d_frame.setValue(target)

    def _correction_busy(self):
        """True while a re-track, a re-export or a full 2D track is running."""
        for w in (self.correction_worker, self.worker_2d):
            if w is not None and w.isRunning():
                return True
        return False

    def _start_correction_worker(self, job):
        self.btn_start_2d.setEnabled(False)
        self.btn_stop_2d.setEnabled(True)
        self.correction_worker = TrackCorrectionWorker(job)
        self.correction_worker.log_signal.connect(self._append_log_2d)
        self.correction_worker.progress_signal.connect(self._update_progress_2d)
        self.correction_worker.finished_signal.connect(self._on_correction_finished)
        self.correction_worker.start()
        self._refresh_correction_ui()

    def _on_correction_finished(self, success, message):
        self.btn_start_2d.setEnabled(True)
        self.btn_stop_2d.setEnabled(False)
        worker = self.correction_worker
        if success and worker is not None and worker.result is not None:
            # The worker corrected a copy; adopt it, so the canvas and the next
            # correction both work on the spliced numbers.
            self._track_result = worker.result
            self._push_result_to_canvas()
            self.canvas_2d.update()
        self._append_log_2d(message, OK if success else ERR)
        if success and worker is not None and worker.job.get("mode") == "retrack":
            self._append_log_2d(
                "   The delivered files still hold the old positions — press Re-export 2D "
                "when the track is how you want it.", TEXT_DIM)
        self._refresh_correction_ui()

    def _update_video_status(self, video_name, status):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text() == video_name:
                stat_item = self.table.item(row, 2)
                if not stat_item:
                    stat_item = QTableWidgetItem()
                    self.table.setItem(row, 2, stat_item)
                stat_item.setTextAlignment(Qt.AlignCenter)
                if "✔" in status or "Completed" in status:
                    stat_item.setText("● Solved (3D Ready)")
                    stat_item.setForeground(QColor(OK))
                elif "✖" in status or "Failed" in status or "Error" in status:
                    stat_item.setText("✕ " + status.replace("✖", "").strip())
                    stat_item.setForeground(QColor(ERR))
                elif "Processing" in status or "Extracting" in status or "Matching" in status or "Tracking" in status:
                    stat_item.setText("◌ " + status)
                    stat_item.setForeground(QColor(WARN))
                else:
                    stat_item.setText(status)
                    stat_item.setForeground(QColor(ACCENT))


    # =========================================================================
    # SCENE SETUP: SCALE, GROUND AND ORIGIN  (roadmap 1.4)
    #
    # The artist sets all three by picking the solve's OWN 3D points on the 2D
    # canvas of the same clip. Everything below works in COLMAP's world, which
    # is where core.scene_transform and export_tools.colmap_pose_to start, and
    # the transform is built with up=COLMAP_UP because COLMAP's y points down.
    # =========================================================================
    def _current_shot_name(self):
        """The shot the 2D canvas is showing, which is the one scene setup works on."""
        name = self.combo_2d_video.currentText()
        return Path(name).stem if name else None

    @staticmethod
    def _read_ply_points(ply_path):
        """
        The solve's point cloud as (N, 3) in COLMAP's own world, or None.

        points3D.ply is written for the DCCs, in the Nuke/USD Y-up basis
        (export_tools.WORLD_BASES["nuke"] = diag(1, -1, -1)). That basis is its
        own inverse, so flipping y and z again puts the points back in the frame
        the cameras, the reprojection and scene_transform all work in.
        """
        try:
            with open(ply_path, "r", encoding="utf-8", errors="replace") as fh:
                count = 0
                for line in fh:
                    stripped = line.strip()
                    if stripped.startswith("element vertex"):
                        count = int(stripped.split()[-1])
                    if stripped == "end_header":
                        break
                else:
                    return None
                rows = np.loadtxt(fh, usecols=(0, 1, 2),
                                  max_rows=count or None, ndmin=2)
        except Exception as e:
            log.warning("could not read %s: %s", ply_path, e)
            return None
        if rows.size == 0:
            return None
        return rows.reshape(-1, 3) * np.array([1.0, -1.0, -1.0])

    def _load_solve_for_shot(self, shot_name):
        """
        Load the newest solve for a shot into `self._solve`, or clear it.

        Cameras and the per-frame poses come from camera_track.json; the points
        come from points3D.ply beside it. Nothing here raises: a shot with no
        solve, or with a half-written one, simply leaves the panel disabled with
        a sentence saying so.
        """
        self._solve = None
        if not shot_name:
            return
        track_json, folder = find_latest_output(
            SCENES_DIR / shot_name, "3D_CAMERA_TRACK", "camera_track.json",
            legacy_subdirs=("",))
        if not track_json:
            return
        try:
            import json
            with open(track_json, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            log.warning("could not read %s: %s", track_json, e)
            return

        images = data.get("images") or {}
        # The solve's own timeline start, not the spin box: the frame numbers in
        # this file were written with it, and the artist may have changed the
        # box since. current_frame 0 is the plate's first frame either way.
        start = int(data.get("timeline_start", 1) or 1)
        by_frame = {}
        for img in images.values():
            try:
                by_frame[int(img["frame"])] = img
            except (KeyError, TypeError, ValueError):
                continue

        points = self._read_ply_points(Path(folder) / "points3D.ply")
        self._solve = {
            "dir": Path(folder),
            "json": data,
            "cameras": data.get("cameras") or {},
            "by_frame": by_frame,
            "timeline_start": start,
            "points": points,
        }

    def _solve_point_count(self):
        pts = (self._solve or {}).get("points")
        return 0 if pts is None else int(len(pts))

    def _solved_image_for_frame(self, frame_idx):
        """The solved camera sitting on the plate frame the 2D tab is showing, or None."""
        if not self._solve:
            return None
        return self._solve["by_frame"].get(
            int(self._solve["timeline_start"]) + int(frame_idx))

    def _solve_camera(self, img):
        """The intrinsics of a solved image. JSON keys are strings; ids are not."""
        cams = self._solve["cameras"]
        cam_id = img.get("camera_id")
        return (cams.get(str(cam_id)) or cams.get(cam_id)
                or (next(iter(cams.values())) if cams else None))

    def _refresh_scene_setup(self):
        """Enable or disable the Scene setup card and say why, then redraw it."""
        enabled, reason = project_file.scene_setup_enabled(
            (self._solve or {}).get("json"),
            self._solve_point_count() if self._solve else None)
        self.scene_setup_card.setEnabled(enabled)
        if not enabled:
            self._set_scene_pick_mode(None)
            self.lbl_scene_points.setText(reason)
        else:
            # _update_scene_overlay replaces this the moment a picker is armed;
            # until then the artist gets the size of what they are about to pick.
            self.lbl_scene_points.setText(
                "%d solved points from the %s solve. Arm a picker below to see them "
                "on the plate." % (self._solve_point_count(), self._solve["dir"].name))
        self._update_pick_labels()
        self.lbl_scene_status.setText(self._scene_transform_summary())
        self._update_scene_overlay()

    def _update_pick_labels(self):
        picks = self._scene_picks
        self.lbl_scale_picks.setText("%d / 2" % len(picks["scale"]))
        self.lbl_ground_picks.setText("%d / 3" % len(picks["ground"]))
        self.lbl_origin_picks.setText("%d / 1" % len(picks["origin"]))

    def _all_picked_indices(self):
        """Every picked point, scale first, so the numbering on screen is stable."""
        picks = self._scene_picks
        return picks["scale"] + picks["ground"] + picks["origin"]

    def _set_scene_pick_mode(self, mode):
        """
        Arm one of the three pickers, or None for off.

        The three toggle buttons are mutually exclusive: a click has to mean one
        thing, and an artist who forgot which picker was armed would silently
        put floor points into the scale pair.
        """
        self._scene_pick_mode = mode
        for name, btn in (("scale", self.btn_pick_scale),
                          ("ground", self.btn_pick_ground),
                          ("origin", self.btn_pick_origin)):
            want = (name == mode)
            if btn.isChecked() != want:
                btn.blockSignals(True)
                btn.setChecked(want)
                btn.blockSignals(False)
        self._update_scene_overlay()

    def _on_scene_pick_toggled(self, which, checked):
        self._set_scene_pick_mode(which if checked else None)
        if checked:
            # The points are picked on the plate, which lives on the other tab.
            self.tabs.setCurrentWidget(self.tab_2d)
            self._append_log_3d(
                "Picking %s points: click the amber solved points on the 2D tab." % which,
                ACCENT)

    def _update_scene_overlay(self):
        """Reproject the solve onto the frame in view, or take the overlay away."""
        canvas = self.canvas_2d
        if not self._scene_pick_mode or not self._solve or self._solve.get("points") is None:
            canvas.scene_pick_active = False
            canvas.show_solved_points = False
            canvas.clear_solved_points()
            return

        canvas.scene_pick_active = True
        canvas.show_solved_points = True
        points = self._solve["points"]
        img = self._solved_image_for_frame(canvas.current_frame)
        if img is None:
            canvas.clear_solved_points()
            solved = sorted(self._solve["by_frame"])
            nearest = ""
            if solved:
                start = int(self._solve["timeline_start"])
                here = start + int(canvas.current_frame)
                closest = min(solved, key=lambda f: abs(f - here))
                nearest = "  Nearest solved frame: %d." % closest
            self.lbl_scene_points.setText(
                "%d solved points, but this frame has no solved camera, so they "
                "cannot be drawn on it.%s" % (len(points), nearest))
            return

        cam = self._solve_camera(img)
        if not cam:
            canvas.clear_solved_points()
            self.lbl_scene_points.setText("This solve carries no intrinsics for the frame in view.")
            return
        try:
            xy, visible = project_solved_points(points, img["center"], img["R_world"], cam)
        except Exception as e:
            canvas.clear_solved_points()
            self.lbl_scene_points.setText("Could not reproject the solved points: %s" % e)
            return

        canvas.set_solved_points(xy, visible, (cam.get("width"), cam.get("height")))
        canvas.set_solved_selection(self._all_picked_indices())
        self.lbl_scene_points.setText(
            "%d solved points, %d on this frame. Click one to pick it."
            % (len(points), int(np.count_nonzero(visible))))

    def _on_solved_point_picked(self, index):
        """A click landed on a reprojected point; file it under the armed picker."""
        mode = self._scene_pick_mode
        if not mode or not self._solve:
            return
        bucket = self._scene_picks[mode]
        limit = {"scale": 2, "ground": None, "origin": 1}[mode]
        if index in bucket:
            bucket.remove(index)          # clicking a picked point unpicks it
        else:
            if limit is not None and len(bucket) >= limit:
                # The newest pick wins rather than being ignored: the artist
                # clicked it, so they meant it.
                bucket.pop(0)
            bucket.append(int(index))
        self._update_pick_labels()
        self.canvas_2d.set_solved_selection(self._all_picked_indices())
        pt = self._solve["points"][int(index)]
        self._append_log_3d(
            "%s: %d point(s) picked  (last at %.3f, %.3f, %.3f in solve units)."
            % (mode.capitalize(), len(bucket), pt[0], pt[1], pt[2]), TEXT_DIM)

    def _clear_scene_picks(self):
        for bucket in self._scene_picks.values():
            del bucket[:]
        self._update_pick_labels()
        self.canvas_2d.set_solved_selection(())
        self._append_log_3d("Cleared the picked points.", TEXT_DIM)

    def _ground_points_for_build(self, notes):
        """
        The points a ground fit should be run on, or None with a note saying why not.

        The auto plane is turned into three points ON that plane rather than
        being fitted separately, so both routes go through the same
        scene_transform.fit_ground and cannot drift apart.
        """
        picks = self._scene_picks["ground"]
        points = self._solve["points"]
        if self.chk_auto_ground.isChecked():
            try:
                from export_tools import detect_ground_plane_ransac
                plane = detect_ground_plane_ransac(points)
            except Exception as e:
                notes.append("Ground not levelled: the auto plane fit failed (%s)." % e)
                return None
            if plane is None:
                notes.append("Ground not levelled: no convincing plane in the point cloud - "
                             "pick three points on the floor instead.")
                return None
            normal = plane.normal / (np.linalg.norm(plane.normal) or 1.0)
            # Two directions inside the plane, from the world axis least like
            # the normal, so the triangle is never degenerate.
            helper = np.zeros(3)
            helper[int(np.argmin(np.abs(normal)))] = 1.0
            a = np.cross(normal, helper)
            a /= (np.linalg.norm(a) or 1.0)
            b = np.cross(normal, a)
            return np.array([plane.centroid, plane.centroid + a, plane.centroid + b])
        if len(picks) >= 3:
            return points[picks]
        if picks:
            notes.append("Ground not levelled: it needs at least three points, %d picked."
                         % len(picks))
        return None

    def _apply_scene_transform(self):
        """Build the transform from what the panel holds, log it, and save it."""
        if not self._solve or self._solve.get("points") is None:
            self._append_log_3d("There is no solve loaded to build a scene transform from.", WARN)
            return
        points = self._solve["points"]
        notes = []

        # --- scale
        scale_pair, real_metres = None, None
        picks = self._scene_picks["scale"]
        typed = project_file.to_metres(
            self.spin_scale_distance.value(), self.combo_scale_unit.currentText())
        if len(picks) == 2 and typed > 0:
            scale_pair = (points[picks[0]], points[picks[1]])
            real_metres = typed
        elif len(picks) == 2:
            notes.append("Scale not set: type the real distance between the two picked points.")
        elif picks:
            notes.append("Scale not set: it needs two points, %d picked." % len(picks))

        # --- ground
        ground_points = self._ground_points_for_build(notes)

        # --- origin
        origin_point = None
        if self.chk_origin_under_camera.isChecked():
            img = self._solved_image_for_frame(self.canvas_2d.current_frame)
            if ground_points is None:
                notes.append("Origin not moved: the ground under the camera needs a ground "
                             "plane - pick three floor points or tick the auto plane.")
            elif img is None:
                notes.append("Origin not moved: the frame the 2D tab is showing has no "
                             "solved camera.")
            else:
                # Work out where the camera stands once scale and levelling are
                # applied, drop it onto the floor there, and hand `build` the
                # ORIGINAL-space point that lands on it - which is what it wants.
                base = scene_transform.build(
                    points_for_ground=ground_points, scale_pair=scale_pair,
                    real_distance=real_metres, up=scene_transform.COLMAP_UP, notes=[])
                centre = scene_transform.apply_to_points(
                    base, np.asarray(img["center"], dtype=float))
                floor = scene_transform.apply_to_points(base, ground_points)
                # With up=COLMAP_UP a levelled floor is a plane of constant y,
                # so "under the camera" is the camera's x and z at the floor's y.
                target = np.array([centre[0], float(np.mean(floor[:, 1])), centre[2]])
                origin_point = scene_transform.apply_to_points(
                    scene_transform.invert(base), target)
        elif self._scene_picks["origin"]:
            origin_point = points[self._scene_picks["origin"][0]]

        if scale_pair is None and ground_points is None and origin_point is None:
            self._append_log_3d(
                "Nothing to apply yet: pick two points and type a distance for scale, "
                "three for the ground, or one for the origin.", WARN)
            for note in notes:
                self._append_log_3d("   %s" % note, WARN)
            return

        try:
            transform = scene_transform.build(
                points_for_ground=ground_points,
                scale_pair=scale_pair,
                real_distance=real_metres,
                origin_point=origin_point,
                up=scene_transform.COLMAP_UP,
                notes=notes)
        except Exception as e:
            self._append_log_3d("✖ Could not build the scene transform: %s" % e, ERR)
            return

        self._scene_transform = transform
        scale = float(transform["scale"])
        self._append_log_3d(
            "✔ Scene transform applied: scale ×%.6f — one solve unit is now %.6f m."
            % (scale, scale), OK)
        if ground_points is not None and not any(n.startswith("Ground") for n in notes):
            self._append_log_3d("   Ground levelled onto Y = 0.", OK)
        if origin_point is not None:
            self._append_log_3d("   Origin moved to the picked position.", OK)
        for note in notes:
            self._append_log_3d("   %s" % note, WARN)
        self._append_log_3d(
            "   Press Re-export This Solve to write a new export folder with it.", TEXT_DIM)

        self.lbl_scene_status.setText(self._scene_transform_summary())
        self._schedule_project_save()

    def _reset_scene_transform(self):
        self._scene_transform = None
        self.lbl_scene_status.setText(self._scene_transform_summary())
        self._append_log_3d(
            "Scene transform cleared: the solve goes back to COLMAP's arbitrary "
            "scale, tilt and origin.", ACCENT)
        self._schedule_project_save()

    def _scene_transform_summary(self):
        """One line describing the stored transform, for the panel."""
        transform = self._scene_transform
        if not transform:
            return "No scene transform: the solve is in COLMAP's own units."
        try:
            scale, rotation, translation = scene_transform.parts(transform)
        except Exception:
            return "The stored scene transform could not be read."
        levelled = float(np.abs(rotation - np.eye(3)).max()) > 1e-9
        moved = float(np.abs(translation).max()) > 1e-9
        return "Scene transform: scale ×%.4f, ground %s, origin %s." % (
            scale,
            "levelled" if levelled else "as solved",
            "moved" if moved else "as solved")

    # -- Re-export ------------------------------------------------------------
    def _reexport_current_solve(self):
        """Write a new export folder from the existing solve, off the GUI thread."""
        if self.reexport_worker is not None and self.reexport_worker.isRunning():
            self._append_log_3d("A re-export is already running.", WARN)
            return
        shot = self._current_shot_name()
        if not shot:
            QMessageBox.warning(self, "No Shot Selected",
                                "Select a clip first - re-export works on its existing solve.")
            return
        shot_dir = SCENES_DIR / shot
        found, folder = find_latest_output(
            shot_dir, "3D_CAMERA_TRACK", ["sparse/cameras.txt", "sparse/cameras.bin"],
            legacy_subdirs=("",))
        if not found:
            QMessageBox.warning(
                self, "No Solve Found",
                "No solved COLMAP model was found for '%s'.\n\nRun 3D Camera Tracking first."
                % shot)
            return

        options = {
            "video_path": VIDEOS_DIR / self.combo_2d_video.currentText(),
            "blender_path": self.txt_blender_path.text().strip() or None,
            "fps": self.current_fps if self.current_fps and self.current_fps > 0 else None,
            "start_frame": self.spin_start_frame_3d.value(),
            "colmap_exe": COLMAP_EXE,
            "frame_step": self.spin_step.value(),
            "scene_transform": self._scene_transform,
            "overscan": self._overscan_value(),
            "write_undistort": bool(self.chk_write_undistort.isChecked()),
            "pixel_aspect": float(self.spin_pixel_aspect.value()),
        }
        self._append_log_3d(
            "▶ Re-exporting '%s' from %s — scene transform %s, overscan %d%%, "
            "undistorted plate %s, pixel aspect %.4f."
            % (shot, Path(folder).name,
               "set" if self._scene_transform else "none",
               int(round(options["overscan"] * 100)),
               "yes" if options["write_undistort"] else "no",
               options["pixel_aspect"]), ACCENT)

        self.btn_reexport.setEnabled(False)
        self.reexport_worker = ReExportWorker(folder, shot_dir, options)
        self.reexport_worker.log_signal.connect(self._append_log_3d)
        self.reexport_worker.finished_signal.connect(self._on_reexport_finished)
        self.reexport_worker.start()

    def _overscan_value(self):
        """The overscan spin box as the fraction the exporters take."""
        return max(0.0, min(project_file.MAX_OVERSCAN, self.spin_overscan.value() / 100.0))

    def _on_reexport_finished(self, success, message):
        self.btn_reexport.setEnabled(True)
        self._append_log_3d(message, OK if success else ERR)
        if success:
            # The media table's status column and the panel both read the newest
            # output, so both have to be told it moved.
            self._refresh_videos()
            self._load_solve_for_shot(self._current_shot_name())
            self._refresh_scene_setup()

    # =========================================================================
    # PER-SHOT PROJECT FILE
    # =========================================================================
    def _max_dimension_2d(self):
        """Working resolution the Resolution combo asks for. 0 means original."""
        res_text = self.combo_2d_res.currentText()
        if "512p" in res_text:
            return 512
        if "1080p" in res_text:
            return 1080
        if "Original" in res_text:
            return 0
        return 720

    def _project_state(self):
        """Everything about the current shot that belongs in its project file."""
        canvas = self.canvas_2d
        data = project_file.default_project()
        data["fps"] = float(self.current_fps) if self.current_fps and self.current_fps > 0 else 24.0
        # One start for the shot: the two spin boxes mirror each other.
        data["timeline_start"] = int(self.spin_start_frame_2d.value())
        data["in_point"] = int(canvas.in_point)
        data["out_point"] = int(canvas.out_point)
        data["layers"] = canvas.layers_to_config()
        data["settings_2d"] = {
            "model": self.combo_2d_model.currentText(),
            "resolution": self.combo_2d_res.currentText(),
            "max_dimension": self._max_dimension_2d(),
            "min_confidence": float(self.spin_min_conf.value()),
            "grid_size": int(self.spin_grid_size.value()),
            "auto_chunk": bool(self.chk_vram_chunk.isChecked()),
            "track_backwards": bool(self.chk_track_backwards.isChecked()),
        }
        data["settings_3d"] = {
            "preset": self.preset_combo.currentText(),
            "solver_engine": self.combo_solver_engine.currentText(),
            "camera_model": self.combo_cam.currentText(),
            "tri_angle": float(self.spin_tri.value()),
            "overlap": int(self.spin_overlap.value()),
            "inliers": int(self.spin_inliers.value()),
            "frame_step": int(self.spin_step.value()),
            "single_camera": bool(self.chk_single_cam.isChecked()),
            "ba_refine_distortion": bool(self.chk_ba_refine.isChecked()),
            "use_gpu": bool(self.chk_gpu.isChecked()),
            "caspar_ba": bool(self.chk_caspar_ba.isChecked()),
            "generate_mesh": bool(self.chk_mesh_gen.isChecked()),
            "blender_path": self.txt_blender_path.text().strip(),
            "write_undistort": bool(self.chk_write_undistort.isChecked()),
            "overscan": self._overscan_value(),
            "pixel_aspect": float(self.spin_pixel_aspect.value()),
        }
        # Scale, ground and origin live at the top level, not in settings_3d:
        # they describe the shot's world, not a control on a tab.
        data["scene_transform"] = self._scene_transform
        return data

    def _apply_project(self, data):
        """
        Put a loaded project back into the window.

        Every widget is set with its signals blocked and `_restoring_project`
        held, so restoring a shot cannot be mistaken for the artist editing it
        and write the file back before it has finished loading.
        """
        self._restoring_project = True
        try:
            def combo_text(combo, text, quiet=True):
                if text and combo.findText(text) >= 0:
                    combo.blockSignals(quiet)
                    combo.setCurrentText(text)
                    combo.blockSignals(False)

            def quiet(widget, value):
                widget.blockSignals(True)
                widget.setValue(value)
                widget.blockSignals(False)

            def quiet_check(widget, value):
                widget.blockSignals(True)
                widget.setChecked(bool(value))
                widget.blockSignals(False)

            s3 = data.get("settings_3d") or {}
            # The preset first, and noisily, so its description panel updates:
            # it writes the solver fields, and the saved values below are
            # whatever the artist tuned on top of it.
            combo_text(self.preset_combo, s3.get("preset"), quiet=False)
            combo_text(self.combo_solver_engine, s3.get("solver_engine"))
            combo_text(self.combo_cam, s3.get("camera_model"))
            quiet(self.spin_tri, float(s3.get("tri_angle", self.spin_tri.value())))
            quiet(self.spin_overlap, int(s3.get("overlap", self.spin_overlap.value())))
            quiet(self.spin_inliers, int(s3.get("inliers", self.spin_inliers.value())))
            quiet(self.spin_step, int(s3.get("frame_step", self.spin_step.value())))
            quiet_check(self.chk_single_cam, s3.get("single_camera", True))
            quiet_check(self.chk_ba_refine, s3.get("ba_refine_distortion", True))
            quiet_check(self.chk_gpu, s3.get("use_gpu", True))
            quiet_check(self.chk_caspar_ba, s3.get("caspar_ba", True))
            quiet_check(self.chk_mesh_gen, s3.get("generate_mesh", False))
            quiet_check(self.chk_write_undistort, s3.get("write_undistort", False))
            quiet(self.spin_overscan, int(round(float(s3.get("overscan", 0.0)) * 100)))
            # Pixel aspect is set by _apply_pixel_aspect_for_clip, for the same
            # reason the frame rate is: a video file's own value wins over a
            # saved one, and only a sequence's is the artist's to keep.
            blender = (s3.get("blender_path") or "").strip()
            if blender:
                self.txt_blender_path.blockSignals(True)
                self.txt_blender_path.setText(blender)
                self.txt_blender_path.blockSignals(False)

            s2 = data.get("settings_2d") or {}
            combo_text(self.combo_2d_model, s2.get("model"))
            combo_text(self.combo_2d_res, s2.get("resolution"))
            quiet_check(self.chk_vram_chunk, s2.get("auto_chunk", True))
            quiet_check(self.chk_track_backwards, s2.get("track_backwards", False))

            self._set_timeline_start(int(data.get("timeline_start", 1)))

            # The scene transform is the shot's, not a widget's: it only has to
            # come back into the window state the panel and the exports read.
            self._scene_transform = data.get("scene_transform")

            self.canvas_2d.layers_from_config(
                data.get("layers"),
                in_point=data.get("in_point", 0),
                out_point=data.get("out_point", -1))
            self._refresh_layer_list()
            self._on_layer_selected(self.canvas_2d.active_layer_idx)
            self._sync_range_status()
        except Exception as e:
            log.exception("restoring the project failed")
            self._append_log_2d(f"! Could not fully restore the saved project: {e}", WARN)
        finally:
            self._restoring_project = False

    def _sync_range_status(self):
        """Redraw the in/out chip from whatever the canvas now holds."""
        canvas = self.canvas_2d
        if canvas.in_point == 0 and canvas.out_point < 0:
            self.lbl_range_status.setText("Full")
            self._set_chip_state(self.lbl_range_status, "idle")
            return
        out_p = canvas.out_point if canvas.out_point >= 0 else self.slider_2d_frame.maximum()
        self.lbl_range_status.setText(f"{canvas.in_point + 1} – {out_p + 1}")
        self._set_chip_state(self.lbl_range_status, "key")

    def _set_timeline_start(self, value):
        """
        Set both Timeline start boxes at once.

        The shot has one start frame; two fields that can disagree is how a
        camera ends up exported onto different frames from its own 2D tracks.
        """
        value = int(value)
        for spin in (self.spin_start_frame_2d, self.spin_start_frame_3d):
            if spin.value() != value:
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)

    def _on_timeline_start_changed(self, value):
        self._set_timeline_start(value)
        self._schedule_project_save()

    def _apply_fps_for_clip(self, video_path, saved_fps=None):
        """
        Decide the shot's frame rate and put it in the fps field.

        A video file knows its own rate, so that is read from the file and the
        field locked. An image sequence does not, so the saved project wins,
        then the last rate the artist typed, then 24 - and the field stays
        editable, because only the artist knows what the plate was shot at.
        """
        video_path = Path(video_path)
        if video_path.is_dir():
            if saved_fps and float(saved_fps) > 0:
                fps, source = float(saved_fps), "the saved project"
            elif self._last_user_fps and self._last_user_fps > 0:
                fps, source = float(self._last_user_fps), "the last rate you used"
            else:
                fps, source = 24.0, "the default"
            editable = True
            tip_source = ("An image sequence carries no frame rate, so this one is yours "
                          "to set.")
        else:
            fps, source = float(probe_fps(video_path)), "the file"
            editable = False
            tip_source = "Read from the file, so it cannot be edited here."

        self.current_fps = fps if fps > 0 else 24.0
        for spin in (self.spin_fps, self.spin_fps_3d):
            spin.blockSignals(True)
            spin.setValue(self.current_fps)
            spin.blockSignals(False)
        self.spin_fps.setReadOnly(not editable)
        self.spin_fps.setButtonSymbols(
            QDoubleSpinBox.UpDownArrows if editable else QDoubleSpinBox.NoButtons)
        self.spin_fps.setToolTip(
            "Frame rate of the plate. Drives playback, the timecode readout and\n"
            "every exported curve, so a wrong value puts the keys on wrong times.\n"
            + tip_source + "\n"
            "Common rates: 23.976, 24, 25, 29.97, 30, 48, 50, 60.")
        self.canvas_2d.fps = self.current_fps
        self._append_log_2d(
            f"Frame rate {self.current_fps:.3f} fps, from {source} — used for playback, "
            f"timecode and every export.", TEXT_DIM)

    def _apply_pixel_aspect_for_clip(self, video_path, saved_aspect=None):
        """
        Decide the shot's pixel aspect and put it in the field (roadmap 1.6).

        A video file can carry a sample aspect ratio, so that is read from the
        file when the probe knows how; an image sequence carries nothing, so the
        saved project wins and the field stays the artist's. The field is left
        editable either way - a wrapper that claims square pixels on an
        anamorphic plate is common enough that locking the artist out would be
        worse than trusting them.
        """
        video_path = Path(video_path)
        aspect, source = 1.0, "the default"
        if saved_aspect:
            try:
                if float(saved_aspect) > 0:
                    aspect, source = float(saved_aspect), "the saved project"
            except (TypeError, ValueError):
                pass

        if video_path.is_file():
            try:
                probed = float(media_info.probe_pixel_aspect(video_path))
            except Exception as e:
                log.warning("pixel aspect probe failed for %s: %s", video_path.name, e)
                probed = 0.0
            # A probe that found a real squeeze is the best answer there is. A
            # probe that came back square may only mean the file says nothing,
            # so it must not quietly undo a value the artist typed for this shot.
            if probed > 0 and abs(probed - 1.0) > 1e-6:
                aspect, source = probed, "the file"

        self.spin_pixel_aspect.blockSignals(True)
        self.spin_pixel_aspect.setValue(aspect)
        self.spin_pixel_aspect.blockSignals(False)
        if abs(aspect - 1.0) > 1e-6:
            self._append_log_3d(
                "Pixel aspect %.4f, from %s — the plate is squeezed, so the solve and "
                "the exports are corrected for it." % (aspect, source), ACCENT)

    def _on_fps_changed(self, value):
        """The artist typed a rate for a sequence."""
        fps = float(value) if value and float(value) > 0 else 24.0
        self.current_fps = fps
        self._last_user_fps = fps
        self.spin_fps_3d.blockSignals(True)
        self.spin_fps_3d.setValue(fps)
        self.spin_fps_3d.blockSignals(False)
        self.canvas_2d.fps = fps
        self.canvas_2d.update()
        if self.is_playing:
            self.play_timer.start(max(10, int(round(1000.0 / fps))))
        self._append_log_2d(f"Frame rate set to {fps:.3f} fps by hand.", ACCENT)
        self._schedule_project_save()
        self._schedule_settings_save()

    def _schedule_project_save(self):
        if not self._closing and not self._restoring_project and self._project_shot:
            self._project_timer.start()

    def _save_project(self):
        """
        Write the current shot's project file.

        A failure is not fatal - the artist carries on working - but it is the
        one case where quitting really would lose work, so it is remembered and
        closeEvent asks before quitting.
        """
        shot = self._project_shot
        if not shot:
            return False
        path = project_file.project_path(SCENES_DIR, shot)
        try:
            project_file.save_project(path, self._project_state())
            self._project_save_failed = False
            return True
        except Exception as e:
            self._project_save_failed = True
            log.warning("saving the project for %s failed: %s", shot, e)
            self._status("Could not save the project for '%s': %s" % (shot, e), error=True)
            return False

    def _flush_project_save(self):
        """Write now instead of waiting for the debounce (clip switch, close)."""
        self._project_timer.stop()
        if self._project_shot and not self._restoring_project:
            self._save_project()

    # =========================================================================
    # SHUTDOWN AND SETTINGS
    # =========================================================================

    def closeEvent(self, event):
        running = [(name, w) for name, w in (("3D solve", self.worker_3d),
                                             ("2D track", self.worker_2d),
                                             ("2D correction", self.correction_worker))
                   if w is not None and w.isRunning()]
        if running:
            r = QMessageBox.question(
                self, "Job Running",
                "A job is running (%s). Cancel it and quit?" % ", ".join(n for n, _ in running),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                event.ignore()
                return

        # Layers, masks and the range now live in the shot's project file, so
        # there is nothing to warn about - unless writing that file failed, in
        # which case quitting really does lose the work.
        self._flush_project_save()
        if self._project_save_failed:
            r = QMessageBox.question(
                self, "Project Not Saved",
                "The project file for '%s' could not be written, so the layers, points, "
                "masks and in/out range on the 2D tab are not saved anywhere.\n\n"
                "Check that 04 SCENES is writable.\n\nQuit and lose them?"
                % (self._project_shot or "this shot"),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if r != QMessageBox.Yes:
                event.ignore()
                return

        self._closing = True
        self._pause_playback()
        self.vram_timer.stop()
        self._settings_timer.stop()
        self._project_timer.stop()
        if self.update_worker is not None:
            self.update_worker.wait(3500)

        for name, w in running:
            self._status("Cancelling %s..." % name)
            try:
                w.cancel()
            except Exception as e:
                log.warning("cancel() failed for %s: %s", name, e)
        for name, w in running:
            # Bounded: COLMAP is terminated by cancel(); the mapper steps still
            # take a moment to notice. Beyond this we leave anyway.
            if not w.wait(15000):
                log.warning("%s worker did not stop within 15 s; exiting anyway", name)

        for w in (self.frame_extractor, self.copy_worker, self.reexport_worker):
            if w is not None and w.isRunning():
                if hasattr(w, "cancel"):
                    try:
                        w.cancel()
                    except Exception:
                        pass
                w.wait(5000)

        try:
            gpu_monitor.stop()
        except Exception as e:
            log.warning("gpu monitor stop failed: %s", e)

        self._save_settings()
        log.info("window closed")
        event.accept()

    # -- QSettings (C18) ------------------------------------------------------
    def _wire_settings_autosave(self):
        """
        Persist a moment after any persisted control changes.

        QSettings now only carries app-wide preferences (window, tab, Blender
        path, last clip, last frame rate); every solver field is per shot and
        goes to that shot's project file instead.
        """
        s = self._schedule_settings_save
        p = self._schedule_project_save
        self.tabs.currentChanged.connect(lambda _i: s())
        self.txt_blender_path.textChanged.connect(lambda _t: s())
        self.txt_blender_path.textChanged.connect(lambda _t: p())
        self.preset_combo.currentTextChanged.connect(lambda _t: p())
        for w in (self.combo_solver_engine, self.combo_cam,
                  self.combo_2d_model, self.combo_2d_res):
            w.currentIndexChanged.connect(lambda _i: p())
        for w in (self.spin_tri, self.spin_overlap, self.spin_inliers, self.spin_step,
                  self.spin_min_conf):
            w.valueChanged.connect(lambda _v: p())
        for w in (self.spin_overscan, self.spin_pixel_aspect):
            w.valueChanged.connect(lambda _v: p())
        for w in (self.chk_single_cam, self.chk_ba_refine, self.chk_gpu,
                  self.chk_caspar_ba, self.chk_mesh_gen, self.chk_vram_chunk,
                  self.chk_write_undistort, self.chk_track_backwards):
            w.toggled.connect(lambda _b: p())
        # Scene setup: a pick is not saved (it is a step towards a transform),
        # but the frame in view decides which camera the points are drawn
        # through, so the overlay follows the playhead.
        self.canvas_2d.solved_point_picked.connect(self._on_solved_point_picked)
        self.slider_2d_frame.valueChanged.connect(lambda _v: self._update_scene_overlay())
        # Both Timeline start boxes describe the same thing, so either one
        # moving carries the other with it before the project is written.
        for w in (self.spin_start_frame_2d, self.spin_start_frame_3d):
            w.valueChanged.connect(self._on_timeline_start_changed)
        # Roto, points and the in/out range are the work itself.
        self.canvas_2d.masks_changed.connect(p)
        self.canvas_2d.in_point_requested.connect(lambda _f: p())
        self.canvas_2d.out_point_requested.connect(lambda _f: p())

    def _schedule_settings_save(self):
        if not self._closing:
            self._settings_timer.start()

    def _on_update_pref(self, on):
        self._check_updates = bool(on)
        self._schedule_settings_save()

    def _start_update_check(self):
        if not self._check_updates or self.update_worker is not None:
            return
        self.update_worker = UpdateCheckWorker()
        self.update_worker.found_signal.connect(self._on_update_found)
        self.update_worker.start()

    def _on_update_found(self, info):
        self.lbl_update.setText(
            'Version %s is available (you have %s) - '
            '<a href="%s" style="color:%s">release notes</a>'
            % (info.get("tag", "?"), display_version(), info.get("url", ""), ACCENT))
        self.update_banner.setVisible(True)

    def _save_settings(self):
        st = self.settings
        try:
            st.setValue("window/geometry", self.saveGeometry())
            st.setValue("window/state", self.saveState())
            st.setValue("window/tab", self.tabs.currentIndex())
            st.setValue("blender/path", self.txt_blender_path.text().strip())
            # The frame rate of a sequence is a guess until the artist makes it
            # one, so the last one they typed is worth remembering app-wide.
            st.setValue("track2d/last_fps", float(self._last_user_fps))
            st.setValue("media/last_clip", self.combo_2d_video.currentText() or self._last_clip)
            st.setValue("updates/check_on_start", bool(self._check_updates))
            st.sync()
        except Exception as e:
            log.warning("saving settings failed: %s", e)

    def _load_settings(self):
        st = self.settings

        def val(key, default, typ):
            try:
                v = st.value(key, default, type=typ)
                return default if v is None else v
            except Exception:
                return default

        try:
            geo = st.value("window/geometry")
            if geo:
                self.restoreGeometry(geo)
            state = st.value("window/state")
            if state:
                self.restoreState(state)
            self.tabs.setCurrentIndex(max(0, min(self.tabs.count() - 1, val("window/tab", 0, int))))

            self.txt_blender_path.setText(val("blender/path", "", str))

            last_fps = val("track2d/last_fps", 24.0, float)
            self._last_user_fps = last_fps if last_fps and last_fps > 0 else 24.0

            self._check_updates = val("updates/check_on_start", False, bool)
            self.act_updates.setChecked(self._check_updates)

            # Everything else the solver tabs hold is per shot and comes from
            # 04 SCENES/<shot>/project.json when the clip is selected.
        except Exception as e:
            log.warning("loading settings failed: %s", e)


# =============================================================================
# CRASH HANDLER AND LOGGING (B3)
# =============================================================================
class _CrashRelay(QObject):
    """Carries a crash report from any thread to a dialog on the GUI thread."""
    report = Signal(str, str)


_crash_relay = None


class _LogStream:
    """
    File-like stand-in for sys.stdout / sys.stderr. The windowed build has
    neither, so print() would raise; this writes to app.log (and to the real
    stream too when there is one).
    """
    encoding = "utf-8"

    def __init__(self, level, mirror=None):
        self._level = level
        self._mirror = mirror
        self._buf = ""

    def write(self, text):
        if not text:
            return 0
        if self._mirror is not None:
            try:
                self._mirror.write(text)
            except Exception:
                pass
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                logging.getLogger("stdout").log(self._level, line.rstrip())
        return len(text)

    def flush(self):
        if self._mirror is not None:
            try:
                self._mirror.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def setup_logging():
    """Everything printed or logged goes to a rotating app.log the user can send."""
    folder = logs_dir()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(
            folder / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(handler)
    except Exception:
        return folder
    sys.stdout = _LogStream(logging.INFO, sys.__stdout__)
    sys.stderr = _LogStream(logging.ERROR, sys.__stderr__)
    log.info("=" * 60)
    log.info("Automated Tracker %s starting  (frozen=%s, python=%s)",
             display_version(), bool(getattr(sys, "frozen", False)), sys.version.split()[0])
    return folder


def _write_crash_log(text):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = logs_dir() / ("error_%s.log" % stamp)
    try:
        path.write_text(text, encoding="utf-8")
    except Exception:
        path = None
    return path


def _show_crash_dialog(message, path_text):
    try:
        box = QMessageBox(QMessageBox.Critical, "Automated Tracker - Unexpected Error",
                          "Something went wrong. The action you tried may not have completed.\n\n"
                          "%s\n\nA full report was written to:\n%s\n\n"
                          "Please send that file with a bug report." % (message, path_text))
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.exec()
    except Exception:
        pass


def install_crash_handler():
    """
    Catch what would otherwise vanish. In the frozen, windowed build there is
    no stderr, so an exception raised in a Qt slot just made that button stop
    working. Now it is logged to error_<timestamp>.log and shown in a dialog.
    """
    global _crash_relay
    _crash_relay = _CrashRelay()
    _crash_relay.report.connect(_show_crash_dialog)

    def _handle(exc_type, exc_value, exc_tb, where="main thread"):
        if issubclass(exc_type, KeyboardInterrupt):
            return
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        header = ("Automated Tracker %s  crash in %s\n%s\n\n"
                  % (display_version(), where, datetime.datetime.now().isoformat()))
        try:
            log.critical("unhandled exception in %s:\n%s", where, tb)
        except Exception:
            pass
        path = _write_crash_log(header + tb)
        summary = "%s: %s" % (exc_type.__name__, str(exc_value)[:300])
        _crash_relay.report.emit(summary, str(path) if path else "(the log folder is not writable)")

    def excepthook(exc_type, exc_value, exc_tb):
        _handle(exc_type, exc_value, exc_tb)

    def thread_hook(args):
        _handle(args.exc_type, args.exc_value, args.exc_traceback,
                where="thread %s" % getattr(args.thread, "name", "?"))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook


def main():
    if "--selftest" in sys.argv:
        # Normally handled at import time above; kept for callers of main().
        from core.selftest import run_selftest
        sys.exit(run_selftest())

    log_folder = setup_logging()

    app = QApplication(sys.argv)
    app.setOrganizationName(SETTINGS_ORG)
    app.setApplicationName(SETTINGS_APP)
    app.setApplicationVersion(APP_VERSION)
    # PySide6 routes exceptions raised inside slots (and QThread.run) through
    # sys.excepthook, so one hook covers the Qt side as well.
    install_crash_handler()
    # Fusion draws its sub-controls from the palette on every platform, which keeps
    # arrows and spin buttons consistent instead of inheriting the Windows look.
    app.setStyle("Fusion")
    apply_dark_palette(app)
    window = TrackerMainWindow()
    window.show()
    window._status("Ready  (logs: %s)" % log_folder)
    rc = app.exec()
    logging.shutdown()
    sys.exit(rc)


if __name__ == "__main__":
    main()
