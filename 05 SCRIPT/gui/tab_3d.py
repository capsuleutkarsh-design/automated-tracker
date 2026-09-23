"""
3D Camera Tracking tab.

Layout: a scrollable inspector on the left (preset -> solver -> options ->
integration, in the order you actually touch them), and on the right the media
pool over the console, with the run controls pinned to the bottom.

Built against the AppContext, not the window: every widget is registered
through `ctx.ui` under the same name the main window uses, and every button
runs a named method on `ctx`. gui/context.py documents what that allows.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton, QTextEdit,
    QProgressBar, QSplitter, QLineEdit,
)
from PySide6.QtCore import Qt

from gui.ui_kit import (
    tame_combos,
    card, form_row, button_row, checkbox_grid, inspector_scroll,
    make_button, hint_label,
)


def build_3d_tab(ctx, tab):
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
    ctx.ui.preset_combo = QComboBox()
    for name in ctx.presets.keys():
        ctx.ui.preset_combo.addItem(name)
    ctx.ui.preset_combo.setToolTip("Starting point tuned for a type of camera move.\n"
                                   "Every value below stays editable afterwards.")
    ctx.ui.preset_combo.currentTextChanged.connect(ctx.on_preset_changed)
    pbody.addWidget(ctx.ui.preset_combo)

    ctx.ui.preset_desc = QLabel()
    ctx.ui.preset_desc.setObjectName("hint")
    ctx.ui.preset_desc.setWordWrap(True)
    pbody.addWidget(ctx.ui.preset_desc)
    ins.addWidget(preset_card)

    # ---- Solver ---------------------------------------------------------
    solver_card, sbody = card("Camera & Solver")

    ctx.ui.combo_solver_engine = QComboBox()
    ctx.ui.combo_solver_engine.addItems([
        "Incremental SfM (Standard / All GPUs)",
        "Hierarchical Multi-Cluster Mapper (faster on long shots)",
    ])
    ctx.ui.combo_solver_engine.setToolTip(
        "Incremental registers frames one at a time - the most reliable choice.\n"
        "Hierarchical splits a long shot into clusters and merges them, which is\n"
        "faster on long takes but falls back to incremental if it fails."
    )
    form_row(sbody, "Solver engine", ctx.ui.combo_solver_engine)

    # First token of every item must be a real COLMAP --ImageReader.camera_model
    # name: tracker_gui.py passes currentText().split()[0] straight to COLMAP.
    ctx.ui.combo_cam = QComboBox()
    ctx.ui.combo_cam.addItems([
        "SIMPLE_RADIAL (Standard / Recommended)",
        "RADIAL (Two radial terms)",
        "OPENCV (Radial + tangential)",
        "OPENCV_FISHEYE (Wide-Angle / Action Cam)",
        "PINHOLE (Zero Distortion)",
        "FULL_OPENCV (Heavy distortion / Drone Fisheye)",
    ])
    ctx.ui.combo_cam.setToolTip("Lens distortion model COLMAP solves for.")
    form_row(sbody, "Camera model", ctx.ui.combo_cam)

    ctx.ui.spin_tri = QDoubleSpinBox()
    ctx.ui.spin_tri.setRange(0.5, 30.0)
    ctx.ui.spin_tri.setSingleStep(0.5)
    ctx.ui.spin_tri.setValue(2.5)
    ctx.ui.spin_tri.setSuffix("  °")
    ctx.ui.spin_tri.setToolTip(
        "Parallax the first image pair must show before the solve starts.\n"
        "Lower it for walking and dolly shots; raise it for orbits."
    )
    form_row(sbody, "Min triangulation", ctx.ui.spin_tri)

    ctx.ui.spin_overlap = QSpinBox()
    ctx.ui.spin_overlap.setRange(5, 100)
    ctx.ui.spin_overlap.setValue(35)
    ctx.ui.spin_overlap.setSuffix("  frames")
    ctx.ui.spin_overlap.setToolTip("How many neighbouring frames each frame is matched against.")
    form_row(sbody, "Matching overlap", ctx.ui.spin_overlap)

    ctx.ui.spin_inliers = QSpinBox()
    ctx.ui.spin_inliers.setRange(15, 500)
    ctx.ui.spin_inliers.setValue(40)
    ctx.ui.spin_inliers.setToolTip("Minimum verified matches needed to accept the starting pair.")
    form_row(sbody, "Min initial inliers", ctx.ui.spin_inliers)

    ctx.ui.spin_step = QSpinBox()
    ctx.ui.spin_step.setRange(1, 10)
    ctx.ui.spin_step.setValue(1)
    ctx.ui.spin_step.setToolTip(
        "1 uses every frame. 2 uses every second frame, which widens the baseline\n"
        "on slow moves - exported frame numbers are adjusted to match."
    )
    form_row(sbody, "Frame step", ctx.ui.spin_step)

    ctx.ui.spin_start_frame_3d = QSpinBox()
    ctx.ui.spin_start_frame_3d.setRange(0, 9999999)
    ctx.ui.spin_start_frame_3d.setValue(1)
    ctx.ui.spin_start_frame_3d.setToolTip("Frame number the shot's first frame sits on in your timeline.\nLeave at 1 unless the plate is numbered from something else -\na 1001-1200 sequence needs 1001, or the exported keys land\noff the end of your comp and read as a single static value.")
    form_row(sbody, "Timeline start", ctx.ui.spin_start_frame_3d)

    # The rate belongs to the shot, not to a tab, so this only shows what the
    # 2D tab holds - one field the artist can get out of step with the other
    # would be worse than no field at all.
    ctx.ui.spin_fps_3d = QDoubleSpinBox()
    ctx.ui.spin_fps_3d.setRange(1.0, 240.0)
    ctx.ui.spin_fps_3d.setDecimals(3)
    ctx.ui.spin_fps_3d.setValue(24.0)
    ctx.ui.spin_fps_3d.setReadOnly(True)
    ctx.ui.spin_fps_3d.setButtonSymbols(QDoubleSpinBox.NoButtons)
    ctx.ui.spin_fps_3d.setFocusPolicy(Qt.NoFocus)
    ctx.ui.spin_fps_3d.setToolTip(
        "Frame rate of the shot, shared with the 2D tab and set there.\n"
        "Read from the file for a video; for an image sequence it is whatever\n"
        "the 2D tab's Frame rate field says.\n"
        "Common rates: 23.976, 24, 25, 29.97, 30, 48, 50, 60.")
    form_row(sbody, "Frame rate", ctx.ui.spin_fps_3d)

    # Pixel aspect belongs to the plate, like the frame rate, so it sits with
    # the other things read off the clip rather than with the delivery options.
    ctx.ui.spin_pixel_aspect = QDoubleSpinBox()
    ctx.ui.spin_pixel_aspect.setRange(0.1, 4.0)
    ctx.ui.spin_pixel_aspect.setDecimals(4)
    ctx.ui.spin_pixel_aspect.setSingleStep(0.01)
    ctx.ui.spin_pixel_aspect.setValue(1.0)
    ctx.ui.spin_pixel_aspect.setToolTip(
        "Shape of one pixel: width divided by height. Leave it at 1.0 for any\n"
        "ordinary plate.\n"
        "An anamorphic or otherwise squeezed plate has non-square pixels - a 2x\n"
        "anamorphic delivered as 1920x1080 is really a 3840x1080 image squeezed\n"
        "sideways - and if the tool is not told, the solve fits a lens that does\n"
        "not exist and the exported camera is wrong in both focal length and\n"
        "framing. Read from the file for a video; type it for a sequence.\n"
        "Common values: 1.0 square, 2.0 anamorphic scope, 1.333 HDV, 0.91 NTSC D1.")
    form_row(sbody, "Pixel aspect", ctx.ui.spin_pixel_aspect)

    sbody.addWidget(hint_label(
        "Auto-filled from the file numbering when you select an image sequence."))
    ins.addWidget(solver_card)

    # ---- Options --------------------------------------------------------
    opt_card, obody = card("Solve Options")

    ctx.ui.chk_single_cam = QCheckBox("Single camera")
    ctx.ui.chk_single_cam.setChecked(True)
    ctx.ui.chk_single_cam.setToolTip("All frames share one fixed focal length. Turn off for a zoom.")

    ctx.ui.chk_ba_refine = QCheckBox("Refine distortion")
    ctx.ui.chk_ba_refine.setChecked(True)
    ctx.ui.chk_ba_refine.setToolTip(
        "Lets bundle adjustment solve focal length and distortion instead of\n"
        "trusting the initial guess. Turn off if you know the exact lens."
    )

    ctx.ui.chk_gpu = QCheckBox("GPU features")
    ctx.ui.chk_gpu.setChecked(True)
    ctx.ui.chk_gpu.setToolTip("CUDA SIFT. Much faster than the CPU path.")

    ctx.ui.chk_caspar_ba = QCheckBox("GPU bundle adj.")
    ctx.ui.chk_caspar_ba.setChecked(True)
    ctx.ui.chk_caspar_ba.setToolTip(
        "Runs COLMAP bundle adjustment on the GPU (--Mapper.ba_use_gpu).\n"
        "Needs a CUDA GPU; turn off to solve on the CPU."
    )

    ctx.ui.chk_mesh_gen = QCheckBox("Environment mesh")
    ctx.ui.chk_mesh_gen.setChecked(True)
    ctx.ui.chk_mesh_gen.setToolTip("Meshes the sparse cloud for a rough collision surface.")

    checkbox_grid(obody, [
        ctx.ui.chk_single_cam, ctx.ui.chk_gpu,
        ctx.ui.chk_ba_refine, ctx.ui.chk_caspar_ba,
        ctx.ui.chk_mesh_gen,
    ], columns=2)
    ins.addWidget(opt_card)

    # ---- Scene setup: scale, ground, origin ------------------------------
    # The card, the picking and the transform it builds live in
    # gui/scene_setup.py; the tab only decides where it sits.
    ins.addWidget(ctx.scene_setup.build_card())

    # ---- Lens delivery ---------------------------------------------------
    lens_card, lnbody = card("Lens & Delivery")

    ctx.ui.chk_write_undistort = QCheckBox("Write undistorted plate and STMaps")
    ctx.ui.chk_write_undistort.setToolTip(
        "Comp cannot use k1 and k2 in a text file, so this writes what it can use:\n"
        "  • an undistorted plate sequence, straight-lined by the solved lens\n"
        "  • a pinhole camera that matches that plate exactly\n"
        "  • two 32-bit STMaps - undistort to work on, redistort to hand back -\n"
        "    so the render lands back on the original plate, pixel for pixel.\n"
        "Costs a full image sequence of disk and a minute or two of writing.")
    lnbody.addWidget(ctx.ui.chk_write_undistort)

    ctx.ui.spin_overscan = QSpinBox()
    ctx.ui.spin_overscan.setRange(0, 50)
    ctx.ui.spin_overscan.setValue(0)
    ctx.ui.spin_overscan.setSuffix("  %")
    ctx.ui.spin_overscan.setToolTip(
        "Extra canvas around the undistorted plate, as a percentage of its size.\n"
        "Straightening a barrel lens pushes the corners of the frame OUTWARDS, and\n"
        "whatever falls outside the undistorted frame is simply lost. A mild lens\n"
        "needs 5-10 %; a strong wide-angle barrel needs nearer 40 % before its\n"
        "corners fit, which is why this goes to 50 rather than offering presets.\n"
        "The same value is used for both STMaps, so they stay a matched pair.")
    form_row(lnbody, "Overscan", ctx.ui.spin_overscan)

    lnbody.addWidget(hint_label(
        "Both are written by the next solve, or by Re-export below on a solve you already have."))

    ctx.ui.btn_reexport = make_button(
        "Re-export This Solve",
        "Write a fresh export folder from the existing solve using the scene transform,\n"
        "overscan, undistortion and pixel aspect set here - without solving again.",
        "compact")
    ctx.ui.btn_reexport.clicked.connect(ctx.reexport_current_solve)
    lnbody.addWidget(ctx.ui.btn_reexport)
    ins.addWidget(lens_card)

    # ---- Blender --------------------------------------------------------
    blend_card, bbody = card("Blender Integration")
    brow = QHBoxLayout()
    brow.setSpacing(6)
    ctx.ui.txt_blender_path = QLineEdit()
    ctx.ui.txt_blender_path.setPlaceholderText("Auto-detected - or point at blender.exe")
    ctx.ui.txt_blender_path.setToolTip(
        "Used to bake camera_track.abc and .blend automatically after a solve.\n"
        "Leave empty to let the app find Blender itself."
    )
    btn_browse_blender = make_button("Browse", "Locate blender.exe")
    btn_browse_blender.setFixedWidth(84)
    btn_browse_blender.clicked.connect(ctx.browse_blender_exe)
    brow.addWidget(ctx.ui.txt_blender_path, 1)
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
    # The table, its buttons and the folder scan behind them live in
    # gui/media_panel.py; the tab only decides where it sits.
    rl.addWidget(ctx.media.build_card(), 3)

    # ---- Console --------------------------------------------------------
    log_card, lbody = card("Solver Console")
    # COLMAP's INFO stream is hundreds of lines a stage and used to bury the
    # app's own warnings, so it now goes to the log file and this puts it back
    # on screen when a run needs diagnosing (2.4).
    ctx.ui.chk_show_engine_output = QCheckBox("Show engine output")
    ctx.ui.chk_show_engine_output.setToolTip(
        "Print COLMAP's own output in this console as well.\n"
        "It is always written to the app log file; off, the console keeps the\n"
        "stage lines, warnings, errors and summaries this app writes - and any\n"
        "engine line that reports a real problem still comes through.")
    ctx.ui.chk_show_engine_output.toggled.connect(ctx.on_show_engine_output)
    log_card.header_layout.addWidget(ctx.ui.chk_show_engine_output)

    btn_clear_3d_log = make_button("Clear", "Empty the console", "compact")
    btn_clear_3d_log.setFixedWidth(64)
    log_card.header_layout.addWidget(btn_clear_3d_log)

    ctx.ui.log_text = QTextEdit()
    ctx.ui.log_text.setReadOnly(True)
    ctx.ui.log_text.setMinimumHeight(120)
    btn_clear_3d_log.clicked.connect(ctx.ui.log_text.clear)
    lbody.addWidget(ctx.ui.log_text, 1)

    ctx.ui.progress_bar = QProgressBar()
    ctx.ui.progress_bar.setValue(0)
    ctx.ui.progress_bar.setFormat("Ready")
    lbody.addWidget(ctx.ui.progress_bar)
    rl.addWidget(log_card, 2)

    # ---- Actions --------------------------------------------------------
    act_card, abody = card(None)
    abody.setSpacing(7)

    # Every shot carries its own saved settings now, and a batch uses them
    # (2.3). This is the exception: the artist who really does want one lens
    # model or one frame step across the whole selection.
    ctx.ui.chk_force_current_3d = QCheckBox("Apply the settings on screen to every selected shot")
    ctx.ui.chk_force_current_3d.setToolTip(
        "Off, each selected shot is solved with the settings saved in its own\n"
        "project file, and only a shot that has never been saved uses what is\n"
        "on screen.\n"
        "On, this ignores the shots' saved settings and solves all of them with\n"
        "the settings in these controls.")
    abody.addWidget(ctx.ui.chk_force_current_3d)

    run_row = QHBoxLayout()
    run_row.setSpacing(7)
    ctx.ui.btn_start_3d = QPushButton("▶   Start 3D Camera Tracking")
    ctx.ui.btn_start_3d.setObjectName("primary")
    ctx.ui.btn_start_3d.setToolTip("Solve camera motion for the selected shots")
    ctx.ui.btn_start_3d.clicked.connect(ctx.start_tracking_3d)

    ctx.ui.btn_stop_3d = QPushButton("Cancel")
    ctx.ui.btn_stop_3d.setObjectName("danger")
    ctx.ui.btn_stop_3d.setToolTip("Stop the solve after the current COLMAP stage")
    ctx.ui.btn_stop_3d.setFixedWidth(100)
    ctx.ui.btn_stop_3d.setEnabled(False)
    ctx.ui.btn_stop_3d.clicked.connect(ctx.stop_tracking_3d)

    run_row.addWidget(ctx.ui.btn_start_3d, 1)
    run_row.addWidget(ctx.ui.btn_stop_3d)
    abody.addLayout(run_row)

    ctx.ui.btn_export_3d_blender = make_button(
        "Blender  (.abc)", "Bake and reveal camera_track.abc and the import script", "export")
    ctx.ui.btn_export_3d_blender.clicked.connect(ctx.export_3d_for_blender)

    ctx.ui.btn_export_3d_nuke = make_button(
        "Nuke  (.nk / .chan)", "Copy the 3D camera and point cloud to the clipboard for Ctrl+V in Nuke", "export")
    ctx.ui.btn_export_3d_nuke.clicked.connect(ctx.export_3d_for_nuke)

    ctx.ui.btn_open_3d_exports = make_button(
        "Open Output", "Show the solved scene folder", "export")
    ctx.ui.btn_open_3d_exports.clicked.connect(ctx.media.open_3d_output_folder)

    button_row(abody, [ctx.ui.btn_export_3d_blender,
                       ctx.ui.btn_export_3d_nuke,
                       ctx.ui.btn_open_3d_exports], compact=False)
    rl.addWidget(act_card)

    splitter.addWidget(right)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    # Combos on the right-hand side get the same treatment as the inspector.
    tame_combos(right, shrink=False)
    splitter.setSizes([430, 1000])
