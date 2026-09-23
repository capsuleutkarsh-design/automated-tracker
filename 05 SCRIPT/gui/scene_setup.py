"""
Scene setup: scale, ground and origin  (roadmap 1.4).

The artist sets all three by picking the solve's OWN 3D points on the 2D canvas
of the same clip. Everything here works in COLMAP's world, which is where
core.scene_transform and export_tools.colmap_pose_to start, and the transform
is built with up=COLMAP_UP because COLMAP's y points down.

The card sits on the 3D tab but the picking happens on the 2D one, which is why
this was the hardest part of the window to follow while it was spread through
it: the solve is loaded here, reprojected onto whatever frame the player is
showing, and the picks are indices into that one point cloud.

The transform this builds belongs to the shot, not to the panel - the solver,
the re-export and the project file all read it - so the window reads it back
off `panel.transform`.
"""

import logging
from pathlib import Path

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QWidget,
)

from core import project as project_file
from core import scene_transform
from core.media_pool import find_latest_output
from gui.canvas import project_solved_points
from gui.ui_kit import (
    button_row, card, divider, form_row, group_label, hint_label, make_button,
)

# The window's logger on purpose: an unreadable solve is a line in app.log the
# artist may send in, and moving this code must not move the name it is
# written under.
log = logging.getLogger("tracker_gui")


def _wrap_row(layout):
    """A layout as a widget, so a pair of fields can share one form_row label."""
    holder = QWidget()
    holder.setLayout(layout)
    layout.setContentsMargins(0, 0, 0, 0)
    return holder


