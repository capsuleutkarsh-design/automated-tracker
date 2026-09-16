"""
3D Camera Tracker Tab UI Builder
Modern High-End VFX Studio Layout (COLMAP / GLOMAP Photogrammetry)
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton, QTextEdit,
    QProgressBar, QGroupBox, QTableWidget, QHeaderView, QSplitter,
    QLineEdit, QFormLayout
)
from PySide6.QtCore import Qt


def build_3d_tab(win, tab, presets):
    """
    Constructs the 3D Camera Tracking Tab with modern studio card layout,
    high-contrast parameters, and balanced transport controls.
    """
    tab_layout = QVBoxLayout(tab)
    tab_layout.setContentsMargins(10, 10, 10, 10)
    tab_layout.setSpacing(8)

    splitter = QSplitter(Qt.Horizontal)
    tab_layout.addWidget(splitter, 1)

    # ---------------- Left Panel (Settings & Parameters) ----------------
    left_widget = QWidget()
    left_layout = QVBoxLayout(left_widget)
    left_layout.setContentsMargins(0, 0, 6, 0)
    left_layout.setSpacing(10)

    # Group 1: Presets
    preset_group = QGroupBox("⚙️ Shot Type Preset")
    preset_layout = QVBoxLayout(preset_group)
    preset_layout.setSpacing(8)
    preset_layout.setContentsMargins(10, 12, 10, 10)

    win.preset_combo = QComboBox()
    for name in presets.keys():
        win.preset_combo.addItem(name)
    win.preset_combo.currentTextChanged.connect(win._on_preset_changed)
    preset_layout.addWidget(win.preset_combo)

    win.preset_desc = QLabel()
    win.preset_desc.setWordWrap(True)
    win.preset_desc.setStyleSheet("""
        font-size: 11.5px;
        color: #94a3b8;
        background-color: #0d1017;
        border: 1px solid #1a2234;
        padding: 8px 10px;
        border-radius: 6px;
        line-height: 1.4;
    """)
    preset_layout.addWidget(win.preset_desc)
    left_layout.addWidget(preset_group)

    # Group 2: Fine-Tuning Parameters
    param_group = QGroupBox("🎛️ Parameters Tuning")
    form_lay = QFormLayout(param_group)
    form_lay.setContentsMargins(10, 12, 10, 10)
    form_lay.setSpacing(8)
    form_lay.setLabelAlignment(Qt.AlignLeft)

    win.combo_solver_engine = QComboBox()
    win.combo_solver_engine.addItems([
        "Incremental SfM (Standard / All GPUs)",
        "GLOMAP / Global SfM (10x-30x Faster / RTX 30/40 & A-Series)"
    ])
    form_lay.addRow("Solver Engine:", win.combo_solver_engine)

    win.spin_tri = QDoubleSpinBox()
    win.spin_tri.setRange(1.0, 30.0)
    win.spin_tri.setValue(2.5)
    form_lay.addRow("Min Triangulation Angle (°):", win.spin_tri)

    win.spin_overlap = QSpinBox()
    win.spin_overlap.setRange(5, 100)
    win.spin_overlap.setValue(35)
    form_lay.addRow("Matching Overlap (Frames):", win.spin_overlap)

    win.spin_inliers = QSpinBox()
    win.spin_inliers.setRange(15, 500)
    win.spin_inliers.setValue(40)
    form_lay.addRow("Min Initial Inliers:", win.spin_inliers)

    win.combo_cam = QComboBox()
    win.combo_cam.addItems([
        "SIMPLE_RADIAL (Standard / Recommended)",
        "RADIAL",
        "OPENCV",
        "OPENCV_FISHEYE (Wide-Angle / Action Cam)",
        "PINHOLE (Zero Distortion)",
        "SPHERICAL (360° Panoramic VR / Insta360)",
        "EUCM (Enhanced Unified Model / Drone Fisheye)"
    ])
    form_lay.addRow("Camera Model:", win.combo_cam)

    win.spin_step = QSpinBox()
    win.spin_step.setRange(1, 10)
    win.spin_step.setValue(1)
    form_lay.addRow("Frame Step (1=All, 2=Sub):", win.spin_step)

    win.chk_single_cam = QCheckBox("Single Camera (Fixed Focal Length)")
    win.chk_single_cam.setChecked(True)
    win.chk_ba_refine = QCheckBox("Auto-Refine Lens Distortion (BA)")
    win.chk_ba_refine.setChecked(True)
    win.chk_mesh_gen = QCheckBox("Auto-Generate 3D Environment Mesh (.ply)")
    win.chk_mesh_gen.setChecked(True)
    win.chk_gpu = QCheckBox("Use NVIDIA GPU Acceleration (CUDA)")
    win.chk_gpu.setChecked(True)
    win.chk_caspar_ba = QCheckBox("Enable Caspar GPU Bundle Adjuster (RTX GPUs)")
    win.chk_caspar_ba.setChecked(True)

    form_lay.addRow(win.chk_single_cam)
    form_lay.addRow(win.chk_ba_refine)
    form_lay.addRow(win.chk_mesh_gen)
    form_lay.addRow(win.chk_gpu)
    form_lay.addRow(win.chk_caspar_ba)

    left_layout.addWidget(param_group)

    # Group 3: Blender Executable (Optional)
    blender_group = QGroupBox("🎬 Blender Integration (Optional)")
    b_lay = QHBoxLayout(blender_group)
    b_lay.setContentsMargins(10, 12, 10, 10)
    b_lay.setSpacing(6)
    win.txt_blender_path = QLineEdit()
    win.txt_blender_path.setPlaceholderText("Auto-detects installed Blender for .abc baking...")
    btn_browse_blender = QPushButton("📁 Browse...")
    btn_browse_blender.clicked.connect(win._browse_blender_exe)
    b_lay.addWidget(win.txt_blender_path, 1)
    b_lay.addWidget(btn_browse_blender)
    left_layout.addWidget(blender_group)

    left_layout.addStretch()
    splitter.addWidget(left_widget)

    # ---------------- Right Panel (Video Table & Real-Time Log) ----------------
    right_widget = QWidget()
    right_layout = QVBoxLayout(right_widget)
    right_layout.setContentsMargins(6, 0, 0, 0)
    right_layout.setSpacing(10)

    video_group = QGroupBox("📁 Input Sequences & Media")
    video_layout = QVBoxLayout(video_group)
    video_layout.setContentsMargins(10, 12, 10, 10)
    video_layout.setSpacing(8)

    btn_bar = QHBoxLayout()
    btn_bar.setSpacing(6)
    btn_add = QPushButton("➕ Add Video / Sequence...")
    btn_add.clicked.connect(win._add_videos)
    btn_refresh = QPushButton("🔄 Refresh")
    btn_refresh.clicked.connect(win._refresh_videos)
    btn_open_videos = QPushButton("📂 Open 02 VIDEOS")
    btn_open_videos.clicked.connect(win._open_videos_folder)
    btn_bar.addWidget(btn_add)
    btn_bar.addWidget(btn_refresh)
    btn_bar.addWidget(btn_open_videos)
    btn_bar.addStretch()
    video_layout.addLayout(btn_bar)

    win.table = QTableWidget(0, 3)
    win.table.setHorizontalHeaderLabels(["Video Filename", "Size", "3D Tracking Status"])
    win.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    win.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    win.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
    win.table.itemSelectionChanged.connect(win._on_table_row_selected)
    win.table.itemDoubleClicked.connect(win._on_table_row_double_clicked)
    video_layout.addWidget(win.table)
    right_layout.addWidget(video_group, 2)

    # Log & Actions
    log_group = QGroupBox("📊 3D Solver Real-Time Diagnostics & Exports")
    log_layout = QVBoxLayout(log_group)
    log_layout.setContentsMargins(10, 12, 10, 10)
    log_layout.setSpacing(8)

    win.log_text = QTextEdit()
    win.log_text.setReadOnly(True)

    log_top_bar = QHBoxLayout()
    lbl_engine_log = QLabel("Engine Diagnostics Console")
    lbl_engine_log.setStyleSheet("font-size: 11px; color: #64748b; font-weight: 600;")
    btn_clear_3d_log = QPushButton("🧹 Clear Log")
    btn_clear_3d_log.setFixedHeight(22)
    btn_clear_3d_log.setStyleSheet("""
        font-size: 10.5px;
        padding: 2px 8px;
        background-color: #161b26;
        border: 1px solid #283348;
        color: #94a3b8;
        border-radius: 4px;
    """)
    btn_clear_3d_log.clicked.connect(win.log_text.clear)
    log_top_bar.addWidget(lbl_engine_log)
    log_top_bar.addStretch()
    log_top_bar.addWidget(btn_clear_3d_log)
    log_layout.addLayout(log_top_bar)

    log_layout.addWidget(win.log_text)

    win.progress_bar = QProgressBar()
    win.progress_bar.setValue(0)
    win.progress_bar.setFormat("Ready for 3D Camera Tracking")
    log_layout.addWidget(win.progress_bar)

    # Action Bar (2-Tier Balanced Studio Layout)
    action_box = QVBoxLayout()
    action_box.setSpacing(6)

    # Row 1: Primary Execution & Cancel
    row1 = QHBoxLayout()
    row1.setSpacing(8)

    win.btn_start_3d = QPushButton("▶  START 3D CAMERA TRACKING")
    win.btn_start_3d.setObjectName("primaryBtn")
    win.btn_start_3d.setFixedHeight(38)
    win.btn_start_3d.clicked.connect(win._start_tracking_3d)

    win.btn_stop_3d = QPushButton("⏹ Cancel")
    win.btn_stop_3d.setObjectName("stopBtn")
    win.btn_stop_3d.setFixedHeight(38)
    win.btn_stop_3d.setFixedWidth(110)
    win.btn_stop_3d.setEnabled(False)
    win.btn_stop_3d.clicked.connect(win._stop_tracking_3d)

    row1.addWidget(win.btn_start_3d, 1)
    row1.addWidget(win.btn_stop_3d)
    action_box.addLayout(row1)

    # Row 2: Exports & Outputs
    row2 = QHBoxLayout()
    row2.setSpacing(8)

    win.btn_export_3d_blender = QPushButton("🎬 Export for Blender (.abc)")
    win.btn_export_3d_blender.setObjectName("blenderBtn")
    win.btn_export_3d_blender.setFixedHeight(32)
    win.btn_export_3d_blender.setToolTip("Bakes and reveals camera_track.abc & 1-click script for Blender")
    win.btn_export_3d_blender.clicked.connect(win._export_3d_for_blender)

    win.btn_export_3d_nuke = QPushButton("🎥 Export for Nuke (.abc / .nk)")
    win.btn_export_3d_nuke.setObjectName("nukeBtn")
    win.btn_export_3d_nuke.setFixedHeight(32)
    win.btn_export_3d_nuke.setToolTip("Copies 3D Camera & Point Cloud node script to clipboard for instant Ctrl+V into Nuke")
    win.btn_export_3d_nuke.clicked.connect(win._export_3d_for_nuke)

    win.btn_open_3d_exports = QPushButton("📂 Open Folder")
    win.btn_open_3d_exports.setFixedHeight(32)
    win.btn_open_3d_exports.setFixedWidth(120)
    win.btn_open_3d_exports.clicked.connect(win._open_3d_output_folder)

    row2.addWidget(win.btn_export_3d_blender, 1)
    row2.addWidget(win.btn_export_3d_nuke, 1)
    row2.addWidget(win.btn_open_3d_exports)
    action_box.addLayout(row2)

    log_layout.addLayout(action_box)

    right_layout.addWidget(log_group, 3)
    splitter.addWidget(right_widget)
    splitter.setSizes([340, 780])
