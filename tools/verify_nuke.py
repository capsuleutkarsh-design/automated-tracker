"""
Check an exported camera inside Nuke and print a report to paste back.

Four things in the Nuke export were written to the documented conventions but
have never been confirmed inside Nuke itself: the sign of the camera's window
translate, whether the .chan importer tolerates the two leading comment lines,
whether the Read lands the plate on the timeline frames we claim, and whether
the stack order in the .nk file wires ScanlineRender (bg = plate, obj = Scene,
cam = camera) and the STMaps (src = image, stmap = map) the way it is meant
to. This script answers all four and says plainly which look wrong.

Run it inside Nuke (Script Editor, or `nuke -t verify_nuke.py <scene folder>`):

    import sys; sys.argv = ['x', r'C:\\...\\04 SCENES\\my_shot\\3D_CAMERA_TRACK\\_latest']
    exec(open(r'C:\\...\\tools\\verify_nuke.py').read())

With no argument it uses the newest _latest folder it can find under 04 SCENES
relative to this file. Nothing is saved; the current script is left alone.
"""
import json
import os
import sys
import traceback

try:
    import nuke
except ImportError:
    sys.exit("This script has to run inside Nuke (Script Editor, or nuke -t).")

OK, BAD, INFO = "  ok   ", "  BAD  ", "       "


def find_scene():
    if len(sys.argv) > 1 and os.path.isdir(sys.argv[-1]):
        return sys.argv[-1]
    here = os.path.dirname(os.path.abspath(__file__))
    scenes = os.path.join(os.path.dirname(here), "04 SCENES")
    best, best_t = None, -1
    for shot in os.listdir(scenes) if os.path.isdir(scenes) else []:
        d = os.path.join(scenes, shot, "3D_CAMERA_TRACK", "_latest")
        if os.path.isdir(d) and os.path.getmtime(d) > best_t:
            best, best_t = d, os.path.getmtime(d)
    return best


def _named(nodes, base):
    """The pasted node whose name is `base`, or `base` plus the digits Nuke adds on a clash."""
    for n in nodes:
        name = n.name()
        if name == base or (name.startswith(base) and name[len(base):].isdigit()):
            return n
    return None


def _input_name(node, i):
    src = node.input(i)
    return src.name() if src is not None else None


def check_wiring(added):
    """Every input of the rig and the STMap chain, against what the export meant."""
    print("\nWiring")
    render = _named(added, "ScanlineRender_Comp")
    plate = _named(added, "Plate_Timewarp") or _named(added, "Plate_Footage")
    scene3d = _named(added, "Scene3D")
    cam = _named(added, "Solved_Camera")
    if render is None:
        print(BAD + "no ScanlineRender_Comp in the script")
    else:
        for i, label, want in ((0, "bg", plate), (1, "obj", scene3d), (2, "cam", cam)):
            got = _input_name(render, i)
            ok = want is not None and got == want.name()
            print((OK if ok else BAD) + "ScanlineRender input %d (%s) = %s  (expected %s)"
                  % (i, label, got, want.name() if want is not None else "?"))
    if scene3d is not None:
        geo = [_input_name(scene3d, i) for i in range(scene3d.inputs())]
        points = _named(added, "Sparse_PointCloud")
        ok = points is not None and points.name() in geo and None not in geo
        print((OK if ok else BAD) + "Scene3D inputs = %s" % ", ".join(str(g) for g in geo))

    undist = _named(added, "Undistort_Plate")
    if undist is not None:
        for i, label, base in ((0, "src", "Plate_Original"), (1, "stmap", "Undistort_Map")):
            want = _named(added, base)
            got = _input_name(undist, i)
            ok = want is not None and got == want.name()
            print((OK if ok else BAD) + "Undistort_Plate input %d (%s) = %s  (expected %s)"
                  % (i, label, got, base))
    redist = _named(added, "Redistort_Comp")
    if redist is not None:
        got_src = _input_name(redist, 0)
        print((OK if got_src is None else BAD) + "Redistort_Comp input 0 (src) = %s  (expected empty)"
              % got_src)
        want = _named(added, "Redistort_Map")
        got = _input_name(redist, 1)
        ok = want is not None and got == want.name()
        print((OK if ok else BAD) + "Redistort_Comp input 1 (stmap) = %s  (expected Redistort_Map)" % got)


