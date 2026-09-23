"""
Golden-string tests for the 2D exporters: Nuke Tracker4, Nuke CornerPin2D, After
Effects .jsx (nulls and corner pin), Nuke Roto, and the Blender scripts.

Fixture: 2 frames, 4 points, 200x100 frame, Timeline Start 1001.
"""
import re

import numpy as np
import pytest

import cotracker_2d as c2d
from mask_animator import AnimatedMask, export_to_nuke_roto_script

W, H = 200, 100
FPS = 24.0
# Frame 0: TL (10,20) TR (100,20) BR (100,90) BL (10,90); frame 1: everything +1.
TRACKS = np.array([
    [[10.0, 20.0], [100.0, 20.0], [100.0, 90.0], [10.0, 90.0]],
    [[11.0, 21.0], [101.0, 21.0], [101.0, 91.0], [11.0, 91.0]],
])
VIS = np.ones((2, 4), dtype=bool)


def _read(p):
    return p.read_text(encoding="utf-8")


# ----------------------------------------------------------------- Nuke Tracker4
# Column 9 is the error curve. With no confidence handed in, every sample counts
# as certain, so the error is a flat 0 - one key per frame, not a single key.
TRACKER4_ROW_1 = (
    '{ {curve K x1001 1} "track_001" {curve x1001 10.00 x1002 11.00} {curve x1001 80.00 x1002 79.00} '
    '{curve K x1001 0} {curve K x1001 0} 1 0 0 {curve x1001 0.0000 x1002 0.0000} 0 1 '
    '-15 -15 15 15 -25 -25 25 25 '
    '{} {} {} {} {} {} {} {} {} {} {} }'
)


def test_tracker4_golden(tmp_path):
    out = tmp_path / "t.nk"
    assert c2d.export_2d_nuke_tracker(TRACKS, VIS, W, H, FPS, out, start_frame=1001)
    text = _read(out)
    expected_head = (
        "set cut_paste_input [stack 0]\n"
        "version 14.0 v1\n"
        "push $cut_paste_input\n"
        "Tracker4 {\n"
        " tracks { { 1 31 4 }\n"
        "  { " + TRACKER4_ROW_1 + "\n"
    )
    assert text.startswith(expected_head)
    assert text.endswith(" }\n }\n name CoTracker2D_Tracker\n selected true\n xpos 0\n ypos 0\n}\n")
    assert text.count('"track_0') == 4


def test_tracker4_error_column_is_one_minus_confidence(tmp_path):
    """Roadmap 2.2: a soft section of a track has to show in Nuke's curve editor."""
    conf = np.array([[0.98, 0.5, 1.0, 0.0], [0.25, 0.5, 1.0, 0.0]])
    out = tmp_path / "t.nk"
    c2d.export_2d_nuke_tracker(TRACKS, VIS, W, H, FPS, out, start_frame=1001, conf=conf)
    text = _read(out)
    # Track 1 goes soft on the second frame: error 0.02 then 0.75.
    assert "1 0 0 {curve x1001 0.0200 x1002 0.7500} 0 1 " in text
    # A confidence of exactly 1 is error 0, and 0 is error 1 - the two ends.
    assert "1 0 0 {curve x1001 0.0000 x1002 0.0000} 0 1 " in text
    assert "1 0 0 {curve x1001 1.0000 x1002 1.0000} 0 1 " in text

    # Multi-layer nodes carry each layer's own confidence.
    multi = tmp_path / "m.nk"
    c2d.export_multi_layer_nuke_tracker(
        [{"name": "Wall A", "tracks": TRACKS[:, :1], "vis": VIS[:, :1], "conf": conf[:, :1]},
         {"name": "Floor", "tracks": TRACKS[:, 1:2], "vis": VIS[:, 1:2]}],
        W, H, FPS, multi, start_frame=1001)
    mtext = _read(multi)
    assert '"Wall_A_001"' in mtext and "{curve x1001 0.0200 x1002 0.7500}" in mtext
    # The layer with no confidence keeps the old flat-zero error.
    assert "{curve x1001 0.0000 x1002 0.0000}" in mtext


