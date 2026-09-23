"""
Automated Photogrammetry & 2D AI Motion Tracker — VFX Studio Edition
Modular Main Coordinator & GUI Application Window
"""

import sys
import os
import random
import logging
import threading
from pathlib import Path

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QTabWidget, QMessageBox, QFileDialog, QStackedLayout,
        QGraphicsOpacityEffect, QDoubleSpinBox, QPushButton
    )
    from PySide6.QtCore import Qt, QTimer, QSettings
    from PySide6.QtGui import QPixmap, QDragEnterEvent, QDropEvent
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
from gui.context import AppContext
from gui.crash import install_crash_handler, setup_logging
from gui.layer_panel import LayerPanel
from gui.media_panel import MediaPanel
from gui.player import PlayerController
from gui.correction import CorrectionController
from gui.scene_setup import SceneSetupPanel
from gui.workers import (
    MediaCopyWorker, ReExportWorker, TrackCorrectionWorker, UpdateCheckWorker,
)
from gui import menus as menu_table
from gui.tab_3d import build_3d_tab
from gui.tab_2d import build_2d_tab
from gui import shortcuts as shortcut_table
from core.workers import TrackerWorker, CoTrackerWorker, FrameExtractorWorker
from core.hardware import gpu_monitor
from core.proc import popen_gui
from core import media_info
from core.media_info import probe_fps, probe_frame_count, detect_sequence_start, sequence_files
from core.presets import PRESETS, DEFAULT_PRESET
from core import project as project_file
from core.media_pool import (
    VIDEO_EXTS, scan_media_pool, find_latest_output, prune_thumb_cache,
)

