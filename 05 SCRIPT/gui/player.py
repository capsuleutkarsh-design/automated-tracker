"""
The 2D player: the transport, the frame in view, the keyframes and the range.

Everything the artist uses to move through a shot. It used to be spread across
the main window - a slider handler here, a playback timer there, the timecode
inside the frame loader - which meant that changing how a frame is shown meant
reading the whole window first.

It owns its widgets: the transport bar, and the Keys and Range clusters of the
roto bar beneath the viewport. build_2d_tab decides where they sit and nothing
else; every button here is wired to a method on this object.

It talks to the rest of the app through the AppContext, and the window drives
it by name - `win.player.pause_playback()` - rather than through a signal,
because the one thing the window has to react to, the playhead moving, it
already hears straight from the slider's own valueChanged.
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPen, QColor
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QMessageBox, QSlider

from core.media_info import sequence_files
from gui.theme import OK
from gui.ui_kit import divider, group_label, make_button, strip

# The window's logger on purpose: a frame that will not display is a line in
# app.log the artist may send in, and moving this code must not move the name
# that line is written under.
log = logging.getLogger("tracker_gui")


class MarkedSlider(QSlider):
    """
    The transport slider, with a tick on every frame the artist has corrected.

    A correction is a decision about one frame out of several hundred, and
    scrubbing to find it again is the kind of hunting the tool exists to
    remove - so the frames that carry one are drawn straight onto the
    timeline. Qt's own tick marks are evenly spaced, so these are painted here.
    """

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.marks = ()

    def set_marks(self, frames):
        """Which frame indices to mark. Repaints only when the set changed."""
        marks = tuple(sorted({int(f) for f in (frames or ())}))
        if marks != self.marks:
            self.marks = marks
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.marks:
            return
        span = max(1, self.maximum() - self.minimum())
        usable = max(1, self.width() - 12)
        painter = QPainter(self)
        painter.setPen(QPen(QColor(OK), 2))
        for f in self.marks:
            if not (self.minimum() <= f <= self.maximum()):
                continue
            x = 6 + int(round((f - self.minimum()) / span * usable))
            painter.drawLine(x, 2, x, 8)


class PlayerController:
    """
    The player's state and its widgets.

    Built by the window before the 2D tab, so the tab can ask it for the bars
    it owns at the point they belong in the layout.
    """

    def __init__(self, ctx):
        self.ctx = ctx

        # Video player state. `overlay_frames` holds the rendered motion-trail
        # video when the artist is looking at it, `loaded_video_frames` a clip
        # decoded into memory; either one short-circuits the frame loader.
        self.overlay_frames = None
        self.loaded_video_frames = None
        self.current_play_frame = 0
        self.is_playing = False
        # J/K/L shuttle (2.5): which way playback is running and how many
        # frames it moves per tick. 1 forward at 1x is ordinary play.
        self.play_direction = 1
        self.play_speed = 1
        self.play_timer = QTimer()
        self.play_timer.timeout.connect(self._on_play_timer_tick)

    @property
    def canvas(self):
        return self.ctx.canvas

    def _register(self, name, widget):
        """Keep the widget here, and publish it under the name the app uses."""
        setattr(self, name, widget)
        setattr(self.ctx.ui, name, widget)

    # =====================================================================
    # THE BARS THIS OWNS
    # =====================================================================
    def build_transport_bar(self):
        """Step, play, scrub, timecode, loop and the clean/overlay switch."""
        t_frame, tbar = strip(spacing=7)

        self._register("btn_step_back", make_button(
            "◀", "Step back one frame  (←, or Shift+← for ten)", "transport"))
        self.btn_step_back.clicked.connect(self.step_back_frame)

        self._register("btn_play_pause", make_button(
            "▶  Play", "Play / pause  (Space, or J / K / L to shuttle)", "transportPlay"))
        self.btn_play_pause.clicked.connect(self.toggle_playback)

        self._register("btn_step_fwd", make_button(
            "▶", "Step forward one frame  (→, or Shift+→ for ten)", "transport"))
        self.btn_step_fwd.clicked.connect(self.step_fwd_frame)

        self._register("slider_2d_frame", MarkedSlider(Qt.Horizontal))
        self.slider_2d_frame.setRange(0, 0)
        self.slider_2d_frame.setFixedHeight(30)
        self.slider_2d_frame.setToolTip(
            "Scrub the timeline.\n"
            "Green ticks are frames where you have corrected a tracked point.")
        self.slider_2d_frame.valueChanged.connect(self.on_frame_slider_changed)

        self._register("lbl_frame_idx", QLabel("00:00:00:00  (1/1)"))
        self.lbl_frame_idx.setObjectName("valueChip")
        self.lbl_frame_idx.setAlignment(Qt.AlignCenter)
        self.lbl_frame_idx.setMinimumWidth(150)
        self.lbl_frame_idx.setToolTip("Timecode and frame, at the clip's real frame rate")

        self._register("chk_loop", QCheckBox("Loop"))
        self.chk_loop.setChecked(True)
        self.chk_loop.setToolTip(
            "Restart playback from the first frame when it reaches the end")

        self._register("combo_view_layer", QComboBox())
        self.combo_view_layer.addItems(["Clean Video", "Motion Overlay"])
        self.combo_view_layer.setFixedWidth(148)
        self.combo_view_layer.setToolTip(
            "Switch between the plate and the rendered motion trails")
        self.combo_view_layer.currentIndexChanged.connect(self.on_view_layer_changed)

        tbar.addWidget(self.btn_step_back)
        tbar.addWidget(self.btn_play_pause)
        tbar.addWidget(self.btn_step_fwd)
        tbar.addWidget(self.slider_2d_frame, 1)
        tbar.addWidget(self.lbl_frame_idx)
        divider(tbar, vertical=True)
        tbar.addWidget(self.chk_loop)
        tbar.addWidget(self.combo_view_layer)
        return t_frame

    def add_keys_cluster(self, kbar):
        """The Keys cluster of the roto bar, appended to the bar as it is."""
        kbar.addWidget(group_label("Keys"))
        self._register("btn_prev_key", make_button(
            "◀ Prev", "Previous mask keyframe  ( , or [ )", "compact"))
        self.btn_prev_key.clicked.connect(self.jump_prev_keyframe)
        self._register("btn_set_key", make_button(
            "Set", "Create or update a keyframe here", "compact"))
        self.btn_set_key.clicked.connect(self.set_mask_keyframe_on_current)
        self._register("btn_del_key", make_button(
            "Del", "Delete the keyframe here  (Del)", "compact"))
        self.btn_del_key.clicked.connect(self.delete_mask_keyframe_on_current)
        self._register("btn_next_key", make_button(
            "Next ▶", "Next mask keyframe  ( . or ] )", "compact"))
        self.btn_next_key.clicked.connect(self.jump_next_keyframe)
        self._register("btn_del_mask", make_button(
            "Del Mask", "Delete the selected mask with all its keyframes  (Shift+Del)",
            "compact"))
        self.btn_del_mask.clicked.connect(self.canvas.delete_selected_mask)

        self._register("lbl_key_status", QLabel("◆ f1"))
        self.lbl_key_status.setObjectName("valueChip")
        self.lbl_key_status.setAlignment(Qt.AlignCenter)
        self.lbl_key_status.setMinimumWidth(74)

        for wdg in (self.btn_prev_key, self.btn_set_key, self.btn_del_key,
                    self.btn_next_key, self.btn_del_mask, self.lbl_key_status):
            kbar.addWidget(wdg)

    def add_range_cluster(self, kbar):
        """The Range cluster: the in and out points the tracker works between."""
        kbar.addWidget(group_label("Range"))
        self._register("btn_set_in", make_button(
            "In", "Set the tracking in-point  (I, Alt+I clears it)", "compact"))
        self.btn_set_in.clicked.connect(lambda: self.set_in_point(self.canvas.current_frame))
        self._register("btn_set_out", make_button(
            "Out", "Set the tracking out-point  (O, Alt+O clears it)", "compact"))
        self.btn_set_out.clicked.connect(lambda: self.set_out_point(self.canvas.current_frame))
        self._register("btn_reset_range", make_button(
            "Reset", "Track the whole clip again", "compact"))
        self.btn_reset_range.clicked.connect(self.reset_tracking_range)

        self._register("lbl_range_status", QLabel("Full"))
        self.lbl_range_status.setObjectName("valueChip")
        self.lbl_range_status.setAlignment(Qt.AlignCenter)
        self.lbl_range_status.setMinimumWidth(74)

        for wdg in (self.btn_set_in, self.btn_set_out, self.btn_reset_range,
                    self.lbl_range_status):
            kbar.addWidget(wdg)

    # =====================================================================
    # TRANSPORT
    # =====================================================================
    def toggle_playback(self):
        if self.is_playing:
            self.pause_playback()
        else:
            self.start_playback()

    def start_playback(self, direction=1, speed=1):
        self.is_playing = True
        self.play_direction = 1 if int(direction) >= 0 else -1
        self.play_speed = max(1, int(speed))
        self._update_transport_button()
        fps = self.ctx.fps if self.ctx.fps and self.ctx.fps > 0 else 24.0
        self.play_timer.start(max(10, int(round(1000.0 / fps))))

    def pause_playback(self):
        self.is_playing = False
        self.play_direction = 1
        self.play_speed = 1
        self._update_transport_button()
        self.play_timer.stop()

    def _update_transport_button(self):
        """The play button says which way and how fast, the way a deck does."""
        if not self.is_playing:
            self.btn_play_pause.setText("▶  Play")
            return
        arrow = "▶" if self.play_direction > 0 else "◀"
        self.btn_play_pause.setText(
            "⏸ Pause" if self.play_speed == 1 and self.play_direction > 0
            else "⏸ %s %dx" % (arrow, self.play_speed))

    def shuttle(self, direction):
        """
        J and L, the way an editorial timeline shuttles.

        Pressing the key for the way it is already going doubles the speed;
        pressing the other one stops first, because an artist hammering J to
        crawl backwards out of a forward play expects the plate to stop, not to
        lurch straight into reverse.
        """
        direction = 1 if int(direction) >= 0 else -1
        if not self.is_playing:
            self.start_playback(direction, 1)
        elif self.play_direction == direction:
            self.start_playback(direction, min(16, self.play_speed * 2))
        else:
            self.pause_playback()

    def step_back_frame(self):
        self.step_frames(-1)

    def step_fwd_frame(self):
        self.step_frames(1)

    def step_frames(self, offset):
        """Step by N frames, clamped to the clip. Stepping always stops playback."""
        self.pause_playback()
        cur = self.slider_2d_frame.value()
        target = max(0, min(self.slider_2d_frame.maximum(), cur + int(offset)))
        if target != cur:
            self.slider_2d_frame.setValue(target)

    def go_to_frame(self, frame_idx):
        """Home and End. Also stops playback - a jump is a decision to look."""
        self.pause_playback()
        target = max(0, min(self.slider_2d_frame.maximum(), int(frame_idx)))
        if target != self.slider_2d_frame.value():
            self.slider_2d_frame.setValue(target)

    def _on_play_timer_tick(self):
        max_f = self.slider_2d_frame.maximum()
        if max_f <= 0:
            self.pause_playback()
            return

        cur = self.slider_2d_frame.value()
        step = self.play_direction * self.play_speed
        nxt = cur + step
        if 0 <= nxt <= max_f:
            self.slider_2d_frame.setValue(nxt)
        elif self.chk_loop.isChecked():
            # Wrap to the far end, so a looping reverse play runs the shot
            # backwards over and over instead of stopping dead at the head.
            self.slider_2d_frame.setValue(max_f if step < 0 else 0)
        else:
            self.slider_2d_frame.setValue(max(0, min(max_f, nxt)))
            self.pause_playback()

    def on_frame_slider_changed(self, val):
        if self.overlay_frames is not None and len(self.overlay_frames) > 0 and 0 <= val < len(self.overlay_frames):
            self.load_frame_preview(self.overlay_frames[val], val, len(self.overlay_frames))
            return

        if self.loaded_video_frames is not None and len(self.loaded_video_frames) > 0 and 0 <= val < len(self.loaded_video_frames):
            self.load_frame_preview(self.loaded_video_frames[val], val, len(self.loaded_video_frames))
            return

        video_name = self.ctx.current_clip_name()
        if not video_name:
            return
        video_path = self.ctx.videos_dir / video_name
        if video_path.is_dir():
            files = sequence_files(video_path)
            if 0 <= val < len(files):
                self.load_frame_preview(files[val], val, len(files))
                self.update_keyframe_status()
            return
        scene_images_dir = self.ctx.scenes_dir / video_path.stem / "images"
        if scene_images_dir.exists():
            jpgs = sorted(list(scene_images_dir.glob("*.jpg")))
            if 0 <= val < len(jpgs):
                self.load_frame_preview(jpgs[val], val, len(jpgs))
                self.update_keyframe_status()
                return

        # The clip's real rate, not 24 (B13): on a 30 fps clip the old maths
        # showed the wrong frame while the cache was still being built.
        fps = self.ctx.fps if self.ctx.fps and self.ctx.fps > 0 else 24.0
        sec = val / fps
        tmp_img = self.ctx.media.thumbnail_for(video_path, val, "-ss", f"{sec:.3f}", "-noaccurate_seek")
        if tmp_img is not None:
            total_f = max(1, self.slider_2d_frame.maximum() + 1)
            self.load_frame_preview(tmp_img, val, total_f)

        self.update_keyframe_status()

    def load_frame_preview(self, img_path_or_array, frame_idx, total_frames):
        from PIL import Image
        try:
            if isinstance(img_path_or_array, (str, Path)):
                im = Image.open(img_path_or_array).convert("RGB")
            else:
                im = Image.fromarray(img_path_or_array).convert("RGB")
            w, h = im.size
            qim = QImage(im.tobytes(), w, h, w * 3, QImage.Format_RGB888)
            fps = self.ctx.fps if self.ctx.fps and self.ctx.fps > 0 else 24.0
            self.canvas.set_frame_image(qim, frame_idx, total_frames, w, h, fps=fps)

            fps_int = max(1, int(round(fps)))
            total_sec = frame_idx / fps
            hrs = int(total_sec // 3600)
            mins = int((total_sec % 3600) // 60)
            secs = int(total_sec % 60)
            fr = int(frame_idx % fps_int)
            self.lbl_frame_idx.setText(f"{hrs:02d}:{mins:02d}:{secs:02d}:{fr:02d} ({frame_idx+1}/{total_frames})")
        except Exception as e:
            log.exception("frame preview failed")
            self.ctx.status("Could not display frame %d: %s" % (frame_idx + 1, e), error=True)

    def on_view_layer_changed(self, idx):
        if idx == 0:
            self.canvas.is_overlay_active = False
            self.overlay_frames = None
            cur = self.slider_2d_frame.value()
            self.on_frame_slider_changed(cur)
            self.ctx.log_2d("👁️ Switched view to Clean Raw Video.", self.ctx.ACCENT)
        elif idx == 1:
            self.load_overlay_into_player()

    def load_overlay_into_player(self):
        v_name = self.ctx.current_clip_name()
        if not v_name:
            return
        overlay_file = self.ctx.media.find_latest_overlay(Path(v_name).stem)
        if not overlay_file or not overlay_file.exists():
            QMessageBox.information(self.ctx.dialog_parent(), "Overlay Not Found", "Run 2D Point Tracking first to generate the motion overlay video!")
            self.combo_view_layer.blockSignals(True)
            self.combo_view_layer.setCurrentIndex(0)
            self.combo_view_layer.blockSignals(False)
            return

        self.canvas.is_overlay_active = True
        self.combo_view_layer.blockSignals(True)
        self.combo_view_layer.setCurrentIndex(1)
        self.combo_view_layer.blockSignals(False)

        self.ctx.log_2d("Loading motion overlay video into built-in player...", self.ctx.ACCENT)
        try:
            import imageio.v3 as iio
            self.overlay_frames = iio.imread(str(overlay_file), plugin="FFMPEG")
            self.slider_2d_frame.setRange(0, len(self.overlay_frames) - 1)
            self.slider_2d_frame.setValue(0)
            self.load_frame_preview(self.overlay_frames[0], 0, len(self.overlay_frames))
            self.start_playback()
            self.ctx.log_2d(f"✔ Loaded {len(self.overlay_frames)} overlay frames into player.", self.ctx.OK)
        except Exception as e:
            self.ctx.log_2d(f"Notice: Loading overlay failed: {e}", self.ctx.WARN)

    def jump_prev_keyframe(self):
        cur_f = self.slider_2d_frame.value()
        all_keys = self.canvas.active_layer.get_all_keyframe_frames() if self.canvas.active_layer else []
        prev_keys = [k for k in all_keys if k < cur_f]
        if prev_keys:
            self.slider_2d_frame.setValue(max(prev_keys))
        elif all_keys:
            self.slider_2d_frame.setValue(all_keys[0])

    def jump_next_keyframe(self):
        cur_f = self.slider_2d_frame.value()
        all_keys = self.canvas.active_layer.get_all_keyframe_frames() if self.canvas.active_layer else []
        next_keys = [k for k in all_keys if k > cur_f]
        if next_keys:
            self.slider_2d_frame.setValue(min(next_keys))
        elif all_keys:
            self.slider_2d_frame.setValue(all_keys[-1])

    def set_mask_keyframe_on_current(self):
        layer = self.canvas.active_layer
        if not layer or not layer.animated_masks:
            QMessageBox.information(self.ctx.dialog_parent(), "No Masks", "Draw an Exclusion or Inclusion mask first, then set keyframes as objects move!")
            return
        cur_f = self.slider_2d_frame.value()
        target_masks = [m for m in layer.animated_masks if m.id == self.canvas.selected_mask_id] if self.canvas.selected_mask_id else layer.animated_masks
        before = self.canvas.mask_snapshot()
        for m in target_masks:
            geom = m.get_interpolated_geometry(cur_f)
            if geom:
                m.set_keyframe(cur_f, geom["points"], "poly")
        self.canvas.push_mask_edit("Set a mask keyframe", before)
        self.canvas.masks_changed.emit()
        self.canvas.update()
        self.update_keyframe_status()
        self.ctx.log_2d(f"🔷 Keyframe created/updated at Frame {cur_f+1} on [{layer.name}].", self.ctx.OK)

    def delete_mask_keyframe_on_current(self):
        layer = self.canvas.active_layer
        if not layer or not layer.animated_masks:
            return
        cur_f = self.slider_2d_frame.value()
        target_masks = [m for m in layer.animated_masks if m.id == self.canvas.selected_mask_id] if self.canvas.selected_mask_id else layer.animated_masks
        before = self.canvas.mask_snapshot()
        deleted = False
        for m in target_masks:
            if m.delete_keyframe(cur_f):
                deleted = True
        if deleted:
            self.canvas.push_mask_edit("Delete a mask keyframe", before)
            self.canvas.masks_changed.emit()
            self.canvas.update()
            self.update_keyframe_status()
            self.ctx.log_2d(f"🗑 Deleted keyframe at Frame {cur_f+1}.", self.ctx.WARN)

    def update_keyframe_status(self):
        cur_f = self.slider_2d_frame.value()
        layer = self.canvas.active_layer
        all_keys = layer.get_all_keyframe_frames() if layer else []
        is_kf = cur_f in all_keys
        if is_kf:
            self.lbl_key_status.setText(f"◆ f{cur_f+1}")
            self.ctx.set_chip_state(self.lbl_key_status, "key")
        elif all_keys:
            self.lbl_key_status.setText(f"~ f{cur_f+1}")
            self.ctx.set_chip_state(self.lbl_key_status, "interp")
        else:
            self.lbl_key_status.setText("No masks")
            self.ctx.set_chip_state(self.lbl_key_status, "idle")

    def set_in_point(self, frame_idx):
        # Through the undo stack, because trimming the range is a decision the
        # artist can want back - and the chip redraws from range_changed.
        if self.canvas.push_range(in_point=int(frame_idx),
                                  text="Set the tracking in-point"):
            self.ctx.log_2d(
                f"📍 Set Tracking In-Point to Frame {self.canvas.in_point+1}.", self.ctx.ACCENT)

    def set_out_point(self, frame_idx):
        if self.canvas.push_range(out_point=int(frame_idx),
                                  text="Set the tracking out-point"):
            self.ctx.log_2d(
                f"📍 Set Tracking Out-Point to Frame {self.canvas.out_point+1}.", self.ctx.ACCENT)

    def clear_in_point(self):
        """Alt+I: track from the head again."""
        if self.canvas.push_range(in_point=0, text="Clear the in-point"):
            self.ctx.log_2d("↺ Cleared the tracking in-point.", self.ctx.ACCENT)

    def clear_out_point(self):
        """Alt+O: track to the tail again."""
        if self.canvas.push_range(out_point=-1, text="Clear the out-point"):
            self.ctx.log_2d("↺ Cleared the tracking out-point.", self.ctx.ACCENT)

    def reset_tracking_range(self):
        if self.canvas.push_range(in_point=0, out_point=-1,
                                  text="Reset the tracking range"):
            self.ctx.log_2d("↺ Reset Tracking Range to full sequence.", self.ctx.ACCENT)

    def sync_range_status(self):
        """Redraw the in/out chip from whatever the canvas now holds."""
        canvas = self.canvas
        if canvas.in_point == 0 and canvas.out_point < 0:
            self.lbl_range_status.setText("Full")
            self.ctx.set_chip_state(self.lbl_range_status, "idle")
            return
        out_p = canvas.out_point if canvas.out_point >= 0 else self.slider_2d_frame.maximum()
        self.lbl_range_status.setText(f"{canvas.in_point + 1} – {out_p + 1}")
        self.ctx.set_chip_state(self.lbl_range_status, "key")
