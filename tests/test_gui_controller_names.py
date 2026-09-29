"""
The panels that are not widgets reach the window only through `self.ctx`.

CorrectionController and SceneSetupPanel are plain classes handed an
AppContext. Code moved into them from the window kept a few `self.player`,
`self.current_fps`, `QMessageBox.information(self, ...)` and bare `ACCENT`
names that only made sense on the window, and each one was a crash the first
time an artist pressed the button. Two guards here:

- a static pass over every plain (non-Qt) class in gui/: every `self.X` it
  reads must be something the class itself defines or assigns, no Qt dialog
  may be parented to a plain object, and no function may use a module-level
  name that is never defined;
- the correction and scene-setup paths driven for real with a fake context.
"""
import ast
import builtins
import os
import symtable
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "05 SCRIPT" / "gui"


# =============================================================================
# STATIC: NAMES THAT ARE NEVER DEFINED
# =============================================================================
def _base_names(cls):
    out = []
    for b in cls.bases:
        if isinstance(b, ast.Name):
            out.append(b.id)
        elif isinstance(b, ast.Attribute):
            out.append(b.attr)
    return out


def _provided(cls, classes):
    """Everything a plain class defines, assigns or registers on itself."""
    names = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    for node in ast.walk(cls):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self" and isinstance(node.ctx, (ast.Store, ast.Del))):
            names.add(node.attr)
        if isinstance(node, ast.Call) and node.args:
            f = node.func
            fname = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
            a0 = node.args[0]
            # _register("btn_x", widget) and setattr(self, "btn_x", widget) /
            # object.__setattr__(self, "_x", ...) publish a name on self.
            if fname == "_register" and isinstance(a0, ast.Constant):
                names.add(a0.value)
            if (fname in ("setattr", "__setattr__") and isinstance(a0, ast.Name)
                    and a0.id == "self" and len(node.args) > 1
                    and isinstance(node.args[1], ast.Constant)):
                names.add(node.args[1].value)
    for base in _base_names(cls):
        if base in classes:
            names |= _provided(classes[base], classes)
    return names


def _plain_class_problems(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    problems = []
    for cls in classes.values():
        external = [b for b in _base_names(cls) if b not in classes and b != "object"]
        if external:
            # A Qt (or other library) base brings attributes this pass cannot see.
            continue
        provided = _provided(cls, classes)
        for node in ast.walk(cls):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "self" and isinstance(node.ctx, ast.Load)
                    and not node.attr.startswith("__") and node.attr not in provided):
                problems.append("%s:%d %s reads self.%s, which it never defines"
                                % (path.name, node.lineno, cls.name, node.attr))
            if (isinstance(node, ast.Call) and node.args
                    and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id.startswith("Q")):
                problems.append("%s:%d %s passes itself (not a widget) to %s.%s"
                                % (path.name, node.lineno, cls.name,
                                   node.func.value.id, node.func.attr))
    return problems


def _undefined_globals(path):
    src = path.read_text(encoding="utf-8")
    top = symtable.symtable(src, str(path), "exec")
    known = {s.get_name() for s in top.get_symbols()
             if s.is_assigned() or s.is_imported() or s.is_namespace()}
    known |= set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__"}
    problems = []

    def walk(table):
        for sym in table.get_symbols():
            if (sym.is_referenced() and sym.is_global() and not sym.is_assigned()
                    and not sym.is_imported() and sym.get_name() not in known):
                problems.append("%s: %s uses undefined name %s"
                                % (path.name, table.get_name(), sym.get_name()))
        for child in table.get_children():
            walk(child)

    walk(top)
    return problems


@pytest.mark.parametrize("path", sorted(GUI.glob("*.py")), ids=lambda p: p.name)
def test_plain_gui_classes_only_read_what_they_define(path):
    assert _plain_class_problems(path) == []


@pytest.mark.parametrize("path", sorted(GUI.glob("*.py")), ids=lambda p: p.name)
def test_gui_modules_use_no_undefined_names(path):
    assert _undefined_globals(path) == []


# =============================================================================
# DRIVEN: THE CORRECTION AND SCENE-SETUP PATHS WITH A FAKE CONTEXT
# =============================================================================
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record


class _FakeWidget:
    def __init__(self, checked=False, text=""):
        self._checked = checked
        self._text = text

    def isChecked(self):
        return self._checked

    def setChecked(self, v):
        self._checked = bool(v)

    def currentText(self):
        return self._text

    def __getattr__(self, name):
        return lambda *a, **k: None


class _FakeLayer:
    def __init__(self, name):
        self.name = name
        self.corrections = []
        self.export_cornerpin = False
        self.mode = "track"

    def correction_at(self, frame, point):
        return None


