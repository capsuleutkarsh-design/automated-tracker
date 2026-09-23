"""
2D AI Point Tracking tab (CoTracker3).

Layout: inspector on the left in working order (media -> layers -> mode & tools
-> solver), and on the right the viewport with a transport bar and a roto bar
beneath it, then the console and run controls.

The transport and roto controls are grouped into labelled clusters instead of
one long undifferentiated run of buttons.

Built against the AppContext, not the window: every widget is registered
through `ctx.ui` under the name tracker_gui.py, the tests and the headless
scripts already use, and every button runs a named method on `ctx` or on one of
the panels it carries. gui/context.py documents what that allows.

The layer card, the transport bar and the Keys and Range clusters are owned by
gui/layer_panel.py and gui/player.py; this file only decides where they sit.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton, QTextEdit,
    QProgressBar, QRadioButton, QSplitter,
    QSizePolicy,
)
from PySide6.QtCore import Qt

from gui.canvas import VideoPointPickerCanvas
from gui.ui_kit import (
    tame_combos, scrollable_strip,
    card, form_row, button_row, inspector_scroll, make_button,
    strip, divider, group_label,
)


def build_2d_tab(ctx, tab):
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
    ctx.ui.combo_2d_video = QComboBox()
    ctx.ui.combo_2d_video.setToolTip("Clips found in 02 VIDEOS")
    ctx.ui.combo_2d_video.currentTextChanged.connect(ctx.on_2d_video_selected)
    mbody.addWidget(ctx.ui.combo_2d_video)

    ctx.ui.btn_extract_frames = make_button(
        "Unpack Frame Cache",
        "Extract every frame to disk so scrubbing is instant")
    ctx.ui.btn_extract_frames.clicked.connect(ctx.extract_frames_for_current_video)
    mbody.addWidget(ctx.ui.btn_extract_frames)

    ctx.ui.spin_start_frame_2d = QSpinBox()
    ctx.ui.spin_start_frame_2d.setRange(0, 9999999)
    ctx.ui.spin_start_frame_2d.setValue(1)
    ctx.ui.spin_start_frame_2d.setToolTip("Frame number the shot's first frame sits on in your timeline.\nLeave at 1 unless the plate is numbered from something else -\na 1001-1200 sequence needs 1001, or the exported keys land\noff the end of your comp and read as a single static value.")
    form_row(mbody, "Timeline start", ctx.ui.spin_start_frame_2d)

    # An image sequence carries no frame rate, and 24 was simply assumed - so a
    # 25 fps plate had every exported key placed at the wrong time. Video files
    # fill this from the probe and lock it; sequences are the artist's to set.
    ctx.ui.spin_fps = QDoubleSpinBox()
    ctx.ui.spin_fps.setRange(1.0, 240.0)
    ctx.ui.spin_fps.setDecimals(3)
    ctx.ui.spin_fps.setSingleStep(1.0)
    ctx.ui.spin_fps.setValue(24.0)
    ctx.ui.spin_fps.setToolTip(
        "Frame rate of the plate. Drives playback, the timecode readout and\n"
        "every exported curve, so a wrong value puts the keys on wrong times.\n"
        "Read from the file for a video and locked; editable for an image\n"
        "sequence, which carries no rate of its own.\n"
        "Common rates: 23.976, 24, 25, 29.97, 30, 48, 50, 60.")
    ctx.ui.spin_fps.valueChanged.connect(ctx.on_fps_changed)
    form_row(mbody, "Frame rate", ctx.ui.spin_fps)
    ins.addWidget(media_card)

    # ---- Layers ---------------------------------------------------------
    # The whole card, its buttons and the rules behind them live in
    # gui/layer_panel.py; the tab only decides where it sits.
    ins.addWidget(ctx.layers.build_card())

    # ---- Mode & tools ---------------------------------------------------
    mode_card, obody = card("Mode & Tools")

    ctx.ui.radio_grid = QRadioButton("Automatic dense grid")
    ctx.ui.radio_grid.setChecked(True)
    ctx.ui.radio_grid.setToolTip("Scatter a regular grid of points over the frame")
    ctx.ui.radio_grid.toggled.connect(ctx.layers.on_2d_mode_toggled)

    ctx.ui.radio_points = QRadioButton("Interactive point picker")
    ctx.ui.radio_points.setToolTip("Click the features you want tracked")
    ctx.ui.radio_points.toggled.connect(ctx.layers.on_2d_mode_toggled)

    ctx.ui.radio_cornerpin = QRadioButton("4-point corner pin (screen replace)")
    ctx.ui.radio_cornerpin.setToolTip("Click 4 corners clockwise from top-left")
    ctx.ui.radio_cornerpin.toggled.connect(ctx.layers.on_2d_mode_toggled)

    for r in (ctx.ui.radio_grid, ctx.ui.radio_points, ctx.ui.radio_cornerpin):
        obody.addWidget(r)

    divider(obody)

    ctx.ui.combo_canvas_tool = QComboBox()
    ctx.ui.combo_canvas_tool.addItems([
        "Select & Edit",
        "Place Points (Click)",
        "Draw Inclusion Box",
        "Draw Inclusion Polygon",
        "Draw Exclusion Box",
        "Draw Exclusion Polygon",
    ])
    ctx.ui.combo_canvas_tool.setToolTip(
        "What a click on the viewport does.\n"
        "Inclusion keeps points inside the shape; exclusion removes them.")
    ctx.ui.combo_canvas_tool.currentIndexChanged.connect(ctx.on_canvas_tool_changed)
    form_row(obody, "Canvas tool", ctx.ui.combo_canvas_tool)

    ctx.ui.grid_settings_widget = QWidget()
    g_lay = QVBoxLayout(ctx.ui.grid_settings_widget)
    g_lay.setContentsMargins(0, 0, 0, 0)
    g_lay.setSpacing(0)
    ctx.ui.spin_grid_size = QSpinBox()
    ctx.ui.spin_grid_size.setRange(2, 50)
    ctx.ui.spin_grid_size.setValue(10)
    ctx.ui.spin_grid_size.setToolTip("Points per side. 10 gives a 10x10 grid.")
    ctx.ui.spin_grid_size.valueChanged.connect(ctx.layers.on_grid_size_changed)
    ctx.ui.lbl_total_pts = QLabel("100 pts")
    ctx.ui.lbl_total_pts.setObjectName("valueChip")
    ctx.ui.lbl_total_pts.setAlignment(Qt.AlignCenter)
    ctx.ui.lbl_total_pts.setMinimumWidth(72)
    form_row(g_lay, "Grid size (N×N)", ctx.ui.spin_grid_size, hint=ctx.ui.lbl_total_pts)
    obody.addWidget(ctx.ui.grid_settings_widget)

    divider(obody)

    btn_clear_masks = make_button(
        "Clear Masks", "Clear all inclusion and exclusion masks on the active layer")
    btn_clear_masks.clicked.connect(ctx.clear_active_layer_masks)
    btn_clear_pts = make_button(
        "Clear Points", "Clear manual tracking points on the active layer")
    btn_clear_pts.clicked.connect(ctx.clear_manual_points)
    btn_jump_key = make_button(
        "Go to Key", "Jump the timeline to the active layer's keyframe")
    btn_jump_key.clicked.connect(ctx.jump_to_point_keyframe)
    button_row(obody, [btn_clear_masks, btn_clear_pts, btn_jump_key])
    ins.addWidget(mode_card)

    # ---- Solver ---------------------------------------------------------
    ai_card, abody = card("AI Solver")

    ctx.ui.combo_2d_res = QComboBox()
    ctx.ui.combo_2d_res.addItems([
        "720p (HD - Recommended)", "512p (Fast)", "1080p (Full HD)", "Original"])
    ctx.ui.combo_2d_res.setToolTip(
        "Size the clip is loaded at. CoTracker itself always samples every block\n"
        "at its own fixed model resolution, so this does not make the tracking more\n"
        "precise - it only changes how much detail survives the downscale and how\n"
        "much RAM, VRAM upload and load time the clip costs. 720p suits most clips.")
    form_row(abody, "Resolution", ctx.ui.combo_2d_res)

    ctx.ui.spin_min_conf = QDoubleSpinBox()
    ctx.ui.spin_min_conf.setRange(0.1, 0.99)
    ctx.ui.spin_min_conf.setValue(0.70)
    ctx.ui.spin_min_conf.setSingleStep(0.05)
    ctx.ui.spin_min_conf.setToolTip(
        "Minimum tracking confidence a sample must reach to count as visible.\n"
        "Higher = fewer but more reliable points.")
    ctx.ui.spin_min_conf.valueChanged.connect(ctx.layers.on_min_conf_changed)
    form_row(abody, "Confidence", ctx.ui.spin_min_conf)

    ctx.ui.combo_2d_model = QComboBox()
    ctx.ui.combo_2d_model.addItems([
        "CoTracker3 Offline (High Accuracy)", "CoTracker3 Online (Streaming)"])
    ctx.ui.combo_2d_model.setToolTip("Offline sees the whole window at once and is more accurate.")
    form_row(abody, "Model", ctx.ui.combo_2d_model)

    ctx.ui.chk_vram_chunk = QCheckBox("Auto VRAM chunking (prevent out-of-memory)")
    ctx.ui.chk_vram_chunk.setChecked(True)
    ctx.ui.chk_vram_chunk.setToolTip(
        "Sends the clip to the GPU a few frames at a time, sized to the free VRAM.\n"
        "Turn off only if you have VRAM to spare - the whole clip then goes up at once.")
    abody.addWidget(ctx.ui.chk_vram_chunk)

    # Some shots only have a clean reference at the tail: the feature enters
    # frame late, or the plate softens towards the head. Tracking from frame 1
    # is then tracking from the worst frame in the shot.
    ctx.ui.chk_track_backwards = QCheckBox("Track backwards (last frame first)")
    ctx.ui.chk_track_backwards.setToolTip(
        "Runs the whole layer over the reversed frame range and flips the result\n"
        "back, for shots whose good reference is at the end. The exports still\n"
        "start at the head - only the direction the tracker works in changes.\n"
        "Manual points are tracked from the frame you placed them on either way.")
    abody.addWidget(ctx.ui.chk_track_backwards)
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
    ctx.ui.canvas_2d = VideoPointPickerCanvas()
    ctx.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    ctx.canvas.file_dropped.connect(ctx.import_dropped_video)
    ctx.canvas.point_added.connect(ctx.on_point_added_on_canvas)
    ctx.canvas.masks_changed.connect(ctx.layers.refresh_layer_list)
    # An undo puts masks, points, corrections or the range back without going
    # through any of the handlers that normally redraw the chips, so the canvas
    # says what changed and the chips follow it.
    ctx.canvas.masks_changed.connect(ctx.player.update_keyframe_status)
    ctx.canvas.corrections_changed.connect(ctx.correction.refresh)
    ctx.canvas.range_changed.connect(ctx.player.sync_range_status)
    ctx.canvas.tracked_point_moved.connect(ctx.correction.on_tracked_point_moved)
    ctx.canvas.retrack_requested.connect(ctx.correction.on_retrack_requested)
    ctx.canvas.correction_cleared.connect(ctx.correction.on_correction_cleared)
    rl.addWidget(ctx.canvas, 1)

    # ---- Transport ------------------------------------------------------
    # Built, owned and wired by gui/player.py; the tab only places it.
    rl.addWidget(ctx.player.build_transport_bar())

    # ---- Roto / range / view --------------------------------------------
    k_frame, kbar = strip(spacing=6)

    # cluster 1 - keyframes
    ctx.player.add_keys_cluster(kbar)

    divider(kbar, vertical=True)

    # cluster 2 - tracking range
    ctx.player.add_range_cluster(kbar)

    divider(kbar, vertical=True)

    # cluster 3 - viewport modes
    kbar.addWidget(group_label("View"))
    ctx.ui.btn_toggle_matte = make_button("Matte", "Show the live mask overlay  (M)", "toggle", checkable=True)
    ctx.ui.btn_toggle_matte.setChecked(True)
    ctx.ui.btn_toggle_matte.toggled.connect(ctx.toggle_canvas_matte_overlay)

    ctx.ui.btn_toggle_alpha = make_button("Alpha", "High-contrast alpha channel view  (A)", "toggle", checkable=True)
    ctx.ui.btn_toggle_alpha.toggled.connect(ctx.toggle_canvas_alpha_mode)

    ctx.ui.btn_toggle_loupe = make_button("Loupe", "Sub-pixel magnifier  (hold Ctrl)", "toggle", checkable=True)
    ctx.ui.btn_toggle_loupe.toggled.connect(ctx.toggle_canvas_loupe)

    for wdg in (ctx.ui.btn_toggle_matte, ctx.ui.btn_toggle_alpha, ctx.ui.btn_toggle_loupe):
        kbar.addWidget(wdg)

    divider(kbar, vertical=True)

    # cluster 4 - fixing a drifting track (roadmap 2.1)
    ctx.correction.add_fix_cluster(kbar)

    kbar.addStretch(1)
    # Three clusters of controls do not fit a narrow window; scroll rather than clip.
    rl.addWidget(scrollable_strip(k_frame))

    # ---- Console --------------------------------------------------------
    log_card, logbody = card("Tracker Console")
    btn_clear_2d_log = make_button("Clear", "Empty the console", "compact")
    btn_clear_2d_log.setFixedWidth(64)
    log_card.header_layout.addWidget(btn_clear_2d_log)

    ctx.ui.log_2d_text = QTextEdit()
    ctx.ui.log_2d_text.setReadOnly(True)
    ctx.ui.log_2d_text.setMinimumHeight(96)
    ctx.ui.log_2d_text.setMaximumHeight(200)
    btn_clear_2d_log.clicked.connect(ctx.ui.log_2d_text.clear)
    logbody.addWidget(ctx.ui.log_2d_text)

    ctx.ui.progress_2d = QProgressBar()
    ctx.ui.progress_2d.setValue(0)
    ctx.ui.progress_2d.setFormat("Ready")
    logbody.addWidget(ctx.ui.progress_2d)
    rl.addWidget(log_card)

    # ---- Actions --------------------------------------------------------
    act_card, actbody = card(None)
    actbody.setSpacing(7)

    run_row = QHBoxLayout()
    run_row.setSpacing(7)
    ctx.ui.btn_start_2d = QPushButton("▶   Run 2D Point Tracking")
    ctx.ui.btn_start_2d.setObjectName("primary")
    ctx.ui.btn_start_2d.setToolTip("Track every layer through the current range")
    ctx.ui.btn_start_2d.clicked.connect(ctx.start_tracking_2d)

    ctx.ui.btn_stop_2d = QPushButton("Cancel")
    ctx.ui.btn_stop_2d.setObjectName("danger")
    ctx.ui.btn_stop_2d.setToolTip("Stop the running track after the current block of frames")
    ctx.ui.btn_stop_2d.setFixedWidth(100)
    ctx.ui.btn_stop_2d.setEnabled(False)
    ctx.ui.btn_stop_2d.clicked.connect(ctx.stop_tracking_2d)

    run_row.addWidget(ctx.ui.btn_start_2d, 1)
    run_row.addWidget(ctx.ui.btn_stop_2d)
    actbody.addLayout(run_row)

    ctx.ui.btn_export_2d_nuke = make_button(
        "Nuke Tracker Node",
        "Copy the solved Tracker4 node to the clipboard for Ctrl+V in Nuke", "export")
    ctx.ui.btn_export_2d_nuke.clicked.connect(ctx.export_2d_for_nuke)

    ctx.ui.btn_load_overlay_player = make_button(
        "Play Overlay", "Load the rendered motion-trail video into the viewport", "export")
    ctx.ui.btn_load_overlay_player.clicked.connect(
        ctx.player.load_overlay_into_player)

    ctx.ui.btn_open_2d_dir = make_button(
        "Open Output", "Show the 2D track folder", "export")
    ctx.ui.btn_open_2d_dir.clicked.connect(ctx.media.open_2d_output_folder)

    # After a correction the delivered files no longer match the result the
    # artist is looking at, and nothing about rewriting them needs the GPU.
    ctx.ui.btn_reexport_2d = make_button(
        "Re-export 2D",
        "Write every 2D format again from the corrected result, without re-tracking",
        "export")
    ctx.ui.btn_reexport_2d.clicked.connect(ctx.correction.reexport_2d_result)

    button_row(actbody, [ctx.ui.btn_export_2d_nuke,
                         ctx.ui.btn_load_overlay_player,
                         ctx.ui.btn_open_2d_dir,
                         ctx.ui.btn_reexport_2d], compact=False)
    rl.addWidget(act_card)

    splitter.addWidget(right)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    # Combos on the right-hand side get the same treatment as the inspector.
    tame_combos(right, shrink=False)
    splitter.setSizes([430, 1000])
