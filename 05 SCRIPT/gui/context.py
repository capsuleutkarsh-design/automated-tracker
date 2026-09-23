"""
The contract between the main window and the panels it builds.

The tab builders and the panels used to be handed the window itself and reach
into it for roughly forty `win._*` methods and as many widgets, none of which
was written down anywhere. Nothing could be moved without grepping for every
name, and a typo in a panel was a dead button nobody noticed until an artist
pressed it.

`AppContext` is that contract, made explicit and in one place.


WHAT A TAB OR PANEL MAY READ
----------------------------
The folders and executables the app was installed into (`videos_dir`,
`scenes_dir`, `base_dir`, `colmap_exe`, `ffmpeg_exe`, `thumbs_dir`), the theme
tokens (`OK`, `ERR`, `WARN`, `ACCENT`, `TEXT_DIM`), the solver presets
(`presets`), and the small amount of shot state a panel genuinely has to know:
`fps`, `canvas`, `current_clip_name()`, `current_shot_name()`, `timeline_start`.

WHAT A TAB OR PANEL MAY CALL
----------------------------
Only the methods defined below. Every one is a named method on this object, so
the whole surface a panel can reach is the list you are reading. A panel that
needs something new adds a method HERE first, which is the point: the contract
grows on purpose rather than by accident.

WHAT A TAB OR PANEL MUST REGISTER
---------------------------------
Every widget the window, the tests or the headless scripts refer to by name
goes through `ctx.ui`. Setting `ctx.ui.combo_2d_video = QComboBox()` publishes
the widget under that exact name on the window as well, which is the name the
handlers, `tools/e2e_plate.py` and the tests already use. Registration is
one-way: a panel reads other panels' widgets off `ctx.ui`, never off the
window.

WHAT A TAB OR PANEL MUST NOT TOUCH
----------------------------------
The window object. It is deliberately not reachable from here - no `ctx.window`,
no `ctx.parent()`. The workers, the QSettings, the project file and the crash
handler are the window's own business, and a panel that wants one of them wants
a method on this contract instead. Nor may a panel rename a Qt object name, a
stylesheet selector or a theme token: the QSS matches on them.
"""

from gui.theme import OK, ERR, WARN, ACCENT, TEXT_DIM


class WidgetRegistry:
    """
    The widgets a tab builds, published under the names everything else uses.

    Assigning here also assigns on the window, so `win.combo_2d_video` keeps
    working for the handlers, the tests and `tools/e2e_plate.py`. Reading is
    plain attribute access, and a name that was never registered raises
    AttributeError naming it - which beats a silently missing widget.
    """

    def __init__(self, window):
        # Through object.__setattr__, or the line below would recurse into the
        # __setattr__ that is the whole point of this class.
        object.__setattr__(self, "_window", window)

    def __setattr__(self, name, widget):
        object.__setattr__(self, name, widget)
        setattr(self._window, name, widget)


