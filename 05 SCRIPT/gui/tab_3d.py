"""
3D Camera Tracking tab.

Layout: a scrollable inspector on the left (preset -> solver -> options ->
integration, in the order you actually touch them), and on the right the media
pool over the console, with the run controls pinned to the bottom.

Every widget is attached to `win` under the same name the main window uses, so
the handlers in tracker_gui.py are unchanged.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton, QTextEdit,
    QProgressBar, QTableWidget, QHeaderView, QSplitter,
    QLineEdit, QAbstractItemView,
)
from PySide6.QtCore import Qt

from gui.ui_kit import (
    tame_combos,
    card, form_row, button_row, checkbox_grid, inspector_scroll,
    make_button, hint_label, group_label, divider,
)


def _wrap_row(layout):
    """A layout as a widget, so a pair of fields can share one form_row label."""
    holder = QWidget()
    holder.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return holder


def build_3d_tab(win, tab, presets):
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

    # ---- Shot preset ----------------------------------------------------
    preset_card, pbody = card("Shot Preset")
    win.preset_combo = QComboBox()
    for name in presets.keys():
        win.preset_combo.addItem(name)
    win.preset_combo.setToolTip("Starting point tuned for a type of camera move.\n"
                                "Every value below stays editable afterwards.")
    win.preset_combo.currentTextChanged.connect(win._on_preset_changed)
    pbody.addWidget(win.preset_combo)

    win.preset_desc = QLabel()
    win.preset_desc.setObjectName("hint")
    win.preset_desc.setWordWrap(True)
    pbody.addWidget(win.preset_desc)
    ins.addWidget(preset_card)

    # ---- Solver ---------------------------------------------------------
    solver_card, sbody = card("Camera & Solver")

    win.combo_solver_engine = QComboBox()
    win.combo_solver_engine.addItems([
        "Incremental SfM (Standard / All GPUs)",
        "Hierarchical Multi-Cluster Mapper (faster on long shots)",
    ])
    win.combo_solver_engine.setToolTip(
        "Incremental registers frames one at a time - the most reliable choice.\n"
        "Hierarchical splits a long shot into clusters and merges them, which is\n"
        "faster on long takes but falls back to incremental if it fails."
    )
    form_row(sbody, "Solver engine", win.combo_solver_engine)

    # First token of every item must be a real COLMAP --ImageReader.camera_model
    # name: tracker_gui.py passes currentText().split()[0] straight to COLMAP.
    win.combo_cam = QComboBox()
    win.combo_cam.addItems([
        "SIMPLE_RADIAL (Standard / Recommended)",
        "RADIAL (Two radial terms)",
        "OPENCV (Radial + tangential)",
        "OPENCV_FISHEYE (Wide-Angle / Action Cam)",
        "PINHOLE (Zero Distortion)",
        "FULL_OPENCV (Heavy distortion / Drone Fisheye)",
    ])
    win.combo_cam.setToolTip("Lens distortion model COLMAP solves for.")
    form_row(sbody, "Camera model", win.combo_cam)

    win.spin_tri = QDoubleSpinBox()
    win.spin_tri.setRange(0.5, 30.0)
    win.spin_tri.setSingleStep(0.5)
    win.spin_tri.setValue(2.5)
    win.spin_tri.setSuffix("  °")
    win.spin_tri.setToolTip(
        "Parallax the first image pair must show before the solve starts.\n"
        "Lower it for walking and dolly shots; raise it for orbits."
    )
    form_row(sbody, "Min triangulation", win.spin_tri)

    win.spin_overlap = QSpinBox()
    win.spin_overlap.setRange(5, 100)
    win.spin_overlap.setValue(35)
    win.spin_overlap.setSuffix("  frames")
    win.spin_overlap.setToolTip("How many neighbouring frames each frame is matched against.")
    form_row(sbody, "Matching overlap", win.spin_overlap)

    win.spin_inliers = QSpinBox()
    win.spin_inliers.setRange(15, 500)
    win.spin_inliers.setValue(40)
    win.spin_inliers.setToolTip("Minimum verified matches needed to accept the starting pair.")
    form_row(sbody, "Min initial inliers", win.spin_inliers)

    win.spin_step = QSpinBox()
    win.spin_step.setRange(1, 10)
    win.spin_step.setValue(1)
    win.spin_step.setToolTip(
        "1 uses every frame. 2 uses every second frame, which widens the baseline\n"
        "on slow moves - exported frame numbers are adjusted to match."
    )
    form_row(sbody, "Frame step", win.spin_step)

    win.spin_start_frame_3d = QSpinBox()
    win.spin_start_frame_3d.setRange(0, 9999999)
    win.spin_start_frame_3d.setValue(1)
    win.spin_start_frame_3d.setToolTip("Frame number the shot's first frame sits on in your timeline.\nLeave at 1 unless the plate is numbered from something else -\na 1001-1200 sequence needs 1001, or the exported keys land\noff the end of your comp and read as a single static value.")
    form_row(sbody, "Timeline start", win.spin_start_frame_3d)

    # The rate belongs to the shot, not to a tab, so this only shows what the
    # 2D tab holds - one field the artist can get out of step with the other
    # would be worse than no field at all.
    win.spin_fps_3d = QDoubleSpinBox()
    win.spin_fps_3d.setRange(1.0, 240.0)
    win.spin_fps_3d.setDecimals(3)
    win.spin_fps_3d.setValue(24.0)
    win.spin_fps_3d.setReadOnly(True)
    win.spin_fps_3d.setButtonSymbols(QDoubleSpinBox.NoButtons)
    win.spin_fps_3d.setFocusPolicy(Qt.NoFocus)
    win.spin_fps_3d.setToolTip(
        "Frame rate of the shot, shared with the 2D tab and set there.\n"
        "Read from the file for a video; for an image sequence it is whatever\n"
        "the 2D tab's Frame rate field says.\n"
        "Common rates: 23.976, 24, 25, 29.97, 30, 48, 50, 60.")
    form_row(sbody, "Frame rate", win.spin_fps_3d)

    # Pixel aspect belongs to the plate, like the frame rate, so it sits with
    # the other things read off the clip rather than with the delivery options.
    win.spin_pixel_aspect = QDoubleSpinBox()
    win.spin_pixel_aspect.setRange(0.1, 4.0)
    win.spin_pixel_aspect.setDecimals(4)
    win.spin_pixel_aspect.setSingleStep(0.01)
    win.spin_pixel_aspect.setValue(1.0)
    win.spin_pixel_aspect.setToolTip(
        "Shape of one pixel: width divided by height. Leave it at 1.0 for any\n"
        "ordinary plate.\n"
        "An anamorphic or otherwise squeezed plate has non-square pixels - a 2x\n"
        "anamorphic delivered as 1920x1080 is really a 3840x1080 image squeezed\n"
        "sideways - and if the tool is not told, the solve fits a lens that does\n"
        "not exist and the exported camera is wrong in both focal length and\n"
        "framing. Read from the file for a video; type it for a sequence.\n"
        "Common values: 1.0 square, 2.0 anamorphic scope, 1.333 HDV, 0.91 NTSC D1.")
    form_row(sbody, "Pixel aspect", win.spin_pixel_aspect)

    sbody.addWidget(hint_label(
        "Auto-filled from the file numbering when you select an image sequence."))
    ins.addWidget(solver_card)

    # ---- Options --------------------------------------------------------
    opt_card, obody = card("Solve Options")

    win.chk_single_cam = QCheckBox("Single camera")
    win.chk_single_cam.setChecked(True)
    win.chk_single_cam.setToolTip("All frames share one fixed focal length. Turn off for a zoom.")

    win.chk_ba_refine = QCheckBox("Refine distortion")
    win.chk_ba_refine.setChecked(True)
    win.chk_ba_refine.setToolTip(
        "Lets bundle adjustment solve focal length and distortion instead of\n"
        "trusting the initial guess. Turn off if you know the exact lens."
    )

    win.chk_gpu = QCheckBox("GPU features")
    win.chk_gpu.setChecked(True)
    win.chk_gpu.setToolTip("CUDA SIFT. Much faster than the CPU path.")

    win.chk_caspar_ba = QCheckBox("GPU bundle adj.")
    win.chk_caspar_ba.setChecked(True)
    win.chk_caspar_ba.setToolTip(
        "Runs COLMAP bundle adjustment on the GPU (--Mapper.ba_use_gpu).\n"
        "Needs a CUDA GPU; turn off to solve on the CPU."
    )

    win.chk_mesh_gen = QCheckBox("Environment mesh")
    win.chk_mesh_gen.setChecked(True)
    win.chk_mesh_gen.setToolTip("Meshes the sparse cloud for a rough collision surface.")

    checkbox_grid(obody, [
        win.chk_single_cam, win.chk_gpu,
        win.chk_ba_refine, win.chk_caspar_ba,
        win.chk_mesh_gen,
    ], columns=2)
    ins.addWidget(opt_card)

    # ---- Scene setup: scale, ground, origin ------------------------------
    # Everything here works by picking the solve's own 3D points on the 2D
    # tab's canvas, so the card stays disabled until there is a solve to pick
    # from and _refresh_scene_setup() says why.
    win.scene_setup_card, scbody = card("Scene Setup")
    win.scene_setup_card.setEnabled(False)

    win.lbl_scene_points = hint_label("Solve this shot first.")
    scbody.addWidget(win.lbl_scene_points)

    # --- scale
    scbody.addWidget(group_label("Scale"))
    win.btn_pick_scale = make_button(
        "Pick 2 points", "Arm picking, then click two solved points on the 2D tab whose\n"
                         "real distance apart you know.", "compact", checkable=True)
    win.btn_pick_scale.toggled.connect(lambda on: win._on_scene_pick_toggled("scale", on))
    win.lbl_scale_picks = QLabel("0 / 2")
    win.lbl_scale_picks.setObjectName("valueChip")
    win.lbl_scale_picks.setAlignment(Qt.AlignCenter)
    win.lbl_scale_picks.setMinimumWidth(56)
    form_row(scbody, "Pick", win.btn_pick_scale, hint=win.lbl_scale_picks)

    dist_row = QHBoxLayout()
    dist_row.setSpacing(6)
    win.spin_scale_distance = QDoubleSpinBox()
    win.spin_scale_distance.setRange(0.0, 100000.0)
    win.spin_scale_distance.setDecimals(4)
    win.spin_scale_distance.setSingleStep(0.1)
    win.spin_scale_distance.setValue(1.0)
    win.spin_scale_distance.setToolTip(
        "The real distance between the two picked points, measured on set.\n"
        "Leave it at zero to leave the solve's arbitrary scale alone.")
    win.combo_scale_unit = QComboBox()
    win.combo_scale_unit.addItems(["metres", "centimetres", "feet", "inches"])
    win.combo_scale_unit.setToolTip(
        "Unit of the distance above. The scene itself is always built in metres,\n"
        "which is what every DCC's units default to.")
    win.combo_scale_unit.setFixedWidth(116)
    dist_row.addWidget(win.spin_scale_distance, 1)
    dist_row.addWidget(win.combo_scale_unit)
    form_row(scbody, "Real distance", _wrap_row(dist_row))

    # --- ground
    scbody.addWidget(group_label("Ground"))
    win.btn_pick_ground = make_button(
        "Pick 3+ points", "Arm picking, then click three or more solved points that lie\n"
                          "on the floor. They end up level, on Y = 0.", "compact", checkable=True)
    win.btn_pick_ground.toggled.connect(lambda on: win._on_scene_pick_toggled("ground", on))
    win.lbl_ground_picks = QLabel("0 / 3")
    win.lbl_ground_picks.setObjectName("valueChip")
    win.lbl_ground_picks.setAlignment(Qt.AlignCenter)
    win.lbl_ground_picks.setMinimumWidth(56)
    form_row(scbody, "Pick", win.btn_pick_ground, hint=win.lbl_ground_picks)

    win.chk_auto_ground = QCheckBox("Use the auto-fitted plane")
    win.chk_auto_ground.setToolTip(
        "Level the floor on the dominant plane the solver already found in the\n"
        "point cloud, instead of on points you pick. Quick, and right whenever\n"
        "the floor is the biggest flat thing in the shot.")
    scbody.addWidget(win.chk_auto_ground)

    # --- origin
    scbody.addWidget(group_label("Origin"))
    win.btn_pick_origin = make_button(
        "Pick 1 point", "Arm picking, then click the solved point that should become\n"
                        "0, 0, 0 in your scene.", "compact", checkable=True)
    win.btn_pick_origin.toggled.connect(lambda on: win._on_scene_pick_toggled("origin", on))
    win.lbl_origin_picks = QLabel("0 / 1")
    win.lbl_origin_picks.setObjectName("valueChip")
    win.lbl_origin_picks.setAlignment(Qt.AlignCenter)
    win.lbl_origin_picks.setMinimumWidth(56)
    form_row(scbody, "Pick", win.btn_pick_origin, hint=win.lbl_origin_picks)

    win.chk_origin_under_camera = QCheckBox("Ground under the camera on this frame")
    win.chk_origin_under_camera.setToolTip(
        "Put the origin on the floor directly below the camera on the frame the\n"
        "2D tab is showing - the usual choice when there is no obvious feature\n"
        "to build on. Needs a ground plane, picked or auto-fitted.")
    scbody.addWidget(win.chk_origin_under_camera)

    divider(scbody)

    win.lbl_scene_status = hint_label("No scene transform: the solve is in COLMAP's own units.")
    scbody.addWidget(win.lbl_scene_status)

    win.btn_scene_clear = make_button(
        "Clear Picks", "Forget the points picked so far", "compact")
    win.btn_scene_clear.clicked.connect(win._clear_scene_picks)
    win.btn_scene_apply = make_button(
        "Apply", "Build the transform from what is set above and save it with the shot", "compact")
    win.btn_scene_apply.clicked.connect(win._apply_scene_transform)
    win.btn_scene_reset = make_button(
        "Reset", "Drop the transform and go back to COLMAP's arbitrary world", "compact")
    win.btn_scene_reset.clicked.connect(win._reset_scene_transform)
    button_row(scbody, [win.btn_scene_clear, win.btn_scene_apply, win.btn_scene_reset])
    ins.addWidget(win.scene_setup_card)

    # ---- Lens delivery ---------------------------------------------------
    lens_card, lnbody = card("Lens & Delivery")

    win.chk_write_undistort = QCheckBox("Write undistorted plate and STMaps")
    win.chk_write_undistort.setToolTip(
        "Comp cannot use k1 and k2 in a text file, so this writes what it can use:\n"
        "  • an undistorted plate sequence, straight-lined by the solved lens\n"
        "  • a pinhole camera that matches that plate exactly\n"
        "  • two 32-bit STMaps - undistort to work on, redistort to hand back -\n"
        "    so the render lands back on the original plate, pixel for pixel.\n"
        "Costs a full image sequence of disk and a minute or two of writing.")
    lnbody.addWidget(win.chk_write_undistort)

    win.spin_overscan = QSpinBox()
    win.spin_overscan.setRange(0, 50)
    win.spin_overscan.setValue(0)
    win.spin_overscan.setSuffix("  %")
    win.spin_overscan.setToolTip(
        "Extra canvas around the undistorted plate, as a percentage of its size.\n"
        "Straightening a barrel lens pushes the corners of the frame OUTWARDS, and\n"
        "whatever falls outside the undistorted frame is simply lost. A mild lens\n"
        "needs 5-10 %; a strong wide-angle barrel needs nearer 40 % before its\n"
        "corners fit, which is why this goes to 50 rather than offering presets.\n"
        "The same value is used for both STMaps, so they stay a matched pair.")
    form_row(lnbody, "Overscan", win.spin_overscan)

    lnbody.addWidget(hint_label(
        "Both are written by the next solve, or by Re-export below on a solve you already have."))

    win.btn_reexport = make_button(
        "Re-export This Solve",
        "Write a fresh export folder from the existing solve using the scene transform,\n"
        "overscan, undistortion and pixel aspect set here - without solving again.",
        "compact")
    win.btn_reexport.clicked.connect(win._reexport_current_solve)
    lnbody.addWidget(win.btn_reexport)
    ins.addWidget(lens_card)

    # ---- Blender --------------------------------------------------------
    blend_card, bbody = card("Blender Integration")
    brow = QHBoxLayout()
    brow.setSpacing(6)
    win.txt_blender_path = QLineEdit()
    win.txt_blender_path.setPlaceholderText("Auto-detected - or point at blender.exe")
    win.txt_blender_path.setToolTip(
        "Used to bake camera_track.abc and .blend automatically after a solve.\n"
        "Leave empty to let the app find Blender itself."
    )
    btn_browse_blender = make_button("Browse", "Locate blender.exe")
    btn_browse_blender.setFixedWidth(84)
    btn_browse_blender.clicked.connect(win._browse_blender_exe)
    brow.addWidget(win.txt_blender_path, 1)
    brow.addWidget(btn_browse_blender)
    bbody.addLayout(brow)
    bbody.addWidget(hint_label(
        "Optional. Without it you still get the 1-click import script."))
    ins.addWidget(blend_card)

    ins.addStretch(1)
    splitter.addWidget(inspector_scroll(inspector))

    # =====================================================================
    # RIGHT - media pool, console, actions
    # =====================================================================
    right = QWidget()
    rl = QVBoxLayout(right)
    rl.setContentsMargins(6, 0, 0, 0)
    rl.setSpacing(10)

    # ---- Media pool -----------------------------------------------------
    media_card, mbody = card("Media Pool")

    btn_add = make_button("+ Add Media", "Copy video files into 02 VIDEOS", "compact")
    btn_add.clicked.connect(win._add_videos)
    btn_refresh = make_button("Refresh", "Rescan the media folder", "compact")
    btn_refresh.clicked.connect(win._refresh_videos)
    btn_open_videos = make_button("Open Folder", "Show 02 VIDEOS in Explorer", "compact")
    btn_open_videos.clicked.connect(win._open_videos_folder)
    for b in (btn_add, btn_refresh, btn_open_videos):
        media_card.header_layout.addWidget(b)

    win.table = QTableWidget(0, 3)
    win.table.setHorizontalHeaderLabels(["Item Name", "Size / Frames", "Tracking Status"])
    win.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    win.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    win.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
    win.table.horizontalHeader().setHighlightSections(False)
    win.table.verticalHeader().setVisible(False)
    win.table.setShowGrid(False)
    win.table.setAlternatingRowColors(False)
    win.table.setSelectionBehavior(QAbstractItemView.SelectRows)
    win.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    win.table.setMinimumHeight(150)
    win.table.setToolTip(
        "Select the shots to solve. With nothing selected, every shot in the\n"
        "folder is solved. Double-click a row to open its output."
    )
    win.table.itemSelectionChanged.connect(win._on_table_row_selected)
    win.table.itemDoubleClicked.connect(win._on_table_row_double_clicked)
    mbody.addWidget(win.table)
    mbody.addWidget(hint_label(
        "Select rows to solve only those shots — select nothing to solve all."))
    rl.addWidget(media_card, 3)

    # ---- Console --------------------------------------------------------
    log_card, lbody = card("Solver Console")
    # COLMAP's INFO stream is hundreds of lines a stage and used to bury the
    # app's own warnings, so it now goes to the log file and this puts it back
    # on screen when a run needs diagnosing (2.4).
    win.chk_show_engine_output = QCheckBox("Show engine output")
    win.chk_show_engine_output.setToolTip(
        "Print COLMAP's own output in this console as well.\n"
        "It is always written to the app log file; off, the console keeps the\n"
        "stage lines, warnings, errors and summaries this app writes - and any\n"
        "engine line that reports a real problem still comes through.")
    win.chk_show_engine_output.toggled.connect(win._on_show_engine_output)
    log_card.header_layout.addWidget(win.chk_show_engine_output)

    btn_clear_3d_log = make_button("Clear", "Empty the console", "compact")
    btn_clear_3d_log.setFixedWidth(64)
    log_card.header_layout.addWidget(btn_clear_3d_log)

    win.log_text = QTextEdit()
    win.log_text.setReadOnly(True)
    win.log_text.setMinimumHeight(120)
    btn_clear_3d_log.clicked.connect(win.log_text.clear)
    lbody.addWidget(win.log_text, 1)

    win.progress_bar = QProgressBar()
    win.progress_bar.setValue(0)
    win.progress_bar.setFormat("Ready")
    lbody.addWidget(win.progress_bar)
    rl.addWidget(log_card, 2)

    # ---- Actions --------------------------------------------------------
    act_card, abody = card(None)
    abody.setSpacing(7)

    # Every shot carries its own saved settings now, and a batch uses them
    # (2.3). This is the exception: the artist who really does want one lens
    # model or one frame step across the whole selection.
    win.chk_force_current_3d = QCheckBox("Apply the settings on screen to every selected shot")
    win.chk_force_current_3d.setToolTip(
        "Off, each selected shot is solved with the settings saved in its own\n"
        "project file, and only a shot that has never been saved uses what is\n"
        "on screen.\n"
        "On, this ignores the shots' saved settings and solves all of them with\n"
        "the settings in these controls.")
    abody.addWidget(win.chk_force_current_3d)

    run_row = QHBoxLayout()
    run_row.setSpacing(7)
    win.btn_start_3d = QPushButton("▶   Start 3D Camera Tracking")
    win.btn_start_3d.setObjectName("primary")
    win.btn_start_3d.setToolTip("Solve camera motion for the selected shots")
    win.btn_start_3d.clicked.connect(win._start_tracking_3d)

    win.btn_stop_3d = QPushButton("Cancel")
    win.btn_stop_3d.setObjectName("danger")
    win.btn_stop_3d.setToolTip("Stop the solve after the current COLMAP stage")
    win.btn_stop_3d.setFixedWidth(100)
    win.btn_stop_3d.setEnabled(False)
    win.btn_stop_3d.clicked.connect(win._stop_tracking_3d)

    run_row.addWidget(win.btn_start_3d, 1)
    run_row.addWidget(win.btn_stop_3d)
    abody.addLayout(run_row)

    win.btn_export_3d_blender = make_button(
        "Blender  (.abc)", "Bake and reveal camera_track.abc and the import script", "export")
    win.btn_export_3d_blender.clicked.connect(win._export_3d_for_blender)

    win.btn_export_3d_nuke = make_button(
        "Nuke  (.nk / .chan)", "Copy the 3D camera and point cloud to the clipboard for Ctrl+V in Nuke", "export")
    win.btn_export_3d_nuke.clicked.connect(win._export_3d_for_nuke)

    win.btn_open_3d_exports = make_button(
        "Open Output", "Show the solved scene folder", "export")
    win.btn_open_3d_exports.clicked.connect(win._open_3d_output_folder)

    button_row(abody, [win.btn_export_3d_blender,
                       win.btn_export_3d_nuke,
                       win.btn_open_3d_exports], compact=False)
    rl.addWidget(act_card)

    splitter.addWidget(right)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    # Combos on the right-hand side get the same treatment as the inspector.
    tame_combos(right, shrink=False)
    splitter.setSizes([430, 1000])