def report(scene):
    print("=" * 66)
    print("Automated Tracker - Nuke check")
    print("scene:", scene)
    print("=" * 66)

    meta = {}
    meta_path = os.path.join(scene, "camera_track.json")
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)
    start = int(meta.get("timeline_start", 1))
    step = int(meta.get("frame_step", 1) or 1)
    print("expected: timeline_start %d, frame_step %d, fps %s"
          % (start, step, meta.get("fps")))

    nk = os.path.join(scene, "camera_track_nuke.nk")
    if not os.path.isfile(nk):
        print(BAD + "camera_track_nuke.nk is missing")
        return
    before = set(n.name() for n in nuke.allNodes())
    nuke.nodePaste(nk)
    added = [n for n in nuke.allNodes() if n.name() not in before]
    print("\nnodes created: %s" % ", ".join(sorted(n.Class() for n in added)))

    cams = [n for n in added if n.Class().startswith("Camera")]
    reads = [n for n in added if n.Class() == "Read"]
    warps = [n for n in added if n.Class() == "TimeWarp"]
    stmaps = [n for n in added if n.Class() == "STMap"]

    # --- camera -----------------------------------------------------------
    if not cams:
        print(BAD + "no Camera node in the script")
    else:
        cam = cams[0]
        print("\nCamera '%s'" % cam.name())
        ro = cam["rot_order"].value() if "rot_order" in cam.knobs() else "?"
        print((OK if ro == "XYZ" else BAD) + "rot_order = %s (exports assume XYZ)" % ro)
        for k in ("focal", "haperture", "vaperture"):
            if k in cam.knobs():
                print(INFO + "%s = %.4f" % (k, cam[k].value()))
        if "win_translate" in cam.knobs():
            wt = cam["win_translate"].value()
            print(INFO + "win_translate = %.5f, %.5f  (0,0 means the lens is centred)" % (wt[0], wt[1]))
        print(INFO + "sample of the animation:")
        for f in (start, start + step, start + step * 2):
            t = [cam["translate"].getValueAt(f, i) for i in range(3)]
            r = [cam["rotate"].getValueAt(f, i) for i in range(3)]
            print(INFO + "   frame %d  translate %8.3f %8.3f %8.3f   rotate %8.3f %8.3f %8.3f"
                  % (f, t[0], t[1], t[2], r[0], r[1], r[2]))
        # A camera that never moves means the keys landed outside the range we
        # asked about, which is the classic symptom of a frame-number mistake.
        t0 = [cam["translate"].getValueAt(start, i) for i in range(3)]
        t1 = [cam["translate"].getValueAt(start + step * 2, i) for i in range(3)]
        moved = any(abs(a - b) > 1e-6 for a, b in zip(t0, t1))
        print((OK if moved else BAD) + "camera %s between frames %d and %d"
              % ("moves" if moved else "DOES NOT move", start, start + step * 2))

    # --- plate ------------------------------------------------------------
    for rd in reads:
        print("\nRead '%s'" % rd.name())
        print(INFO + "file = %s" % rd["file"].value())
        for k in ("first", "last", "origfirst", "origlast", "frame_mode", "frame"):
            if k in rd.knobs():
                print(INFO + "%s = %s" % (k, rd[k].value()))
        # The pipeline de-squeezes before solving, so every plate it hands over
        # has square pixels. A Read that came back with a pixel aspect would
        # mean something squeezed it a second time.
        if "pixel_aspect" in rd.knobs():
            pa = rd["pixel_aspect"].value()
            print((OK if abs(pa - 1.0) < 1e-6 else BAD) + "pixel_aspect = %s" % pa)
        try:
            resolved = rd["file"].evaluate(start) if hasattr(rd["file"], "evaluate") else None
        except Exception:
            resolved = None
        if resolved:
            exists = os.path.isfile(resolved)
            print((OK if exists else BAD) + "at frame %d this resolves to %s%s"
                  % (start, resolved, "" if exists else "  <- that file does not exist"))

    if warps:
        for w in warps:
            print("\nTimeWarp '%s' lookup = %s" % (w.name(), w["lookup"].value()))
            for f in (start, start + step):
                try:
                    print(INFO + "   frame %d -> %s" % (f, w["lookup"].getValueAt(f)))
                except Exception as e:
                    print(BAD + "   could not evaluate at %d: %s" % (f, e))

    for s in stmaps:
        print("\nSTMap '%s' (disabled=%s)" % (s.name(), s["disable"].value() if "disable" in s.knobs() else "?"))

    check_wiring(added)

    # --- chan -------------------------------------------------------------
    chan = os.path.join(scene, "camera_track.chan")
    if os.path.isfile(chan) and cams:
        print("\n.chan import")
        probe = nuke.createNode("Camera2", inpanel=False)
        try:
            probe["rot_order"].setValue("XYZ")
            probe["read_from_file"].setValue(True)
            probe["file"].setValue(chan.replace("\\", "/"))
            nuke.root().begin()
            t = [probe["translate"].getValueAt(start, i) for i in range(3)]
            ref = [cams[0]["translate"].getValueAt(start, i) for i in range(3)]
            same = all(abs(a - b) < 1e-3 for a, b in zip(t, ref))
            print((OK if same else BAD) + "chan frame %d translate %8.3f %8.3f %8.3f  (script camera %8.3f %8.3f %8.3f)"
                  % (start, t[0], t[1], t[2], ref[0], ref[1], ref[2]))
            if not same:
                print(INFO + "   a mismatch here usually means Nuke skipped the '#' comment lines "
                             "differently than expected, or the vertical FOV column is wrong")
        finally:
            nuke.delete(probe)
    print("\nDone. Paste everything above back to whoever asked for it.")


try:
    scene_dir = find_scene()
    if not scene_dir:
        print("No exported scene found. Pass the folder holding camera_track_nuke.nk.")
    else:
        report(scene_dir)
except Exception:
    traceback.print_exc()
