"""
Automated Photogrammetry & 2D AI Motion Tracker — VFX Studio Edition
Modular Main Coordinator & GUI Application Window
"""

import sys
import os
import shutil
import subprocess
from pathlib import Path

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QTabWidget, QMessageBox, QFileDialog, QTableWidgetItem,
        QInputDialog, QColorDialog, QListWidgetItem
    )
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QColor, QImage, QDragEnterEvent, QDropEvent
except ImportError:
    print("[ERROR] PySide6 is not installed. Please run launch_gui.bat or install it via: pip install PySide6")
    sys.exit(1)

# Ensure script dir is in sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Base Path Resolution
BASE_DIR = SCRIPT_DIR.parent
COLMAP_DIR = BASE_DIR / "01 COLMAP"
VIDEOS_DIR = BASE_DIR / "02 VIDEOS"
FFMPEG_DIR = BASE_DIR / "03 FFMPEG"
SCENES_DIR = BASE_DIR / "04 SCENES"
COTRACKER_DIR = BASE_DIR / "06 COTRACKER"

# Executable Resolution
if (COLMAP_DIR / "colmap.exe").exists():
    COLMAP_EXE = COLMAP_DIR / "colmap.exe"
elif (COLMAP_DIR / "bin" / "colmap.exe").exists():
    COLMAP_EXE = COLMAP_DIR / "bin" / "colmap.exe"
else:
    COLMAP_EXE = COLMAP_DIR / "bin" / "colmap.exe"

if (FFMPEG_DIR / "ffmpeg.exe").exists():
    FFMPEG_EXE = FFMPEG_DIR / "ffmpeg.exe"
elif (FFMPEG_DIR / "bin" / "ffmpeg.exe").exists():
    FFMPEG_EXE = FFMPEG_DIR / "bin" / "ffmpeg.exe"
else:
    FFMPEG_EXE = FFMPEG_DIR / "bin" / "ffmpeg.exe"

COLMAP_BAT = COLMAP_DIR / "COLMAP.bat"

# Import Modular GUI & Core Components
from gui.theme import DARK_STUDIO_QSS
from gui.tab_3d import build_3d_tab
from gui.tab_2d import build_2d_tab
from core.tracking_layer import TrackingLayer
from core.workers import TrackerWorker, CoTrackerWorker, FrameExtractorWorker
from core.hardware import gpu_monitor