class SceneSetupPanel:
    """
    The Scene Setup card, the solve behind it and the transform it builds.

    Built by the window before the 3D tab, which asks it for the card at the
    point it belongs in the inspector.
    """

    def __init__(self, ctx):
        self.ctx = ctx
        # `solve` is the newest solve for the shot in view - its
        # camera_track.json, its points in COLMAP's own world and a lookup from
        # timeline frame to solved camera - loaded only when a solve exists.
        # `picks` holds indices into those points, per purpose.
        self.solve = None
        self.transform = None
        self.picks = {"scale": [], "ground": [], "origin": []}
        self.pick_mode = None

    @property
    def canvas(self):
        return self.ctx.canvas

    def _register(self, name, widget):
        """Keep the widget here, and publish it under the name the app uses."""
        setattr(self, name, widget)
        setattr(self.ctx.ui, name, widget)

    # =====================================================================
    # THE CARD
    # =====================================================================
    def build_card(self):
        """
        The inspector card.

        Everything here works by picking the solve's own 3D points on the 2D
        tab's canvas, so the card stays disabled until there is a solve to pick
        from and refresh() says why.
        """
        self._register("scene_setup_card", card("Scene Setup")[0])
        scbody = self.scene_setup_card.body
        self.scene_setup_card.setEnabled(False)

        self._register("lbl_scene_points", hint_label("Solve this shot first."))
        scbody.addWidget(self.lbl_scene_points)

        # --- scale
        scbody.addWidget(group_label("Scale"))
        self._register("btn_pick_scale", make_button(
            "Pick 2 points", "Arm picking, then click two solved points on the 2D tab whose\n"
                             "real distance apart you know.", "compact", checkable=True))
        self.btn_pick_scale.toggled.connect(lambda on: self.on_scene_pick_toggled("scale", on))
        self._register("lbl_scale_picks", QLabel("0 / 2"))
        self.lbl_scale_picks.setObjectName("valueChip")
        self.lbl_scale_picks.setAlignment(Qt.AlignCenter)
        self.lbl_scale_picks.setMinimumWidth(56)
        form_row(scbody, "Pick", self.btn_pick_scale, hint=self.lbl_scale_picks)

        dist_row = QHBoxLayout()
        dist_row.setSpacing(6)
        self._register("spin_scale_distance", QDoubleSpinBox())
        self.spin_scale_distance.setRange(0.0, 100000.0)
        self.spin_scale_distance.setDecimals(4)
        self.spin_scale_distance.setSingleStep(0.1)
        self.spin_scale_distance.setValue(1.0)
        self.spin_scale_distance.setToolTip(
            "The real distance between the two picked points, measured on set.\n"
            "Leave it at zero to leave the solve's arbitrary scale alone.")
        self._register("combo_scale_unit", QComboBox())
        self.combo_scale_unit.addItems(["metres", "centimetres", "feet", "inches"])
        self.combo_scale_unit.setToolTip(
            "Unit of the distance above. The scene itself is always built in metres,\n"
            "which is what every DCC's units default to.")
        self.combo_scale_unit.setFixedWidth(116)
        dist_row.addWidget(self.spin_scale_distance, 1)
        dist_row.addWidget(self.combo_scale_unit)
        form_row(scbody, "Real distance", _wrap_row(dist_row))

        # --- ground
        scbody.addWidget(group_label("Ground"))
        self._register("btn_pick_ground", make_button(
            "Pick 3+ points", "Arm picking, then click three or more solved points that lie\n"
                              "on the floor. They end up level, on Y = 0.", "compact",
            checkable=True))
        self.btn_pick_ground.toggled.connect(lambda on: self.on_scene_pick_toggled("ground", on))
        self._register("lbl_ground_picks", QLabel("0 / 3"))
        self.lbl_ground_picks.setObjectName("valueChip")
        self.lbl_ground_picks.setAlignment(Qt.AlignCenter)
        self.lbl_ground_picks.setMinimumWidth(56)
        form_row(scbody, "Pick", self.btn_pick_ground, hint=self.lbl_ground_picks)

        self._register("chk_auto_ground", QCheckBox("Use the auto-fitted plane"))
        self.chk_auto_ground.setToolTip(
            "Level the floor on the dominant plane the solver already found in the\n"
            "point cloud, instead of on points you pick. Quick, and right whenever\n"
            "the floor is the biggest flat thing in the shot.")
        scbody.addWidget(self.chk_auto_ground)

        # --- origin
        scbody.addWidget(group_label("Origin"))
        self._register("btn_pick_origin", make_button(
            "Pick 1 point", "Arm picking, then click the solved point that should become\n"
                            "0, 0, 0 in your scene.", "compact", checkable=True))
        self.btn_pick_origin.toggled.connect(lambda on: self.on_scene_pick_toggled("origin", on))
        self._register("lbl_origin_picks", QLabel("0 / 1"))
        self.lbl_origin_picks.setObjectName("valueChip")
        self.lbl_origin_picks.setAlignment(Qt.AlignCenter)
        self.lbl_origin_picks.setMinimumWidth(56)
        form_row(scbody, "Pick", self.btn_pick_origin, hint=self.lbl_origin_picks)

        self._register("chk_origin_under_camera",
                       QCheckBox("Ground under the camera on this frame"))
        self.chk_origin_under_camera.setToolTip(
            "Put the origin on the floor directly below the camera on the frame the\n"
            "2D tab is showing - the usual choice when there is no obvious feature\n"
            "to build on. Needs a ground plane, picked or auto-fitted.")
        scbody.addWidget(self.chk_origin_under_camera)

        divider(scbody)

        self._register("lbl_scene_status", hint_label(
            "No scene transform: the solve is in COLMAP's own units."))
        scbody.addWidget(self.lbl_scene_status)

        self._register("btn_scene_clear", make_button(
            "Clear Picks", "Forget the points picked so far", "compact"))
        self.btn_scene_clear.clicked.connect(self.clear_picks)
        self._register("btn_scene_apply", make_button(
            "Apply", "Build the transform from what is set above and save it with the shot",
            "compact"))
        self.btn_scene_apply.clicked.connect(self.apply_scene_transform)
        self._register("btn_scene_reset", make_button(
            "Reset", "Drop the transform and go back to COLMAP's arbitrary world", "compact"))
        self.btn_scene_reset.clicked.connect(self.reset_scene_transform)
        button_row(scbody, [self.btn_scene_clear, self.btn_scene_apply, self.btn_scene_reset])
        return self.scene_setup_card

    # =====================================================================
    # THE SOLVE
    # =====================================================================
    def load_solve_for_shot(self, shot_name):
        """
        Load the newest solve for a shot into `self.solve`, or clear it.

        Cameras and the per-frame poses come from camera_track.json; the points
        come from points3D.ply beside it. Nothing here raises: a shot with no
        solve, or with a half-written one, simply leaves the panel disabled with
        a sentence saying so.
        """
        self.solve = None
        if not shot_name:
            return
        track_json, folder = find_latest_output(
            self.ctx.scenes_dir / shot_name, "3D_CAMERA_TRACK", "camera_track.json",
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
        self.solve = {
            "dir": Path(folder),
            "json": data,
            "cameras": data.get("cameras") or {},
            "by_frame": by_frame,
            "timeline_start": start,
            "points": points,
        }

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

    def _solve_point_count(self):
        pts = (self.solve or {}).get("points")
        return 0 if pts is None else int(len(pts))

    def _solved_image_for_frame(self, frame_idx):
        """The solved camera sitting on the plate frame the 2D tab is showing, or None."""
        if not self.solve:
            return None
        return self.solve["by_frame"].get(
            int(self.solve["timeline_start"]) + int(frame_idx))

    def _solve_camera(self, img):
        """The intrinsics of a solved image. JSON keys are strings; ids are not."""
        cams = self.solve["cameras"]
        cam_id = img.get("camera_id")
        return (cams.get(str(cam_id)) or cams.get(cam_id)
                or (next(iter(cams.values())) if cams else None))

    def refresh(self):
        """Enable or disable the Scene setup card and say why, then redraw it."""
        enabled, reason = project_file.scene_setup_enabled(
            (self.solve or {}).get("json"),
            self._solve_point_count() if self.solve else None)
        self.scene_setup_card.setEnabled(enabled)
        if not enabled:
            self.set_pick_mode(None)
            self.lbl_scene_points.setText(reason)
        else:
            # _update_scene_overlay replaces this the moment a picker is armed;
            # until then the artist gets the size of what they are about to pick.
            self.lbl_scene_points.setText(
                "%d solved points from the %s solve. Arm a picker below to see them "
                "on the plate." % (self._solve_point_count(), self.solve["dir"].name))
        self._update_pick_labels()
        self.lbl_scene_status.setText(self._scene_transform_summary())
        self.update_scene_overlay()

    def _update_pick_labels(self):
        picks = self.picks
        self.lbl_scale_picks.setText("%d / 2" % len(picks["scale"]))
        self.lbl_ground_picks.setText("%d / 3" % len(picks["ground"]))
        self.lbl_origin_picks.setText("%d / 1" % len(picks["origin"]))

    def _all_picked_indices(self):
        """Every picked point, scale first, so the numbering on screen is stable."""
        picks = self.picks
        return picks["scale"] + picks["ground"] + picks["origin"]

    def set_pick_mode(self, mode):
        """
        Arm one of the three pickers, or None for off.

        The three toggle buttons are mutually exclusive: a click has to mean one
        thing, and an artist who forgot which picker was armed would silently
        put floor points into the scale pair.
        """
        self.pick_mode = mode
        for name, btn in (("scale", self.btn_pick_scale),
                          ("ground", self.btn_pick_ground),
                          ("origin", self.btn_pick_origin)):
            want = (name == mode)
            if btn.isChecked() != want:
                btn.blockSignals(True)
                btn.setChecked(want)
                btn.blockSignals(False)
        self.update_scene_overlay()

    def on_scene_pick_toggled(self, which, checked):
        self.set_pick_mode(which if checked else None)
        if checked:
            # The points are picked on the plate, which lives on the other tab.
            self.ctx.show_2d_tab()
            self.ctx.log_3d(
                "Picking %s points: click the amber solved points on the 2D tab." % which,
                ACCENT)

    def update_scene_overlay(self):
        """Reproject the solve onto the frame in view, or take the overlay away."""
        canvas = self.canvas
        if not self.pick_mode or not self.solve or self.solve.get("points") is None:
            canvas.scene_pick_active = False
            canvas.show_solved_points = False
            canvas.clear_solved_points()
            return

        canvas.scene_pick_active = True
        canvas.show_solved_points = True
        points = self.solve["points"]
        img = self._solved_image_for_frame(canvas.current_frame)
        if img is None:
            canvas.clear_solved_points()
            solved = sorted(self.solve["by_frame"])
            nearest = ""
            if solved:
                start = int(self.solve["timeline_start"])
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

    def on_solved_point_picked(self, index):
        """A click landed on a reprojected point; file it under the armed picker."""
        mode = self.pick_mode
        if not mode or not self.solve:
            return
        bucket = self.picks[mode]
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
        self.canvas.set_solved_selection(self._all_picked_indices())
        pt = self.solve["points"][int(index)]
        self.ctx.log_3d(
            "%s: %d point(s) picked  (last at %.3f, %.3f, %.3f in solve units)."
            % (mode.capitalize(), len(bucket), pt[0], pt[1], pt[2]), self.ctx.TEXT_DIM)

    def clear_picks(self):
        for bucket in self.picks.values():
            del bucket[:]
        self._update_pick_labels()
        self.canvas.set_solved_selection(())
        self.ctx.log_3d("Cleared the picked points.", self.ctx.TEXT_DIM)

    def _ground_points_for_build(self, notes):
        """
        The points a ground fit should be run on, or None with a note saying why not.

        The auto plane is turned into three points ON that plane rather than
        being fitted separately, so both routes go through the same
        scene_transform.fit_ground and cannot drift apart.
        """
        picks = self.picks["ground"]
        points = self.solve["points"]
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

    def apply_scene_transform(self):
        """Build the transform from what the panel holds, log it, and save it."""
        if not self.solve or self.solve.get("points") is None:
            self.ctx.log_3d("There is no solve loaded to build a scene transform from.", self.ctx.WARN)
            return
        points = self.solve["points"]
        notes = []

        # --- scale
        scale_pair, real_metres = None, None
        picks = self.picks["scale"]
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
            img = self._solved_image_for_frame(self.canvas.current_frame)
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
        elif self.picks["origin"]:
            origin_point = points[self.picks["origin"][0]]

        if scale_pair is None and ground_points is None and origin_point is None:
            self.ctx.log_3d(
                "Nothing to apply yet: pick two points and type a distance for scale, "
                "three for the ground, or one for the origin.", self.ctx.WARN)
            for note in notes:
                self.ctx.log_3d("   %s" % note, self.ctx.WARN)
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
            self.ctx.log_3d("✖ Could not build the scene transform: %s" % e, self.ctx.ERR)
            return

        self.transform = transform
        scale = float(transform["scale"])
        self.ctx.log_3d(
            "✔ Scene transform applied: scale ×%.6f — one solve unit is now %.6f m."
            % (scale, scale), self.ctx.OK)
        if ground_points is not None and not any(n.startswith("Ground") for n in notes):
            self.ctx.log_3d("   Ground levelled onto Y = 0.", self.ctx.OK)
        if origin_point is not None:
            self.ctx.log_3d("   Origin moved to the picked position.", self.ctx.OK)
        for note in notes:
            self.ctx.log_3d("   %s" % note, self.ctx.WARN)
        self.ctx.log_3d(
            "   Press Re-export This Solve to write a new export folder with it.", self.ctx.TEXT_DIM)

        self.lbl_scene_status.setText(self._scene_transform_summary())
        self.ctx.schedule_project_save()

    def reset_scene_transform(self):
        self.transform = None
        self.lbl_scene_status.setText(self._scene_transform_summary())
        self.ctx.log_3d(
            "Scene transform cleared: the solve goes back to COLMAP's arbitrary "
            "scale, tilt and origin.", self.ctx.ACCENT)
        self.ctx.schedule_project_save()

    def _scene_transform_summary(self):
        """One line describing the stored transform, for the panel."""
        transform = self.transform
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
