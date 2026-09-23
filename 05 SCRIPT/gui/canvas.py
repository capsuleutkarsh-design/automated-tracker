"""
Interactive Video Preview Canvas with Multi-Mask Roto & Point Tracking HUD
"""

import uuid
import numpy as np
from pathlib import Path
from PySide6.QtWidgets import QLabel, QMenu
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import (
    QFont, QColor, QPixmap, QPainter, QPen, QBrush, QPainterPath, QPolygonF,
    QDragEnterEvent, QDropEvent
)

from gui.theme import WARN, TEXT, OK, ACCENT

from mask_animator import AnimatedMask
from core.tracking_layer import TrackingLayer, point_in_poly
from core import lens

# How close to a projected solved point a click has to land to select it, in
# SCREEN pixels - so the feel is the same whether the plate is shown at full
# size or scaled into a small window.
SOLVED_PICK_RADIUS_PX = 8.0

# The same idea for a tracked marker the artist is about to grab and drag.
# Larger, because it is a drag rather than a click and a marker sits under the
# cursor's own tip.
TRACK_GRAB_RADIUS_PX = 10.0


def project_solved_points(points_world, center, r_cam_to_world, cam):
    """
    Solved 3D points -> pixels in one solved frame. Returns (xy, visible).

    Pure numpy, no Qt, so the reprojection the overlay draws is the one the
    tests pin down. `points_world` is (N, 3) in COLMAP's own world - the same
    space scene_transform works in - `center` and `r_cam_to_world` are the
    "center" and "R_world" of that frame's parsed COLMAP image, and `cam` is its
    entry from cameras (focal_x/focal_y/cx/cy/width/height/model/params).

    THE ROTATION. COLMAP's parsed images store the camera-TO-world rotation in
    R_world (it is R_world_to_camera transposed - see parse_colmap_images and
    the note in scene_transform.apply_to_camera), so the camera-space point is
    R_world.T @ (X - C), written here as the row-wise (X - C) @ R_world. Using
    R_world the other way round reprojects to the wrong pixel by hundreds of
    pixels, which is exactly the kind of mistake that looks plausible on screen.

    Then x/z, y/z lands on COLMAP's normalised plane (y DOWN), lens.distort_points
    bends it the way the solved lens did, and fx/fy/cx/cy put it on the sensor -
    the same chain COLMAP itself uses, so the dot sits on the feature it came from.

    `visible` is False for a point behind the camera or off the frame; the
    overlay draws only those, because a dot clamped to the frame edge would be
    pickable and would mean nothing.
    """
    P = np.asarray(points_world, dtype=float).reshape(-1, 3)
    C = np.asarray(center, dtype=float).reshape(3)
    R = np.asarray(r_cam_to_world, dtype=float).reshape(3, 3)

    cam_space = (P - C) @ R
    z = cam_space[:, 2]
    in_front = z > 1e-6
    # A point behind the camera still has to go through the maths (the arrays
    # stay aligned with the cloud), so divide by a harmless 1 and mask it after.
    safe_z = np.where(in_front, z, 1.0)
    normalised = np.stack([cam_space[:, 0] / safe_z, cam_space[:, 1] / safe_z], axis=-1)
    distorted = lens.distort_points(cam, normalised)

    fx = float(cam.get("focal_x", cam.get("fx", 0.0)))
    fy = float(cam.get("focal_y", cam.get("fy", fx)) or fx)
    width = float(cam.get("width", 0.0))
    height = float(cam.get("height", 0.0))
    cx = float(cam.get("cx", width / 2.0))
    cy = float(cam.get("cy", height / 2.0))

    xy = np.stack([fx * distorted[:, 0] + cx, fy * distorted[:, 1] + cy], axis=-1)
    inside = ((xy[:, 0] >= 0.0) & (xy[:, 0] <= width)
              & (xy[:, 1] >= 0.0) & (xy[:, 1] <= height))
    visible = in_front & inside & np.isfinite(xy).all(axis=1)
    return xy, visible


