"""
Fixing a drifting 2D track  (roadmap 2.1).

The result of the last solve is loaded back from tracks_2d.json, drawn on the
plate, and corrected by dragging a marker onto the feature it slid off.
"Re-track from here" then puts that ONE point back through CoTracker from that
frame, and the new positions are spliced into the stored result from the
correction onward - everything the artist already accepted in front of it is
left alone.

This owns the Fix cluster of the roto bar and the loaded result behind it. It
does not own the thread: deciding WHAT job to run is the decision made here,
but starting a QThread and holding it while it runs is the window's, so the
job description is handed to `ctx.start_correction_worker`.
"""

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMessageBox

from core.media_pool import find_latest_output
from gui.ui_kit import group_label, make_button

# The window's logger on purpose: an unreadable result is a line in app.log the
# artist may send in, and moving this code must not move the name it is
# written under.
log = logging.getLogger("tracker_gui")


class CorrectionController:
    """
    The Fix cluster, the result it works on and the corrections made to it.

    Built by the window before the 2D tab, which asks it for its cluster at
    the point it belongs in the roto bar.
    """

    def __init__(self, ctx):
        self.ctx = ctx
        # The last 2D result for the shot in view, loaded back from
        # tracks_2d.json so a correction still works after the app has been
        # closed and reopened. `_track_layer_map` says which layer in the
        # window owns which block of the file, `_track_problem` is the sentence
        # shown when the file does not match the layers, and `_last_correction`
        # is what the Re-track buttons act on when the playhead is not sitting
        # on a corrected frame.
        self._track_result = None
        self._track_layer_map = None
        self._track_problem = ""
        self._last_correction = None

    @property
    def canvas(self):
        return self.ctx.canvas

    @property
    def result(self):
        """The loaded result, for the window's own checks. None when there is none."""
        return self._track_result

    @result.setter
    def result(self, value):
        self._track_result = value

    def _register(self, name, widget):
        """Keep the widget here, and publish it under the name the app uses."""
        setattr(self, name, widget)
        setattr(self.ctx.ui, name, widget)

    # =====================================================================
    # THE FIX CLUSTER
    # =====================================================================
    def add_fix_cluster(self, kbar):
        """The Fix cluster of the roto bar, appended to the bar as it is."""
        kbar.addWidget(group_label("Fix"))
        self._register("btn_show_result", make_button(
            "Result",
            "Draw the last 2D result on the plate.\n"
            "Drag a marker to correct it on that frame, then re-track from there.",
            "toggle", checkable=True))
        self.btn_show_result.toggled.connect(self.toggle_tracked_result)

        self._register("btn_prev_fix", make_button(
            "◀ Fix", "Jump to the previous corrected frame", "compact"))
        self.btn_prev_fix.clicked.connect(lambda: self.jump_correction(-1))
        self._register("btn_next_fix", make_button(
            "Fix ▶", "Jump to the next corrected frame", "compact"))
        self.btn_next_fix.clicked.connect(lambda: self.jump_correction(1))

        self._register("btn_retrack_fwd", make_button(
            "Re-track ▶",
            "Re-track the corrected point from this frame to the out point,\n"
            "and splice the new positions into the saved result.", "compact"))
        self.btn_retrack_fwd.clicked.connect(lambda: self.retrack_correction(False))

        self._register("btn_retrack_both", make_button(
            "Re-track ◀▶",
            "Re-track the corrected point forward to the out point and\n"
            "backwards to the in point.", "compact"))
        self.btn_retrack_both.clicked.connect(lambda: self.retrack_correction(True))

        self._register("lbl_corrections", QLabel("No result"))
        self.lbl_corrections.setObjectName("valueChip")
        self.lbl_corrections.setAlignment(Qt.AlignCenter)
        self.lbl_corrections.setMinimumWidth(96)
        self.lbl_corrections.setToolTip("Corrections stored on the active layer's result")

        for wdg in (self.btn_show_result, self.btn_prev_fix, self.btn_next_fix,
                    self.btn_retrack_fwd, self.btn_retrack_both, self.lbl_corrections):
            kbar.addWidget(wdg)

    # =====================================================================
    # THE LOADED RESULT
    # =====================================================================
    def _track_root_for(self, shot_name):
        return self.ctx.scenes_dir / shot_name / "2D_POINT_TRACK"

    def load_result_for_shot(self, shot_name):
        """
        Load the shot's last 2D result and attach it to the current layers.

        Called when the clip is picked and after a track finishes, so the
        correction tools work on a shot solved days ago just as well as on one
        solved a minute ago. A result that does not line up with the layers in
        the window leaves correction switched off with the reason on screen -
        guessing which stored track belongs to which layer would have the
        artist correcting the wrong point.
        """
        self._track_result = None
        self._track_layer_map = None
        self._track_problem = ""
        self._last_correction = None
        self.canvas.clear_tracked_result()

        if shot_name:
            json_path, _folder = find_latest_output(
                self.ctx.scenes_dir / shot_name, "2D_POINT_TRACK", "tracks_2d.json",
                legacy_subdirs=("cotracker_2d",))
            if json_path:
                try:
                    import cotracker_2d as c2d
                    result = c2d.load_tracks_2d(json_path)
                except Exception as e:
                    log.warning("could not read %s: %s", json_path, e)
                    result = None
                    self._track_problem = "The saved 2D result could not be read: %s" % e
                if result:
                    names = [l.name for l in self.canvas.layers]
                    mapping, problem = c2d.match_result_to_layers(result, names)
                    if mapping:
                        self._track_result = result
                        self._track_layer_map = mapping
                        self.ctx.log_2d(
                            "Loaded the last 2D result for '%s': %d frame(s), %s. Switch "
                            "Result on to see it and drag a point to correct it."
                            % (shot_name, result["frame_count"],
                               ", ".join("%s %d point(s)"
                                         % (n, result["layers"][k]["tracks"].shape[1])
                                         for n, k in mapping.items())), self.ctx.TEXT_DIM)
                    else:
                        self._track_problem = problem
                        self.ctx.log_2d("! %s" % problem, self.ctx.WARN)
                elif not self._track_problem:
                    self._track_problem = "The saved 2D result could not be read."

        self._push_result_to_canvas()
        self.refresh()

    def _result_in_point(self):
        """
        The clip frame the loaded result starts on.

        The file records the timeline frame its first sample sits on, so the
        clip-relative index is that minus the shot's timeline start - the same
        arithmetic the exporters did on the way out.
        """
        if not self._track_result:
            return 0
        return max(0, int(self._track_result["start_frame"])
                   - int(self.ctx.timeline_start))

    def _push_result_to_canvas(self):
        """Hand the loaded result to the canvas, keyed by the layer it belongs to."""
        canvas = self.canvas
        if not (self._track_result and self._track_layer_map):
            canvas.clear_tracked_result()
            return
        layers = {name: self._track_result["layers"][key]
                  for name, key in self._track_layer_map.items()
                  if key in self._track_result["layers"]}
        canvas.set_tracked_result(
            layers,
            in_point=self._result_in_point(),
            frame_step=self._track_result["frame_step"],
            show=bool(self.btn_show_result.isChecked()))

    def _all_corrections(self):
        """Every correction on every layer, as (layer, correction dict) pairs."""
        out = []
        for layer in self.canvas.layers:
            for c in layer.corrections:
                out.append((layer, c))
        return out

    def refresh(self):
        """Chip, timeline ticks and button states, from whatever is loaded now."""
        has_result = bool(self._track_result and self._track_layer_map)
        busy = self.ctx.correction_busy()
        marks = sorted({int(c["frame"]) for _l, c in self._all_corrections()})

        self.btn_show_result.setEnabled(has_result)
        if not has_result and self.btn_show_result.isChecked():
            # A shot with no result must not sit there claiming to show one.
            self.btn_show_result.blockSignals(True)
            self.btn_show_result.setChecked(False)
            self.btn_show_result.blockSignals(False)
        for btn in (self.btn_prev_fix, self.btn_next_fix):
            btn.setEnabled(has_result and bool(marks))
        for btn in (self.btn_retrack_fwd, self.btn_retrack_both):
            btn.setEnabled(has_result and not busy)
        self.ctx.ui.btn_reexport_2d.setEnabled(has_result and not busy)

        if has_result:
            text = "%d fix%s" % (len(marks), "" if len(marks) == 1 else "es")
            self.ctx.set_chip_state(self.lbl_corrections, "key" if marks else "idle")
            self.lbl_corrections.setToolTip(
                "Corrected frames: %s" % (", ".join(str(f + 1) for f in marks[:12]) or "none yet")
                + ("\nRe-export to write these into the delivered files." if marks else ""))
        else:
            text = "No result"
            self.ctx.set_chip_state(self.lbl_corrections, "idle")
            self.lbl_corrections.setToolTip(
                self._track_problem or "Run a 2D track to get a result you can correct.")
        self.lbl_corrections.setText(text)

        # Only the frames this shot's result actually covers get a tick; a
        # correction left over from another range would point at nothing.
        try:
            self.ctx.ui.slider_2d_frame.set_marks(marks)
        except AttributeError:
            pass

    def toggle_tracked_result(self, checked):
        """The Result toggle: draw the last solve over the plate, or stop."""
        if checked and not (self._track_result and self._track_layer_map):
            self.btn_show_result.setChecked(False)
            QMessageBox.information(
                self, "No 2D Result",
                self._track_problem or
                "There is no 2D result for this shot yet.\n\nRun 2D Point Tracking first.")
            return
        self.canvas.show_tracked_points = bool(checked)
        self._push_result_to_canvas()
        self.canvas.update()
        if checked:
            self.ctx.log_2d(
                "Showing the last 2D result. Drag a marker to correct it on this frame, "
                "then Re-track ▶ (or right-click the marker).", self.ctx.ACCENT)

    def _layer_named(self, name):
        return next((l for l in self.canvas.layers if l.name == name), None)

    def _result_block(self, layer_name):
        """The loaded arrays for a layer name, or None."""
        if not (self._track_result and self._track_layer_map):
            return None
        key = self._track_layer_map.get(layer_name)
        if key not in self._track_result["layers"]:
            return None
        return self._track_result["layers"][key]

    def on_tracked_point_moved(self, layer_name, point_index, frame, x, y):
        """A tracked marker was dragged: that frame becomes what the artist set."""
        block = self._result_block(layer_name)
        layer = self._layer_named(layer_name)
        t = self.canvas.tracked_index_for_frame(frame)
        if block is None or layer is None or t is None:
            self.ctx.log_2d(
                "The result does not cover frame %d, so there is nothing to correct there."
                % (int(frame) + 1), self.ctx.WARN)
            return
        # The canvas owns this edit now, so that one Ctrl+Z takes back both the
        # mark on the layer and the sample it wrote into the loaded result.
        self.canvas.push_correction(layer_name, int(point_index), int(frame),
                                    int(t), float(x), float(y))
        self._last_correction = (layer_name, int(point_index), int(frame))
        self.ctx.log_2d(
            "◈ Corrected [%s] point #%d on frame %d to (%.1f, %.1f). Re-track ▶ to carry "
            "it forward." % (layer_name, int(point_index) + 1, int(frame) + 1, x, y), self.ctx.OK)
        self.canvas.update()
        self.refresh()
        self.ctx.schedule_project_save()

    def on_correction_cleared(self, layer_name, point_index, frame):
        """Forget one correction. The spliced positions stay - only the mark goes."""
        if self.canvas.push_clear_correction(layer_name, int(point_index), int(frame)):
            self.ctx.log_2d(
                "Forgot the correction on [%s] point #%d, frame %d."
                % (layer_name, int(point_index) + 1, int(frame) + 1), self.ctx.TEXT_DIM)
            if self._last_correction == (layer_name, int(point_index), int(frame)):
                self._last_correction = None
            self.canvas.update()
            self.refresh()
            self.ctx.schedule_project_save()

    def on_retrack_requested(self, layer_name, point_index, frame, backwards):
        self._run_retrack(layer_name, int(point_index), int(frame), bool(backwards))

    def retrack_correction(self, backwards=False):
        """
        The Re-track buttons: work on the correction under the playhead.

        With nothing corrected on this frame the last correction is used, so
        scrubbing away to look at the fix and then pressing the button still
        does what the artist means.
        """
        frame = int(self.canvas.current_frame)
        target = None
        for layer, c in self._all_corrections():
            if int(c["frame"]) == frame:
                target = (layer.name, int(c["point"]), frame)
                break
        target = target or self._last_correction
        if not target:
            QMessageBox.information(
                self, "Nothing to Re-track",
                "Switch Result on, drag a tracked point onto the feature it slid off, "
                "and then re-track from that frame.\n\n"
                "You can also right-click any marker to re-track it from the frame in view.")
            return
        self._run_retrack(target[0], target[1], target[2], backwards)

    def _run_retrack(self, layer_name, point_index, frame, backwards):
        """Start the re-track worker for one point, from one frame."""
        if self.ctx.correction_busy():
            self.ctx.log_2d("A 2D job is already running.", self.ctx.WARN)
            return
        block = self._result_block(layer_name)
        layer = self._layer_named(layer_name)
        t = self.canvas.tracked_index_for_frame(frame)
        if block is None or layer is None or t is None:
            QMessageBox.warning(
                self, "Frame Not in the Result",
                "The saved result does not cover frame %d, so it cannot be re-tracked from "
                "there." % (int(frame) + 1))
            return

        # A marker re-tracked without being dragged starts from where the solve
        # left it - which is exactly what "re-track from here" means when the
        # track is right on this frame and wrong after it.
        c = layer.correction_at(frame, point_index)
        if c:
            x, y = float(c["x"]), float(c["y"])
        else:
            x, y = float(block["tracks"][t, point_index, 0]), float(block["tracks"][t, point_index, 1])

        v_name = self.ctx.current_clip_name()
        if not v_name:
            return
        step = int(self._track_result["frame_step"])
        in_pt = self._result_in_point()
        config = {
            "max_dimension": self.ctx.max_dimension_2d(),
            "frame_step": step,
            "in_point": in_pt,
            # Exactly the range the result covers, whatever the In/Out chips
            # say now: the spliced frames have to line up with the stored ones.
            "out_point": in_pt + (int(self._track_result["frame_count"]) - 1) * step,
            "offline": "Offline" in self.ctx.ui.combo_2d_model.currentText(),
            "auto_chunk": self.ctx.ui.chk_vram_chunk.isChecked(),
        }
        self.player.pause_playback()
        self.ctx.log_2d(
            "▶ Re-tracking [%s] point #%d from frame %d%s — one point only, not the grid."
            % (layer_name, int(point_index) + 1, int(frame) + 1,
               " (and backwards)" if backwards else ""), self.ctx.ACCENT)
        self.ctx.start_correction_worker({
            "mode": "retrack",
            "result": self._track_result,
            "video_path": self.ctx.videos_dir / v_name,
            "layer_key": self._track_layer_map[layer_name],
            "point_index": int(point_index),
            "frame_t": int(t),
            "x": x, "y": y,
            "backwards": bool(backwards),
            "config": config,
        })

    def reexport_2d_result(self):
        """Write every 2D format again from the corrected result, without re-tracking."""
        if self.ctx.correction_busy():
            self.ctx.log_2d("A 2D job is already running.", self.ctx.WARN)
            return
        if not (self._track_result and self._track_layer_map):
            QMessageBox.information(
                self, "No 2D Result",
                self._track_problem or
                "There is no 2D result to export yet.\n\nRun 2D Point Tracking first.")
            return
        shot = self.ctx.current_shot_name()
        if not shot:
            return
        layers_meta = []
        for layer in self.canvas.layers:
            key = self._track_layer_map.get(layer.name)
            if key in self._track_result["layers"]:
                layers_meta.append({
                    "name": layer.name,
                    "key": key,
                    "export_cornerpin": bool(layer.export_cornerpin or layer.mode == "cornerpin"),
                })
        self.ctx.log_2d(
            "▶ Re-exporting the corrected 2D tracks (no re-tracking, the overlay video "
            "stays as the last real track rendered it).", self.ctx.ACCENT)
        self.ctx.start_correction_worker({
            "mode": "export",
            "result": self._track_result,
            "track_root": self._track_root_for(shot),
            "layers_meta": layers_meta,
            "fps": self.current_fps if self.current_fps and self.current_fps > 0 else 24.0,
            "images_dir": self.ctx.scenes_dir / shot / "images",
            "timeline_start": int(self.ctx.timeline_start),
            "source_name": self.ctx.current_clip_name(),
        })

    def jump_correction(self, direction):
        """Move the playhead to the next or previous corrected frame."""
        marks = sorted({int(c["frame"]) for _l, c in self._all_corrections()})
        if not marks:
            return
        here = int(self.canvas.current_frame)
        later = [f for f in marks if f > here]
        earlier = [f for f in marks if f < here]
        target = (later[0] if later else marks[0]) if direction > 0 else \
                 (earlier[-1] if earlier else marks[-1])
        self.ctx.ui.slider_2d_frame.setValue(target)

    def on_correction_finished(self, success, message):
        self.ctx.ui.btn_start_2d.setEnabled(True)
        self.ctx.ui.btn_stop_2d.setEnabled(False)
        worker = self.ctx.correction_worker()
        if success and worker is not None and worker.result is not None:
            # The worker corrected a copy; adopt it, so the canvas and the next
            # correction both work on the spliced numbers.
            self._track_result = worker.result
            self._push_result_to_canvas()
            self.canvas.update()
        self.ctx.log_2d(message, self.ctx.OK if success else self.ctx.ERR)
        if success and worker is not None and worker.job.get("mode") == "retrack":
            self.ctx.log_2d(
                "   The delivered files still hold the old positions — press Re-export 2D "
                "when the track is how you want it.", self.ctx.TEXT_DIM)
        self.refresh()
