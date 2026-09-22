"""
Interactive Video Preview Canvas with Multi-Mask Roto & Point Tracking HUD
"""

import uuid
import numpy as np
from pathlib import Path
from PySide6.QtWidgets import QLabel, QMenu
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import (
    QFont, QColor, QPixmap, QPainter, QPen, QBrush, QPainterPath,
    QDragEnterEvent, QDropEvent
)

from mask_animator import AnimatedMask
from core.tracking_layer import TrackingLayer, point_in_poly


class VideoPointPickerCanvas(QLabel):
    point_added = Signal(int, float, float)
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

        if self.interaction_mode == "select":
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
