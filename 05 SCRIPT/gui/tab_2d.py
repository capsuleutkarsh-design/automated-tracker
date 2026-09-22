"""
2D AI Point Tracking tab (CoTracker3).

Layout: inspector on the left in working order (media -> layers -> mode & tools
-> solver), and on the right the viewport with a transport bar and a roto bar
beneath it, then the console and run controls.

The transport and roto controls are grouped into labelled clusters instead of
one long undifferentiated run of buttons.

Every widget keeps the attribute name tracker_gui.py expects.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton, QTextEdit,
    QProgressBar, QRadioButton, QSlider, QListWidget, QSplitter,
    QSizePolicy,
)
from PySide6.QtCore import Qt

from gui.canvas import VideoPointPickerCanvas
from gui.ui_kit import (
    tame_combos, scrollable_strip,
    card, form_row, button_row, inspector_scroll, make_button,
    strip, divider,
)


def _cluster_label(text):
    lbl = QLabel(text)
    lbl.setObjectName("sectionTitle")
    return lbl


def build_2d_tab(win, tab):
    root = QVBoxLayout(tab)
    root.setContentsMargins(8, 8, 8, 8)
    root.setSpacing(8)

    splitter = QSplitter(Qt.Horizontal)
    splitter.setChildrenCollapsible(False)
    root.addWidget(splitter, 1)

    # =====================================================================
    # LEFT - inspector
    # =====================================================================
    inspector = QWidget()
    ins = QVBoxLayout(inspector)
    ins.setContentsMargins(0, 0, 6, 0)
    ins.setSpacing(10)

    # ---- Media ----------------------------------------------------------
    media_card, mbody = card("Source Clip")
    win.combo_2d_video = QComboBox()
    win.combo_2d_video.setToolTip("Clips found in 02 VIDEOS")
    win.combo_2d_video.currentTextChanged.connect(win._on_2d_video_selected)
    mbody.addWidget(win.combo_2d_video)

    win.btn_extract_frames = make_button(
        "Unpack Frame Cache",
        "Extract every frame to disk so scrubbing is instant")
    win.btn_extract_frames.clicked.connect(win._extract_frames_for_current_video)
    mbody.addWidget(win.btn_extract_frames)

    win.spin_start_frame_2d = QSpinBox()
    win.spin_start_frame_2d.setRange(0, 9999999)
    win.spin_start_frame_2d.setValue(1)
    win.spin_start_frame_2d.setToolTip("Frame number the shot's first frame sits on in your timeline.\nLeave at 1 unless the plate is numbered from something else -\na 1001-1200 sequence needs 1001, or the exported keys land\noff the end of your comp and read as a single static value.")
    form_row(mbody, "Timeline start", win.spin_start_frame_2d)
    ins.addWidget(media_card)

    # ---- Layers ---------------------------------------------------------
    layer_card, lbody = card("Tracking Layers")

    win.layer_list = QListWidget()
    win.layer_list.setMinimumHeight(92)
    win.layer_list.setMaximumHeight(150)
    win.layer_list.setToolTip("Each layer solves independently and exports its own tracks")
    win.layer_list.currentRowChanged.connect(win._on_layer_selected)
    lbody.addWidget(win.layer_list)

    win.btn_add_layer = make_button("+ Add", "Add a tracking layer")
    win.btn_add_layer.clicked.connect(win._add_new_layer)
    win.btn_del_layer = make_button("Delete", "Remove the active layer")
    win.btn_del_layer.clicked.connect(win._delete_active_layer)
    win.btn_rename_layer = make_button("Rename", "Rename the active layer")
    win.btn_rename_layer.clicked.connect(win._rename_active_layer)
    win.btn_color_layer = make_button("Colour", "Change the layer's overlay colour")
    win.btn_color_layer.clicked.connect(win._change_layer_color)
    button_row(lbody, [win.btn_add_layer, win.btn_del_layer,
                       win.btn_rename_layer, win.btn_color_layer])

    win.btn_export_roto = make_button(
        "Export Roto to Nuke",
        "Export animated rotomasks as a Foundry Nuke Roto node (.nk)")
    win.btn_export_roto.clicked.connect(win._export_active_masks_to_nuke_roto)
    lbody.addWidget(win.btn_export_roto)
    ins.addWidget(layer_card)

    # ---- Mode & tools ---------------------------------------------------
    mode_card, obody = card("Mode & Tools")

    win.radio_grid = QRadioButton("Automatic dense grid")
    win.radio_grid.setChecked(True)
    win.radio_grid.setToolTip("Scatter a regular grid of points over the frame")
    win.radio_grid.toggled.connect(win._on_2d_mode_toggled)

    win.radio_points = QRadioButton("Interactive point picker")
    win.radio_points.setToolTip("Click the features you want tracked")
    win.radio_points.toggled.connect(win._on_2d_mode_toggled)

    win.radio_cornerpin = QRadioButton("4-point corner pin (screen replace)")
    win.radio_cornerpin.setToolTip("Click 4 corners clockwise from top-left")
    win.radio_cornerpin.toggled.connect(win._on_2d_mode_toggled)

    for r in (win.radio_grid, win.radio_points, win.radio_cornerpin):
        obody.addWidget(r)

    divider(obody)

    win.combo_canvas_tool = QComboBox()
    win.combo_canvas_tool.addItems([
        "Select & Edit",
        "Place Points (Click)",
        "Draw Inclusion Box",
        "Draw Inclusion Polygon",
        "Draw Exclusion Box",
        "Draw Exclusion Polygon",
    ])
    win.combo_canvas_tool.setToolTip(
        "What a click on the viewport does.\n"
        "Inclusion keeps points inside the shape; exclusion removes them.")
    win.combo_canvas_tool.currentIndexChanged.connect(win._on_canvas_tool_changed)
    form_row(obody, "Canvas tool", win.combo_canvas_tool)

    win.grid_settings_widget = QWidget()
    g_lay = QVBoxLayout(win.grid_settings_widget)
    g_lay.setContentsMargins(0, 0, 0, 0)
    g_lay.setSpacing(0)
    win.spin_grid_size = QSpinBox()
    win.spin_grid_size.setRange(2, 50)
    win.spin_grid_size.setValue(10)
    win.spin_grid_size.setToolTip("Points per side. 10 gives a 10x10 grid.")
    win.spin_grid_size.valueChanged.connect(win._on_grid_size_changed)
    win.lbl_total_pts = QLabel("100 pts")
    win.lbl_total_pts.setObjectName("valueChip")
    win.lbl_total_pts.setAlignment(Qt.AlignCenter)
    win.lbl_total_pts.setMinimumWidth(72)
    form_row(g_lay, "Grid size (N×N)", win.spin_grid_size, hint=win.lbl_total_pts)
    obody.addWidget(win.grid_settings_widget)

    divider(obody)

    btn_clear_masks = make_button(
        "Clear Masks", "Clear all inclusion and exclusion masks on the active layer")
    btn_clear_masks.clicked.connect(win._clear_active_layer_masks)
    btn_clear_pts = make_button(
        "Clear Points", "Clear manual tracking points on the active layer")
    btn_clear_pts.clicked.connect(win._clear_manual_points)
    btn_jump_key = make_button(
        "Go to Key", "Jump the timeline to the active layer's keyframe")
    btn_jump_key.clicked.connect(win._jump_to_point_keyframe)
    button_row(obody, [btn_clear_masks, btn_clear_pts, btn_jump_key])
    ins.addWidget(mode_card)

    # ---- Solver ---------------------------------------------------------
    ai_card, abody = card("AI Solver")

    win.combo_2d_res = QComboBox()
    win.combo_2d_res.addItems([
        "720p (HD - Recommended)", "512p (Fast)", "1080p (Full HD)", "Original"])
    win.combo_2d_res.setToolTip(
        "Size the clip is loaded at. CoTracker itself always samples every block\n"
        "at its own fixed model resolution, so this does not make the tracking more\n"
        "precise - it only changes how much detail survives the downscale and how\n"
        "much RAM, VRAM upload and load time the clip costs. 720p suits most clips.")
    form_row(abody, "Resolution", win.combo_2d_res)

    win.spin_min_conf = QDoubleSpinBox()
    win.spin_min_conf.setRange(0.1, 0.99)
    win.spin_min_conf.setValue(0.70)
    win.spin_min_conf.setSingleStep(0.05)
    win.spin_min_conf.setToolTip(
        "Minimum tracking confidence a sample must reach to count as visible.\n"
        "Higher = fewer but more reliable points.")
    win.spin_min_conf.valueChanged.connect(win._on_min_conf_changed)
    form_row(abody, "Confidence", win.spin_min_conf)

    win.combo_2d_model = QComboBox()
    win.combo_2d_model.addItems([
        "CoTracker3 Offline (High Accuracy)", "CoTracker3 Online (Streaming)"])
    win.combo_2d_model.setToolTip("Offline sees the whole window at once and is more accurate.")
    form_row(abody, "Model", win.combo_2d_model)

    win.chk_vram_chunk = QCheckBox("Auto VRAM chunking (prevent out-of-memory)")
    win.chk_vram_chunk.setChecked(True)
    win.chk_vram_chunk.setToolTip(
        "Sends the clip to the GPU a few frames at a time, sized to the free VRAM.\n"
        "Turn off only if you have VRAM to spare - the whole clip then goes up at once.")
    abody.addWidget(win.chk_vram_chunk)
    ins.addWidget(ai_card)

    ins.addStretch(1)
    splitter.addWidget(inspector_scroll(inspector))

    # =====================================================================
    # RIGHT - viewport, transport, console, actions
    # =====================================================================
    right = QWidget()
    rl = QVBoxLayout(right)
    rl.setContentsMargins(6, 0, 0, 0)
    rl.setSpacing(8)

    # ---- Viewport -------------------------------------------------------
    win.canvas_2d = VideoPointPickerCanvas()
    win.canvas_2d.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    win.canvas_2d.file_dropped.connect(win._import_dropped_video)
    win.canvas_2d.point_added.connect(win._on_point_added_on_canvas)
    win.canvas_2d.masks_changed.connect(win._refresh_layer_list)
    win.canvas_2d.playback_toggle_requested.connect(win._toggle_playback)
    win.canvas_2d.step_frame_requested.connect(win._step_frame_by_offset)
    win.canvas_2d.keyframe_nav_requested.connect(win._nav_keyframe_by_offset)
    win.canvas_2d.in_point_requested.connect(win._set_in_point)
    win.canvas_2d.out_point_requested.connect(win._set_out_point)
    rl.addWidget(win.canvas_2d, 1)

    # ---- Transport ------------------------------------------------------
    t_frame, tbar = strip(spacing=7)

    win.btn_step_back = make_button("◀", "Step back one frame  (← / J)", "transport")
    win.btn_step_back.clicked.connect(win._step_back_frame)

    win.btn_play_pause = make_button("▶  Play", "Play / pause  (Space / K)", "transportPlay")
    win.btn_play_pause.clicked.connect(win._toggle_playback)

    win.btn_step_fwd = make_button("▶", "Step forward one frame  (→ / L)", "transport")
    win.btn_step_fwd.clicked.connect(win._step_fwd_frame)

    win.slider_2d_frame = QSlider(Qt.Horizontal)
    win.slider_2d_frame.setRange(0, 0)
    win.slider_2d_frame.setFixedHeight(30)
    win.slider_2d_frame.setToolTip("Scrub the timeline")
    win.slider_2d_frame.valueChanged.connect(win._on_2d_frame_slider_changed)

    win.lbl_frame_idx = QLabel("00:00:00:00  (1/1)")
    win.lbl_frame_idx.setObjectName("valueChip")
    win.lbl_frame_idx.setAlignment(Qt.AlignCenter)
    win.lbl_frame_idx.setMinimumWidth(150)
    win.lbl_frame_idx.setToolTip("Timecode and frame, at the clip's real frame rate")

    win.chk_loop = QCheckBox("Loop")
    win.chk_loop.setChecked(True)
    win.chk_loop.setToolTip("Restart playback from the first frame when it reaches the end")

    win.combo_view_layer = QComboBox()
    win.combo_view_layer.addItems(["Clean Video", "Motion Overlay"])
    win.combo_view_layer.setFixedWidth(148)
    win.combo_view_layer.setToolTip("Switch between the plate and the rendered motion trails")
    win.combo_view_layer.currentIndexChanged.connect(win._on_view_layer_changed)

    tbar.addWidget(win.btn_step_back)
    tbar.addWidget(win.btn_play_pause)
    tbar.addWidget(win.btn_step_fwd)
    tbar.addWidget(win.slider_2d_frame, 1)
    tbar.addWidget(win.lbl_frame_idx)
    divider(tbar, vertical=True)
    tbar.addWidget(win.chk_loop)
    tbar.addWidget(win.combo_view_layer)
    rl.addWidget(t_frame)

    # ---- Roto / range / view --------------------------------------------
    k_frame, kbar = strip(spacing=6)

    # cluster 1 - keyframes
    kbar.addWidget(_cluster_label("Keys"))
    win.btn_prev_key = make_button("◀ Prev", "Previous mask keyframe  ( [ )", "compact")
    win.btn_prev_key.clicked.connect(win._jump_prev_keyframe)
    win.btn_set_key = make_button("Set", "Create or update a keyframe here", "compact")
    win.btn_set_key.clicked.connect(win._set_mask_keyframe_on_current)
    win.btn_del_key = make_button("Del", "Delete the keyframe here  (Del)", "compact")
    win.btn_del_key.clicked.connect(win._delete_mask_keyframe_on_current)
    win.btn_next_key = make_button("Next ▶", "Next mask keyframe  ( ] )", "compact")
    win.btn_next_key.clicked.connect(win._jump_next_keyframe)
    win.btn_del_mask = make_button(
        "Del Mask", "Delete the selected mask with all its keyframes  (Shift+Del)", "compact")
    win.btn_del_mask.clicked.connect(win.canvas_2d.delete_selected_mask)

    win.lbl_key_status = QLabel("◆ f1")
    win.lbl_key_status.setObjectName("valueChip")
    win.lbl_key_status.setAlignment(Qt.AlignCenter)
    win.lbl_key_status.setMinimumWidth(74)

    for wdg in (win.btn_prev_key, win.btn_set_key, win.btn_del_key,
                win.btn_next_key, win.btn_del_mask, win.lbl_key_status):
        kbar.addWidget(wdg)

    divider(kbar, vertical=True)

    # cluster 2 - tracking range
    kbar.addWidget(_cluster_label("Range"))
    win.btn_set_in = make_button("In", "Set the tracking in-point  (I)", "compact")
    win.btn_set_in.clicked.connect(lambda: win._set_in_point(win.canvas_2d.current_frame))
    win.btn_set_out = make_button("Out", "Set the tracking out-point  (O)", "compact")
    win.btn_set_out.clicked.connect(lambda: win._set_out_point(win.canvas_2d.current_frame))
    win.btn_reset_range = make_button("Reset", "Track the whole clip again", "compact")
    win.btn_reset_range.clicked.connect(win._reset_tracking_range)

    win.lbl_range_status = QLabel("Full")
    win.lbl_range_status.setObjectName("valueChip")
    win.lbl_range_status.setAlignment(Qt.AlignCenter)
    win.lbl_range_status.setMinimumWidth(74)

    for wdg in (win.btn_set_in, win.btn_set_out, win.btn_reset_range, win.lbl_range_status):
        kbar.addWidget(wdg)

    divider(kbar, vertical=True)

    # cluster 3 - viewport modes
    kbar.addWidget(_cluster_label("View"))
    win.btn_toggle_matte = make_button("Matte", "Show the live mask overlay  (M)", "toggle", checkable=True)
    win.btn_toggle_matte.setChecked(True)
    win.btn_toggle_matte.toggled.connect(win._toggle_canvas_matte_overlay)

    win.btn_toggle_alpha = make_button("Alpha", "High-contrast alpha channel view  (A)", "toggle", checkable=True)
    win.btn_toggle_alpha.toggled.connect(win._toggle_canvas_alpha_mode)

    win.btn_toggle_loupe = make_button("Loupe", "Sub-pixel magnifier  (hold Ctrl)", "toggle", checkable=True)
    win.btn_toggle_loupe.toggled.connect(win._toggle_canvas_loupe)

    for wdg in (win.btn_toggle_matte, win.btn_toggle_alpha, win.btn_toggle_loupe):
        kbar.addWidget(wdg)

    kbar.addStretch(1)
    # Three clusters of controls do not fit a narrow window; scroll rather than clip.
    rl.addWidget(scrollable_strip(k_frame))

    # ---- Console --------------------------------------------------------
    log_card, logbody = card("Tracker Console")
    btn_clear_2d_log = make_button("Clear", "Empty the console", "compact")
    btn_clear_2d_log.setFixedWidth(64)
    log_card.header_layout.addWidget(btn_clear_2d_log)

    win.log_2d_text = QTextEdit()
    win.log_2d_text.setReadOnly(True)
    win.log_2d_text.setMinimumHeight(96)
    win.log_2d_text.setMaximumHeight(200)
    btn_clear_2d_log.clicked.connect(win.log_2d_text.clear)
    logbody.addWidget(win.log_2d_text)

    win.progress_2d = QProgressBar()
    win.progress_2d.setValue(0)
    win.progress_2d.setFormat("Ready")
    logbody.addWidget(win.progress_2d)
    rl.addWidget(log_card)

    # ---- Actions --------------------------------------------------------
    act_card, actbody = card(None)
    actbody.setSpacing(7)

    run_row = QHBoxLayout()
    run_row.setSpacing(7)
    win.btn_start_2d = QPushButton("▶   Run 2D Point Tracking")
    win.btn_start_2d.setObjectName("primary")
    win.btn_start_2d.setToolTip("Track every layer through the current range")
    win.btn_start_2d.clicked.connect(win._start_tracking_2d)

    win.btn_stop_2d = QPushButton("Cancel")
    win.btn_stop_2d.setObjectName("danger")
    win.btn_stop_2d.setToolTip("Stop the running track after the current block of frames")
    win.btn_stop_2d.setFixedWidth(100)
    win.btn_stop_2d.setEnabled(False)
    win.btn_stop_2d.clicked.connect(win._stop_tracking_2d)

    run_row.addWidget(win.btn_start_2d, 1)
    run_row.addWidget(win.btn_stop_2d)
    actbody.addLayout(run_row)

    win.btn_export_2d_nuke = make_button(
        "Nuke Tracker Node",
        "Copy the solved Tracker4 node to the clipboard for Ctrl+V in Nuke", "export")
    win.btn_export_2d_nuke.clicked.connect(win._export_2d_for_nuke)

    win.btn_load_overlay_player = make_button(
        "Play Overlay", "Load the rendered motion-trail video into the viewport", "export")
    win.btn_load_overlay_player.clicked.connect(win._load_overlay_into_player)

    win.btn_open_2d_dir = make_button(
        "Open Output", "Show the 2D track folder", "export")
    win.btn_open_2d_dir.clicked.connect(win._open_2d_output_folder)

    button_row(actbody, [win.btn_export_2d_nuke,
                         win.btn_load_overlay_player,
                         win.btn_open_2d_dir], compact=False)
    rl.addWidget(act_card)

    splitter.addWidget(right)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    # Combos on the right-hand side get the same treatment as the inspector.
    tame_combos(right, shrink=False)
    splitter.setSizes([430, 1000])
