"""
2D AI Point Tracker (CoTracker3) Tab UI Builder
Modern High-End VFX Studio Layout (Mocha-Style Layers & AI Tracker)
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton, QTextEdit,
    QProgressBar, QGroupBox, QRadioButton, QSlider, QFormLayout,
    QListWidget, QSplitter
)
from PySide6.QtCore import Qt

from gui.canvas import VideoPointPickerCanvas


def build_2d_tab(win, tab):
    """
    Constructs the 2D AI Point Tracking Tab with modern studio transport controls,
    Mocha-style roto timeline, and balanced card layout.
    """
    tab_layout = QVBoxLayout(tab)
    tab_layout.setContentsMargins(10, 10, 10, 10)
    tab_layout.setSpacing(8)

    splitter = QSplitter(Qt.Horizontal)
    tab_layout.addWidget(splitter, 1)

    # ---------------- Left Panel (2D Controls) ----------------
    left_widget = QWidget()
    left_layout = QVBoxLayout(left_widget)
    left_layout.setContentsMargins(0, 0, 6, 0)
    left_layout.setSpacing(10)

    # Group 1: Video Selector for 2D
    vid_group = QGroupBox("🎯 Target Media Sequence")
    vid_layout = QVBoxLayout(vid_group)
    vid_layout.setContentsMargins(10, 12, 10, 10)
    vid_layout.setSpacing(8)

    win.combo_2d_video = QComboBox()
    win.combo_2d_video.currentTextChanged.connect(win._on_2d_video_selected)
    vid_layout.addWidget(win.combo_2d_video)

    win.btn_extract_frames = QPushButton("🎬 Unpack Frame Cache (Instant Scrubbing)")
    win.btn_extract_frames.setFixedHeight(28)
    win.btn_extract_frames.setStyleSheet("""
        QPushButton {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1a273b, stop:1 #131c2c);
            border: 1px solid #1e3a5f;
            border-top: 1px solid #38bdf8;
            color: #38bdf8;
            font-weight: 700;
            font-size: 11px;
            border-radius: 6px;
        }
        QPushButton:hover {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #253956, stop:1 #1b263b);
            border-color: #7dd3fc;
            color: #ffffff;
        }
    """)
    win.btn_extract_frames.clicked.connect(win._extract_frames_for_current_video)
    vid_layout.addWidget(win.btn_extract_frames)
    left_layout.addWidget(vid_group)

    # Group 2: Mocha-Style Tracking Layers & Tools
    mode_group = QGroupBox("🎭 Tracking Layers & Mask Tools (Mocha-Style)")
    mode_layout = QVBoxLayout(mode_group)
    mode_layout.setContentsMargins(10, 12, 10, 10)
    mode_layout.setSpacing(8)

    # Layer List Widget
    win.layer_list = QListWidget()
    win.layer_list.setFixedHeight(88)
    win.layer_list.setStyleSheet("""
        QListWidget {
            background-color: #0b0e14;
            border: 1px solid #1a2234;
            border-radius: 7px;
            padding: 3px;
            color: #cbd5e1;
            font-size: 11.5px;
        }
        QListWidget::item {
            padding: 4px 8px;
            border-radius: 4px;
            margin-bottom: 2px;
        }
        QListWidget::item:selected {
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #162438, stop:1 #1a2b42);
            border-left: 3px solid #38bdf8;
            color: #ffffff;
            font-weight: 700;
        }
    """)
    win.layer_list.currentRowChanged.connect(win._on_layer_selected)
    mode_layout.addWidget(win.layer_list)

    # Layer Action Buttons
    layer_btn_row = QHBoxLayout()
    layer_btn_row.setSpacing(4)
    win.btn_add_layer = QPushButton("➕ Add")
    win.btn_add_layer.setFixedHeight(24)
    win.btn_add_layer.setStyleSheet("font-size: 10.5px; padding: 2px 5px;")
    win.btn_add_layer.clicked.connect(win._add_new_layer)

    win.btn_del_layer = QPushButton("🗑 Del")
    win.btn_del_layer.setFixedHeight(24)
    win.btn_del_layer.setStyleSheet("font-size: 10.5px; padding: 2px 5px;")
    win.btn_del_layer.clicked.connect(win._delete_active_layer)

    win.btn_rename_layer = QPushButton("✏️ Name")
    win.btn_rename_layer.setFixedHeight(24)
    win.btn_rename_layer.setStyleSheet("font-size: 10.5px; padding: 2px 5px;")
    win.btn_rename_layer.clicked.connect(win._rename_active_layer)

    win.btn_color_layer = QPushButton("🎨 Color")
    win.btn_color_layer.setFixedHeight(24)
    win.btn_color_layer.setStyleSheet("font-size: 10.5px; padding: 2px 5px;")
    win.btn_color_layer.clicked.connect(win._change_layer_color)

    win.btn_export_roto = QPushButton("📤 Nuke Roto")
    win.btn_export_roto.setFixedHeight(24)
    win.btn_export_roto.setStyleSheet("font-size: 10.5px; padding: 2px 6px;")
    win.btn_export_roto.setToolTip("Export animated rotomasks directly to a Foundry Nuke Roto node (.nk)")
    win.btn_export_roto.clicked.connect(win._export_active_masks_to_nuke_roto)

    layer_btn_row.addWidget(win.btn_add_layer)
    layer_btn_row.addWidget(win.btn_del_layer)
    layer_btn_row.addWidget(win.btn_rename_layer)
    layer_btn_row.addWidget(win.btn_color_layer)
    layer_btn_row.addWidget(win.btn_export_roto)
    mode_layout.addLayout(layer_btn_row)

    # Active Layer Tracking Mode
    mode_radio_row = QVBoxLayout()
    mode_radio_row.setSpacing(4)
    win.radio_grid = QRadioButton("Automatic Dense Grid")
    win.radio_grid.setChecked(True)
    win.radio_grid.toggled.connect(win._on_2d_mode_toggled)

    win.radio_points = QRadioButton("Interactive Point Picker")
    win.radio_points.toggled.connect(win._on_2d_mode_toggled)

    win.radio_cornerpin = QRadioButton("4-Point CornerPin (Screen Replacement)")
    win.radio_cornerpin.toggled.connect(win._on_2d_mode_toggled)

    mode_radio_row.addWidget(win.radio_grid)
    mode_radio_row.addWidget(win.radio_points)
    mode_radio_row.addWidget(win.radio_cornerpin)
    mode_layout.addLayout(mode_radio_row)

    # Tool Selector (Canvas Drawing Mode)
    tool_box = QHBoxLayout()
    tool_box.setSpacing(6)
    lbl_tool = QLabel("Tool:")
    lbl_tool.setStyleSheet("color: #94a3b8; font-weight: 600;")
    tool_box.addWidget(lbl_tool)
    win.combo_canvas_tool = QComboBox()
    win.combo_canvas_tool.addItems([
        "🖱️ Select & Edit",
        "➕ Place Points (Click)",
        "🟩 Draw Inclusion Box (Rectangle)",
        "🟩 Draw Inclusion Polygon (Freehand)",
        "🟥 Draw Exclusion Box (Rectangle)",
        "🟥 Draw Exclusion Polygon (Freehand)"
    ])
    win.combo_canvas_tool.currentIndexChanged.connect(win._on_canvas_tool_changed)
    tool_box.addWidget(win.combo_canvas_tool, 1)
    mode_layout.addLayout(tool_box)

    # Grid settings container
    win.grid_settings_widget = QWidget()
    g_lay = QVBoxLayout(win.grid_settings_widget)
    g_lay.setContentsMargins(0, 2, 0, 0)
    h_g = QHBoxLayout()
    lbl_grid = QLabel("Grid Size (N x N):")
    lbl_grid.setStyleSheet("color: #94a3b8; font-weight: 600;")
    h_g.addWidget(lbl_grid)
    win.spin_grid_size = QSpinBox()
    win.spin_grid_size.setRange(2, 50)
    win.spin_grid_size.setValue(10)
    win.lbl_total_pts = QLabel("(100 points)")
    win.lbl_total_pts.setStyleSheet("color: #38bdf8; font-weight: 700;")
    win.spin_grid_size.valueChanged.connect(win._on_grid_size_changed)
    h_g.addWidget(win.spin_grid_size)
    h_g.addWidget(win.lbl_total_pts)
    g_lay.addLayout(h_g)
    mode_layout.addWidget(win.grid_settings_widget)

    # Action Buttons (Masks & Points on Active Layer)
    btn_row = QHBoxLayout()
    btn_row.setSpacing(5)
    btn_clear_masks = QPushButton("🗑 Masks")
    btn_clear_masks.setFixedHeight(24)
    btn_clear_masks.setStyleSheet("font-size: 10.5px; padding: 2px 5px;")
    btn_clear_masks.setToolTip("Clear all inclusion and exclusion masks on the active layer")
    btn_clear_masks.clicked.connect(win._clear_active_layer_masks)

    btn_clear_pts = QPushButton("🗑 Points")
    btn_clear_pts.setFixedHeight(24)
    btn_clear_pts.setStyleSheet("font-size: 10.5px; padding: 2px 5px;")
    btn_clear_pts.setToolTip("Clear manual tracking points on the active layer")
    btn_clear_pts.clicked.connect(win._clear_manual_points)

    btn_jump_key = QPushButton("⏮ Keyframe")
    btn_jump_key.setFixedHeight(24)
    btn_jump_key.setStyleSheet("font-size: 10.5px; padding: 2px 5px;")
    btn_jump_key.setToolTip("Jump timeline to active layer keyframe")
    btn_jump_key.clicked.connect(win._jump_to_point_keyframe)

    btn_row.addWidget(btn_clear_masks)
    btn_row.addWidget(btn_clear_pts)
    btn_row.addWidget(btn_jump_key)
    mode_layout.addLayout(btn_row)
    left_layout.addWidget(mode_group)

    # Group 3: AI Engine & Filtering Parameters
    ai_group = QGroupBox("🧠 AI Parameters & Tracking Model")
    form_ai = QFormLayout(ai_group)
    form_ai.setContentsMargins(10, 12, 10, 10)
    form_ai.setSpacing(8)
    form_ai.setLabelAlignment(Qt.AlignLeft)

    win.combo_2d_res = QComboBox()
    win.combo_2d_res.addItems(["720p (HD - Recommended)", "512p (Fast)", "1080p (Full HD)", "Original"])
    form_ai.addRow("Process Resolution:", win.combo_2d_res)

    win.spin_min_conf = QDoubleSpinBox()
    win.spin_min_conf.setRange(0.1, 0.99)
    win.spin_min_conf.setValue(0.70)
    win.spin_min_conf.setSingleStep(0.05)
    form_ai.addRow("Min Confidence:", win.spin_min_conf)

    win.combo_2d_model = QComboBox()
    win.combo_2d_model.addItems(["CoTracker3 Offline (High Accuracy)", "CoTracker3 Online (Streaming)"])
    form_ai.addRow("Model Engine:", win.combo_2d_model)

    win.chk_vram_chunk = QCheckBox("Auto VRAM Chunking (Prevents OOM)")
    win.chk_vram_chunk.setChecked(True)
    form_ai.addRow(win.chk_vram_chunk)

    left_layout.addWidget(ai_group)
    left_layout.addStretch()
    splitter.addWidget(left_widget)

    # ---------------- Right Panel (Canvas, Timeline & 2D Log) ----------------
    right_widget = QWidget()
    right_layout = QVBoxLayout(right_widget)
    right_layout.setContentsMargins(6, 0, 0, 0)
    right_layout.setSpacing(10)

    # Interactive Canvas Group
    canvas_group = QGroupBox("🎬 Interactive Viewport & Built-in Player")
    canvas_layout = QVBoxLayout(canvas_group)
    canvas_layout.setContentsMargins(10, 12, 10, 10)
    canvas_layout.setSpacing(8)

    win.canvas_2d = VideoPointPickerCanvas()
    win.canvas_2d.file_dropped.connect(win._import_dropped_video)
    win.canvas_2d.point_added.connect(win._on_point_added_on_canvas)
    win.canvas_2d.masks_changed.connect(win._refresh_layer_list)
    win.canvas_2d.playback_toggle_requested.connect(win._toggle_playback)
    win.canvas_2d.step_frame_requested.connect(win._step_frame_by_offset)
    win.canvas_2d.keyframe_nav_requested.connect(win._nav_keyframe_by_offset)
    win.canvas_2d.in_point_requested.connect(win._set_in_point)
    win.canvas_2d.out_point_requested.connect(win._set_out_point)
    canvas_layout.addWidget(win.canvas_2d)

    # Custom Dark Timeline & Transport Control Bar
    ctrl_bar = QHBoxLayout()
    ctrl_bar.setSpacing(5)
    ctrl_bar.setContentsMargins(0, 2, 0, 2)

    win.btn_step_back = QPushButton("◀")
    win.btn_step_back.setFixedSize(32, 26)
    win.btn_step_back.setToolTip("Step Back 1 Frame (Left Arrow / J)")
    win.btn_step_back.clicked.connect(win._step_back_frame)

    win.btn_play_pause = QPushButton("▶ Play")
    win.btn_play_pause.setFixedSize(62, 26)
    win.btn_play_pause.setStyleSheet("font-size: 11px; font-weight: 700;")
    win.btn_play_pause.setToolTip("Play/Pause Scrubbing (Spacebar / K)")
    win.btn_play_pause.clicked.connect(win._toggle_playback)

    win.btn_step_fwd = QPushButton("▶")
    win.btn_step_fwd.setFixedSize(32, 26)
    win.btn_step_fwd.setToolTip("Step Forward 1 Frame (Right Arrow / L)")
    win.btn_step_fwd.clicked.connect(win._step_fwd_frame)

    win.slider_2d_frame = QSlider(Qt.Horizontal)
    win.slider_2d_frame.setRange(0, 0)
    win.slider_2d_frame.setFixedHeight(22)
    win.slider_2d_frame.valueChanged.connect(win._on_2d_frame_slider_changed)

    win.lbl_frame_idx = QLabel("[ 0001 / 0001 ]")
    win.lbl_frame_idx.setStyleSheet("""
        color: #38bdf8;
        font-family: 'Consolas', 'Cascadia Code', monospace;
        font-weight: 700;
        font-size: 11px;
        background-color: #0c1017;
        padding: 3px 8px;
        border-radius: 5px;
        border: 1px solid #1a2538;
    """)
    win.lbl_frame_idx.setAlignment(Qt.AlignCenter)

    win.chk_loop = QCheckBox("Loop")
    win.chk_loop.setChecked(True)
    win.chk_loop.setStyleSheet("font-size: 11px; font-weight: 600;")

    win.combo_view_layer = QComboBox()
    win.combo_view_layer.addItems(["👁️ Clean Video", "🎬 Motion Overlay"])
    win.combo_view_layer.setFixedHeight(26)
    win.combo_view_layer.setStyleSheet("font-size: 11px; padding: 2px 8px;")
    win.combo_view_layer.currentIndexChanged.connect(win._on_view_layer_changed)

    ctrl_bar.addWidget(win.btn_step_back)
    ctrl_bar.addWidget(win.btn_play_pause)
    ctrl_bar.addWidget(win.btn_step_fwd)
    ctrl_bar.addWidget(win.slider_2d_frame, 1)
    ctrl_bar.addWidget(win.lbl_frame_idx)
    ctrl_bar.addWidget(win.chk_loop)
    ctrl_bar.addWidget(win.combo_view_layer)
    canvas_layout.addLayout(ctrl_bar)

    # Keyframe Timeline Navigation Bar & Range In/Out Bar (Studio Layout)
    kf_bar = QHBoxLayout()
    kf_bar.setSpacing(4)
    kf_bar.setContentsMargins(0, 0, 0, 2)

    lbl_kf_title = QLabel("Roto:")
    lbl_kf_title.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 700;")
    
    win.btn_prev_key = QPushButton("⏮ Prev")
    win.btn_prev_key.setFixedHeight(24)
    win.btn_prev_key.setStyleSheet("font-size: 10.5px; padding: 2px 6px;")
    win.btn_prev_key.setToolTip("Jump to previous mask keyframe ([ key)")
    win.btn_prev_key.clicked.connect(win._jump_prev_keyframe)

    win.lbl_key_status = QLabel("◆ f1")
    win.lbl_key_status.setStyleSheet("""
        color: #34d399;
        font-weight: 700;
        font-size: 10.5px;
        padding: 2px 7px;
        background-color: #0f2319;
        border: 1px solid #10b981;
        border-radius: 4px;
    """)
    win.lbl_key_status.setAlignment(Qt.AlignCenter)

    win.btn_set_key = QPushButton("◆ Key")
    win.btn_set_key.setFixedHeight(24)
    win.btn_set_key.setStyleSheet("font-size: 10.5px; padding: 2px 6px;")
    win.btn_set_key.setToolTip("Create or update keyframe on current frame")
    win.btn_set_key.clicked.connect(win._set_mask_keyframe_on_current)

    win.btn_del_key = QPushButton("🗑 Del")
    win.btn_del_key.setFixedHeight(24)
    win.btn_del_key.setStyleSheet("font-size: 10.5px; padding: 2px 6px;")
    win.btn_del_key.setToolTip("Delete keyframe on current frame (Del key)")
    win.btn_del_key.clicked.connect(win._delete_mask_keyframe_on_current)

    win.btn_next_key = QPushButton("Next ⏭")
    win.btn_next_key.setFixedHeight(24)
    win.btn_next_key.setStyleSheet("font-size: 10.5px; padding: 2px 6px;")
    win.btn_next_key.setToolTip("Jump to next mask keyframe (] key)")
    win.btn_next_key.clicked.connect(win._jump_next_keyframe)

    # In / Out Range Trimmer
    win.btn_set_in = QPushButton("In [I]")
    win.btn_set_in.setFixedHeight(24)
    win.btn_set_in.setStyleSheet("font-size: 10.5px; padding: 2px 6px;")
    win.btn_set_in.setToolTip("Set Track In-Point (I key)")
    win.btn_set_in.clicked.connect(lambda: win._set_in_point(win.canvas_2d.current_frame))

    win.btn_set_out = QPushButton("Out [O]")
    win.btn_set_out.setFixedHeight(24)
    win.btn_set_out.setStyleSheet("font-size: 10.5px; padding: 2px 6px;")
    win.btn_set_out.setToolTip("Set Track Out-Point (O key)")
    win.btn_set_out.clicked.connect(lambda: win._set_out_point(win.canvas_2d.current_frame))

    win.btn_reset_range = QPushButton("↺")
    win.btn_reset_range.setFixedHeight(24)
    win.btn_reset_range.setFixedWidth(24)
    win.btn_reset_range.setStyleSheet("font-size: 11px; padding: 0px;")
    win.btn_reset_range.setToolTip("Reset Tracking Range to Full Video")
    win.btn_reset_range.clicked.connect(win._reset_tracking_range)

    win.lbl_range_status = QLabel("Full")
    win.lbl_range_status.setStyleSheet("""
        color: #38bdf8;
        font-size: 10.5px;
        font-weight: 600;
        padding: 2px 6px;
        background-color: #101a28;
        border: 1px solid #1e364e;
        border-radius: 4px;
    """)

    # Visual Mode Toggles
    win.btn_toggle_matte = QPushButton("👁️ Matte")
    win.btn_toggle_matte.setFixedHeight(24)
    win.btn_toggle_matte.setStyleSheet("font-size: 10.5px; padding: 2px 7px;")
    win.btn_toggle_matte.setCheckable(True)
    win.btn_toggle_matte.setChecked(True)
    win.btn_toggle_matte.setToolTip("Toggle Live Mask Matte Overlay (M key)")
    win.btn_toggle_matte.toggled.connect(win._toggle_canvas_matte_overlay)

    win.btn_toggle_alpha = QPushButton("⬛ Alpha")
    win.btn_toggle_alpha.setFixedHeight(24)
    win.btn_toggle_alpha.setStyleSheet("font-size: 10.5px; padding: 2px 7px;")
    win.btn_toggle_alpha.setCheckable(True)
    win.btn_toggle_alpha.setToolTip("Toggle High-Contrast Alpha Channel Matte View (A key)")
    win.btn_toggle_alpha.toggled.connect(win._toggle_canvas_alpha_mode)

    win.btn_toggle_loupe = QPushButton("🔍 Loupe")
    win.btn_toggle_loupe.setFixedHeight(24)
    win.btn_toggle_loupe.setStyleSheet("font-size: 10.5px; padding: 2px 7px;")
    win.btn_toggle_loupe.setCheckable(True)
    win.btn_toggle_loupe.setToolTip("Toggle Sub-Pixel Precision Magnifier Loupe (Hold Ctrl)")
    win.btn_toggle_loupe.toggled.connect(win._toggle_canvas_loupe)

    kf_bar.addWidget(lbl_kf_title)
    kf_bar.addWidget(win.btn_prev_key)
    kf_bar.addWidget(win.lbl_key_status)
    kf_bar.addWidget(win.btn_set_key)
    kf_bar.addWidget(win.btn_del_key)
    kf_bar.addWidget(win.btn_next_key)
    kf_bar.addSpacing(6)
    kf_bar.addWidget(win.btn_set_in)
    kf_bar.addWidget(win.btn_set_out)
    kf_bar.addWidget(win.btn_reset_range)
    kf_bar.addWidget(win.lbl_range_status)
    kf_bar.addSpacing(6)
    kf_bar.addWidget(win.btn_toggle_matte)
    kf_bar.addWidget(win.btn_toggle_alpha)
    kf_bar.addWidget(win.btn_toggle_loupe)
    kf_bar.addStretch()
    canvas_layout.addLayout(kf_bar)

    right_layout.addWidget(canvas_group, 3)

    # 2D Log & Actions
    log_2d_group = QGroupBox("📊 2D AI Tracker Diagnostics & Exports")
    log_2d_layout = QVBoxLayout(log_2d_group)
    log_2d_layout.setContentsMargins(10, 12, 10, 10)
    log_2d_layout.setSpacing(8)

    win.log_2d_text = QTextEdit()
    win.log_2d_text.setReadOnly(True)

    log_2d_top = QHBoxLayout()
    lbl_2d_engine = QLabel("Engine Diagnostics Console")
    lbl_2d_engine.setStyleSheet("font-size: 11px; color: #64748b; font-weight: 600;")
    btn_clear_2d_log = QPushButton("🧹 Clear Log")
    btn_clear_2d_log.setFixedHeight(22)
    btn_clear_2d_log.setStyleSheet("""
        font-size: 10.5px;
        padding: 2px 8px;
        background-color: #161b26;
        border: 1px solid #283348;
        color: #94a3b8;
        border-radius: 4px;
    """)
    btn_clear_2d_log.clicked.connect(win.log_2d_text.clear)
    log_2d_top.addWidget(lbl_2d_engine)
    log_2d_top.addStretch()
    log_2d_top.addWidget(btn_clear_2d_log)
    log_2d_layout.addLayout(log_2d_top)

    log_2d_layout.addWidget(win.log_2d_text)

    win.progress_2d = QProgressBar()
    win.progress_2d.setValue(0)
    win.progress_2d.setFormat("Ready for 2D Point Tracking")
    log_2d_layout.addWidget(win.progress_2d)

    # Action Bar (2-Tier Balanced Studio Layout)
    action_2d_box = QVBoxLayout()
    action_2d_box.setSpacing(6)

    # Row 1: Primary 2D Execution & Cancel
    row1 = QHBoxLayout()
    row1.setSpacing(8)

    win.btn_start_2d = QPushButton("▶  RUN 2D POINT TRACKING")
    win.btn_start_2d.setObjectName("primaryBtn")
    win.btn_start_2d.setFixedHeight(38)
    win.btn_start_2d.clicked.connect(win._start_tracking_2d)

    win.btn_stop_2d = QPushButton("⏹ Cancel")
    win.btn_stop_2d.setObjectName("stopBtn")
    win.btn_stop_2d.setFixedHeight(38)
    win.btn_stop_2d.setFixedWidth(110)
    win.btn_stop_2d.setEnabled(False)
    win.btn_stop_2d.clicked.connect(win._stop_tracking_2d)

    row1.addWidget(win.btn_start_2d, 1)
    row1.addWidget(win.btn_stop_2d)
    action_2d_box.addLayout(row1)

    # Row 2: Nuke Export, Overlay Player & Folder
    row2 = QHBoxLayout()
    row2.setSpacing(8)

    win.btn_export_2d_nuke = QPushButton("📋 Export for Nuke (Tracker Node)")
    win.btn_export_2d_nuke.setObjectName("nukeBtn")
    win.btn_export_2d_nuke.setFixedHeight(32)
    win.btn_export_2d_nuke.setToolTip("Copy solved 2D Tracker4 / CornerPin2D node directly to clipboard for 1-click paste (Ctrl+V) into Nuke")
    win.btn_export_2d_nuke.clicked.connect(win._export_2d_for_nuke)

    win.btn_load_overlay_player = QPushButton("🎬 Play Overlay")
    win.btn_load_overlay_player.setFixedHeight(32)
    win.btn_load_overlay_player.setFixedWidth(130)
    win.btn_load_overlay_player.clicked.connect(win._load_overlay_into_player)

    win.btn_open_2d_dir = QPushButton("📂 Open Folder")
    win.btn_open_2d_dir.setFixedHeight(32)
    win.btn_open_2d_dir.setFixedWidth(120)
    win.btn_open_2d_dir.clicked.connect(win._open_2d_output_folder)

    row2.addWidget(win.btn_export_2d_nuke, 1)
    row2.addWidget(win.btn_load_overlay_player)
    row2.addWidget(win.btn_open_2d_dir)
    action_2d_box.addLayout(row2)

    log_2d_layout.addLayout(action_2d_box)

    right_layout.addWidget(log_2d_group, 2)
    splitter.addWidget(right_widget)
    splitter.setSizes([340, 780])