class AppContext:
    """
    What the tab builders and the panels are allowed to see and to do.

    Built by the window before any tab exists, so a panel can be constructed
    with it and register its widgets as it builds them.
    """

    # Theme tokens, so a panel can colour a log line without reaching past the
    # contract for them. Same values as gui.theme; the QSS matches on the
    # names, so neither may be renamed.
    OK = OK
    ERR = ERR
    WARN = WARN
    ACCENT = ACCENT
    TEXT_DIM = TEXT_DIM

    def __init__(self, window, paths, presets):
        self._win = window
        self.ui = WidgetRegistry(window)
        self.presets = presets
        # The panels that own a slice of a tab. The window puts them here as
        # soon as it has built them, before any tab exists, so a tab can
        # connect its buttons straight to them.
        self.layers = None          # gui.layer_panel.LayerPanel
        self.player = None          # gui.player.PlayerController
        self.scene_setup = None     # gui.scene_setup.SceneSetupPanel
        self.correction = None      # gui.correction.CorrectionController
        self.media = None           # gui.media_panel.MediaPanel
        # Folders and executables, resolved once at startup by core.app_paths.
        self.base_dir = paths["base_dir"]
        self.videos_dir = paths["videos_dir"]
        self.scenes_dir = paths["scenes_dir"]
        self.colmap_exe = paths["colmap_exe"]
        self.ffmpeg_exe = paths["ffmpeg_exe"]
        self.thumbs_dir = paths["thumbs_dir"]

    # =====================================================================
    # SHOT STATE A PANEL MAY READ
    # =====================================================================
    @property
    def canvas(self):
        """The 2D viewport. Registered by build_2d_tab as `canvas_2d`."""
        return self.ui.canvas_2d

    @property
    def fps(self):
        """The shot's frame rate, as decided when the clip was selected."""
        return self._win.current_fps

    @property
    def timeline_start(self):
        """The frame the shot's first frame sits on in the artist's timeline."""
        return self.ui.spin_start_frame_2d.value()

    def current_clip_name(self):
        """The file name of the clip the 2D tab is showing, or ''."""
        return self.ui.combo_2d_video.currentText()

    def current_shot_name(self):
        """That clip's stem, which is the shot every output folder is named for."""
        return self._win._current_shot_name()

    # =====================================================================
    # TELLING THE ARTIST SOMETHING
    # =====================================================================
    def log_2d(self, text, color=TEXT_DIM):
        self._win._append_log_2d(text, color)

    def log_3d(self, text, color=TEXT_DIM):
        self._win._append_log_3d(text, color)

    def status(self, text, error=False):
        self._win._status(text, error=error)

    def set_chip_state(self, widget, state):
        """Chips are styled by the theme; a panel only sets the state."""
        self._win._set_chip_state(widget, state)

    # =====================================================================
    # ASKING THE WINDOW TO DO SOMETHING
    # =====================================================================
    def schedule_project_save(self):
        """Mark the shot changed. The window writes it a moment later."""
        self._win._schedule_project_save()

    def show_2d_tab(self):
        """Bring the plate to the front - the scene pickers work on it."""
        self._win.tabs.setCurrentWidget(self._win.tab_2d)

    def dialog_parent(self):
        """
        What a panel parents a modal dialog to.

        The window, so a question about a layer opens over the middle of the
        app the way it always did rather than over the small card that asked
        it. This is the one place a panel is handed the window at all, and it
        may only put it in a dialog's parent slot.
        """
        return self._win

    def exit_app(self):
        """File > Exit."""
        self._win.close()

    # -- media pool -------------------------------------------------------
    # gui/media_panel.py reads the folder and fills the table; importing and
    # unpacking are QThreads the window holds, so they are asked for here.
    def import_media_files(self, files, overwrite=False):
        """Copy clips into 02 VIDEOS, one job at a time, off the GUI thread."""
        self._win._import_media_files(files, overwrite=overwrite)

    def extract_frames_for_current_video(self):
        """Unpack the selected clip's frame cache, off the GUI thread."""
        self._win._extract_frames_for_current_video()

    def on_2d_video_selected(self, video_name):
        """A different clip is now the one in view."""
        self._win._on_2d_video_selected(video_name)

    def on_fps_changed(self, value):
        """The artist typed a frame rate for a sequence."""
        self._win._on_fps_changed(value)

    def import_dropped_video(self, filepath):
        """A clip was dropped on the window or the viewport."""
        self._win._import_dropped_video(filepath)

    @property
    def last_clip(self):
        """The clip name to select again when the pool is rescanned."""
        return self._win._last_clip

    @last_clip.setter
    def last_clip(self, name):
        self._win._last_clip = name

    @property
    def project_loaded(self):
        """True once a shot with a project file on disk has been restored."""
        return self._win._project_loaded

    @property
    def project_shot(self):
        """The shot whose project file the window is currently holding."""
        return self._win._project_shot

    # -- 3D solver --------------------------------------------------------
    def on_preset_changed(self, preset_name):
        self._win._on_preset_changed(preset_name)

    def browse_blender_exe(self):
        self._win._browse_blender_exe()

    def on_show_engine_output(self, on):
        self._win._on_show_engine_output(on)

    def start_tracking_3d(self):
        self._win._start_tracking_3d()

    def stop_tracking_3d(self):
        self._win._stop_tracking_3d()

    def clear_3d_log(self):
        self._win._clear_3d_log()

    def export_3d_for_blender(self):
        self._win._export_3d_for_blender()

    def export_3d_for_nuke(self):
        self._win._export_3d_for_nuke()

    def reexport_current_solve(self):
        self._win._reexport_current_solve()

    # -- layers and the canvas tools --------------------------------------
    def on_canvas_tool_changed(self, idx):
        self._win._on_canvas_tool_changed(idx)

    def on_point_added_on_canvas(self, frame_idx, x, y):
        self._win._on_point_added_on_canvas(frame_idx, x, y)

    def clear_active_layer_masks(self):
        self._win._clear_active_layer_masks()

    def clear_manual_points(self):
        self._win._clear_manual_points()

    def jump_to_point_keyframe(self):
        self._win._jump_to_point_keyframe()

    def export_active_masks_to_nuke_roto(self):
        self._win._export_active_masks_to_nuke_roto()

    # -- 2D tracking ------------------------------------------------------
    def start_tracking_2d(self):
        self._win._start_tracking_2d()

    def stop_tracking_2d(self):
        self._win._stop_tracking_2d()

    def export_2d_for_nuke(self):
        self._win._export_2d_for_nuke()

    # -- fixing a drifting track (roadmap 2.1) ----------------------------
    # gui/correction.py decides what to re-track or re-export; the window
    # owns the thread that does it, so the job description comes back here.
    def max_dimension_2d(self):
        """Working resolution the Resolution combo asks for. 0 means original."""
        return self._win._max_dimension_2d()

    def correction_busy(self):
        """True while a re-track, a re-export or a full 2D track is running."""
        return self._win._correction_busy()

    def start_correction_worker(self, job):
        """Run one correction job off the GUI thread."""
        self._win._start_correction_worker(job)

    def correction_worker(self):
        """The running (or just-finished) correction job, or None."""
        return self._win.correction_worker

    # -- viewport modes ---------------------------------------------------
    def toggle_canvas_matte_overlay(self, checked):
        self._win._toggle_canvas_matte_overlay(checked)

    def toggle_canvas_alpha_mode(self, checked):
        self._win._toggle_canvas_alpha_mode(checked)

    def toggle_canvas_loupe(self, checked):
        self._win._toggle_canvas_loupe(checked)

    # -- help and housekeeping --------------------------------------------
    def open_colmap_gui(self):
        self._win._open_colmap_gui()

    def show_shortcuts_dialog(self):
        self._win._show_shortcuts_dialog()

    def show_about_dialog(self):
        self._win._show_about_dialog()

    def on_update_pref(self, on):
        self._win._on_update_pref(on)