def test_tracker4_flips_y_to_nuke_bottom_left_origin(tmp_path):
    out = tmp_path / "t.nk"
    c2d.export_2d_nuke_tracker(TRACKS, VIS, W, H, FPS, out, start_frame=1001)
    text = _read(out)
    # Point 3 (BL in screen space, y=90) must land at y = 100 - 90 = 10 in Nuke.
    assert '"track_004" {curve x1001 10.00 x1002 11.00} {curve x1001 10.00 x1002 9.00}' in text


def test_tracker4_frame_step_and_multi_layer(tmp_path):
    out = tmp_path / "m.nk"
    layers = [{"name": "Wall A", "tracks": TRACKS, "vis": VIS}, {"name": "Floor", "tracks": TRACKS[:, :2], "vis": VIS[:, :2]}]
    assert c2d.export_multi_layer_nuke_tracker(layers, W, H, FPS, out, start_frame=1001, frame_step=3)
    text = _read(out)
    assert text.startswith("set cut_paste_input [stack 0]\nversion 14.0 v1\npush $cut_paste_input\nTracker4 {\n")
    assert text.count("Tracker4 {") == 2
    assert ' name Tracker_Wall_A\n label "Layer: Wall_A"\n' in text
    assert ' name Tracker_Floor\n label "Layer: Floor"\n selected true\n xpos 150\n' in text
    assert '"Wall_A_001" {curve x1001 10.00 x1004 11.00}' in text  # step 3
    assert '"Floor_002"' in text and '"Floor_003"' not in text


# ----------------------------------------------------------------- Nuke CornerPin2D
CORNERPIN_GOLDEN = """set cut_paste_input [stack 0]
version 14.0 v1
push $cut_paste_input
CornerPin2D {
 to1 {{curve x1001 10.00 x1002 11.00}} {{curve x1001 10.00 x1002 9.00}}
 to2 {{curve x1001 100.00 x1002 101.00}} {{curve x1001 10.00 x1002 9.00}}
 to3 {{curve x1001 100.00 x1002 101.00}} {{curve x1001 80.00 x1002 79.00}}
 to4 {{curve x1001 10.00 x1002 11.00}} {{curve x1001 80.00 x1002 79.00}}
 from1 {0 0}
 from2 {200 0}
 from3 {200 100}
 from4 {0 100}
 name CoTracker_CornerPin2D
 selected true
 xpos 0
 ypos 0
}
"""


def test_cornerpin_nuke_golden_and_y_flip(tmp_path):
    out = tmp_path / "cp.nk"
    assert c2d.export_2d_cornerpin_nuke(TRACKS, W, H, FPS, out, start_frame=1001)
    assert _read(out) == CORNERPIN_GOLDEN


def test_cornerpin_writers_order_corners_tl_tr_br_bl_regardless_of_input_order(tmp_path):
    # A 2x2 grid comes out TL, TR, BL, BR; an anticlockwise click gives TL, BL, BR, TR.
    grid_order = TRACKS[:, [0, 1, 3, 2]]
    anticlockwise = TRACKS[:, [0, 3, 2, 1]]
    assert c2d.order_corners_tl_tr_br_bl(TRACKS) == [0, 1, 2, 3]
    assert c2d.order_corners_tl_tr_br_bl(grid_order) == [0, 1, 3, 2]
    assert c2d.order_corners_tl_tr_br_bl(anticlockwise) == [0, 3, 2, 1]
    for tr in (grid_order, anticlockwise):
        out = tmp_path / "cp.nk"
        c2d.export_2d_cornerpin_nuke(tr, W, H, FPS, out, start_frame=1001)
        assert _read(out) == CORNERPIN_GOLDEN
        out = tmp_path / "cp.jsx"
        c2d.export_2d_cornerpin_ae(tr, W, H, FPS, out, start_frame=1001, timeline_start=1001)
        text = _read(out)
        assert "to1.setValueAtTime(0/fps, [10.00, 20.00]);" in text   # Upper Left
        assert "to2.setValueAtTime(0/fps, [100.00, 20.00]);" in text  # Upper Right
        assert "to3.setValueAtTime(0/fps, [100.00, 90.00]);" in text  # Lower Right
        assert "to4.setValueAtTime(0/fps, [10.00, 90.00]);" in text   # Lower Left