class VideoPointPickerCanvas(QLabel):
    point_added = Signal(int, float, float)
    solved_point_picked = Signal(int)
    # A solved 2D track was dragged to a new place on a frame (roadmap 2.1):
    # (layer name, point index, clip frame, plate x, plate y).
    tracked_point_moved = Signal(str, int, int, float, float)
    # The context menu asked to re-track one point from a frame:
    # (layer name, point index, clip frame, also backwards).
    retrack_requested = Signal(str, int, int, bool)
    # The context menu asked to forget a correction: (layer name, point, frame).
    correction_cleared = Signal(str, int, int)
    masks_changed = Signal()
    file_dropped = Signal(str)
    playback_toggle_requested = Signal()
    step_frame_requested = Signal(int)
    keyframe_nav_requested = Signal(int)
    in_point_requested = Signal(int)
    out_point_requested = Signal(int)
    mask_overlay_toggled = Signal(bool)
    alpha_mode_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("""
            background-color: #090b10;
            border: 1px dashed #232d42;
            border-radius: 8px;
        """)
        self.setMinimumSize(480, 280)
        self.current_pixmap = None
        # Scaled copies of recently shown frames at display size, keyed by frame
        # index (plus source size, widget size and overlay/clean view), so scrubbing
        # back and forth and looping playback never re-run SmoothTransformation on
        # a full-resolution pixmap for a frame already scaled once.
        self._scaled_cache = {}
        self._scaled_cache_max = 96
        self._source_sig = None
        self.orig_w = 1920
        self.orig_h = 1080
        self.current_frame = 0
        self.total_frames = 0
        self.fps = 24.0
        self.in_point = 0
        self.out_point = -1

        # Viewport Modes
        self.show_mask_overlay = True
        self.view_alpha_mode = False
        self.show_loupe = False

        # Multi-Layer Tracking Model
        self.layers = [TrackingLayer("Layer 1 (Wall)", "#38bdf8", "grid")]
        self.active_layer_idx = 0
        self.selected_mask_id = None

        # Solved 3D points reprojected onto this frame (roadmap 1.4). They are
        # only held while the Scene setup panel is picking, so the ordinary
        # tracking view keeps the plate and the roto to itself.
        self.solved_xy = None            # (N, 2) pixels in the SOLVE's raster
        self.solved_visible = None       # (N,) bool: in front of the camera and on frame
        self.solved_src_size = None      # (width, height) that raster, which may
                                         # differ from the preview's own size
        self.solved_selection = ()       # indices the artist has picked, in pick order
        self.show_solved_points = False
        self.scene_pick_active = False

        # The last 2D result, drawn over the plate so the artist can see what
        # the solve produced and drag a drifting marker back onto its feature
        # (roadmap 2.1). `tracked_layers` maps a layer NAME to
        # {"tracks": [T, N, 2] plate px, "vis": [T, N]}; the clip's frame index
        # maps to a sample index through the range the track was made on.
        self.tracked_layers = None
        self.tracked_in_point = 0
        self.tracked_step = 1
        self.show_tracked_points = False
        self.tracked_drag = None         # (layer name, point index) while dragging
        self.tracked_drag_pos = None     # (ox, oy) the marker is being held at

        self.interaction_mode = "select"  # "select", "point", "inclusion_box", "inclusion_poly", "exclusion_box", "exclusion_poly"
        self.drag_start = None
        self.drag_current = None
        self.current_poly = []  # list of (ox, oy) vertices while drawing
        self.hover_pos = None
        self.is_overlay_active = False
        self.selected_vertex_idx = None
        self.is_dragging_shape = False
        self.is_dragging_vertex = False
        self._mask_drag_dirty = False

    @property
    def active_layer(self):
        if 0 <= self.active_layer_idx < len(self.layers):
            return self.layers[self.active_layer_idx]
        return self.layers[0] if self.layers else None

    @property
    def points(self):
        return self.active_layer.points if self.active_layer else []

    @points.setter
    def points(self, val):
        if self.active_layer:
            self.active_layer.points = val

    def set_frame_image(self, qimage, frame_idx, total_frames, orig_w, orig_h, fps=24.0):
        self.orig_w = orig_w
        self.orig_h = orig_h
        self.current_frame = frame_idx
        self.total_frames = total_frames
        self.fps = fps
        self.current_pixmap = QPixmap.fromImage(qimage)
        # A different clip (size or length) means every cached frame is stale.
        sig = (orig_w, orig_h, total_frames)
        if sig != self._source_sig:
            self._source_sig = sig
            self.invalidate_frame_cache()
        self.setStyleSheet("""
            background-color: #090b10;
            border: 1px solid #1c2436;
            border-radius: 8px;
        """)
        self.update()

    def clear_active_layer_points(self):
        if self.active_layer:
            self.active_layer.points.clear()
            self.update()

    def clear_active_layer_masks(self):
        if self.active_layer:
            self.active_layer.animated_masks.clear()
            self.current_poly.clear()
            self.drag_start = None
            self.drag_current = None
            self.selected_mask_id = None
            self.masks_changed.emit()
            self.update()

    def layers_to_config(self):
        """Every layer as the engine and the project file want it."""
        return [l.to_config_dict() for l in self.layers]

    def layers_from_config(self, configs, in_point=0, out_point=-1):
        """
        Replace the layer stack with one restored from a project file.

        The counterpart to layers_to_config: points and animated masks come
        back with their keyframes, and the in/out range with them, because a
        trimmed range is as much of the artist's decision as the roto is. An
        empty or unusable list leaves the default single layer alone rather
        than dropping the canvas into a state with no active layer.

        masks_changed is emitted so the layer panel relists, and the scaled
        frame cache is dropped because every cached frame was drawn with the
        previous layers' overlays baked in.
        """
        layers = []
        for cfg in (configs or []):
            if not isinstance(cfg, dict):
                continue
            try:
                layers.append(TrackingLayer.from_config_dict(cfg))
            except Exception:
                # One unreadable layer must not cost the artist the others.
                continue
        if layers:
            self.layers = layers
        self.active_layer_idx = 0
        self.selected_mask_id = None
        self.current_poly.clear()
        self.drag_start = None
        self.drag_current = None
        self.in_point = int(in_point)
        self.out_point = int(out_point)
        self.invalidate_frame_cache()
        self.masks_changed.emit()
        self.update()
        return len(layers)

    def invalidate_frame_cache(self):
        """Drop every cached scaled frame. Call when a different clip is loaded."""
        self._scaled_cache.clear()

    # -- Solved-point overlay (roadmap 1.4) ---------------------------------
    def set_solved_points(self, xy, visible=None, src_size=None):
        """
        Show the solve's points reprojected onto the current frame.

        `xy` is (N, 2) in the raster the solve was made at; `src_size` is that
        raster, so a solve made on half-size frames still lands on the plate.
        Passing None for `xy` is the same as clear_solved_points().
        """
        if xy is None:
            self.clear_solved_points()
            return
        self.solved_xy = np.asarray(xy, dtype=float).reshape(-1, 2)
        if visible is None:
            self.solved_visible = np.ones(len(self.solved_xy), dtype=bool)
        else:
            self.solved_visible = np.asarray(visible, dtype=bool).reshape(-1)
        self.solved_src_size = tuple(src_size) if src_size else (self.orig_w, self.orig_h)
        self.update()

    def clear_solved_points(self):
        """Forget the reprojected points; the overlay stops being drawn."""
        self.solved_xy = None
        self.solved_visible = None
        self.solved_src_size = None
        self.update()

    def set_solved_selection(self, indices):
        """Which reprojected points are drawn as picked, in the order picked."""
        self.solved_selection = tuple(int(i) for i in (indices or ()))
        self.update()

    def _solved_in_plate_coords(self):
        """The reprojected points in the preview's own pixel space, or None."""
        if self.solved_xy is None or not len(self.solved_xy):
            return None
        src_w, src_h = self.solved_src_size or (self.orig_w, self.orig_h)
        if not src_w or not src_h:
            return None
        return self.solved_xy * np.array([self.orig_w / float(src_w),
                                          self.orig_h / float(src_h)])

    def nearest_solved_point(self, ox, oy, radius_px=SOLVED_PICK_RADIUS_PX):
        """
        Index of the visible reprojected point nearest (ox, oy), or None.

        (ox, oy) is in plate coordinates, but the radius is in screen pixels:
        the artist is aiming with a mouse, so the target has to be the same size
        on screen however far the plate is zoomed out.
        """
        plate = self._solved_in_plate_coords()
        if plate is None:
            return None
        scaled = self._scaled_pixmap()
        if scaled is None or scaled.width() == 0 or scaled.height() == 0:
            return None
        sx = scaled.width() / float(self.orig_w or 1)
        sy = scaled.height() / float(self.orig_h or 1)

        dx = (plate[:, 0] - ox) * sx
        dy = (plate[:, 1] - oy) * sy
        dist = np.hypot(dx, dy)
        if self.solved_visible is not None and len(self.solved_visible) == len(dist):
            dist = np.where(self.solved_visible, dist, np.inf)
        idx = int(np.argmin(dist))
        return idx if dist[idx] <= float(radius_px) else None

    # -- Solved 2D tracks (roadmap 2.1) -------------------------------------
    def set_tracked_result(self, layers, in_point=0, frame_step=1, show=True):
        """
        Show the last 2D result on the plate. `layers` maps layer name -> arrays.

        `in_point` and `frame_step` are the range the track was made on, which
        is how a clip frame becomes a sample index: a frame the track does not
        cover simply draws nothing. Passing None takes the overlay away, which
        is what happens on a shot with no result.
        """
        if not layers:
            self.clear_tracked_result()
            return
        self.tracked_layers = dict(layers)
        self.tracked_in_point = max(0, int(in_point))
        self.tracked_step = max(1, int(frame_step))
        self.show_tracked_points = bool(show)
        self.update()

    def clear_tracked_result(self):
        """Forget the loaded result; the overlay stops being drawn."""
        self.tracked_layers = None
        self.tracked_drag = None
        self.tracked_drag_pos = None
        self.show_tracked_points = False
        self.update()

    def has_tracked_result(self):
        return bool(self.tracked_layers)

    def tracked_index_for_frame(self, frame_idx=None):
        """
        The sample index of a clip frame in the loaded result, or None.

        A solve made with a frame step only holds every Nth frame, and the
        frames in between belong to no sample - drawing an interpolated marker
        there would invite the artist to correct a frame that does not exist.
        """
        if not self.tracked_layers:
            return None
        frame_idx = self.current_frame if frame_idx is None else int(frame_idx)
        offset = frame_idx - self.tracked_in_point
        if offset < 0 or offset % self.tracked_step:
            return None
        t = offset // self.tracked_step
        first = next(iter(self.tracked_layers.values()))
        return t if 0 <= t < int(first["tracks"].shape[0]) else None

    def _layer_by_name(self, name):
        return next((l for l in self.layers if l.name == name), None)

    def tracked_points_at(self, frame_idx=None):
        """
        Every visible tracked marker on a frame: (layer name, point index, ox, oy).

        Plate coordinates, the same space clicks arrive in, so the caller can
        measure distance without knowing how the frame is scaled on screen.
        """
        out = []
        t = self.tracked_index_for_frame(frame_idx)
        if t is None:
            return out
        for l_name, block in self.tracked_layers.items():
            layer = self._layer_by_name(l_name)
            if layer is not None and not layer.visible:
                continue
            tracks, vis = block["tracks"], block.get("vis")
            for n in range(int(tracks.shape[1])):
                if vis is not None and not bool(vis[t, n]):
                    continue
                out.append((l_name, n, float(tracks[t, n, 0]), float(tracks[t, n, 1])))
        return out

    def nearest_tracked_point(self, ox, oy, radius_px=TRACK_GRAB_RADIUS_PX):
        """
        The tracked marker nearest a click, or None. Radius is in SCREEN pixels.
        """
        if not self.show_tracked_points:
            return None
        candidates = self.tracked_points_at()
        if not candidates:
            return None
        scaled = self._scaled_pixmap()
        if scaled is None or scaled.width() == 0 or scaled.height() == 0:
            return None
        sx = scaled.width() / float(self.orig_w or 1)
        sy = scaled.height() / float(self.orig_h or 1)
        best, best_d = None, float("inf")
        for l_name, n, px, py in candidates:
            d = np.hypot((px - ox) * sx, (py - oy) * sy)
            if d < best_d:
                best, best_d = (l_name, n, px, py), d
        return best if best_d <= float(radius_px) else None

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if Path(url.toLocalFile()).suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".exr", ".png", ".jpg"}:
                    event.acceptProposedAction()
                    self.setStyleSheet("background-color: #111828; border: 2px dashed #38bdf8; border-radius: 8px;")
                    return

    def dragLeaveEvent(self, event):
        if not self.current_pixmap:
            self.setStyleSheet("background-color: #090b10; border: 1px dashed #232d42; border-radius: 8px;")
        else:
            self.setStyleSheet("background-color: #090b10; border: 1px solid #1c2436; border-radius: 8px;")

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                filepath = url.toLocalFile()
                if Path(filepath).suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".exr", ".png", ".jpg"}:
                    event.acceptProposedAction()
                    self.file_dropped.emit(filepath)
                    return

    def _scaled_pixmap(self):
        """Scaled copy of the current frame at display size, cached per frame index."""
        if self.current_pixmap is None:
            return None
        key = (self.current_frame, self.current_pixmap.width(), self.current_pixmap.height(),
               self.width(), self.height(), self.is_overlay_active)
        pix = self._scaled_cache.get(key)
        if pix is None:
            pix = self.current_pixmap.scaled(
                self.width(), self.height(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            if len(self._scaled_cache) >= self._scaled_cache_max:
                # Drop the oldest entry (dicts keep insertion order).
                self._scaled_cache.pop(next(iter(self._scaled_cache)))
            self._scaled_cache[key] = pix
        return pix

    def _new_mask_id(self):
        """Unique mask id. The old m_<count>_<frame> scheme repeated after a delete."""
        return f"m_{uuid.uuid4().hex[:8]}"

    def _find_or_create_mask(self, category, kind_label, mask_type):
        """
        The selected mask if it has this category (a new keyframe on it), otherwise a
        new mask of the category appended to the active layer and selected.
        """
        if self.selected_mask_id:
            for m in self.active_layer.animated_masks:
                if m.id == self.selected_mask_id and m.category == category:
                    return m
        count = len(self.active_layer.animated_masks) + 1
        prefix = "Inc" if category == "inclusion" else "Exc"
        mask = AnimatedMask(self._new_mask_id(), f"{prefix} {kind_label} #{count}", category, mask_type)
        self.active_layer.animated_masks.append(mask)
        self.selected_mask_id = mask.id
        return mask

    def _selected_mask(self):
        if not (self.selected_mask_id and self.active_layer):
            return None
        return next((m for m in self.active_layer.animated_masks if m.id == self.selected_mask_id), None)

    def delete_selected_keyframe(self):
        """Delete the selected mask's keyframe on the current frame (Del)."""
        m = self._selected_mask()
        if m and m.has_keyframe(self.current_frame):
            self._delete_keyframe_on_mask(m, self.current_frame)
            return True
        return False

    def delete_selected_mask(self):
        """Delete the selected mask with all its keyframes (Shift+Del / Del Mask button)."""
        m = self._selected_mask()
        if m:
            self._delete_entire_mask(m)
            return True
        return False

    def _view_to_orig_coords(self, pos):
        lbl_w, lbl_h = self.width(), self.height()
        scaled_pix = self._scaled_pixmap()
        if scaled_pix is None or scaled_pix.width() == 0 or scaled_pix.height() == 0:
            return 0.0, 0.0
        pw, ph = scaled_pix.width(), scaled_pix.height()
        offset_x, offset_y = (lbl_w - pw) / 2.0, (lbl_h - ph) / 2.0

        click_x = max(0, min(pw, pos.x() - offset_x))
        click_y = max(0, min(ph, pos.y() - offset_y))

        orig_x = (click_x / pw) * self.orig_w
        orig_y = (click_y / ph) * self.orig_h
        return orig_x, orig_y

    def mousePressEvent(self, event):
        if not self.current_pixmap:
            return

        if event.button() == Qt.RightButton:
            if self.current_poly:
                self.current_poly.clear()
                self.update()
                return
            self._show_context_menu(event.position())
            return

        if event.button() != Qt.LeftButton:
            return

        ox, oy = self._view_to_orig_coords(event.position())

        # While the Scene setup panel is armed the click belongs to it, hit or
        # miss. Letting a miss fall through would drop a 2D tracking point onto
        # the layer the artist is not even looking at.
        if self.scene_pick_active:
            idx = self.nearest_solved_point(ox, oy)
            if idx is not None:
                self.solved_point_picked.emit(idx)
            return

        if self.interaction_mode == "select":
            # A tracked marker under the cursor is grabbed before anything else:
            # while the result is on screen, dragging it is what the artist came
            # here to do, and the roto underneath is not moving.
            hit = self.nearest_tracked_point(ox, oy)
            if hit is not None:
                l_name, n, px, py = hit
                self.tracked_drag = (l_name, n)
                self.tracked_drag_pos = (px, py)
                self.drag_start = (ox, oy)
                self.update()
                return

            if self.active_layer:
                if self.selected_mask_id:
                    for m in self.active_layer.animated_masks:
                        if m.id == self.selected_mask_id:
                            geom = m.get_interpolated_geometry(self.current_frame)
                            if geom and geom.get("points"):
                                pts = geom["points"]
                                for i, (px, py) in enumerate(pts):
                                    if np.hypot(ox - px, (oy - py) * (self.orig_w / self.orig_h)) < (self.orig_w * 0.015):
                                        self.selected_vertex_idx = i
                                        self.is_dragging_vertex = True
                                        self.drag_start = (ox, oy)
                                        return
                masks_at_f = self.active_layer.get_masks_at_frame(self.current_frame)
                for minfo in reversed(masks_at_f):
                    if minfo["type"] == "poly" and point_in_poly(ox, oy, minfo["points"]):
                        self.selected_mask_id = minfo["mask_obj"].id
                        self.is_dragging_shape = True
                        self.drag_start = (ox, oy)
                        self.update()
                        return
                self.selected_mask_id = None
                self.update()

        elif self.interaction_mode == "point":
            if self.active_layer:
                self.active_layer.points.append((self.current_frame, ox, oy))
                self.point_added.emit(self.current_frame, ox, oy)
                self.update()

        elif self.interaction_mode in ("inclusion_box", "exclusion_box"):
            self.drag_start = (ox, oy)
            self.drag_current = (ox, oy)
            self.update()

        elif self.interaction_mode in ("inclusion_poly", "exclusion_poly"):
            if len(self.current_poly) >= 3:
                start_x, start_y = self.current_poly[0]
                dist_orig = np.hypot(ox - start_x, (oy - start_y) * (self.orig_w / self.orig_h))
                if dist_orig < (self.orig_w * 0.03):
                    self._finish_current_poly()
                    return

            self.current_poly.append((ox, oy))
            self.update()

    def mouseDoubleClickEvent(self, event):
        if not self.current_pixmap or event.button() != Qt.LeftButton:
            return
        if self.interaction_mode in ("inclusion_poly", "exclusion_poly") and len(self.current_poly) >= 3:
            self._finish_current_poly()

    def _finish_current_poly(self):
        if len(self.current_poly) >= 3 and self.active_layer:
            category = "inclusion" if self.interaction_mode == "inclusion_poly" else "exclusion"
            target_mask = self._find_or_create_mask(category, "Poly", "poly")
            target_mask.set_keyframe(self.current_frame, list(self.current_poly), "poly")
            self.current_poly.clear()
            self.masks_changed.emit()
            self.update()

    def mouseMoveEvent(self, event):
        if not self.current_pixmap:
            return
        ox, oy = self._view_to_orig_coords(event.position())
        self.hover_pos = (ox, oy)

        if self.tracked_drag is not None:
            # The marker follows the cursor; nothing is committed until release,
            # so a grab the artist changes their mind about costs nothing.
            self.tracked_drag_pos = (ox, oy)
            self.update()
            return

        if self.interaction_mode == "select" and self.drag_start and self.selected_mask_id and self.active_layer:
            m = next((mask for mask in self.active_layer.animated_masks if mask.id == self.selected_mask_id), None)
            if m:
                if not m.has_keyframe(self.current_frame):
                    geom = m.get_interpolated_geometry(self.current_frame)
                    if geom and geom.get("points"):
                        m.set_keyframe(self.current_frame, list(geom["points"]), "poly")
                
                kf = m.keyframes.get(int(self.current_frame))
                if kf and kf.mask_type == "poly":
                    dx = ox - self.drag_start[0]
                    dy = oy - self.drag_start[1]
                    
                    if self.is_dragging_vertex and self.selected_vertex_idx is not None:
                        pts = kf.data
                        px, py = pts[self.selected_vertex_idx]
                        pts[self.selected_vertex_idx] = (px + dx, py + dy)
                    elif self.is_dragging_shape:
                        pts = kf.data
                        kf.data = [(px + dx, py + dy) for px, py in pts]
                    
                    self.drag_start = (ox, oy)
                    # Listeners rebuild lists on masks_changed; emit once on release.
                    self._mask_drag_dirty = True
                    self.update()

        elif self.drag_start and self.interaction_mode in ("inclusion_box", "exclusion_box"):
            self.drag_current = (ox, oy)
            self.update()
        elif self.current_poly:
            self.update()

    def mouseReleaseEvent(self, event):
        if not self.current_pixmap or event.button() != Qt.LeftButton:
            return

        if self.tracked_drag is not None:
            l_name, n = self.tracked_drag
            ox, oy = self._view_to_orig_coords(event.position())
            self.tracked_drag = None
            self.tracked_drag_pos = None
            self.drag_start = None
            self.tracked_point_moved.emit(l_name, int(n), int(self.current_frame),
                                          float(ox), float(oy))
            self.update()
            return

        if self.interaction_mode == "select":
            self.is_dragging_shape = False
            self.is_dragging_vertex = False
            self.drag_start = None
            self.selected_vertex_idx = None
            if self._mask_drag_dirty:
                self._mask_drag_dirty = False
                self.masks_changed.emit()
            self.update()

        elif self.drag_start and self.interaction_mode in ("inclusion_box", "exclusion_box"):
            ox, oy = self._view_to_orig_coords(event.position())
            x1, y1 = self.drag_start
            x2, y2 = ox, oy
            if abs(x2 - x1) > 5 and abs(y2 - y1) > 5 and self.active_layer:
                category = "inclusion" if self.interaction_mode == "inclusion_box" else "exclusion"
                target_mask = self._find_or_create_mask(category, "Box", "rect")
                target_mask.set_keyframe(self.current_frame, [x1, y1, x2, y2], "rect")
                self.masks_changed.emit()

            self.drag_start = None
            self.drag_current = None
            self.update()

    def _show_context_menu(self, pos):
        ox, oy = self._view_to_orig_coords(pos)
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #161620;
                color: #e0e0e0;
                border: 1px solid #333348;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 18px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #00d2ff;
                color: #000000;
                font-weight: bold;
            }
        """)

        # A right-click on a tracked marker is about that track, not the roto.
        hit = self.nearest_tracked_point(ox, oy)
        if hit is not None:
            l_name, n, _px, _py = hit
            frame = int(self.current_frame)
            layer = self._layer_by_name(l_name)
            corrected = layer is not None and layer.correction_at(frame, n) is not None

            menu.addAction(f"◈ {l_name}  ·  point #{n + 1}  ·  frame {frame + 1}").setEnabled(False)
            menu.addSeparator()
            act_fwd = menu.addAction(f"Re-track this point from frame {frame + 1} forward")
            act_fwd.triggered.connect(
                lambda: self.retrack_requested.emit(l_name, int(n), frame, False))
            act_both = menu.addAction(f"Re-track forward and backwards from frame {frame + 1}")
            act_both.triggered.connect(
                lambda: self.retrack_requested.emit(l_name, int(n), frame, True))
            if corrected:
                act_clear = menu.addAction(f"Forget the correction on frame {frame + 1}")
                act_clear.triggered.connect(
                    lambda: self.correction_cleared.emit(l_name, int(n), frame))
            menu.exec(self.mapToGlobal(QPointF(pos.x(), pos.y()).toPoint()))
            return

        clicked_mask_info = None
        if self.active_layer:
            masks_at_f = self.active_layer.get_masks_at_frame(self.current_frame)
            for minfo in reversed(masks_at_f):
                if minfo["type"] == "poly" and point_in_poly(ox, oy, minfo["points"]):
                    clicked_mask_info = minfo
                    break

        if clicked_mask_info:
            m_obj = clicked_mask_info["mask_obj"]
            self.selected_mask_id = m_obj.id
            is_kf = clicked_mask_info["is_keyframe"]
            
            menu.addAction(f"🎯 Select '{m_obj.name}' for Editing")
            
            if is_kf:
                act_del_kf = menu.addAction(f"🗑 Delete Keyframe at Frame {self.current_frame+1}")
                act_del_kf.triggered.connect(lambda: self._delete_keyframe_on_mask(m_obj, self.current_frame))
            else:
                act_add_kf = menu.addAction(f"🔷 Add Keyframe at Frame {self.current_frame+1}")
                act_add_kf.triggered.connect(lambda: self._set_keyframe_on_mask(m_obj, self.current_frame, clicked_mask_info))

            act_del_m = menu.addAction(f"❌ Delete Entire Mask '{m_obj.name}'")
            act_del_m.triggered.connect(lambda: self._delete_entire_mask(m_obj))
            menu.addSeparator()

        act_clear_masks = menu.addAction("🗑 Clear All Masks on Active Layer")
        act_clear_masks.triggered.connect(self.clear_active_layer_masks)

        act_clear_pts = menu.addAction("🗑 Clear All Points on Active Layer")
        act_clear_pts.triggered.connect(self.clear_active_layer_points)

        menu.exec(self.mapToGlobal(QPointF(pos.x(), pos.y()).toPoint()))

    def _set_keyframe_on_mask(self, mask_obj, frame_idx, mask_info):
        mask_obj.set_keyframe(frame_idx, mask_info["points"], "poly")
        self.masks_changed.emit()
        self.update()

    def _delete_keyframe_on_mask(self, mask_obj, frame_idx):
        mask_obj.delete_keyframe(frame_idx)
        self.masks_changed.emit()
        self.update()

    def _delete_entire_mask(self, mask_obj):
        if self.active_layer and mask_obj in self.active_layer.animated_masks:
            self.active_layer.animated_masks.remove(mask_obj)
            if self.selected_mask_id == mask_obj.id:
                self.selected_mask_id = None
            self.masks_changed.emit()
            self.update()

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Space, Qt.Key_K):
            self.playback_toggle_requested.emit()
            event.accept()
        elif key in (Qt.Key_Left, Qt.Key_J):
            self.step_frame_requested.emit(-1)
            event.accept()
        elif key in (Qt.Key_Right, Qt.Key_L):
            self.step_frame_requested.emit(1)
            event.accept()
        elif key in (Qt.Key_Up, Qt.Key_BracketRight):
            self.keyframe_nav_requested.emit(1)
            event.accept()
        elif key in (Qt.Key_Down, Qt.Key_BracketLeft):
            self.keyframe_nav_requested.emit(-1)
            event.accept()
        elif key == Qt.Key_I:
            self.in_point = self.current_frame
            self.in_point_requested.emit(self.current_frame)
            self.update()
            event.accept()
        elif key == Qt.Key_O:
            self.out_point = self.current_frame
            self.out_point_requested.emit(self.current_frame)
            self.update()
            event.accept()
        elif key == Qt.Key_M:
            self.show_mask_overlay = not self.show_mask_overlay
            self.mask_overlay_toggled.emit(self.show_mask_overlay)
            self.update()
            event.accept()
        elif key == Qt.Key_A:
            self.view_alpha_mode = not self.view_alpha_mode
            self.alpha_mode_toggled.emit(self.view_alpha_mode)
            self.update()
            event.accept()
        elif key == Qt.Key_Control:
            self.show_loupe = True
            self.update()
            event.accept()
        elif key in (Qt.Key_Delete, Qt.Key_Backspace):
            # Del removes only the keyframe under the playhead; Shift+Del the whole mask.
            if event.modifiers() & Qt.ShiftModifier:
                self.delete_selected_mask()
            else:
                self.delete_selected_keyframe()
            event.accept()
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Control:
            self.show_loupe = False
            self.update()
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if not self.current_pixmap:
            painter.setPen(QColor("#8b929e"))
            painter.setFont(QFont("Segoe UI", 12, QFont.Bold))
            painter.drawText(QRectF(0, self.height()/2 - 25, self.width(), 25), Qt.AlignCenter, "Drop Media File Here")
            painter.setFont(QFont("Segoe UI", 10.5))
            painter.setPen(QColor("#5e626b"))
            painter.drawText(QRectF(0, self.height()/2 + 4, self.width(), 25), Qt.AlignCenter, "or select a media item from the Target Media list on the left")
            return

        lbl_w, lbl_h = self.width(), self.height()
        scaled_pix = self._scaled_pixmap()
        if scaled_pix is None or scaled_pix.width() == 0 or scaled_pix.height() == 0:
            return
        pw, ph = scaled_pix.width(), scaled_pix.height()
        offset_x, offset_y = (lbl_w - pw) / 2.0, (lbl_h - ph) / 2.0

        # Alpha Channel Silhouette Mode
        if self.view_alpha_mode:
            # Base is solid white (255) for background
            painter.fillRect(QRectF(offset_x, offset_y, pw, ph), QColor("#ffffff"))
            for layer in self.layers:
                if not layer.visible:
                    continue
                masks_at_frame = layer.get_masks_at_frame(self.current_frame)
                for minfo in masks_at_frame:
                    if minfo.get("points") and len(minfo["points"]) >= 3:
                        qpoly = [QPointF(offset_x + (p[0] / self.orig_w) * pw, offset_y + (p[1] / self.orig_h) * ph) for p in minfo["points"]]
                        cat = minfo["category"]
                        # Exclusion is black (0), Inclusion is white (255)
                        col = QColor(0, 0, 0) if cat == "exclusion" else QColor(255, 255, 255)
                        painter.setPen(Qt.NoPen)
                        painter.setBrush(QBrush(col))
                        painter.drawPolygon(qpoly)
        else:
            painter.drawPixmap(int(offset_x), int(offset_y), scaled_pix)

        # Draw Animated Masks for all visible layers
        if not self.is_overlay_active and self.show_mask_overlay:
            for l_idx, layer in enumerate(self.layers):
                if not layer.visible:
                    continue
                is_active_layer = (l_idx == self.active_layer_idx)
                masks_at_frame = layer.get_masks_at_frame(self.current_frame)

                for minfo in masks_at_frame:
                    cat = minfo["category"]
                    is_kf = minfo["is_keyframe"]
                    m_obj = minfo["mask_obj"]
                    is_sel = (self.selected_mask_id == m_obj.id)

                    is_inc = (cat == "inclusion")
                    base_color = QColor(16, 185, 129) if is_inc else QColor(239, 68, 68)
                    
                    alpha_fill = 55 if is_sel else (35 if is_active_layer else 15)
                    alpha_border = 230 if is_sel else (200 if is_kf else 120)
                    pen_style = Qt.SolidLine if (is_kf or is_sel) else Qt.DashLine
                    pen_width = 2 if is_sel else (2 if is_kf else 1)

                    painter.setPen(QPen(QColor(base_color.red(), base_color.green(), base_color.blue(), alpha_border), pen_width, pen_style))
                    painter.setBrush(QBrush(QColor(base_color.red(), base_color.green(), base_color.blue(), alpha_fill)))

                    kf_tag = f"f{self.current_frame+1} [Key]" if is_kf else "Interp"
                    prefix = "[+] Inc" if is_inc else "[-] Exc"
                    label_text = f"{prefix}: {m_obj.name} ({kf_tag})"

                    if minfo["type"] == "poly" and minfo.get("points"):
                        poly_pts = minfo["points"]
                        if len(poly_pts) >= 3:
                            qpoly = [QPointF(offset_x + (p[0] / self.orig_w) * pw, offset_y + (p[1] / self.orig_h) * ph) for p in poly_pts]
                            painter.drawPolygon(qpoly)
                            for i, pt in enumerate(qpoly):
                                painter.drawEllipse(pt, 3, 3)
                                if is_sel and self.interaction_mode == "select":
                                    if self.selected_vertex_idx == i:
                                        painter.setBrush(QBrush(QColor(255, 255, 255)))
                                        painter.drawRect(QRectF(pt.x() - 4, pt.y() - 4, 8, 8))
                                    else:
                                        painter.setBrush(QBrush(base_color))
                                        painter.drawRect(QRectF(pt.x() - 3, pt.y() - 3, 6, 6))
                                elif is_sel:
                                    painter.setBrush(QBrush(base_color))
                                    painter.drawRect(QRectF(pt.x() - 3, pt.y() - 3, 6, 6))
                            if is_active_layer:
                                top_pt = min(qpoly, key=lambda p: p.y())
                                painter.setPen(base_color)
                                painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
                                painter.drawText(int(top_pt.x() + 4), int(top_pt.y() - 4), label_text)

                # Draw Clicked Point Markers for each layer
                for idx, (f_num, ox, oy) in enumerate(layer.points, start=1):
                    disp_x = offset_x + (ox / self.orig_w) * pw
                    disp_y = offset_y + (oy / self.orig_h) * ph
                    is_keyframe = (f_num == self.current_frame)
                    l_qcol = QColor(layer.color)

                    if is_keyframe:
                        painter.setPen(QPen(l_qcol, 2))
                        painter.drawLine(int(disp_x - 10), int(disp_y), int(disp_x + 10), int(disp_y))
                        painter.drawLine(int(disp_x), int(disp_y - 10), int(disp_x), int(disp_y + 10))
                        painter.setBrush(QBrush(QColor("#ef4444")))
                        painter.drawEllipse(QPointF(disp_x, disp_y), 4, 4)
                        painter.setPen(QColor("#ffffff"))
                        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
                        painter.drawText(int(disp_x + 6), int(disp_y - 6), f"#{idx} ({layer.name})")
                    else:
                        painter.setPen(QPen(QColor(l_qcol.red(), l_qcol.green(), l_qcol.blue(), 100), 1, Qt.DashLine))
                        painter.drawEllipse(QPointF(disp_x, disp_y), 5, 5)
                        painter.setPen(QColor(160, 165, 175, 120))
                        painter.setFont(QFont("Segoe UI", 8))
                        painter.drawText(int(disp_x + 6), int(disp_y - 3), f"#{idx} @ f{f_num+1}")

        # The last 2D result (roadmap 2.1). Squares, so they read as something
        # the solve produced rather than as the round un-solved points the
        # artist placed; a corrected frame is filled and ringed in the OK
        # colour, and the marker being dragged trails a line from where the
        # solve had put it.
        if self.show_tracked_points and self.tracked_layers and not self.is_overlay_active:
            t_idx = self.tracked_index_for_frame()
            if t_idx is not None:
                sx, sy = pw / float(self.orig_w or 1), ph / float(self.orig_h or 1)
                painter.setFont(QFont("Segoe UI", 8))
                for l_name, n, px, py in self.tracked_points_at():
                    layer = self._layer_by_name(l_name)
                    col = QColor(layer.color if layer is not None else ACCENT)
                    corrected = layer is not None and layer.correction_at(self.current_frame, n)
                    dx = offset_x + px * sx
                    dy = offset_y + py * sy

                    if corrected:
                        painter.setPen(QPen(QColor(OK), 1.6))
                        painter.setBrush(QBrush(QColor(OK)))
                        painter.drawRect(QRectF(dx - 3.5, dy - 3.5, 7, 7))
                        painter.setBrush(Qt.NoBrush)
                        painter.drawEllipse(QPointF(dx, dy), 7, 7)
                    else:
                        painter.setPen(QPen(col, 1.4))
                        painter.setBrush(Qt.NoBrush)
                        painter.drawRect(QRectF(dx - 3.5, dy - 3.5, 7, 7))
                        painter.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 200), 1))
                        painter.drawPoint(QPointF(dx, dy))

                    # Numbering only while there are few enough to read; a dense
                    # grid would be a wall of text over the plate.
                    if len(self.tracked_layers) and self.tracked_layers[l_name]["tracks"].shape[1] <= 24:
                        painter.setPen(QColor(TEXT))
                        painter.drawText(int(dx + 7), int(dy - 5), f"#{n + 1}")

                    if self.tracked_drag == (l_name, n) and self.tracked_drag_pos:
                        hx = offset_x + self.tracked_drag_pos[0] * sx
                        hy = offset_y + self.tracked_drag_pos[1] * sy
                        painter.setPen(QPen(QColor(WARN), 1, Qt.DotLine))
                        painter.drawLine(QPointF(dx, dy), QPointF(hx, hy))
                        painter.setPen(QPen(QColor(WARN), 1.8))
                        painter.drawLine(int(hx - 8), int(hy), int(hx + 8), int(hy))
                        painter.drawLine(int(hx), int(hy - 8), int(hx), int(hy + 8))

        # Solved 3D points, drawn only while the Scene setup panel is picking so
        # the ordinary tracking view is not buried under thousands of dots.
        if self.show_solved_points:
            plate = self._solved_in_plate_coords()
            if plate is not None:
                vis = self.solved_visible
                if vis is None or len(vis) != len(plate):
                    vis = np.ones(len(plate), dtype=bool)
                sx, sy = pw / float(self.orig_w or 1), ph / float(self.orig_h or 1)
                shown = plate[vis]
                # One drawPoints call rather than thousands of drawEllipse: a
                # sparse cloud is routinely 10,000 points and this is repainted
                # on every frame step.
                pen = QPen(QColor(WARN), 3.0)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                painter.setBrush(Qt.NoBrush)
                painter.drawPoints(QPolygonF(
                    [QPointF(offset_x + x * sx, offset_y + y * sy) for x, y in shown]))

                # The picked ones are numbered in the order they were picked -
                # scale wants to know which two, ground which three.
                painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
                for order, idx in enumerate(self.solved_selection, start=1):
                    if not (0 <= idx < len(plate)) or not vis[idx]:
                        continue
                    cx_sel = offset_x + plate[idx][0] * sx
                    cy_sel = offset_y + plate[idx][1] * sy
                    painter.setPen(QPen(QColor(TEXT), 1.6))
                    painter.setBrush(Qt.NoBrush)
                    painter.drawEllipse(QPointF(cx_sel, cy_sel), 6, 6)
                    painter.drawText(int(cx_sel + 8), int(cy_sel - 6), str(order))

        # Draw Active Drag Rectangle preview
        if self.drag_start and self.drag_current:
            x1, y1 = self.drag_start
            x2, y2 = self.drag_current
            rx1 = offset_x + (min(x1, x2) / self.orig_w) * pw
            ry1 = offset_y + (min(y1, y2) / self.orig_h) * ph
            rw = (abs(x2 - x1) / self.orig_w) * pw
            rh = (abs(y2 - y1) / self.orig_h) * ph
            is_inc = (self.interaction_mode == "inclusion_box")
            col = QColor("#10b981") if is_inc else QColor("#ef4444")
            painter.setPen(QPen(col, 1.5, Qt.DashLine))
            painter.setBrush(QBrush(QColor(col.red(), col.green(), col.blue(), 40)))
            painter.drawRect(QRectF(rx1, ry1, rw, rh))

        # Draw Active Polygon In-Progress
        if self.current_poly:
            is_inc = (self.interaction_mode == "inclusion_poly")
            col = QColor("#10b981") if is_inc else QColor("#ef4444")
            painter.setPen(QPen(col, 1.5, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            qpts = [QPointF(offset_x + (p[0] / self.orig_w) * pw, offset_y + (p[1] / self.orig_h) * ph) for p in self.current_poly]
            for i in range(len(qpts) - 1):
                painter.drawLine(qpts[i], qpts[i+1])
            if self.hover_pos:
                hx = offset_x + (self.hover_pos[0] / self.orig_w) * pw
                hy = offset_y + (self.hover_pos[1] / self.orig_h) * ph
                painter.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 140), 1, Qt.DotLine))
                painter.drawLine(qpts[-1], QPointF(hx, hy))
            for pt in qpts:
                painter.setBrush(QBrush(col))
                painter.drawEllipse(pt, 3, 3)

            # HUD Help Pill for Polygon
            painter.setPen(QPen(QColor(40, 42, 54), 1))
            painter.setBrush(QBrush(QColor(16, 17, 22, 220)))
            painter.drawRoundedRect(QRectF(10, self.height() - 32, 480, 24), 4, 4)
            painter.setPen(QColor("#9ca3af"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(QRectF(10, self.height() - 32, 480, 24), Qt.AlignCenter, "Click to add vertices  |  Double-Click to Close  |  Right-Click to Cancel")

        # Top-Left HUD Info Pill
        alpha_tag = "  |  ALPHA MATTE" if self.view_alpha_mode else ""
        # Say when the solved result is on screen, and when this frame is not
        # one the result covers - an artist dragging at nothing deserves to know.
        if self.show_tracked_points and self.tracked_layers:
            alpha_tag += ("  |  RESULT" if self.tracked_index_for_frame() is not None
                          else "  |  RESULT (not on this frame)")
        hud_text = f"{self.orig_w}x{self.orig_h}  |  {self.fps:.1f} FPS  |  Frame {self.current_frame+1:04d} / {max(1, self.total_frames):04d}{alpha_tag}"
        hud_font = QFont("Consolas", 9.5, QFont.Bold)
        painter.setFont(hud_font)
        fm = painter.fontMetrics()
        pill_w = fm.horizontalAdvance(hud_text) + 24
        pill_rect = QRectF(10, 10, pill_w, 24)
        painter.setPen(QPen(QColor(40, 42, 54), 1))
        painter.setBrush(QBrush(QColor(16, 17, 22, 220)))
        painter.drawRoundedRect(pill_rect, 4, 4)
        painter.setPen(QColor("#38bdf8" if not self.view_alpha_mode else "#34d399"))
        painter.drawText(pill_rect, Qt.AlignCenter, hud_text)

        # Draw 4x Precision Loupe Magnifier (if active)
        if self.show_loupe and self.hover_pos and self.current_pixmap:
            hx, hy = self.hover_pos
            cx = offset_x + (hx / self.orig_w) * pw
            cy = offset_y + (hy / self.orig_h) * ph

            loupe_radius = 48.0
            loupe_cx = cx + 70 if cx < self.width() - 140 else cx - 70
            loupe_cy = cy - 70 if cy > 140 else cy + 70

            crop_size = 24
            crop_x = int(max(0, min(self.orig_w - crop_size, hx - crop_size / 2)))
            crop_y = int(max(0, min(self.orig_h - crop_size, hy - crop_size / 2)))

            patch = self.current_pixmap.copy(crop_x, crop_y, crop_size, crop_size)
            if not patch.isNull():
                painter.save()
                path = QPainterPath()
                path.addEllipse(QPointF(loupe_cx, loupe_cy), loupe_radius, loupe_radius)
                painter.setClipPath(path)

                scaled_patch = patch.scaled(int(loupe_radius * 2), int(loupe_radius * 2), Qt.KeepAspectRatio, Qt.FastTransformation)
                painter.drawPixmap(int(loupe_cx - loupe_radius), int(loupe_cy - loupe_radius), scaled_patch)

                # Crosshair
                painter.setPen(QPen(QColor(56, 189, 248, 180), 1))
                painter.drawLine(int(loupe_cx - loupe_radius), int(loupe_cy), int(loupe_cx + loupe_radius), int(loupe_cy))
                painter.drawLine(int(loupe_cx), int(loupe_cy - loupe_radius), int(loupe_cx), int(loupe_cy + loupe_radius))
                painter.restore()

                # Loupe Border Ring
                painter.setPen(QPen(QColor("#38bdf8"), 1.5))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(loupe_cx, loupe_cy), loupe_radius, loupe_radius)

                # Coordinate Badge
                coord_rect = QRectF(loupe_cx - 40, loupe_cy + loupe_radius + 4, 80, 18)
                painter.setPen(QPen(QColor(40, 42, 54), 1))
                painter.setBrush(QBrush(QColor(16, 17, 22, 220)))
                painter.drawRoundedRect(coord_rect, 3, 3)
                painter.setFont(QFont("Consolas", 8, QFont.Bold))
                painter.setPen(QColor("#d1d5db"))
                painter.drawText(coord_rect, Qt.AlignCenter, f"X:{int(hx)} Y:{int(hy)}")
