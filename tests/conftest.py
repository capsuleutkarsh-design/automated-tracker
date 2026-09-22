"""
Test configuration.

Run from the repository root with the bundled interpreter:

    "00 PYTHON\\python.exe" -m pytest tests -q

The app code lives in "05 SCRIPT" (a folder with a space, so not importable
by name); put it and the vendored CoTracker on sys.path here.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT / "05 SCRIPT", ROOT / "06 COTRACKER"):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
