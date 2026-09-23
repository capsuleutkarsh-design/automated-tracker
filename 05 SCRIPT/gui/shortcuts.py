"""
Every keyboard shortcut in the window, in one table (roadmap 2.5).

A shortcut scheme is only worth having if the artist can find out what it is,
so the Help dialog is GENERATED from this table rather than written beside it:
the two cannot drift apart, and adding a binding documents itself.

The keys are the ones a compositor's hands already know from an editorial
timeline - Space, J/K/L, arrows, Home/End, I and O - because a tool that
invents its own transport keys is a tool that gets driven with the mouse.

The one rule that keeps a scheme from being hated: a bare letter must never be
stolen from a field the artist is typing in. `wants_text_input` decides that,
and `_TypingGuard` hands the key back to the widget through Qt's own
ShortcutOverride mechanism, so a spin box still gets its "O" and a line edit
still gets its own Ctrl+Z.
"""

from collections import OrderedDict

from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QAbstractSpinBox, QComboBox, QDialog,
    QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QTextBrowser,
    QTextEdit, QVBoxLayout,
)

from gui.theme import ACCENT, BG_INPUT, BORDER, TEXT, TEXT_DIM


class Shortcut:
    """
    One row of the table.

    `action` is the name of the handler the window supplies; it is None for a
    row that is documented but not bound, such as holding Ctrl for the loupe,
    which is a held modifier rather than a shortcut Qt could fire.
    """

    __slots__ = ("action", "keys", "label", "group")

    def __init__(self, action, keys, label, group):
        self.action = action
        self.keys = tuple(keys)
        self.label = label
        self.group = group

    def __repr__(self):
        return "Shortcut(%r, %r)" % (self.action, self.keys)


# Order here is the order the dialog lists them in, so it reads as a workflow:
# move through the shot, trim it, work the roto, look at it, undo a mistake.
SHORTCUTS = (
    Shortcut("play_pause", ("Space",), "Play or pause", "Transport"),
    Shortcut("shuttle_back", ("J",),
             "Stop if it is playing forwards, otherwise play backwards - "
             "press again to go faster", "Transport"),
    Shortcut("pause", ("K",), "Pause", "Transport"),
    Shortcut("shuttle_fwd", ("L",),
             "Play forwards - press again to go faster", "Transport"),
    Shortcut("step_back", ("Left",), "Step back one frame", "Transport"),
    Shortcut("step_fwd", ("Right",), "Step forward one frame", "Transport"),
    Shortcut("step_back_10", ("Shift+Left",), "Step back ten frames", "Transport"),
    Shortcut("step_fwd_10", ("Shift+Right",), "Step forward ten frames", "Transport"),
    Shortcut("go_start", ("Home",), "Jump to the first frame", "Transport"),
    Shortcut("go_end", ("End",), "Jump to the last frame", "Transport"),

    Shortcut("set_in", ("I",), "Set the tracking in-point on this frame", "Range"),
    Shortcut("set_out", ("O",), "Set the tracking out-point on this frame", "Range"),
    Shortcut("clear_in", ("Alt+I",), "Clear the in-point (track from the head)", "Range"),
    Shortcut("clear_out", ("Alt+O",), "Clear the out-point (track to the tail)", "Range"),

    Shortcut("prev_key", (",", "["), "Step to the previous mask keyframe", "Roto"),
    Shortcut("next_key", (".", "]"), "Step to the next mask keyframe", "Roto"),
    Shortcut("del_key", ("Del",), "Delete the mask keyframe on this frame", "Roto"),
    Shortcut("del_mask", ("Shift+Del",),
             "Delete the selected mask with all its keyframes", "Roto"),

    Shortcut("toggle_matte", ("M",), "Show or hide the mask overlay", "View"),
    Shortcut("toggle_alpha", ("A",), "High-contrast alpha matte view", "View"),
    Shortcut(None, ("Ctrl",), "Hold for the sub-pixel loupe", "View"),

    Shortcut("undo", ("Ctrl+Z",), "Undo the last canvas edit", "Edit"),
    Shortcut("redo", ("Ctrl+Y", "Ctrl+Shift+Z"), "Redo the edit just undone", "Edit"),

    Shortcut("help", ("F1",), "Show this list", "Help"),
)


def normalise(key):
    """One spelling per binding, so 'Del' and 'Delete' cannot both be listed."""
    return QKeySequence(key).toString(QKeySequence.PortableText)


def all_keys(table=SHORTCUTS):
    """Every binding in the table, normalised, in table order."""
    return [normalise(k) for entry in table for k in entry.keys]


def duplicate_bindings(table=SHORTCUTS):
    """
    Keys bound more than once. Anything in here is a bug.

    Two keys for one action is fine and deliberate (Ctrl+Y and Ctrl+Shift+Z
    both redo, because different hands expect different ones); one key for two
    actions is a shortcut that does whichever thing Qt reaches first.
    """
    seen, dupes = set(), []
    for key in all_keys(table):
        if key in seen and key not in dupes:
            dupes.append(key)
        seen.add(key)
    return dupes


