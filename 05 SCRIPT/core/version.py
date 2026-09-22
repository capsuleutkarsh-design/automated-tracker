"""
The one place the application version is written down.

Everything else reads it from here: the window title, the status chip and the
About box in tracker_gui.py, build/build_app.py (which stamps it into
build/version.txt for the Inno Setup script), tools/pack_runtime.py (runtime
zip names) and SETUP.bat (which parses the APP_VERSION line below with findstr
to pick the matching GitHub release). Keep the assignment on a single line in
the form  APP_VERSION = "x.y.z"  so the batch parser keeps working.
"""

APP_VERSION = "1.1.1"
APP_NAME = "Automated Tracker"


def display_version():
    """Version as shown to users: 'v1.1.0'."""
    return "v" + APP_VERSION
