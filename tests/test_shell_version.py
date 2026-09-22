"""The version is defined once and every consumer reads that value (D1)."""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_version_is_semver_and_displayed_with_v():
    from core.version import APP_VERSION, display_version
    assert re.fullmatch(r"\d+\.\d+\.\d+", APP_VERSION)
    assert display_version() == "v" + APP_VERSION


def test_build_app_reads_the_same_value():
    from core.version import APP_VERSION
    build_app = _load(ROOT / "build" / "build_app.py", "build_app_under_test")
    assert build_app.APP_VERSION == APP_VERSION


def test_pack_runtime_reads_the_same_value():
    from core.version import APP_VERSION
    pack = _load(ROOT / "tools" / "pack_runtime.py", "pack_runtime_under_test")
    assert pack.APP_VERSION == APP_VERSION


def test_no_other_hardcoded_version_in_build_or_launchers():
    """Nothing but version.py may carry a literal version string."""
    files = [
        ROOT / "build" / "build_app.py",
        ROOT / "build" / "installer.iss",
        ROOT / "tools" / "pack_runtime.py",
        ROOT / "LAUNCH_UI.bat",
        ROOT / "SETUP.bat",
        ROOT / "05 SCRIPT" / "tracker_gui.py",
    ]
    literal = re.compile(r'"\d+\.\d+\.\d+"|V001')
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        # comments explaining the tag format are fine; code is not
        code = "\n".join(l for l in text.splitlines()
                         if not l.lstrip().startswith(("::", ";", "#")))
        assert not literal.search(code), "%s still carries a literal version" % f.name


def test_setup_bat_can_parse_the_version_line():
    """SETUP.bat greps 'APP_VERSION = "x.y.z"' with findstr; keep the shape."""
    from core.version import APP_VERSION
    src = (ROOT / "05 SCRIPT" / "core" / "version.py").read_text(encoding="utf-8")
    lines = [l for l in src.splitlines() if l.startswith("APP_VERSION")]
    assert len(lines) == 1
    tokens = lines[0].split("=")
    assert tokens[1].replace('"', "").replace(" ", "") == APP_VERSION