def undescribed(table=SHORTCUTS):
    """Rows with nothing to show in the dialog. Also a bug."""
    return [entry for entry in table if not (entry.label or "").strip()]


def by_group(table=SHORTCUTS):
    """The table grouped for display, keeping the order it is written in."""
    groups = OrderedDict()
    for entry in table:
        groups.setdefault(entry.group, []).append(entry)
    return groups


def help_html(table=SHORTCUTS):
    """The Help dialog's body, built from the table so it cannot disagree."""
    rows = [
        "<style>"
        "body { color: %s; }"
        "h3 { color: %s; margin: 14px 0 4px 0; font-size: 12px; "
        "letter-spacing: 1px; text-transform: uppercase; }"
        "td { padding: 3px 10px 3px 0; vertical-align: top; }"
        ".k { color: %s; font-family: Consolas, monospace; white-space: nowrap; }"
        ".d { color: %s; }"
        "</style>" % (TEXT, ACCENT, TEXT, TEXT_DIM),
        '<p class="d">These work on the 2D Point Tracking tab, and never while '
        'you are typing in a field.</p>',
    ]
    for group, entries in by_group(table).items():
        rows.append("<h3>%s</h3><table>" % group)
        for entry in entries:
            keys = "  or  ".join(normalise(k) for k in entry.keys)
            rows.append('<tr><td class="k">%s</td><td class="d">%s</td></tr>'
                        % (keys, entry.label))
        rows.append("</table>")
    return "".join(rows)


def wants_text_input(widget):
    """
    True when this widget is somewhere the artist is typing.

    Every bare-letter binding asks this first. A read-only console or a locked
    frame-rate box is NOT typing - clicking the log and pressing Space should
    still play the shot - so read-only widgets answer False on purpose.

    A spin box and an editable combo both put the focus on an internal
    QLineEdit, which is why the line-edit case is the one that catches them.
    """
    if widget is None:
        return False
    if isinstance(widget, QLineEdit):
        return not widget.isReadOnly()
    if isinstance(widget, QAbstractSpinBox):
        return not widget.isReadOnly()
    if isinstance(widget, (QTextEdit, QPlainTextEdit)):
        return not widget.isReadOnly()
    if isinstance(widget, QComboBox):
        return widget.isEditable()
    if isinstance(widget, QAbstractItemView):
        # A list is only typing while a row is actually being renamed in place.
        return widget.state() == QAbstractItemView.EditingState
    return False


class _TypingGuard(QObject):
    """
    Hands a bound key back to the field the artist is typing in.

    Qt asks the focused widget whether it wants a key before it fires a
    shortcut, as a ShortcutOverride event. Accepting that event here is what
    stops I, O, M or Space being swallowed by the transport mid-word, and it
    leaves a line edit's own Ctrl+Z alone. Only keys this table binds are
    intercepted, so the menu bar's own accelerators are untouched.
    """

    def __init__(self, parent=None, table=SHORTCUTS):
        super().__init__(parent)
        self.bound = frozenset(all_keys(table))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.ShortcutOverride:
            try:
                pressed = QKeySequence(event.keyCombination()).toString(
                    QKeySequence.PortableText)
            except Exception:
                return False
            if pressed in self.bound and wants_text_input(QApplication.focusWidget()):
                event.accept()
                return True
        return False


def install(window, handlers, table=SHORTCUTS):
    """
    Bind the table to `window`. `handlers` maps an action name to a callable.

    A row whose action the window does not implement is skipped rather than
    raising: a half-wired table should cost one dead key, not the whole app.
    Returns the QShortcut objects, which the caller must keep alive.
    """
    created = []
    for entry in table:
        fn = handlers.get(entry.action) if entry.action else None
        if fn is None:
            continue
        for key in entry.keys:
            sc = QShortcut(QKeySequence(key), window)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(_guarded(fn))
            created.append(sc)
    guard = _TypingGuard(window, table)
    app = QApplication.instance()
    if app is not None:
        app.installEventFilter(guard)
    # Held by the window, or Python would collect the filter the moment this
    # function returns and every guard would quietly stop working.
    window._shortcut_guard = guard
    window._shortcuts = created
    return created


def _guarded(fn):
    """Belt and braces: never run a handler while the focus is in a field."""
    def run():
        if wants_text_input(QApplication.focusWidget()):
            return
        fn()
    return run


def show_dialog(parent, table=SHORTCUTS):
    """The Help menu's shortcut list."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("Keyboard Shortcuts")
    dlg.resize(520, 600)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(12, 12, 12, 12)
    lay.setSpacing(8)

    view = QTextBrowser()
    view.setOpenExternalLinks(False)
    view.setStyleSheet("background-color: %s; border: 1px solid %s;" % (BG_INPUT, BORDER))
    view.setHtml(help_html(table))
    lay.addWidget(view, 1)

    row = QHBoxLayout()
    row.addStretch(1)
    close = QPushButton("Close")
    close.clicked.connect(dlg.accept)
    row.addWidget(close)
    lay.addLayout(row)

    dlg.exec()
    return dlg
