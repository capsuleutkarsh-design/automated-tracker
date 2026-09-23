"""
The crash handler and the logging setup (B3).

Neither belongs to the window: both are installed before it is built, and both
exist for the frozen, windowed build, which has no console at all. An exception
raised in a Qt slot there used to make a button quietly stop working, and a
print() would raise because there was nowhere to print to.

So everything printed or logged goes to a rotating app.log the artist can send
in, and an unhandled exception is written to error_<timestamp>.log and shown in
a dialog, from whichever thread raised it.
"""

import sys
import logging
import logging.handlers
import threading
import traceback
import datetime

from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import QMessageBox

from core.app_paths import logs_dir
from core.version import display_version

# The window's logger: the startup banner and every crash line are written
# under this name, and moving the code must not move the name.
log = logging.getLogger("tracker_gui")


class _CrashRelay(QObject):
    """Carries a crash report from any thread to a dialog on the GUI thread."""
    report = Signal(str, str)


_crash_relay = None


class _LogStream:
    """
    File-like stand-in for sys.stdout / sys.stderr. The windowed build has
    neither, so print() would raise; this writes to app.log (and to the real
    stream too when there is one).
    """
    encoding = "utf-8"

    def __init__(self, level, mirror=None):
        self._level = level
        self._mirror = mirror
        self._buf = ""

    def write(self, text):
        if not text:
            return 0
        if self._mirror is not None:
            try:
                self._mirror.write(text)
            except Exception:
                pass
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                logging.getLogger("stdout").log(self._level, line.rstrip())
        return len(text)

    def flush(self):
        if self._mirror is not None:
            try:
                self._mirror.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def setup_logging():
    """Everything printed or logged goes to a rotating app.log the user can send."""
    folder = logs_dir()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    try:
        handler = logging.handlers.RotatingFileHandler(
            folder / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(handler)
    except Exception:
        return folder
    sys.stdout = _LogStream(logging.INFO, sys.__stdout__)
    sys.stderr = _LogStream(logging.ERROR, sys.__stderr__)
    log.info("=" * 60)
    log.info("Automated Tracker %s starting  (frozen=%s, python=%s)",
             display_version(), bool(getattr(sys, "frozen", False)), sys.version.split()[0])
    return folder


def _write_crash_log(text):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = logs_dir() / ("error_%s.log" % stamp)
    try:
        path.write_text(text, encoding="utf-8")
    except Exception:
        path = None
    return path


def _show_crash_dialog(message, path_text):
    try:
        box = QMessageBox(QMessageBox.Critical, "Automated Tracker - Unexpected Error",
                          "Something went wrong. The action you tried may not have completed.\n\n"
                          "%s\n\nA full report was written to:\n%s\n\n"
                          "Please send that file with a bug report." % (message, path_text))
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.exec()
    except Exception:
        pass


def install_crash_handler():
    """
    Catch what would otherwise vanish. In the frozen, windowed build there is
    no stderr, so an exception raised in a Qt slot just made that button stop
    working. Now it is logged to error_<timestamp>.log and shown in a dialog.
    """
    global _crash_relay
    _crash_relay = _CrashRelay()
    _crash_relay.report.connect(_show_crash_dialog)

    def _handle(exc_type, exc_value, exc_tb, where="main thread"):
        if issubclass(exc_type, KeyboardInterrupt):
            return
        tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        header = ("Automated Tracker %s  crash in %s\n%s\n\n"
                  % (display_version(), where, datetime.datetime.now().isoformat()))
        try:
            log.critical("unhandled exception in %s:\n%s", where, tb)
        except Exception:
            pass
        path = _write_crash_log(header + tb)
        summary = "%s: %s" % (exc_type.__name__, str(exc_value)[:300])
        _crash_relay.report.emit(summary, str(path) if path else "(the log folder is not writable)")

    def excepthook(exc_type, exc_value, exc_tb):
        _handle(exc_type, exc_value, exc_tb)

    def thread_hook(args):
        _handle(args.exc_type, args.exc_value, args.exc_traceback,
                where="thread %s" % getattr(args.thread, "name", "?"))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
