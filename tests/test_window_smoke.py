"""
The whole window, opened headless and driven the way an artist drives it.

The unit tests cover the pure parts; this one covers the wiring between them,
which is the thing a refactor of the window actually threatens. It opens the
real TrackerMainWindow offscreen, selects the sample clip, presses the
transport keys, adds and renames a layer, draws a mask, undoes it, and closes -
so a panel that was moved out of the window and left unconnected fails here
rather than in front of the artist.

Runnable on its own as well, which is what you want while you are moving code:

    set QT_QPA_PLATFORM=offscreen
    "00 PYTHON\\python.exe" tests\\test_window_smoke.py

QSettings is pointed at a test-only organisation so a run cannot move the
artist's own window geometry or last clip, and the sample shot's project file
is put back exactly as it was afterwards.
"""
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QPoint, QTimer, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QInputDialog, QMessageBox,
)

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "02 VIDEOS" / "uhd_30fps.mp4"
PROJECT = ROOT / "04 SCENES" / "uhd_30fps" / "project.json"


def _pump(ms=120):
    """Let the event loop run, the way a real click gives Qt a moment."""
    loop = QEventLoop()
    QTimer.singleShot(int(ms), loop.quit)
    loop.exec()


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def window(qapp, monkeypatch):
    if not SAMPLE.exists():
        pytest.skip("no sample clip in 02 VIDEOS to open the window on")

    # No modal dialog may block a headless run, and "Yes" is the answer that
    # keeps going - the same trick tools/e2e_plate.py uses.
    for kind in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, kind,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    import tracker_gui

    # A test must not move the artist's saved geometry, tab or last clip.
    monkeypatch.setattr(tracker_gui, "SETTINGS_ORG", "AutomatedTrackerTests")
    monkeypatch.setattr(tracker_gui, "SETTINGS_APP", "AutomatedTrackerTests")

    saved_project = PROJECT.read_bytes() if PROJECT.exists() else None

    win = tracker_gui.TrackerMainWindow()
    win.resize(1280, 850)
    win.show()
    _pump(200)              # the deferred init runs 20 ms in
    try:
        yield win
    finally:
        try:
            win.close()
        except Exception:
            pass
        _pump(50)
        # Put the shot's project file back: the window writes it whenever a
        # layer changes, and this test changes layers on purpose.
        if saved_project is None:
            PROJECT.unlink(missing_ok=True)
        else:
            PROJECT.write_bytes(saved_project)


def _select_sample(win):
    win._refresh_videos()
    names = [win.combo_2d_video.itemText(i) for i in range(win.combo_2d_video.count())]
    assert SAMPLE.name in names, "the sample clip is not in the media pool"
    win.combo_2d_video.setCurrentIndex(names.index(SAMPLE.name))
    _pump(300)
    win.tabs.setCurrentWidget(win.tab_2d)
    _pump()


def test_window_opens_and_shows_the_sample_clip(window):
    win = window
    _select_sample(win)
    assert win.combo_2d_video.currentText() == SAMPLE.name
    assert win.slider_2d_frame.maximum() > 0, "the clip has no frames on the timeline"
    assert win.canvas_2d.total_frames > 0
    assert win.current_fps > 0


def test_transport_keys_move_the_playhead(window):
    win = window
    _select_sample(win)
    win.slider_2d_frame.setValue(10)

    QTest.keyClick(win, Qt.Key_Right)
    _pump(40)
    assert win.slider_2d_frame.value() == 11, "Right did not step forward one frame"

    QTest.keyClick(win, Qt.Key_Left)
    _pump(40)
    assert win.slider_2d_frame.value() == 10, "Left did not step back one frame"

    QTest.keyClick(win, Qt.Key_Right, Qt.ShiftModifier)
    _pump(40)
    assert win.slider_2d_frame.value() == 20, "Shift+Right did not step ten frames"

    QTest.keyClick(win, Qt.Key_Home)
    _pump(40)
    assert win.slider_2d_frame.value() == 0

    QTest.keyClick(win, Qt.Key_End)
    _pump(40)
    assert win.slider_2d_frame.value() == win.slider_2d_frame.maximum()

    # Space plays, Space pauses. The timer is what actually drives playback.
    QTest.keyClick(win, Qt.Key_Home)
    _pump(40)
    QTest.keyClick(win, Qt.Key_Space)
    _pump(40)
    assert win.is_playing and win.play_timer.isActive()
    QTest.keyClick(win, Qt.Key_Space)
    _pump(40)
    assert not win.is_playing and not win.play_timer.isActive()