def test_cornerpin_needs_four_points(tmp_path):
    assert c2d.export_2d_cornerpin_nuke(TRACKS[:, :3], W, H, FPS, tmp_path / "a.nk") is False
    assert c2d.export_2d_cornerpin_ae(TRACKS[:, :3], W, H, FPS, tmp_path / "a.jsx") is False
    assert c2d.export_2d_cornerpin_blender(TRACKS[:, :3], W, H, FPS, tmp_path / "a.py") is False


# ----------------------------------------------------------------- After Effects (B15)
def test_ae_jsx_time_is_frame_minus_timeline_start_over_fps(tmp_path):
    out = tmp_path / "ae.jsx"
    c2d.export_2d_after_effects_jsx(TRACKS, VIS, W, H, FPS, out, start_frame=1001, timeline_start=1001)
    text = _read(out)
    assert "var fps = 24.0;" in text
    assert 'nullLayer_0.name = "Track_001";' in text
    assert "posProp_0.setValueAtTime(0/fps, [10.00, 20.00, 0]);" in text
    assert "posProp_0.setValueAtTime(1/fps, [11.00, 21.00, 0]);" in text
    assert "posProp_3.setValueAtTime(1/fps, [11.00, 91.00, 0]);" in text
    # The old bug: keys at (1001-1)/fps ~ 41 s
    assert "1000/fps" not in text
    # comp duration covers the exported range (2 frames at 24 fps)
    assert 'addComp("CoTracker_2D_Comp", 200, 100, 1.0, 0.08, 24.0)' in text


def test_ae_time_with_in_point_and_step():
    # In point 10 on a 1001 plate, step 2: local t=3 is frame 1017 -> 16 frames into the comp.
    assert c2d.ae_time_expr(c2d.frame_number(3, 1001 + 10, 2), 1001) == "16/fps"
    assert c2d._ae_comp_duration(2, 24.0, 1011, 2, 1001) == pytest.approx(13 / 24.0)


def test_ae_cornerpin_golden_tail(tmp_path):
    out = tmp_path / "cp.jsx"
    c2d.export_2d_cornerpin_ae(TRACKS, W, H, FPS, out, start_frame=1001, timeline_start=1001)
    text = _read(out)
    assert "var fps = 24.0;" in text
    expected = (
        "    to1.setValueAtTime(0/fps, [10.00, 20.00]);\n"
        "    to2.setValueAtTime(0/fps, [100.00, 20.00]);\n"
        "    to3.setValueAtTime(0/fps, [100.00, 90.00]);\n"
        "    to4.setValueAtTime(0/fps, [10.00, 90.00]);\n"
        "    to1.setValueAtTime(1/fps, [11.00, 21.00]);\n"
        "    to2.setValueAtTime(1/fps, [101.00, 21.00]);\n"
        "    to3.setValueAtTime(1/fps, [101.00, 91.00]);\n"
        "    to4.setValueAtTime(1/fps, [11.00, 91.00]);\n"
        "\n    app.endUndoGroup();\n"
    )
    assert expected in text
    assert text.rstrip().endswith("})();")


