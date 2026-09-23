"""
The one shortcut table, and the rule that keeps it liveable (roadmap 2.5).

Two things are worth pinning down. One: the table itself has to be sane - no
key bound twice, every row with something the Help dialog can print - because
the dialog is generated from it and a silent clash is a key that does whichever
thing Qt reached first. Two: a bare letter must never be stolen from a field
the artist is typing in, which is what wants_text_input decides.

Needs PySide6 (the predicate answers questions about real widgets), so it runs
offscreen and is skipped where PySide6 is not installed.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QComboBox, QDoubleSpinBox, QLineEdit, QListWidget,
    QPushButton, QSlider, QSpinBox, QTextEdit, QWidget,
)

from gui import shortcuts  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# -- the table --------------------------------------------------------------
def test_no_key_is_bound_twice():
    assert shortcuts.duplicate_bindings() == []


def test_every_row_has_something_to_show_in_the_dialog():
    assert shortcuts.undescribed() == []
    for entry in shortcuts.SHORTCUTS:
        assert entry.keys, "a row with no key cannot be documented or bound"
        assert entry.group


def test_the_keys_an_editor_expects_are_all_there(qapp):
    keys = set(shortcuts.all_keys())
    for expected in ("Space", "J", "K", "L", "Left", "Right",
                     "Shift+Left", "Shift+Right", "Home", "End",
                     "I", "O", "Alt+I", "Alt+O", ",", ".",
                     "Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z"):
        assert shortcuts.normalise(expected) in keys, expected


def test_two_keys_for_one_action_is_allowed(qapp):
    redo = next(e for e in shortcuts.SHORTCUTS if e.action == "redo")
    # Different hands reach for different redo keys; both are bound, and that
    # is not a duplicate binding.
    assert len(redo.keys) == 2
    assert shortcuts.duplicate_bindings() == []


def test_the_help_dialog_is_generated_from_the_table(qapp):
    html = shortcuts.help_html()
    for entry in shortcuts.SHORTCUTS:
        assert entry.label in html
        for key in entry.keys:
            assert shortcuts.normalise(key) in html
    for group in ("Transport", "Range", "Roto", "View", "Edit", "Help"):
        assert group in html


def test_a_row_with_no_action_is_documented_but_not_bound(qapp):
    window = QWidget()
    handlers = {e.action: (lambda: None) for e in shortcuts.SHORTCUTS if e.action}
    created = shortcuts.install(window, handlers)
    bound = sum(len(e.keys) for e in shortcuts.SHORTCUTS if e.action)
    assert len(created) == bound
    # Holding Ctrl for the loupe is in the list for the artist to read, but it
    # is a held modifier, not something Qt can fire.
    assert any(e.action is None for e in shortcuts.SHORTCUTS)


def test_an_action_the_window_does_not_implement_is_skipped_not_fatal(qapp):
    window = QWidget()
    created = shortcuts.install(window, {"undo": (lambda: None)})
    assert len(created) == 1


# -- the "don't steal keys" predicate ---------------------------------------
def test_a_field_being_typed_in_keeps_its_keys(qapp):
    assert shortcuts.wants_text_input(QLineEdit()) is True
    assert shortcuts.wants_text_input(QSpinBox()) is True
    assert shortcuts.wants_text_input(QDoubleSpinBox()) is True
    assert shortcuts.wants_text_input(QTextEdit()) is True

    editable = QComboBox()
    editable.setEditable(True)
    assert shortcuts.wants_text_input(editable) is True


def test_a_spin_box_asks_through_its_internal_line_edit(qapp):
    # Qt puts the focus on the spin box's own QLineEdit, so that is the widget
    # the predicate is really handed at runtime.
    spin = QSpinBox()
    assert shortcuts.wants_text_input(spin.lineEdit()) is True


def test_nothing_read_only_or_clickable_blocks_the_transport(qapp):
    log = QTextEdit()
    log.setReadOnly(True)
    # Clicking the console and pressing Space should still play the shot.
    assert shortcuts.wants_text_input(log) is False

    locked = QDoubleSpinBox()
    locked.setReadOnly(True)
    assert shortcuts.wants_text_input(locked) is False

    for widget in (None, QWidget(), QPushButton("Run"), QSlider(),
                   QListWidget(), QComboBox()):
        assert shortcuts.wants_text_input(widget) is False


def test_a_handler_does_not_run_while_a_field_has_the_focus(qapp):
    fired = []
    run = shortcuts._guarded(lambda: fired.append(1))

    holder = QWidget()
    field = QLineEdit(holder)
    button = QPushButton("Run", holder)
    holder.show()

    button.setFocus()
    QApplication.processEvents()
    run()

    field.setFocus()
    QApplication.processEvents()
    run()

    holder.hide()
    # Once, from the press while the button had focus - never from the field.
    assert len(fired) == 1
