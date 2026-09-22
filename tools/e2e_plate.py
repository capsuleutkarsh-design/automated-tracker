"""
Headless end-to-end check: run the real GUI code path on one shot in 02 VIDEOS.

    "00 PYTHON\\python.exe" tools\\e2e_plate.py <shot> [2d,3d]

<shot> is a clip file or an image-sequence folder inside 02 VIDEOS, e.g. a
sequence numbered from 1001 to prove exports land on the plate's frames. The
window is created offscreen, the shot is selected, the 2D tracker and/or the
3D solve are started through the same methods the buttons call, and the log
tails are printed. Results land in 04 SCENES/<shot>/ as usual.

Set PYTHONIOENCODING=utf-8 first on a cp1252 console; the app logs contain
check marks.
"""
import os
import sys
import time
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "05 SCRIPT"))
sys.path.insert(0, str(ROOT / "06 COTRACKER"))
os.chdir(ROOT / "05 SCRIPT")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402


# No modal dialogs in a headless run: log them and carry on as if "Yes".
def _fake(kind):
    def f(parent, title, text, *a, **k):
        print(f"[dialog:{kind}] {title}: {text}")
        return QMessageBox.StandardButton.Yes
    return staticmethod(f)


for _kind in ("warning", "critical", "information", "question"):
    setattr(QMessageBox, _kind, _fake(_kind))

app = QApplication([])
import tracker_gui  # noqa: E402

win = tracker_gui.TrackerMainWindow()
win.show()
SHOT = sys.argv[1] if len(sys.argv) > 1 else "plate_1001"
RUN = sys.argv[2] if len(sys.argv) > 2 else "2d,3d"


def wait_for(signal, timeout_s, what):
    loop = QEventLoop()
    result = {}

    def done(*args):
        result["args"] = args
        loop.quit()

    signal.connect(done)
    QTimer.singleShot(int(timeout_s * 1000), loop.quit)
    t0 = time.time()
    loop.exec()
    try:
        signal.disconnect(done)
    except Exception:
        pass
    print(f"[wait] {what}: {'done' if 'args' in result else 'TIMEOUT'} "
          f"in {time.time() - t0:.0f}s -> {result.get('args')}")
    return result.get("args")


def pump(seconds):
    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


try:
    win._refresh_videos()
    names = [win.combo_2d_video.itemText(i) for i in range(win.combo_2d_video.count())]
    print("media pool:", names)
    idx = names.index(SHOT)
    win.combo_2d_video.setCurrentIndex(idx)
    pump(0.5)
    if getattr(win, "frame_extractor", None) is not None and win.frame_extractor.isRunning():
        wait_for(win.frame_extractor.finished_signal, 300, "frame extraction")
    pump(0.5)
    print("2D clip:", win.combo_2d_video.currentText(), "fps:", win.current_fps,
          "frames:", win.canvas_2d.total_frames, "slider:", win.slider_2d_frame.maximum() + 1,
          "timeline start (auto):", win.spin_start_frame_2d.value())
    print("--- 2D log ---")
    print("\n".join(win.log_2d_text.toPlainText().splitlines()[-6:]))

    # ---- 2D ---------------------------------------------------------------
    if "2d" not in RUN:
        print("2D skipped")
    else:
        win.spin_grid_size.setValue(6)
        win.combo_2d_model.setCurrentIndex(0)   # offline model
        win._start_tracking_2d()
        if win.worker_2d is None:
            print("2D worker did not start")
        else:
            print("2D result:", wait_for(win.worker_2d.finished_signal, 900, "2D tracking"))

    # ---- 3D ---------------------------------------------------------------
    if "3d" not in RUN:
        print("3D skipped")
        raise SystemExit
    win._refresh_videos()
    pump(0.3)
    for r in range(win.table.rowCount()):
        it = win.table.item(r, 0)
        if it and it.text().strip() == SHOT:
            win.table.selectRow(r)
            break
    win.combo_2d_video.setCurrentIndex(idx)   # masks are keyed to the 2D clip
    pump(0.3)
    print("3D timeline start (auto):", win.spin_start_frame_3d.value(),
          "camera model:", win.combo_cam.currentText(), "preset:", win.preset_combo.currentText())
    win.chk_mesh_gen.setChecked(False)
    win._start_tracking_3d()
    if win.worker_3d is None:
        print("3D worker did not start")
    else:
        print("3D result:", wait_for(win.worker_3d.finished_signal, 1800, "3D solve"))
        print("--- 3D log tail ---")
        print("\n".join(l for l in win.log_text.toPlainText().splitlines()[-40:]
                        if not l[:1] in ("I", "W", "E") or not l[1:5].isdigit()))
except SystemExit:
    pass
except Exception:
    traceback.print_exc()
finally:
    try:
        win.close()
    except Exception:
        pass
    print("E2E DONE")
