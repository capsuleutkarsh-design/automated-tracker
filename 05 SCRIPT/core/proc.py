"""
Subprocess helpers that behave the same frozen and from source.

A windowed PyInstaller build has no console, so sys.stdin/stdout/stderr are None
and the standard handles it would hand to a child process are invalid. A plain
subprocess.Popen(cmd) then fails to start the child - silently, because there is
nowhere for the error to go. That is why frame extraction worked from source and
did nothing in the first frozen build.

Every launch therefore goes through here, which:
  * always gives the child explicit handles (DEVNULL or PIPE), and
  * hides the console window Windows would otherwise flash up.
"""

import os
import subprocess

CREATE_NO_WINDOW = 0x08000000


def _startupinfo():
    if os.name != "nt":
        return None
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0  # SW_HIDE
    return si


def hidden_kwargs(capture=False, hide_console=True):
    """
    Keyword arguments for Popen/run that are safe in a windowed build.

    capture=True pipes the child's output back (stderr folded into stdout);
    otherwise it is discarded. stdin is always DEVNULL - nothing here is
    interactive, and an inherited bad handle is what breaks the child.
    """
    kw = {"stdin": subprocess.DEVNULL}

    if capture:
        kw["stdout"] = subprocess.PIPE
        kw["stderr"] = subprocess.STDOUT
    else:
        kw["stdout"] = subprocess.DEVNULL
        kw["stderr"] = subprocess.DEVNULL

    if os.name == "nt" and hide_console:
        kw["creationflags"] = CREATE_NO_WINDOW
        kw["startupinfo"] = _startupinfo()

    return kw


def run_hidden(cmd, capture=False, hide_console=True, **extra):
    """subprocess.run with safe handles. Returns the CompletedProcess."""
    kw = hidden_kwargs(capture=capture, hide_console=hide_console)
    kw.update(extra)
    return subprocess.run(cmd, **kw)


def popen_hidden(cmd, capture=False, hide_console=True, **extra):
    """subprocess.Popen with safe handles. Returns the Popen."""
    kw = hidden_kwargs(capture=capture, hide_console=hide_console)
    kw.update(extra)
    return subprocess.Popen(cmd, **kw)


def popen_gui(cmd, **extra):
    """
    Launch another windowed program (COLMAP's own GUI).

    Its window should appear, so only the console is suppressed; the handles are
    still set explicitly because the parent has none to inherit.
    """
    kw = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kw["creationflags"] = CREATE_NO_WINDOW
    kw.update(extra)
    return subprocess.Popen(cmd, **kw)