# ----------------------------------------------------------------- Nuke Roto (B15)
def test_roto_export_adds_timeline_start_and_flips_y(tmp_path):
    m = AnimatedMask("m_a", "Wall Mask", "exclusion")
    m.set_keyframe(0, [10, 20, 100, 90], "rect")
    m.set_keyframe(1, [11, 21, 101, 91], "rect")
    out = tmp_path / "roto.nk"
    assert export_to_nuke_roto_script([m], W, H, 2, out, timeline_start=1001)
    text = _read(out)
    assert text.startswith("set cut_paste_input [stack 0]\nversion 14.0 v1\npush $cut_paste_input\nRoto {\n")
    assert ' format "200 100 0 0 200 100 1.0 200x100"' in text
    assert "   {shape Roto_Wall_Mask {" in text
    # TL corner (10,20) -> Nuke (10, 80) on frame 1001, (11, 79) on 1002
    assert "    {pt {{curve x1001 10.00 x1002 11.00}} {{curve x1001 80.00 x1002 79.00}} 0 0 0 0}" in text
    # BL corner (10,90) -> Nuke y = 10
    assert "    {pt {{curve x1001 10.00 x1002 11.00}} {{curve x1001 10.00 x1002 9.00}} 0 0 0 0}" in text
    assert text.count("{pt ") == 4
    assert text.rstrip().endswith("}")

    # default keeps the old 1-based numbering
    export_to_nuke_roto_script([m], W, H, 2, out)
    assert "x1 10.00 x2 11.00" in _read(out)


# ----------------------------------------------------------------- Blender (C5)
def test_track_blob_roundtrip_and_size():
    rng = np.random.default_rng(0)
    T, N = 700, 2500
    base = rng.uniform(0, 3840, (N, 2))
    vel = rng.normal(0, 1.5, (N, 2))
    tracks = np.clip(base[None] + vel[None] * np.arange(T)[:, None, None] + rng.normal(0, 0.3, (T, N, 2)), 0, 3840)
    blob = c2d.pack_tracks_blob(tracks)
    assert blob["dtype"] == "<i2"
    back = c2d.unpack_tracks_blob(blob)
    assert back.shape == (T, N, 2)
    assert np.abs(back - tracks).max() <= 0.05 + 1e-9
    assert len(blob["b64"]) + len(blob["first"]) < 5_000_000

    # a jump too big for int16 deltas falls back to int32 and still round-trips
    jumpy = np.array([[[0.0, 0.0]], [[5000.0, 0.0]]])
    b2 = c2d.pack_tracks_blob(jumpy)
    assert b2["dtype"] == "<i4"
    np.testing.assert_allclose(c2d.unpack_tracks_blob(b2), jumpy)


def test_blender_empties_script_is_compact_and_self_decoding(tmp_path):
    rng = np.random.default_rng(1)
    T, N = 700, 2500
    tracks = rng.uniform(0, 1920, (N, 2))[None] + rng.normal(0, 0.5, (T, N, 2)).cumsum(axis=0)
    out = tmp_path / "tracks_2d_blender.py"
    assert c2d.export_2d_blender_empties(tracks, np.ones((T, N), bool), 1920, 1080, FPS, out, start_frame=1001)
    src = _read(out)
    assert out.stat().st_size < 5_000_000
    assert src.count("\n") < 200  # data is a blob, the loop runs in Blender
    compile(src, str(out), "exec")

    # Run the module header (everything before the bpy-dependent function) without bpy.
    ns = {}
    exec(src.split("def import_2d_tracks")[0].replace("import bpy\n", ""), ns)
    layer = ns["DATA"]["layers"][0]
    assert layer["collection"] == "CoTracker_2D_Tracks" and layer["prefix"] == "Track_2D_"
    assert layer["frames"][:3] == [1001, 1002, 1003]
    back = ns["unpack_tracks_blob"](layer["tracks"])
    assert np.abs(back - tracks).max() <= 0.05 + 1e-9
    assert "scene.frame_start = 1001" in src and "scene.frame_end = 1700" in src


