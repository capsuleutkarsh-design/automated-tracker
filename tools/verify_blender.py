"""
Check an exported camera inside Blender and print a report to paste back.

Two things in the Blender export were written to the documented conventions but
have never been confirmed in Blender itself: whether the point cloud's colour
attribute is really called "Col", and whether the background image lands on the
right frames when the plate is a numbered sequence. This runs the generated
import script in a clean scene and reports what actually happened.

    blender --background --python tools\\verify_blender.py -- "<scene folder>"

With no argument it uses the newest _latest folder under 04 SCENES relative to
this file. Nothing is saved.
"""
import json
import os
import sys
import traceback

try:
    import bpy
except ImportError:
    sys.exit("This script has to run inside Blender (blender --background --python ...).")

OK, BAD, INFO = "  ok   ", "  BAD  ", "       "


def arg_scene():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if argv and os.path.isdir(argv[0]):
        return argv[0]
    here = os.path.dirname(os.path.abspath(__file__))
    scenes = os.path.join(os.path.dirname(here), "04 SCENES")
    best, best_t = None, -1
    for shot in os.listdir(scenes) if os.path.isdir(scenes) else []:
        d = os.path.join(scenes, shot, "3D_CAMERA_TRACK", "_latest")
        if os.path.isdir(d) and os.path.getmtime(d) > best_t:
            best, best_t = d, os.path.getmtime(d)
    return best


def report(scene):
    print("=" * 66)
    print("Automated Tracker - Blender check")
    print("scene:", scene)
    print("blender:", bpy.app.version_string)
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

    script = os.path.join(scene, "import_to_blender.py")
    if not os.path.isfile(script):
        print(BAD + "import_to_blender.py is missing")
        return

    bpy.ops.wm.read_factory_settings(use_empty=True)
    before_obj = set(bpy.data.objects.keys())
    print("\nrunning import_to_blender.py ...")
    with open(script, encoding="utf-8") as f:
        code = f.read()
    exec(compile(code, script, "exec"), {"__name__": "__main__", "__file__": script})
    added = [n for n in bpy.data.objects.keys() if n not in before_obj]
    print("objects created: %s" % ", ".join(sorted(added)) or "(none)")

    sc = bpy.context.scene
    print("\nscene range %d-%d at %.3f fps (fps_base %.6f)"
          % (sc.frame_start, sc.frame_end, sc.render.fps / sc.render.fps_base, sc.render.fps_base))
    print((OK if sc.frame_start == start else BAD) + "frame_start is %d, expected %d" % (sc.frame_start, start))
    print(INFO + "pixel aspect x=%.4f y=%.4f" % (sc.render.pixel_aspect_x, sc.render.pixel_aspect_y))

    cams = [o for o in bpy.data.objects if o.type == "CAMERA"]
    if not cams:
        print(BAD + "no camera was created")
    else:
        cam = cams[0]
        print("\nCamera '%s'  lens %.4f mm  sensor %.3f x %.3f  shift %.5f %.5f"
              % (cam.name, cam.data.lens, cam.data.sensor_width, cam.data.sensor_height,
                 cam.data.shift_x, cam.data.shift_y))
        for f in (start, start + step, start + step * 2):
            sc.frame_set(f)
            loc = cam.matrix_world.translation
            rot = cam.matrix_world.to_euler("XYZ")
            print(INFO + "   frame %d  loc %8.3f %8.3f %8.3f   rot %8.3f %8.3f %8.3f"
                  % (f, loc.x, loc.y, loc.z,
                     rot.x * 57.29578, rot.y * 57.29578, rot.z * 57.29578))
        sc.frame_set(start)
        a = cam.matrix_world.translation.copy()
        sc.frame_set(start + step * 2)
        moved = (cam.matrix_world.translation - a).length > 1e-6
        print((OK if moved else BAD) + "camera %s between frames %d and %d"
              % ("moves" if moved else "DOES NOT move", start, start + step * 2))

    # --- background plate --------------------------------------------------
    for cam in cams:
        for bg in getattr(cam.data, "background_images", []):
            img = bg.image
            print("\nbackground image: %s" % (img.filepath if img else "(none)"))
            if img:
                print(INFO + "source=%s frames=%s start=%s offset=%s"
                      % (img.source, getattr(bg.image_user, "frame_duration", "?"),
                         getattr(bg.image_user, "frame_start", "?"),
                         getattr(bg.image_user, "frame_offset", "?")))
                resolved = bpy.path.abspath(img.filepath)
                print((OK if os.path.exists(os.path.dirname(resolved)) else BAD)
                      + "plate folder %s" % os.path.dirname(resolved))

    # --- point cloud -------------------------------------------------------
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    for o in meshes:
        me = o.data
        names = [a.name for a in me.color_attributes] if hasattr(me, "color_attributes") else []
        print("\nmesh '%s': %d verts, colour attributes %s"
              % (o.name, len(me.vertices), names or "(none)"))
        if len(me.vertices) > 100:
            if names:
                print(OK + "point cloud carries colour as '%s'" % names[0])
                if "Col" not in names:
                    print(INFO + "   the exporter assumed 'Col'; update it to '%s'" % names[0])
            else:
                print(BAD + "point cloud has no colour attribute - the PLY colours were lost")

    print("\nDone. Paste everything above back to whoever asked for it.")


try:
    scene_dir = arg_scene()
    if not scene_dir:
        print("No exported scene found. Pass the folder holding import_to_blender.py after --.")
    else:
        report(scene_dir)
except Exception:
    traceback.print_exc()