def test_in_and_out_points_come_from_the_keys(window):
    win = window
    _select_sample(win)
    win.slider_2d_frame.setValue(5)
    QTest.keyClick(win, Qt.Key_I)
    _pump(40)
    assert win.canvas_2d.in_point == 5
    win.slider_2d_frame.setValue(25)
    QTest.keyClick(win, Qt.Key_O)
    _pump(40)
    assert win.canvas_2d.out_point == 25
    assert win.lbl_range_status.text() == "6 – 26"

    QTest.keyClick(win, Qt.Key_I, Qt.AltModifier)
    QTest.keyClick(win, Qt.Key_O, Qt.AltModifier)
    _pump(40)
    assert win.canvas_2d.in_point == 0 and win.canvas_2d.out_point == -1
    assert win.lbl_range_status.text() == "Full"


def test_a_layer_can_be_added_and_renamed(window, monkeypatch):
    win = window
    _select_sample(win)
    before = len(win.canvas_2d.layers)

    # Through the buttons, so a panel that was moved out and left unconnected
    # fails here rather than looking fine to a direct call.
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Smoke Layer", True)))
    win.btn_add_layer.click()
    assert len(win.canvas_2d.layers) == before + 1
    assert win.canvas_2d.active_layer.name == "Smoke Layer"
    assert win.layer_list.count() == before + 1
    assert "Smoke Layer" in win.layer_list.item(before).text()

    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Renamed Layer", True)))
    win.btn_rename_layer.click()
    assert win.canvas_2d.active_layer.name == "Renamed Layer"
    assert "Renamed Layer" in win.layer_list.item(before).text()

    win.btn_del_layer.click()
    assert len(win.canvas_2d.layers) == before


def test_a_mask_can_be_drawn_and_undone(window):
    win = window
    _select_sample(win)
    canvas = win.canvas_2d
    assert canvas.width() > 0 and canvas.current_pixmap is not None

    # "Draw Exclusion Box" is the fifth canvas tool.
    win.combo_canvas_tool.setCurrentIndex(4)
    assert canvas.interaction_mode == "exclusion_box"

    layer = canvas.active_layer
    before = len(layer.animated_masks)

    a = QPoint(int(canvas.width() * 0.30), int(canvas.height() * 0.30))
    b = QPoint(int(canvas.width() * 0.60), int(canvas.height() * 0.60))
    QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, a)
    QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, b)
    _pump(60)

    assert len(layer.animated_masks) == before + 1, "the box mask was not drawn"
    assert canvas.undo_stack.canUndo()
    assert canvas.undo_stack.undoText() == "Draw a box mask"
    assert win.status_undo.text() == "↶ Draw a box mask"

    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier)
    _pump(60)
    assert len(layer.animated_masks) == before, "Ctrl+Z did not take the mask back"
    assert win.status_undo.text() == "Nothing to undo"

    # Back to the select tool, so the window closes in the state it opened in.
    win.combo_canvas_tool.setCurrentIndex(0)


def test_the_window_closes_cleanly(window):
    win = window
    _select_sample(win)
    win.close()
    _pump(80)
    assert win._closing
    assert not win.play_timer.isActive()
    assert not win.vram_timer.isActive()


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