def test_blender_multi_layer_and_cornerpin_scripts_parse(tmp_path):
    out = tmp_path / "multi.py"
    layers = [{"name": "Wall A", "tracks": TRACKS}, {"name": "Floor", "tracks": TRACKS[:, :2]}]
    assert c2d.export_multi_layer_blender(layers, W, H, FPS, out, start_frame=1001, frame_step=2)
    src = _read(out)
    compile(src, str(out), "exec")
    ns = {}
    exec(src.split("def import_multi_layer_tracks")[0].replace("import bpy\n", ""), ns)
    cols = [l["collection"] for l in ns["DATA"]["layers"]]
    assert cols == ["Layer_Wall_A", "Layer_Floor"]
    assert ns["DATA"]["layers"][0]["frames"] == [1001, 1003]
    assert "scene.frame_end = 1003" in src

    out2 = tmp_path / "cp.py"
    assert c2d.export_2d_cornerpin_blender(TRACKS[:, [0, 3, 2, 1]], W, H, FPS, out2, start_frame=1001)
    src2 = _read(out2)
    compile(src2, str(out2), "exec")
    ns2 = {}
    exec(src2.split("def setup_cornerpin_mesh")[0].replace("import bpy\n", ""), ns2)
    keys = ns2["KEYS"]
    assert [k[0] for k in keys] == [1001, 1002]
    # corners normalised in TL, TR, BR, BL order whatever the input order was
    tl, tr, br, bl = keys[0][1]
    assert tl[0] < tr[0] and bl[0] < br[0]
    assert tl[1] > bl[1] and tr[1] > br[1]


# ----------------------------------------------------------------- JSON / CSV frame numbering
def test_json_and_csv_use_timeline_frames(tmp_path):
    import json
    jp = tmp_path / "t.json"
    c2d.export_2d_json(TRACKS, VIS, W, H, jp, start_frame=1001, frame_step=2)
    data = json.loads(_read(jp))
    assert data["start_frame"] == 1001 and data["frame_step"] == 2
    assert [f["frame"] for f in data["tracks"][0]["frames"]] == [1001, 1003]
    cp = tmp_path / "t.csv"
    c2d.export_2d_csv(TRACKS, VIS, W, H, cp, start_frame=1001, frame_step=2)
    lines = _read(cp).splitlines()
    assert lines[0] == "frame,track_id,x,y,norm_x,norm_y,visible,confidence"
    assert lines[1].startswith("1001,1,10.00,20.00,")
    assert lines[5].startswith("1003,1,11.00,21.00,")
    assert re.match(r"^1003,4,11\.00,91\.00,0\.05500,0\.91000,1,1\.0000$", lines[8])


def test_json_and_csv_carry_confidence_and_layer_name(tmp_path):
    """Roadmap 2.2, and what the correction pass reads back out of the JSON."""
    import json
    conf = np.array([[0.9, 0.8, 0.7, 0.6], [0.5, 0.4, 0.3, 0.2]])
    jp = tmp_path / "t.json"
    c2d.export_2d_json(TRACKS, VIS, W, H, jp, start_frame=1001, conf=conf,
                       layer_name="Wall A")
    data = json.loads(_read(jp))
    assert data["layer"] == "Wall_A"          # spaces become underscores, as in every writer
    assert [f["confidence"] for f in data["tracks"][0]["frames"]] == [0.9, 0.5]
    assert [f["confidence"] for f in data["tracks"][3]["frames"]] == [0.6, 0.2]

    cp = tmp_path / "t.csv"
    c2d.export_2d_csv(TRACKS, VIS, W, H, cp, start_frame=1001, conf=conf)
    rows = [l.split(",") for l in _read(cp).splitlines()[1:]]
    assert [r[-1] for r in rows[:4]] == ["0.9000", "0.8000", "0.7000", "0.6000"]

    # No confidence given: the files still carry the column, as a flat 1.
    c2d.export_2d_json(TRACKS, VIS, W, H, jp, start_frame=1001)
    plain = json.loads(_read(jp))
    assert plain["layer"] is None
    assert all(f["confidence"] == 1.0 for f in plain["tracks"][0]["frames"])
