"""
The native desktop menu bar, as a declaration.

Built inline, the menu bar was sixty lines of addMenu / addAction / connect in
the middle of the window's layout code, and the only way to find out whether
the Help menu still offered the shortcut list was to read all of it. Written
out as a table the whole menu bar fits on a screen, the order it appears in is
the order it is written in, and adding an entry is one line.

Every action names a method on the AppContext, which is the only thing a menu
is allowed to call. An entry whose method the context does not have is a typo,
and `build` says so loudly rather than leaving a dead menu item behind.
"""


class Item:
    """
    One menu entry.

    `action` names the AppContext method the entry runs; a dotted name reaches
    one of the panels the context carries, such as "media.add_videos".
    `checkable`
    makes it a tick box, which connects `toggled` (so the handler is given the
    new state) rather than `triggered`. `attr` publishes the QAction under that
    name on the window, for the few the window has to set later - the update
    preference is restored from QSettings after the menus are built.
    """

    __slots__ = ("label", "action", "checkable", "attr")

    def __init__(self, label, action, checkable=False, attr=None):
        self.label = label
        self.action = action
        self.checkable = checkable
        self.attr = attr


SEPARATOR = Item(None, None)


# Order here is the order on screen, left to right and top to bottom.
MENUS = (
    ("&File", (
        Item("Import Media / Video...", "media.add_videos"),
        Item("Open Media Folder", "media.open_videos_folder"),
        Item("Open Output Scenes Folder", "media.open_3d_output_folder"),
        SEPARATOR,
        Item("Exit", "exit_app"),
    )),
    ("&3D Solver", (
        Item("Start 3D Camera Tracking", "start_tracking_3d"),
        Item("Cancel 3D Solver", "stop_tracking_3d"),
        SEPARATOR,
        Item("Clear Diagnostics Log", "clear_3d_log"),
    )),
    ("&2D Tracker", (
        Item("Run 2D Point Tracking", "start_tracking_2d"),
        Item("Cancel 2D Tracker", "stop_tracking_2d"),
        SEPARATOR,
        Item("Unpack Frame Cache", "extract_frames_for_current_video"),
        Item("Clear Active Layer Masks", "clear_active_layer_masks"),
        Item("Clear Active Layer Points", "clear_manual_points"),
    )),
    ("&Export", (
        Item("Export 3D Camera for Blender (.abc)", "export_3d_for_blender"),
        Item("Export 3D Camera for Nuke (.nk / .abc)", "export_3d_for_nuke"),
        SEPARATOR,
        Item("Export 2D Tracker Node for Nuke (.nk)", "export_2d_for_nuke"),
        Item("Export Animated Roto Masks for Nuke (.nk)",
             "export_active_masks_to_nuke_roto"),
    )),
    ("&View", (
        Item("Open COLMAP 3D Viewport", "open_colmap_gui"),
    )),
    ("&Help", (
        Item("Keyboard Shortcuts...", "show_shortcuts_dialog"),
        SEPARATOR,
        Item("About Automated Tracker", "show_about_dialog"),
        Item("Check for Updates on Start", "on_update_pref",
             checkable=True, attr="act_updates"),
    )),
)


def build(menubar, ctx, menus=MENUS):
    """
    Fill `menubar` from the table and wire every entry to `ctx`.

    Returns the QActions by label, which is only of use to a test; the entries
    the window needs by name are registered through `ctx.ui` instead.
    """
    actions = {}
    for title, items in menus:
        menu = menubar.addMenu(title)
        for item in items:
            if item.label is None:
                menu.addSeparator()
                continue
            handler = ctx
            for part in item.action.split("."):
                handler = getattr(handler, part, None)
                if handler is None:
                    break
            if handler is None:
                raise AttributeError(
                    "menu entry %r asks for AppContext.%s, which does not exist"
                    % (item.label, item.action))
            act = menu.addAction(item.label)
            if item.checkable:
                act.setCheckable(True)
                act.toggled.connect(handler)
            else:
                act.triggered.connect(handler)
            if item.attr:
                setattr(ctx.ui, item.attr, act)
            actions[item.label] = act
    return actions