class _FakeCanvas:
    def __init__(self, layers):
        self.layers = layers
        self.current_frame = 0

    def tracked_index_for_frame(self, frame):
        return int(frame)

    def __getattr__(self, name):
        return lambda *a, **k: None


def _fake_ctx(tmp_path):
    np = pytest.importorskip("numpy")
    player = _Recorder()
    parent = object()
    jobs = []
    ctx = SimpleNamespace(
        OK="ok", ERR="err", WARN="warn", ACCENT="accent", TEXT_DIM="dim",
        canvas=_FakeCanvas([_FakeLayer("Layer 1")]),
        player=player,
        fps=25.0,
        timeline_start=1001,
        scenes_dir=tmp_path / "04 SCENES",
        videos_dir=tmp_path / "02 VIDEOS",
        ui=SimpleNamespace(combo_2d_model=_FakeWidget(text="Online"),
                           chk_vram_chunk=_FakeWidget(checked=True)),
        logs=[],
        jobs=jobs,
    )
    ctx.dialog_parent = lambda: parent
    ctx.parent_sentinel = parent
    ctx.log_2d = lambda text, color=None: ctx.logs.append((text, color))
    ctx.log_3d = lambda text, color=None: ctx.logs.append((text, color))
    ctx.correction_busy = lambda: False
    ctx.max_dimension_2d = lambda: 0
    ctx.current_clip_name = lambda: "shot.mp4"
    ctx.current_shot_name = lambda: "shot"
    ctx.start_correction_worker = jobs.append
    ctx.schedule_project_save = lambda: None
    ctx.show_2d_tab = lambda: None
    tracks = np.zeros((4, 1, 2), dtype=np.float32)
    ctx.result = {"start_frame": 1001, "frame_step": 1, "frame_count": 4,
                  "layers": {"Layer 1": {"tracks": tracks}}}
    return ctx


@pytest.fixture
def correction(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from gui import correction as mod

    boxes = []

    class _Box:
        @staticmethod
        def information(parent, title, text):
            boxes.append(("information", parent, title))

        @staticmethod
        def warning(parent, title, text):
            boxes.append(("warning", parent, title))

    monkeypatch.setattr(mod, "QMessageBox", _Box)
    ctx = _fake_ctx(tmp_path)
    ctrl = mod.CorrectionController(ctx)
    ctrl.btn_show_result = _FakeWidget()
    return ctrl, ctx, boxes


def test_retrack_with_no_result_asks_over_the_window(correction):
    ctrl, ctx, boxes = correction
    ctrl.retrack_correction(False)
    assert boxes == [("information", ctx.parent_sentinel, "Nothing to Re-track")]


def test_reexport_and_result_toggle_with_no_result_ask_over_the_window(correction):
    ctrl, ctx, boxes = correction
    ctrl.reexport_2d_result()
    ctrl.toggle_tracked_result(True)
    assert [b[1] for b in boxes] == [ctx.parent_sentinel, ctx.parent_sentinel]


def test_retrack_outside_the_result_warns_over_the_window(correction):
    ctrl, ctx, boxes = correction
    ctrl._run_retrack("Layer 1", 0, 0, False)
    assert boxes == [("warning", ctx.parent_sentinel, "Frame Not in the Result")]


def test_retrack_pauses_the_player_and_starts_the_job(correction):
    ctrl, ctx, boxes = correction
    ctrl.result = ctx.result
    ctrl._track_layer_map = {"Layer 1": "Layer 1"}
    ctrl._run_retrack("Layer 1", 0, 2, True)
    assert ("pause_playback", (), {}) in ctx.player.calls
    assert len(ctx.jobs) == 1 and ctx.jobs[0]["mode"] == "retrack"
    assert ctx.jobs[0]["backwards"] is True
    assert boxes == []


def test_reexport_uses_the_context_frame_rate(correction):
    ctrl, ctx, boxes = correction
    ctrl.result = ctx.result
    ctrl._track_layer_map = {"Layer 1": "Layer 1"}
    ctrl.reexport_2d_result()
    assert len(ctx.jobs) == 1 and ctx.jobs[0]["mode"] == "export"
    assert ctx.jobs[0]["fps"] == 25.0
    ctx.fps = 0
    ctrl.reexport_2d_result()
    assert ctx.jobs[1]["fps"] == 24.0


def test_scene_pick_toggle_logs_in_the_accent_colour(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from gui.scene_setup import SceneSetupPanel

    ctx = _fake_ctx(tmp_path)
    panel = SceneSetupPanel(ctx)
    monkeypatch.setattr(panel, "set_pick_mode", lambda mode: None)
    panel.on_scene_pick_toggled("scale", True)
    assert ctx.logs and ctx.logs[-1][1] == "accent"