# QSettings identity - registry key on Windows.
SETTINGS_ORG = "AutomatedTracker"
SETTINGS_APP = "AutomatedTracker"


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
        # Everything else about playback - the frame buffers, the direction,
        # the speed and the timer that drives it - belongs to the
        # PlayerController, which _init_ui builds a moment from now.

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

    # -- the player's state, under the names it has always had -------------
    # Playback belongs to the PlayerController now, but `win.is_playing` and
    # the rest are what the handlers, the tests and tools/e2e_plate.py read,
    # so they stay readable here rather than becoming two names for one thing.
    @property
    def is_playing(self):
        return self.player.is_playing

    @property
    def play_timer(self):
        return self.player.play_timer

    @property
    def overlay_frames(self):
        return self.player.overlay_frames

    @property
    def loaded_video_frames(self):
        return self.player.loaded_video_frames

    def _refresh_videos(self):
        """
        Rescan the media folder.

        The panel does the work; this name stays because it is how
        tools/e2e_plate.py drives a headless run, and a headless run that
        cannot find the media pool is a headless run that proves nothing.
        """
        self.media.refresh_videos()

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
        self.correction.refresh()

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
            self.media.refresh_videos()
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
        # The contract the menus, the tabs and the panels are built against.
        # It exists before any of them, because they register their widgets
        # through it as they build.
        self.ctx = AppContext(self, {
            "base_dir": BASE_DIR,
            "videos_dir": VIDEOS_DIR,
            "scenes_dir": SCENES_DIR,
            "colmap_exe": COLMAP_EXE,
            "ffmpeg_exe": FFMPEG_EXE,
            "thumbs_dir": thumbs_dir(),
        }, PRESETS)
        # The panels that own a slice of a tab, before the tabs that show
        # them: each tab builder connects its buttons straight to these.
        self.layers = LayerPanel(self.ctx)
        self.ctx.layers = self.layers
        self.media = MediaPanel(self.ctx)
        self.ctx.media = self.media
        self.player = PlayerController(self.ctx)
        self.ctx.player = self.player
        self.scene_setup = SceneSetupPanel(self.ctx)
        self.ctx.scene_setup = self.scene_setup
        self.correction = CorrectionController(self.ctx)
        self.ctx.correction = self.correction

        # ---------------- Native Desktop Menu Bar ----------------
        menu_table.build(self.menuBar(), self.ctx)

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

        build_3d_tab(self.ctx, self.tab_3d)
        build_2d_tab(self.ctx, self.tab_2d)

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

        # What Ctrl+Z would take back, kept quietly at the end of the status
        # bar: an undo the artist cannot name is an undo they are afraid of.
        self.status_undo = QLabel("Nothing to undo")
        self.status_undo.setObjectName("statusChip")
        self.status_undo.setToolTip("Ctrl+Z undoes this. Ctrl+Y (or Ctrl+Shift+Z) redoes it.")
        statusbar.addPermanentWidget(self.status_undo)

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

        # The keys, and the stack they drive, last: both need the 2D tab's
        # canvas and transport, which the tab builders have just made.
        self._install_shortcuts()
        self.canvas_2d.undo_stack.indexChanged.connect(self._on_undo_index_changed)
        self._refresh_undo_status()

    # =========================================================================
    # KEYBOARD SHORTCUTS AND UNDO (roadmap 2.5)
    # =========================================================================
    def _install_shortcuts(self):
        """
        Bind gui/shortcuts.py to this window.

        Every handler is named in that table, so a key that does nothing is a
        missing entry here rather than a binding hidden somewhere in a widget's
        keyPressEvent.
        """
        handlers = {
            "play_pause": self.player.toggle_playback,
            "shuttle_back": lambda: self.player.shuttle(-1),
            "pause": self.player.pause_playback,
            "shuttle_fwd": lambda: self.player.shuttle(1),
            "step_back": lambda: self.player.step_frames(-1),
            "step_fwd": lambda: self.player.step_frames(1),
            "step_back_10": lambda: self.player.step_frames(-10),
            "step_fwd_10": lambda: self.player.step_frames(10),
            "go_start": lambda: self.player.go_to_frame(0),
            "go_end": lambda: self.player.go_to_frame(self.slider_2d_frame.maximum()),
            "set_in": lambda: self.player.set_in_point(self.slider_2d_frame.value()),
            "set_out": lambda: self.player.set_out_point(self.slider_2d_frame.value()),
            "clear_in": self.player.clear_in_point,
            "clear_out": self.player.clear_out_point,
            "prev_key": self.player.jump_prev_keyframe,
            "next_key": self.player.jump_next_keyframe,
            "del_key": self.player.delete_mask_keyframe_on_current,
            "del_mask": self.canvas_2d.delete_selected_mask,
            "toggle_matte": self.btn_toggle_matte.toggle,
            "toggle_alpha": self.btn_toggle_alpha.toggle,
            "undo": self._undo,
            "redo": self._redo,
        }
        # Every one of these drives the 2D viewport, so none of them may fire
        # from the 3D tab: Del pressed in the media table must not quietly
        # take a roto keyframe out of a shot the artist is not even looking at.
        wired = {name: self._only_on_2d_tab(fn) for name, fn in handlers.items()}
        wired["help"] = self._show_shortcuts_dialog
        shortcut_table.install(self, wired)

    def _only_on_2d_tab(self, fn):
        def run():
            if self.tabs.currentWidget() is self.tab_2d:
                fn()
        return run

    def _show_shortcuts_dialog(self):
        shortcut_table.show_dialog(self)

    def _undo(self):
        """Ctrl+Z, and say in the status bar what it took back."""
        stack = self.canvas_2d.undo_stack
        if not stack.canUndo():
            self._status("Nothing to undo.")
            return
        what = stack.undoText()
        stack.undo()
        self._status("Undid: %s" % (what or "the last edit"))

    def _redo(self):
        stack = self.canvas_2d.undo_stack
        if not stack.canRedo():
            self._status("Nothing to redo.")
            return
        what = stack.redoText()
        stack.redo()
        self._status("Redid: %s" % (what or "the last edit"))

    def _on_undo_index_changed(self, _index):
        """
        Anything on the stack moved: the shot's file is now out of date.

        This is the one place that covers both directions - an undo is an edit
        like any other, and a shot whose roto was just put back must be written
        again or the next launch restores the version the artist rejected.
        """
        self._refresh_undo_status()
        self._schedule_project_save()

    def _refresh_undo_status(self):
        stack = self.canvas_2d.undo_stack
        self.status_undo.setText(
            ("↶ %s" % stack.undoText()) if stack.canUndo() else "Nothing to undo")
        # "ok" is the status bar's own green; the roto chips use "key" for the
        # same colour, but a statusChip only knows these four states.
        self._set_chip_state(self.status_undo, "ok" if stack.canUndo() else "idle")

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
        self.media.refresh_videos()
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

    def _start_tracking_3d(self):
        if self.worker_3d is not None and self.worker_3d.isRunning():
            self._append_log_3d("A 3D solve is already running.", WARN)
            return
        videos = scan_media_pool(VIDEOS_DIR)

        if not videos:
            QMessageBox.warning(self, "No Videos Found", f"Please add at least one video or image sequence into:\n{VIDEOS_DIR}")
            return

        # Cleared here rather than just before the worker starts: what the batch
        # decided about each shot is written below, and clearing after it wiped
        # the very lines the artist needs to read.
        self.log_text.clear()

        # Only solve what is selected in the media table. Selecting nothing means
        # 'all of them', which is what the button used to do unconditionally.
        selected_names = set()
        for item in self.media.table.selectedItems():
            name_item = self.media.table.item(item.row(), 0)
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
            "scene_transform": (self.scene_setup.transform
                                if len(videos) == 1 and Path(videos[0]).stem == self._current_shot_name()
                                else None),
            "overscan": self._overscan_value(),
            "write_undistort": bool(self.chk_write_undistort.isChecked()),
            "pixel_aspect": float(self.spin_pixel_aspect.value()),
        }

        # Each shot is solved with the settings saved in its own project file
        # (2.3): a batch is several shots that were each set up on their own
        # day, and handing all of them whatever the controls happen to show is
        # handing them the settings of the shot that was open last. The shot in
        # view may have edits the debounce has not written yet, and the plan is
        # read from disk, so it is flushed first.
        self._flush_project_save()
        force_current = bool(self.chk_force_current_3d.isChecked())
        shot_names = [Path(v).stem for v in videos]
        saved = {name: project_file.load_project(
            project_file.project_path(SCENES_DIR, name)) for name in shot_names}
        plan = project_file.batch_settings_plan(saved, shot_names, force_current)
        self._append_log_3d(project_file.batch_summary(plan), ACCENT)
        if plan["saved"] and plan["current"]:
            self._append_log_3d(
                f"   Saved settings: {', '.join(plan['saved'])}. "
                f"On-screen settings: {', '.join(plan['current'])}.", TEXT_DIM)

        with_own = set(plan["saved"])
        configs = {
            str(video): (project_file.solve_config_from_project(config, saved[name], PRESETS)
                         if name in with_own else dict(config))
            for video, name in zip(videos, shot_names)
        }

        self.player.pause_playback()
        self.btn_start_3d.setEnabled(False)
        self.btn_stop_3d.setEnabled(True)

        self.worker_3d = TrackerWorker(
            videos, configs,
            base_dir=BASE_DIR,
            colmap_dir=COLMAP_DIR,
            colmap_exe=COLMAP_EXE,
            ffmpeg_dir=FFMPEG_DIR,
            ffmpeg_exe=FFMPEG_EXE,
            scenes_dir=SCENES_DIR
        )
        # The engine's own stream only reaches the console when it is asked for
        # (2.4); the worker reads this on every line, so it can be turned on
        # halfway through a solve that is going wrong.
        self.worker_3d.show_engine_output = bool(self.chk_show_engine_output.isChecked())
        self.worker_3d.log_signal.connect(self._append_log_3d)
        self.worker_3d.progress_signal.connect(self._update_progress_3d)
        self.worker_3d.video_status_signal.connect(self.media.update_video_status)
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
        self.media.refresh_videos()
        # A fresh solve is a fresh point cloud, so the picks that pointed into
        # the old one mean nothing now.
        self.scene_setup.clear_picks()
        self.scene_setup.load_solve_for_shot(self._current_shot_name())
        self.scene_setup.refresh()

    def _on_show_engine_output(self, on):
        """
        The 'Show engine output' toggle (2.4).

        A running solve picks it up on its next line, because the moment you
        want COLMAP's own output is the moment the solve is already going wrong
        - waiting for the next run to see it would mean solving twice.
        """
        on = bool(on)
        if self.worker_3d is not None:
            self.worker_3d.show_engine_output = on
        self._append_log_3d(
            "Engine output is now shown in this console."
            if on else
            "Engine output is hidden; it is still written to the app log file. "
            "Warnings and errors from the engine always appear here.", TEXT_DIM)
        self._schedule_settings_save()

    def _append_log_3d(self, text, color=TEXT_DIM):
        self.log_text.append(f'<span style="color: {color};">{text}</span>')
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clear_3d_log(self):
        # The menu entry can be reached before the tab is built, so the guard
        # stays: an empty console is not worth an AttributeError.
        self.log_text.clear() if hasattr(self, 'log_text') else None

    def _update_progress_3d(self, val, stage_text):
        self.progress_bar.setValue(val)
        self.progress_bar.setFormat(f"{val}% — {stage_text}")

    def _export_3d_for_nuke(self):
        row = self.media.table.currentRow()
        if row < 0 and self.media.table.rowCount() > 0:
            row = 0
        if row < 0:
            QMessageBox.warning(self, "No Video Selected", "Please select a video from the table first.")
            return

        v_name = self.media.table.item(row, 0).text()
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
        row = self.media.table.currentRow()
        if row < 0 and self.media.table.rowCount() > 0:
            row = 0
        if row < 0:
            QMessageBox.warning(self, "No Video Selected", "Please select a video from the table first.")
            return

        v_name = self.media.table.item(row, 0).text()
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
        self.layers.refresh_layer_list()

    def _jump_to_point_keyframe(self):
        if self.canvas_2d.points:
            target_f = int(self.canvas_2d.points[0][0])
            self.slider_2d_frame.setValue(target_f)
            self._append_log_2d(f"⏮ Jumped timeline to point keyframe {target_f+1}.", ACCENT)
        else:
            self._append_log_2d("No manual points placed on active layer yet.", TEXT_DIM)

    def _clear_manual_points(self):
        self.canvas_2d.clear_active_layer_points()
        self.layers.refresh_layer_list()
        self.player.overlay_frames = None
        self.canvas_2d.is_overlay_active = False
        self.combo_view_layer.blockSignals(True)
        self.combo_view_layer.setCurrentIndex(0)
        self.combo_view_layer.blockSignals(False)
        cur = self.slider_2d_frame.value()
        self.player.on_frame_slider_changed(cur)
        l_name = self.canvas_2d.active_layer.name if self.canvas_2d.active_layer else "Active Layer"
        self._append_log_2d(f"🗑 Cleared tracking points on [{l_name}].", OK)

    def _clear_active_layer_masks(self):
        self.canvas_2d.clear_active_layer_masks()
        self.layers.refresh_layer_list()
        cur = self.slider_2d_frame.value()
        self.player.on_frame_slider_changed(cur)
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

        self.player.pause_playback()
        self.player.overlay_frames = None
        self.player.loaded_video_frames = None
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
        self.scene_setup.transform = None
        for bucket in self.scene_setup.picks.values():
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
        self.scene_setup.set_pick_mode(None)
        self.scene_setup.load_solve_for_shot(video_path.stem)
        self.scene_setup.refresh()

        # After the project, because the result is matched to the layers it was
        # tracked from and those have only just come back.
        self.correction.load_result_for_shot(video_path.stem)

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
            self.player.load_frame_preview(files[0], 0, len(files))
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
            self.player.load_frame_preview(jpgs[0], 0, len(jpgs))
            return

        tmp_img = self.media.thumbnail_for(video_path, 0, "-ss", "0.0")
        total_frames = probe_frame_count(video_path, self.current_fps, default=100) or 100

        if tmp_img is not None:
            self.slider_2d_frame.setRange(0, total_frames - 1)
            self.slider_2d_frame.setValue(0)
            self.player.load_frame_preview(tmp_img, 0, total_frames)
        else:
            self.slider_2d_frame.setRange(0, 0)
            self.slider_2d_frame.setValue(0)

        self._append_log_2d(f"⏳ Unpacking frame sequence for '{video_path.name}' in background for smooth 60 FPS scrubbing...", ACCENT)
        self._start_frame_extractor(video_path, scene_images_dir)

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
                    self.player.load_frame_preview(jpgs[cur_val], cur_val, len(jpgs))
        else:
            self._append_log_2d(
                f"✖ Frame extraction produced nothing for '{shot_name}'. "
                f"Check the clip plays and that 04 SCENES is writable.", ERR)
        self.player.update_keyframe_status()

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

        self.player.pause_playback()
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
            self.correction.load_result_for_shot(self._current_shot_name())
            self._schedule_project_save()
            self.player.load_overlay_into_player()

    # =========================================================================
    # THE CORRECTION JOB  (roadmap 2.1)
    #
    # gui/correction.py decides what needs re-tracking or re-exporting; running
    # it is a QThread, which is this window's business like every other one.
    # =========================================================================
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
        self.correction_worker.finished_signal.connect(
            self.correction.on_correction_finished)
        self.correction_worker.start()
        self.correction.refresh()

    # =========================================================================
    # THE SHOT IN VIEW, AND RE-EXPORTING ITS SOLVE
    #
    # Scale, ground and origin moved to gui/scene_setup.py; the transform they
    # build is read back off self.scene_setup and handed to the exporters here.
    # =========================================================================
    def _current_shot_name(self):
        """The shot the 2D canvas is showing, which is the one scene setup works on."""
        name = self.combo_2d_video.currentText()
        return Path(name).stem if name else None

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
            "scene_transform": self.scene_setup.transform,
            "overscan": self._overscan_value(),
            "write_undistort": bool(self.chk_write_undistort.isChecked()),
            "pixel_aspect": float(self.spin_pixel_aspect.value()),
        }
        self._append_log_3d(
            "▶ Re-exporting '%s' from %s — scene transform %s, overscan %d%%, "
            "undistorted plate %s, pixel aspect %.4f."
            % (shot, Path(folder).name,
               "set" if self.scene_setup.transform else "none",
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
            self.media.refresh_videos()
            self.scene_setup.load_solve_for_shot(self._current_shot_name())
            self.scene_setup.refresh()

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
        data["scene_transform"] = self.scene_setup.transform
        # Solve timings (2.4) are written by the solver thread, into shots this
        # window may not even have open, so they are carried over from the file
        # rather than from anything on screen - otherwise the next autosave of
        # the shot in view would quietly throw away the timing it just earned.
        if self._project_shot:
            on_disk = project_file.load_project(
                project_file.project_path(SCENES_DIR, self._project_shot))
            data["solve_timings"] = (on_disk or {}).get("solve_timings") or []
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
            self.scene_setup.transform = data.get("scene_transform")

            self.canvas_2d.layers_from_config(
                data.get("layers"),
                in_point=data.get("in_point", 0),
                out_point=data.get("out_point", -1))
            self.layers.refresh_layer_list()
            self.layers.on_layer_selected(self.canvas_2d.active_layer_idx)
            self.player.sync_range_status()
        except Exception as e:
            log.exception("restoring the project failed")
            self._append_log_2d(f"! Could not fully restore the saved project: {e}", WARN)
        finally:
            self._restoring_project = False

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
        if self.player.is_playing:
            self.player.play_timer.start(max(10, int(round(1000.0 / fps))))
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
        self.player.pause_playback()
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
        self.canvas_2d.solved_point_picked.connect(self.scene_setup.on_solved_point_picked)
        self.slider_2d_frame.valueChanged.connect(lambda _v: self.scene_setup.update_scene_overlay())
        # Both Timeline start boxes describe the same thing, so either one
        # moving carries the other with it before the project is written.
        for w in (self.spin_start_frame_2d, self.spin_start_frame_3d):
            w.valueChanged.connect(self._on_timeline_start_changed)
        # Roto, points and the in/out range are the work itself. Everything
        # that edits them now goes through the undo stack, so its index moving
        # is the one signal that means "this shot changed" - in either
        # direction, since an undo has to be written to disk too.
        self.canvas_2d.masks_changed.connect(p)
        self.canvas_2d.range_changed.connect(p)
        self.canvas_2d.corrections_changed.connect(p)

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
            # Whether COLMAP's own stream is shown is a way of working, not a
            # property of a shot, so it belongs here rather than in a project.
            st.setValue("logs/show_engine_output",
                        bool(self.chk_show_engine_output.isChecked()))
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

            # Restored quietly: announcing the setting in the console on every
            # launch would be the first noise in a panel that is now quiet.
            self.chk_show_engine_output.blockSignals(True)
            self.chk_show_engine_output.setChecked(val("logs/show_engine_output", False, bool))
            self.chk_show_engine_output.blockSignals(False)

            # Everything else the solver tabs hold is per shot and comes from
            # 04 SCENES/<shot>/project.json when the clip is selected.
        except Exception as e:
            log.warning("loading settings failed: %s", e)


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