PRESETS = {
    "Handheld / Walking (Recommended)": {
        "description": "Optimized for moving camera shots (walking, crane, handheld). Uses low initial triangulation angle to lock onto video frames instantly.",
        "solver_engine": "Incremental",
        "tri_angle": 2.5,
        "overlap": 35,
        "inliers": 40,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "GLOMAP High-Speed (RTX 30/40 & A-Series)": {
        "description": "Ultra-fast global Structure-from-Motion (10x-30x speedup). Solves all camera positions and rotations simultaneously with zero continuous drift.",
        "solver_engine": "GLOMAP",
        "tri_angle": 2.5,
        "overlap": 35,
        "inliers": 40,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "360° VR / Panoramic (Insta360 / GoPro Max)": {
        "description": "Native spherical equirectangular camera model for 360 VR cameras and panoramic video stitches.",
        "solver_engine": "Incremental",
        "tri_angle": 3.0,
        "overlap": 25,
        "inliers": 50,
        "camera_model": "SPHERICAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Drone / Aerial Orbit": {
        "description": "Optimized for outdoor and high-altitude shots with wide parallax and high keypoint count.",
        "solver_engine": "Incremental",
        "tri_angle": 12.0,
        "overlap": 20,
        "inliers": 100,
        "camera_model": "OPENCV",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Slow / Subtle Motion (Small Movement)": {
        "description": "Very forgiving on small camera movements. Subsamples frames to increase baseline and lowers initialization angle.",
        "solver_engine": "Incremental",
        "tri_angle": 3.0,
        "overlap": 15,
        "inliers": 40,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 2
    },
    "Fast Action / Quick Turns": {
        "description": "Increases matching overlap window (30 frames) to maintain tracking during rapid camera motion.",
        "solver_engine": "Incremental",
        "tri_angle": 8.0,
        "overlap": 30,
        "inliers": 50,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Action Cam / GoPro / Fisheye": {
        "description": "Uses Fisheye distortion model for wide-angle and action camera lenses.",
        "solver_engine": "Incremental",
        "tri_angle": 6.0,
        "overlap": 20,
        "inliers": 60,
        "camera_model": "OPENCV_FISHEYE",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Standard / Default": {
        "description": "Default COLMAP settings.",
        "solver_engine": "Incremental",
        "tri_angle": 16.0,
        "overlap": 15,
        "inliers": 100,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    },
    "Custom (Manual Tuning)": {
        "description": "Unlock all parameters for full manual control.",
        "solver_engine": "Incremental",
        "tri_angle": 6.0,
        "overlap": 20,
        "inliers": 60,
        "camera_model": "SIMPLE_RADIAL",
        "single_camera": True,
        "use_gpu": True,
        "max_image_size": 4096,
        "frame_step": 1
    }
}


class TrackerMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Automated_Tracker_V001.1 — VFX Studio (3D SfM & 2D AI Motion Tracker)")
        self.resize(1280, 850)
        self.setMinimumSize(1000, 680)
        self.setAcceptDrops(True)
        self.worker_3d = None
        self.worker_2d = None

        # Video Player State for 2D Tab
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

        # Deferred init for fast UI display
        QTimer.singleShot(20, self._deferred_init)

    def _deferred_init(self):
        try:
            from export_tools import find_blender_executable
            detected_b = find_blender_executable()
            if detected_b:
                self.txt_blender_path.setText(str(detected_b))
        except Exception:
            pass

        self._refresh_videos()
        self._update_hardware_monitor()

    def _setup_style(self):
        self.setStyleSheet(DARK_STUDIO_QSS)

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 10, 12, 10)
        main_layout.setSpacing(8)

        # ---------------- Modern Studio Header Bar ----------------
        header = QHBoxLayout()
        header.setSpacing(12)
        header.setContentsMargins(2, 2, 2, 4)

        title_box = QVBoxLayout()
        title_box.setSpacing(3)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)

        title = QLabel("🎬 AUTOMATED TRACKER")
        title.setStyleSheet("""
            font-size: 16px;
            font-weight: 800;
            color: #ffffff;
            letter-spacing: 0.8px;
        """)

        ver_tag = QLabel("V001.1")
        ver_tag.setStyleSheet("""
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #2563eb);
            color: #ffffff;
            font-size: 10px;
            font-weight: 800;
            padding: 2px 7px;
            border-radius: 5px;
            border: 1px solid #38bdf8;
            letter-spacing: 0.5px;
        """)

        title_row.addWidget(title)
        title_row.addWidget(ver_tag)
        title_row.addStretch()

        subtitle = QLabel("COLMAP / GLOMAP 3D Camera Solver  •  Meta CoTracker3 2D AI Tracker  •  Blender & Nuke Pipeline")
        subtitle.setStyleSheet("font-size: 11px; color: #64748b; font-weight: 500;")

        title_box.addLayout(title_row)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)

        # Hardware Badge Pills Container
        hw_box = QHBoxLayout()
        hw_box.setSpacing(6)

        self.badge_gpu = QLabel("⚡ GPU: Checking...")
        self.badge_gpu.setStyleSheet("""
            background-color: #111624;
            border: 1px solid #1f2a3f;
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 600;
            color: #38bdf8;
        """)

        self.badge_blender = QLabel("🎬 Blender: Auto")
        self.badge_blender.setStyleSheet("""
            background-color: #1a140d;
            border: 1px solid #3d2817;
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 600;
            color: #fb923c;
        """)

        self.badge_colmap = QLabel("🌐 COLMAP: Ready")
        self.badge_colmap.setStyleSheet("""
            background-color: #111724;
            border: 1px solid #1f293d;
            border-radius: 6px;
            padding: 4px 10px;
            font-size: 11px;
            font-weight: 600;
            color: #38bdf8;
        """)

        hw_box.addWidget(self.badge_gpu)
        hw_box.addWidget(self.badge_blender)
        hw_box.addWidget(self.badge_colmap)
        header.addLayout(hw_box)
        main_layout.addLayout(header)

        # Main Tab Widget
        self.tabs = QTabWidget()
        self.tab_3d = QWidget()
        self.tab_2d = QWidget()

        build_3d_tab(self, self.tab_3d, PRESETS)
        build_2d_tab(self, self.tab_2d)

        self.tabs.addTab(self.tab_3d, "🎥 3D Camera Tracking (COLMAP)")
        self.tabs.addTab(self.tab_2d, "🎯 2D AI Point Tracking (CoTracker3)")
        main_layout.addWidget(self.tabs, 1)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if Path(url.toLocalFile()).suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".m4v"}:
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                filepath = url.toLocalFile()
                if Path(filepath).suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".m4v"}:
                    event.acceptProposedAction()
                    self._import_dropped_video(filepath)
                    return

    def _import_dropped_video(self, filepath):
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        src = Path(filepath)
        dest = VIDEOS_DIR / src.name
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
        self._refresh_videos()
        idx = self.combo_2d_video.findText(src.name)
        if idx >= 0:
            self.combo_2d_video.setCurrentIndex(idx)
        self._append_log_2d(f"✔ Dropped video imported: {src.name}", "#00ff88")

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

                if load >= 40:
                    bg_col = "#0f2319"
                    border_col = "#10b981"
                    text_col = "#34d399"
                    icon = "⚡"
                else:
                    bg_col = "#111624"
                    border_col = "#1f2a3f"
                    text_col = "#38bdf8"
                    icon = "⚡"

                self.badge_gpu.setText(f"{icon} {name}  •  {load}% GPU  •  {used_gb:.1f}/{total_gb:.1f} GB")
                self.badge_gpu.setStyleSheet(f"""
                    background-color: {bg_col};
                    border: 1px solid {border_col};
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-size: 11px;
                    font-weight: 600;
                    color: {text_col};
                """)
            else:
                self.badge_gpu.setText("🖥️ CPU Mode")
                self.badge_gpu.setStyleSheet("""
                    background-color: #171a24;
                    border: 1px solid #283042;
                    border-radius: 6px;
                    padding: 4px 10px;
                    font-size: 11px;
                    font-weight: 600;
                    color: #94a3b8;
                """)
        except Exception:
            self.badge_gpu.setText("⚡ GPU: Ready")

        b_path = self.txt_blender_path.text().strip() if hasattr(self, 'txt_blender_path') else None
        if b_path:
            self.badge_blender.setText("🎬 Blender: Linked")
            self.badge_blender.setStyleSheet("""
                background-color: #0f2319;
                border: 1px solid #10b981;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
                color: #34d399;
            """)
        else:
            self.badge_blender.setText("🎬 Blender: Auto")
            self.badge_blender.setStyleSheet("""
                background-color: #1a140d;
                border: 1px solid #3d2817;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
                color: #fb923c;
            """)

        if COLMAP_EXE.exists():
            self.badge_colmap.setText("🌐 COLMAP: Ready")
            self.badge_colmap.setStyleSheet("""
                background-color: #111724;
                border: 1px solid #1f293d;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
                color: #38bdf8;
            """)
        else:
            self.badge_colmap.setText("🌐 COLMAP: Missing")
            self.badge_colmap.setStyleSheet("""
                background-color: #261114;
                border: 1px solid #881337;
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
                color: #f43f5e;
            """)

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
            self._append_log_3d(f"✔ Set Blender executable path to: {fpath}", "#00ff88")

    def _add_videos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Video or Image Sequence Files", "", "Video & Image Files (*.mp4 *.mov *.avi *.mkv *.m4v *.exr *.png *.jpg *.jpeg *.tif *.tiff);;All Files (*.*)"
        )
        if files:
            VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
            for f in files:
                dest = VIDEOS_DIR / Path(f).name
                if not dest.exists():
                    shutil.copy2(f, dest)
            self._refresh_videos()

    def _refresh_videos(self):
        VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
        self.table.setRowCount(0)
        self.combo_2d_video.clear()

        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
        seq_exts = {".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
        
        # Files and Sequence folders
        items = []
        for f in VIDEOS_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in video_exts:
                items.append(f)
            elif f.is_dir():
                # Folder containing image sequence
                sub_imgs = [s for s in f.iterdir() if s.is_file() and s.suffix.lower() in seq_exts]
                if sub_imgs:
                    items.append(f)

        for v in sorted(items, key=lambda x: x.name):
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
            is_3d_completed = (
                (scene_dir / "3D_CAMERA_TRACK" / "_latest" / "sparse" / "cameras.txt").exists() or
                (scene_dir / "sparse" / "cameras.txt").exists() or
                (any((scene_dir / "3D_CAMERA_TRACK").glob("*/sparse/cameras.txt")) if (scene_dir / "3D_CAMERA_TRACK").exists() else False)
            )
            if is_3d_completed:
                status_item = QTableWidgetItem("● Solved (3D Ready)")
                status_item.setForeground(QColor("#00ff88"))
                status_item.setTextAlignment(Qt.AlignCenter)
            else:
                status_item = QTableWidgetItem("○ Ready to Track")
                status_item.setForeground(QColor("#00d2ff"))
                status_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, status_item)

            self.combo_2d_video.addItem(v.name)

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
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
        seq_exts = {".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
        videos = []
        for f in VIDEOS_DIR.iterdir():
            if f.is_file() and f.suffix.lower() in video_exts:
                videos.append(f)
            elif f.is_dir():
                sub_imgs = [s for s in f.iterdir() if s.is_file() and s.suffix.lower() in seq_exts]
                if sub_imgs:
                    videos.append(f)

        if not videos:
            QMessageBox.warning(self, "No Videos Found", f"Please add at least one video or image sequence into:\n{VIDEOS_DIR}")
            return

        all_masks = []
        for l in self.canvas_2d.layers:
            for m in l.animated_masks:
                all_masks.append(m)

        cam_raw = self.combo_cam.currentText().split()[0].strip()
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
            "frame_step": self.spin_step.value(),
            "blender_path": self.txt_blender_path.text().strip() or None,
            "animated_masks": all_masks if all_masks else None
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
            self._append_log_3d("⏹ Stopping 3D tracking process...", "#ff4b4b")
            self.worker_3d.cancel()
            self.btn_stop_3d.setEnabled(False)

    def _on_worker_3d_finished(self, success, message):
        self.btn_start_3d.setEnabled(True)
        self.btn_stop_3d.setEnabled(False)
        self._append_log_3d(f"\n{message}", "#00ff88" if success else "#ff4b4b")
        self._refresh_videos()

    def _append_log_3d(self, text, color="#c0c0d0"):
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
        nk_file = None
        target_scene = shot_dir / "3D_CAMERA_TRACK" / "_latest"

        if (target_scene / "camera_track_nuke.nk").exists():
            nk_file = target_scene / "camera_track_nuke.nk"
        else:
            track_root = shot_dir / "3D_CAMERA_TRACK"
            if track_root.exists():
                for sub in sorted(track_root.iterdir(), reverse=True):
                    if sub.is_dir() and sub.name != "_latest" and (sub / "camera_track_nuke.nk").exists():
                        nk_file = sub / "camera_track_nuke.nk"
                        target_scene = sub
                        break
            if not nk_file and (shot_dir / "camera_track_nuke.nk").exists():
                nk_file = shot_dir / "camera_track_nuke.nk"
                target_scene = shot_dir

        if nk_file and nk_file.exists():
            with open(nk_file, "r", encoding="utf-8") as f:
                content = f.read()
            QApplication.clipboard().setText(content)
            self._flash_button_feedback(self.btn_export_3d_nuke, "🎥 Export for Nuke (.abc / .nk)", "✔ Copied to Clipboard!")
            self._append_log_3d(f"📋 Copied Nuke 3D Camera node ({nk_file.name}) to clipboard! Press Ctrl+V inside Nuke.", "#ffd000")
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
        target_scene = shot_dir / "3D_CAMERA_TRACK" / "_latest"
        if not (target_scene / "import_to_blender.py").exists() and not (target_scene / "camera_track.abc").exists():
            target_scene = shot_dir

        if not (target_scene / "import_to_blender.py").exists() and not (target_scene / "camera_track.abc").exists():
            QMessageBox.warning(self, "3D Track Not Found", f"No 3D camera track found for '{v_name}'. Run 3D Camera Tracking first.")
            return

        blender_path = self.txt_blender_path.text().strip() or None
        self._append_log_3d(f"▶ Exporting 3D Track for Blender...", "#ea7600")

        try:
            from export_tools import auto_export_alembic_via_blender
            auto_export_alembic_via_blender(
                target_scene,
                blender_path=blender_path,
                log_callback=lambda m, c: self._append_log_3d(m, c)
            )
        except Exception as e:
            self._append_log_3d(f"Notice: Alembic export check: {e}", "#e0a000")

        abc_file = target_scene / "camera_track.abc"
        if abc_file.exists():
            self._flash_button_feedback(self.btn_export_3d_blender, "🎬 Export for Blender (.abc)", "✔ Alembic Ready!")
            self._append_log_3d(f"🎉 Blender Alembic (.abc) ready: {abc_file.name}", "#00ff88")
        else:
            self._flash_button_feedback(self.btn_export_3d_blender, "🎬 Export for Blender (.abc)", "✔ Script Ready!")
            self._append_log_3d(f"✔ Blender 1-Click Script ready: import_to_blender.py", "#00ff88")

        os.startfile(target_scene)

    def _flash_button_feedback(self, btn, orig_text, success_text="✔ Copied to Clipboard!", duration_ms=1800):
        btn.setText(success_text)
        prev_style = btn.styleSheet()
        btn.setStyleSheet("background-color: #0c2b1a; border: 1.5px solid #00ff88; color: #00ff88; font-weight: bold;")
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
        self.lbl_total_pts.setText(f"({cur_l.grid_size ** 2} points)")
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
            self._append_log_2d(f"➕ Added new tracking layer: [{name.strip()}]", "#00ff88")

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
        self._append_log_2d(f"🗑 Deleted layer [{del_name}].", "#ff3355")

    def _rename_active_layer(self):
        cur_l = self.canvas_2d.active_layer
        if not cur_l:
            return
        name, ok = QInputDialog.getText(self, "Rename Layer", "New Name:", text=cur_l.name)
        if ok and name.strip():
            cur_l.name = name.strip()
            self._refresh_layer_list()
            self._append_log_2d(f"✏️ Renamed layer to [{cur_l.name}].", "#00d2ff")

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

    def _on_grid_size_changed(self, val):
        self.lbl_total_pts.setText(f"({val*val} points)")
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
        self._append_log_2d(f"✔ [{l_name}] Added point #{count} at ({x:.1f}, {y:.1f}) on frame {frame_idx+1}", "#00d2ff")
        self._refresh_layer_list()

    def _jump_to_point_keyframe(self):
        if self.canvas_2d.points:
            target_f = int(self.canvas_2d.points[0][0])
            self.slider_2d_frame.setValue(target_f)
            self._append_log_2d(f"⏮ Jumped timeline to point keyframe {target_f+1}.", "#00d2ff")
        else:
            self._append_log_2d("No manual points placed on active layer yet.", "#a0a0b0")

    def _on_view_layer_changed(self, idx):
        if idx == 0:
            self.canvas_2d.is_overlay_active = False
            self.overlay_frames = None
            cur = self.slider_2d_frame.value()
            self._on_2d_frame_slider_changed(cur)
            self._append_log_2d("👁️ Switched view to Clean Raw Video.", "#00d2ff")
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
        self._append_log_2d(f"🗑 Cleared tracking points on [{l_name}].", "#00ff88")

    def _clear_active_layer_masks(self):
        self.canvas_2d.clear_active_layer_masks()
        self._refresh_layer_list()
        cur = self.slider_2d_frame.value()
        self._on_2d_frame_slider_changed(cur)
        l_name = self.canvas_2d.active_layer.name if self.canvas_2d.active_layer else "Active Layer"
        self._append_log_2d(f"🗑 Cleared inclusion && exclusion masks on [{l_name}].", "#00ff88")

    def _on_2d_video_selected(self, video_name):
        if not video_name:
            return
        video_path = VIDEOS_DIR / video_name
        if not video_path.exists():
            return

        self._pause_playback()
        self.overlay_frames = None
        self.loaded_video_frames = None

        scene_images_dir = SCENES_DIR / video_path.stem / "images"
        if scene_images_dir.exists() and list(scene_images_dir.glob("*.jpg")):
            jpgs = sorted(list(scene_images_dir.glob("*.jpg")))
            self.slider_2d_frame.setRange(0, len(jpgs) - 1)
            self.slider_2d_frame.setValue(0)
            self._load_frame_preview(jpgs[0], 0, len(jpgs))
            return

        import tempfile
        tmp_img = Path(tempfile.gettempdir()) / f"thumb_{video_path.stem}_0.jpg"
        total_frames = 100
        fps = 24.0
        try:
            import imageio.v3 as iio
            meta = iio.immeta(str(video_path), plugin="FFMPEG")
            fps = float(meta.get("fps", 24.0)) or 24.0
            dur = float(meta.get("duration", 0.0))
            if dur > 0:
                total_frames = max(1, int(dur * fps))
        except Exception:
            pass

        if not tmp_img.exists():
            cmd = [str(FFMPEG_EXE), "-y", "-loglevel", "error", "-ss", "0.0", "-i", str(video_path), "-vframes", "1", "-q:v", "2", str(tmp_img)]
            try:
                subprocess.run(cmd)
            except Exception:
                pass

        if tmp_img.exists():
            self.slider_2d_frame.setRange(0, total_frames - 1)
            self.slider_2d_frame.setValue(0)
            self._load_frame_preview(tmp_img, 0, total_frames)
        else:
            self.slider_2d_frame.setRange(0, 0)
            self.slider_2d_frame.setValue(0)

        self._append_log_2d(f"⏳ Unpacking frame sequence for '{video_path.name}' in background for smooth 60 FPS scrubbing...", "#00d2ff")
        self.frame_extractor = FrameExtractorWorker(video_path, scene_images_dir, FFMPEG_EXE)
        self.frame_extractor.finished_signal.connect(self._on_frames_extracted)
        self.frame_extractor.start()

    def _on_frames_extracted(self, shot_name, count):
        if count > 0:
            self._append_log_2d(f"✔ Extracted {count} frames for '{shot_name}' into 04 SCENES/{shot_name}/images/! Scrubbing is now instant.", "#00ff88")
            v_name = self.combo_2d_video.currentText()
            if Path(v_name).stem == shot_name:
                scene_images_dir = SCENES_DIR / shot_name / "images"
                jpgs = sorted(list(scene_images_dir.glob("*.jpg")))
                if jpgs:
                    cur_val = min(self.slider_2d_frame.value(), len(jpgs) - 1)
                    self.slider_2d_frame.setRange(0, len(jpgs) - 1)
                    self.slider_2d_frame.setValue(cur_val)
                    self._load_frame_preview(jpgs[cur_val], cur_val, len(jpgs))
        self._update_keyframe_status()

    def _extract_frames_for_current_video(self):
        v_name = self.combo_2d_video.currentText()
        if not v_name:
            return
        video_path = VIDEOS_DIR / v_name
        scene_images_dir = SCENES_DIR / video_path.stem / "images"
        self._append_log_2d(f"⏳ Extracting frame sequence for '{video_path.name}'...", "#00d2ff")
        self.frame_extractor = FrameExtractorWorker(video_path, scene_images_dir, FFMPEG_EXE)
        self.frame_extractor.finished_signal.connect(self._on_frames_extracted)
        self.frame_extractor.start()

    def _load_frame_preview(self, img_path_or_array, frame_idx, total_frames):
        from PIL import Image
        try:
            if isinstance(img_path_or_array, (str, Path)):
                im = Image.open(img_path_or_array).convert("RGB")
            else:
                im = Image.fromarray(img_path_or_array).convert("RGB")
            w, h = im.size
            qim = QImage(im.tobytes(), w, h, w * 3, QImage.Format_RGB888)
            self.canvas_2d.set_frame_image(qim, frame_idx, total_frames, w, h, fps=24.0)

            total_sec = frame_idx / 24.0
            hrs = int(total_sec // 3600)
            mins = int((total_sec % 3600) // 60)
            secs = int(total_sec % 60)
            fr = int(frame_idx % 24)
            self.lbl_frame_idx.setText(f"{hrs:02d}:{mins:02d}:{secs:02d}:{fr:02d} ({frame_idx+1}/{total_frames})")
        except Exception:
            pass

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
        scene_images_dir = SCENES_DIR / video_path.stem / "images"
        if scene_images_dir.exists():
            jpgs = sorted(list(scene_images_dir.glob("*.jpg")))
            if 0 <= val < len(jpgs):
                self._load_frame_preview(jpgs[val], val, len(jpgs))
                self._update_keyframe_status()
                return

        import tempfile
        sec = val / 24.0
        tmp_img = Path(tempfile.gettempdir()) / f"thumb_{video_path.stem}_{val}.jpg"
        if not tmp_img.exists():
            cmd = [
                str(FFMPEG_EXE), "-y", "-loglevel", "error",
                "-ss", f"{sec:.3f}",
                "-noaccurate_seek",
                "-i", str(video_path),
                "-vframes", "1",
                "-q:v", "3",
                "-threads", "4",
                str(tmp_img)
            ]
            try:
                subprocess.run(cmd)
            except Exception:
                pass
        if tmp_img.exists():
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
        self._append_log_2d(f"🔷 Keyframe created/updated at Frame {cur_f+1} on [{layer.name}].", "#00ff88")

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
            self._append_log_2d(f"🗑 Deleted keyframe at Frame {cur_f+1}.", "#ffaa00")

    def _update_keyframe_status(self):
        cur_f = self.slider_2d_frame.value()
        layer = self.canvas_2d.active_layer
        all_keys = layer.get_all_keyframe_frames() if layer else []
        is_kf = cur_f in all_keys
        if is_kf:
            self.lbl_key_status.setText(f"◆ f{cur_f+1}")
            self.lbl_key_status.setStyleSheet("color: #00ff88; font-weight: bold; font-size: 10px; padding: 1px 6px; background-color: #11261d; border: 1px solid #00ff88; border-radius: 3px;")
        elif all_keys:
            self.lbl_key_status.setText(f"~ f{cur_f+1}")
            self.lbl_key_status.setStyleSheet("color: #38bdf8; font-weight: normal; font-size: 10px; padding: 1px 6px; background-color: #101a26; border: 1px dashed #0284c7; border-radius: 3px;")
        else:
            self.lbl_key_status.setText("No Masks")
            self.lbl_key_status.setStyleSheet("color: #64748b; font-size: 10px; padding: 1px 6px; background-color: #10141d; border-radius: 3px;")

    def _toggle_playback(self):
        if self.is_playing:
            self._pause_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        self.is_playing = True
        self.btn_play_pause.setText("⏸ Pause")
        self.play_timer.start(40)

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
        self.lbl_range_status.setText(f"Range: [{self.canvas_2d.in_point+1} - {out_p+1}]")
        self._append_log_2d(f"📍 Set Tracking In-Point to Frame {self.canvas_2d.in_point+1}.", "#00d2ff")

    def _set_out_point(self, frame_idx):
        self.canvas_2d.out_point = int(frame_idx)
        in_p = self.canvas_2d.in_point
        self.lbl_range_status.setText(f"Range: [{in_p+1} - {self.canvas_2d.out_point+1}]")
        self._append_log_2d(f"📍 Set Tracking Out-Point to Frame {self.canvas_2d.out_point+1}.", "#00d2ff")

    def _reset_tracking_range(self):
        self.canvas_2d.in_point = 0
        self.canvas_2d.out_point = -1
        self.lbl_range_status.setText("Range: Full")
        self._append_log_2d("↺ Reset Tracking Range to full sequence.", "#00d2ff")

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
        success = export_to_nuke_roto_script(layer.animated_masks, w, h, tot_f, out_file)
        if success:
            nk_text = out_file.read_text(encoding="utf-8")
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(nk_text)
            self._append_log_2d(f"✔ Exported Nuke Roto node to: {out_file.name}", "#00ff88")
            self._append_log_2d("📋 Copied Roto node to Clipboard! Press Ctrl+V directly in Nuke Node Graph.", "#00d2ff")
            QMessageBox.information(self, "Nuke Roto Exported", f"Successfully exported Nuke Roto node!\n\nSaved to: {out_file}\n\nCopied to Clipboard! You can paste (Ctrl+V) directly into Nuke's Node Graph.")

    def _find_latest_overlay(self, shot_name):
        shot_dir = SCENES_DIR / shot_name
        if (shot_dir / "2D_POINT_TRACK" / "_latest" / "tracks_2d_overlay.mp4").exists():
            return shot_dir / "2D_POINT_TRACK" / "_latest" / "tracks_2d_overlay.mp4"
        point_track_dir = shot_dir / "2D_POINT_TRACK"
        if point_track_dir.exists():
            ts_dirs = sorted([d for d in point_track_dir.iterdir() if d.is_dir() and d.name != "_latest"], reverse=True)
            for d in ts_dirs:
                ov = d / "tracks_2d_overlay.mp4"
                if ov.exists():
                    return ov
        if (shot_dir / "cotracker_2d" / "tracks_2d_overlay.mp4").exists():
            return shot_dir / "cotracker_2d" / "tracks_2d_overlay.mp4"
        return None

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

        self._append_log_2d("Loading motion overlay video into built-in player...", "#00d2ff")
        try:
            import imageio.v3 as iio
            self.overlay_frames = iio.imread(str(overlay_file), plugin="FFMPEG")
            self.slider_2d_frame.setRange(0, len(self.overlay_frames) - 1)
            self.slider_2d_frame.setValue(0)
            self._load_frame_preview(self.overlay_frames[0], 0, len(self.overlay_frames))
            self._start_playback()
            self._append_log_2d(f"✔ Loaded {len(self.overlay_frames)} overlay frames into player.", "#00ff88")
        except Exception as e:
            self._append_log_2d(f"Notice: Loading overlay failed: {e}", "#e0a000")

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

        nk_file = None
        target_scene = shot_dir / "2D_POINT_TRACK" / "_latest"
        if (target_scene / "tracks_2d_cornerpin_nuke.nk").exists():
            nk_file = target_scene / "tracks_2d_cornerpin_nuke.nk"
        elif (target_scene / "tracks_2d_nuke.nk").exists():
            nk_file = target_scene / "tracks_2d_nuke.nk"

        if not nk_file:
            pt_root = shot_dir / "2D_POINT_TRACK"
            if pt_root.exists():
                for sub in sorted(pt_root.iterdir(), reverse=True):
                    if sub.is_dir() and sub.name != "_latest":
                        if (sub / "tracks_2d_cornerpin_nuke.nk").exists():
                            nk_file = sub / "tracks_2d_cornerpin_nuke.nk"
                            target_scene = sub
                            break
                        elif (sub / "tracks_2d_nuke.nk").exists():
                            nk_file = sub / "tracks_2d_nuke.nk"
                            target_scene = sub
                            break

        if not nk_file and (shot_dir / "cotracker_2d" / "tracks_2d_nuke.nk").exists():
            nk_file = shot_dir / "cotracker_2d" / "tracks_2d_nuke.nk"
            target_scene = shot_dir / "cotracker_2d"

        if nk_file and nk_file.exists():
            with open(nk_file, "r", encoding="utf-8") as f:
                content = f.read()
            QApplication.clipboard().setText(content)
            self._flash_button_feedback(self.btn_export_2d_nuke, "📋 Export for Nuke (Tracker Node)", "✔ Copied to Clipboard!")
            self._append_log_2d(f"📋 Copied Nuke 2D Tracker node ({nk_file.name}) to clipboard! Press Ctrl+V inside Nuke.", "#00ff88")
            os.startfile(target_scene)
        else:
            QMessageBox.information(
                self, "No Nuke File Found",
                f"No Nuke 2D Tracker node found for '{shot_name}'.\n\nRun 2D Point Tracking first."
            )

    def _start_tracking_2d(self):
        v_name = self.combo_2d_video.currentText()
        if not v_name:
            QMessageBox.warning(self, "No Video Selected", "Please select a video from the dropdown.")
            return

        video_path = VIDEOS_DIR / v_name
        res_text = self.combo_2d_res.currentText()
        max_dim = 720
        if "512p" in res_text:
            max_dim = 512
        elif "1080p" in res_text:
            max_dim = 1080
        elif "Original" in res_text:
            max_dim = 0

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
            layers_config.append(l.to_config_dict())

        config = {
            "layers": layers_config,
            "max_dimension": max_dim,
            "offline": offline,
            "fps": 24.0,
            "in_point": self.canvas_2d.in_point,
            "out_point": self.canvas_2d.out_point
        }

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
        if self.worker_2d and self.worker_2d.isRunning():
            self._append_log_2d("⏹ Stopping 2D tracking process...", "#ff4b4b")
            self.worker_2d.cancel()
            self.btn_stop_2d.setEnabled(False)

    def _append_log_2d(self, text, color="#c0c0d0"):
        self.log_2d_text.append(f'<span style="color: {color};">{text}</span>')
        sb = self.log_2d_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _update_progress_2d(self, val, stage_text):
        self.progress_2d.setValue(val)
        self.progress_2d.setFormat(f"{val}% — {stage_text}")

    def _on_worker_2d_finished(self, success, message):
        self.btn_start_2d.setEnabled(True)
        self.btn_stop_2d.setEnabled(False)
        self._append_log_2d(f"\n{message}", "#00ff88" if success else "#ff4b4b")
        if success:
            self._load_overlay_into_player()

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
                    stat_item.setForeground(QColor("#00ff88"))
                elif "✖" in status or "Failed" in status or "Error" in status:
                    stat_item.setText("✕ " + status.replace("✖", "").strip())
                    stat_item.setForeground(QColor("#ff4b4b"))
                elif "Processing" in status or "Extracting" in status or "Matching" in status or "Tracking" in status:
                    stat_item.setText("◌ " + status)
                    stat_item.setForeground(QColor("#ffd700"))
                else:
                    stat_item.setText(status)
                    stat_item.setForeground(QColor("#00d2ff"))


def main():
    app = QApplication(sys.argv)
    window = TrackerMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
