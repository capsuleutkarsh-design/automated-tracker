"""
Multi-Format Photogrammetry Exporter (Enhanced with RANSAC Ground Plane Alignment)
Exports COLMAP sparse reconstruction to:
- import_to_blender.py (1-Click Blender script with camera, background sequence, point cloud & ground plane)
- camera_track.usda (Pixar Universal Scene Description)
- camera_track.chan (Nuke camera tracking channel)
- points3D.ply (Stanford Point Cloud with RGB colors)
- camera_track.json (Full structured matrices and intrinsics)
"""

import sys
import os
import shutil
import subprocess
import math
import json
from pathlib import Path
import numpy as np

# Optional USD Python binding
try:
    from pxr import Usd, UsdGeom, Gf, Sdf
    HAS_USD = True
except ImportError:
    HAS_USD = False


def qvec2rotmat(qvec):
    """Convert COLMAP quaternion (qw, qx, qy, qz) to 3x3 rotation matrix."""
    w, x, y, z = qvec
    return np.array([
        [1 - 2*y*y - 2*z*z, 2*x*y - 2*w*z, 2*x*z + 2*w*y],
        [2*x*y + 2*w*z, 1 - 2*x*x - 2*z*z, 2*y*z - 2*w*x],
        [2*x*z - 2*w*y, 2*y*z + 2*w*x, 1 - 2*x*x - 2*y*y]
    ])


def rotmat2euler(R):
    """
    Convert 3x3 rotation matrix to Euler angles (XYZ order in radians) for Blender/Nuke.
    """
    sy = math.sqrt(R[0, 0] * R[0, 0] + R[1, 0] * R[1, 0])
    singular = sy < 1e-6

    if not singular:
        x = math.atan2(R[2, 1], R[2, 2])
        y = math.atan2(-R[2, 0], sy)
        z = math.atan2(R[1, 0], R[0, 0])
    else:
        x = math.atan2(-R[1, 2], R[1, 1])
        y = math.atan2(-R[2, 0], sy)
        z = 0.0

    return x, y, z


def parse_colmap_cameras(cameras_file):
    """Parses COLMAP cameras.txt"""
    cameras = {}
    if not Path(cameras_file).exists():
        return cameras

    with open(cameras_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            cam_id = int(parts[0])
            model = parts[1]
            width = int(parts[2])
            height = int(parts[3])
            params = [float(p) for p in parts[4:]]

            # Focal length in pixels
            focal_x = params[0]
            focal_y = params[0]
            cx = width / 2.0
            cy = height / 2.0

            if model in ("PINHOLE", "OPENCV", "OPENCV_FISHEYE", "FULL_OPENCV"):
                focal_y = params[1]
                cx = params[2]
                cy = params[3]
            elif model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL"):
                cx = params[1]
                cy = params[2]
            elif model == "RADIAL":
                cx = params[1]
                cy = params[2]

            cameras[cam_id] = {
                "id": cam_id,
                "model": model,
                "width": width,
                "height": height,
                "focal_x": focal_x,
                "focal_y": focal_y,
                "cx": cx,
                "cy": cy,
                "params": params
            }
    return cameras


def parse_colmap_images(images_file):
    """Parses COLMAP images.txt"""
    images = {}
    if not Path(images_file).exists():
        return images

    with open(images_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith('#'):
            continue

        parts = line.split()
        if len(parts) >= 9:
            image_id = int(parts[0])
            qw, qx, qy, qz = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
            tx, ty, tz = float(parts[5]), float(parts[6]), float(parts[7])
            camera_id = int(parts[8])
            name = parts[9] if len(parts) > 9 else f"image_{image_id}"

            # Parse frame number from filename (e.g. frame_000042.jpg -> 42)
            frame_num = image_id
            import re
            m = re.search(r'(\d+)', name)
            if m:
                frame_num = int(m.group(1))

            R_colmap = qvec2rotmat(np.array([qw, qx, qy, qz]))
            t_colmap = np.array([tx, ty, tz])

            # Camera center in COLMAP world coordinates: C = -R^T * t
            R_world = R_colmap.T
            C_world = -R_world @ t_colmap

            images[image_id] = {
                "id": image_id,
                "frame": frame_num,
                "name": name,
                "camera_id": camera_id,
                "qvec": [qw, qx, qy, qz],
                "tvec": [tx, ty, tz],
                "center": C_world.tolist(),
                "R_world": R_world.tolist()
            }

            if i < len(lines) and not lines[i].startswith('#'):
                i += 1
    return images


def parse_colmap_points3D(points_file):
    """Parses COLMAP points3D.txt"""
    points = []
    if not Path(points_file).exists():
        return points

    with open(points_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split()
            if len(parts) >= 7:
                pt_id = int(parts[0])
                x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                r, g, b = int(parts[4]), int(parts[5]), int(parts[6])
                error = float(parts[7]) if len(parts) > 7 else 0.0
                points.append({
                    "id": pt_id,
                    "xyz": [x, y, z],
                    "rgb": [r, g, b],
                    "error": error
                })
    return points


def detect_ground_plane_ransac(points, max_iters=300, dist_thresh=0.08):
    """
    RANSAC plane estimation on 3D point cloud to find the dominant ground floor plane.
    Returns: (normal_vector, d_offset, inlier_ratio)
    """
    if len(points) < 20:
        return None, 0.0, 0.0

    xyz = np.array([p["xyz"] for p in points])
    num_pts = len(xyz)
    best_inliers = 0
    best_plane = None

    for _ in range(max_iters):
        # Sample 3 random points
        idx = np.random.choice(num_pts, 3, replace=False)
        p1, p2, p3 = xyz[idx[0]], xyz[idx[1]], xyz[idx[2]]

        v1 = p2 - p1
        v2 = p3 - p1
        normal = np.cross(v1, v2)
        norm_len = np.linalg.norm(normal)
        if norm_len < 1e-6:
            continue

        normal = normal / norm_len
        d = -np.dot(normal, p1)

        # Distance from points to plane: |n . p + d|
        distances = np.abs(np.dot(xyz, normal) + d)
        inliers = np.sum(distances < dist_thresh)

        if inliers > best_inliers:
            best_inliers = inliers
            best_plane = (normal, d)

    inlier_ratio = best_inliers / num_pts
    if inlier_ratio > 0.15 and best_plane is not None:
        return best_plane[0], best_plane[1], inlier_ratio
    return None, 0.0, 0.0


# =============================================================================
# EXPORTERS
# =============================================================================
def export_blender_script(scene_dir, cameras, images, points, output_script_path=None, fps=None):
    """
    Generates a 1-Click Python script for Blender that sets up camera,
    animation, background image sequence, Geometry Nodes point cloud (EEVEE & Cycles renderable),
    RANSAC ground plane, and provides Alembic (.abc) export.
    """
    scene_path = Path(scene_dir).resolve()
    if output_script_path is None:
        output_script_path = scene_path / "import_to_blender.py"

    sorted_images = sorted(images.values(), key=lambda x: x["frame"])
    if not sorted_images or not cameras:
        return False

    first_cam = next(iter(cameras.values()))
    width = first_cam["width"]
    height = first_cam["height"]
    # Frame rate comes from the caller (probed off the source clip). The old code read a
    # "fps" key that parse_colmap_cameras never sets, so every export was silently 30.
    fps_val = float(fps) if fps else float(first_cam.get("fps", 0) or 0) or 24.0
    # Blender stores the rate as fps / fps_base, which is how NTSC rates such as
    # 29.97 (30000/1001) are represented exactly rather than rounded to 30.
    fps = int(round(fps_val))
    fps_base = round(fps / fps_val, 6) if fps_val > 0 else 1.0
    focal_x = first_cam["focal_x"]
    focal_y = first_cam.get("focal_y", focal_x)
    cx = first_cam.get("cx", width / 2.0)
    cy = first_cam.get("cy", height / 2.0)

    # Sensor and lens calculation
    sensor_width_mm = 36.0
    lens_mm = (focal_x * sensor_width_mm) / width
    sensor_height_mm = (lens_mm * height) / focal_y

    # Principal Point Shift for Blender
    shift_x = float((width / 2.0 - cx) / float(max(width, height)))
    shift_y = -float((height / 2.0 - cy) / float(max(width, height)))

    start_frame = sorted_images[0]["frame"]
    end_frame = sorted_images[-1]["frame"]

    if (scene_path / "images").exists():
        img_dir = scene_path / "images"
    elif (scene_path.parent.parent / "images").exists():
        img_dir = scene_path.parent.parent / "images"
    elif (scene_path.parent / "images").exists():
        img_dir = scene_path.parent / "images"
    else:
        img_dir = scene_path / "images"
    images_dir_str = str(img_dir).replace('\\', '/')

    frames_data = []
    for img in sorted_images:
        f_num = img["frame"]
        C = img["center"]
        R_colmap = np.array(img["R_world"])

        # COLMAP World -> Blender World: X_b = X_c, Y_b = Z_c, Z_b = -Y_c
        loc_blender = [C[0], C[2], -C[1]]

        T_colmap_to_blender = np.array([
            [1,  0,  0],
            [0, -1,  0],
            [0,  0, -1]
        ])
        T_world_colmap_to_blender = np.array([
            [1,  0,  0],
            [0,  0,  1],
            [0, -1,  0]
        ])

        R_blender = T_world_colmap_to_blender @ R_colmap @ T_colmap_to_blender
        rx, ry, rz = rotmat2euler(R_blender)

        frames_data.append({
            "frame": f_num,
            "loc": [round(v, 6) for v in loc_blender],
            "rot": [round(v, 6) for v in [rx, ry, rz]]
        })

    step = max(1, len(points) // 100000)
    sampled_points = points[::step]
    pts_blender = [[p["xyz"][0], p["xyz"][2], -p["xyz"][1]] for p in sampled_points]
    colors_blender = [[p["rgb"][0] / 255.0, p["rgb"][1] / 255.0, p["rgb"][2] / 255.0] for p in sampled_points]

    # RANSAC Ground Plane Detection
    plane_normal, plane_d, inlier_ratio = detect_ground_plane_ransac(points)
    ground_data = None
    if plane_normal is not None and inlier_ratio > 0.15:
        C_c = -plane_d * plane_normal
        loc_b = [C_c[0], C_c[2], -C_c[1]]
        norm_b = [plane_normal[0], plane_normal[2], -plane_normal[1]]
        ground_data = {
            "location": [round(v, 4) for v in loc_b],
            "normal": [round(v, 4) for v in norm_b],
            "inlier_ratio": round(inlier_ratio, 3)
        }

    output_script_name = Path(output_script_path).name
    output_script_dir_str = str(Path(output_script_path).parent).replace('\\', '/')
    mesh_path_str = str(scene_path / "environment_mesh.ply").replace('\\', '/')

    script_content = f'''"""
=============================================================================
1-CLICK BLENDER PHOTOGRAMMETRY CAMERA TRACK & POINT CLOUD IMPORTER
Generated by Automated_Tracker_V001.1

HOW TO USE IN BLENDER:
1. Open Blender.
2. Go to the 'Scripting' workspace at the top.
3. Click 'Open' and select this file ({output_script_name}).
4. Click 'Run Script' (or press Alt+P).
5. The Tracked Camera, Video Background, Point Cloud (Renderable), and Ground Plane load instantly!
=============================================================================
"""

import bpy
import os
import sys
import math
import json
from mathutils import Vector, Euler, Matrix

def set_linear_interpolation(target_obj):
    """Safely sets linear interpolation across Blender 3.x, 4.x, and 5.x Slotted Actions."""
    anim_data = getattr(target_obj, "animation_data", None)
    if not anim_data or not anim_data.action:
        return
    action = anim_data.action
    fcurves = getattr(action, "fcurves", None)
    if fcurves is None:
        slot = getattr(anim_data, "action_slot", None) or (action.slots[0] if getattr(action, "slots", None) else None)
        if slot and getattr(action, "layers", None) and action.layers[0].strips:
            try:
                channelbag = action.layers[0].strips[0].channelbag(slot)
                fcurves = channelbag.fcurves
            except Exception:
                fcurves = []
    if fcurves:
        for fc in fcurves:
            for kf in fc.keyframe_points:
                kf.interpolation = 'LINEAR'

def setup_point_cloud_geometry_nodes(pc_obj):
    """Builds a Geometry Nodes modifier and material so point cloud is visible and renderable in EEVEE & Cycles."""
    try:
        mat_name = "Photogrammetry_PointCloud_Mat"
        mat = bpy.data.materials.get(mat_name)
        if not mat:
            mat = bpy.data.materials.new(name=mat_name)
            mat.use_nodes = True
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            nodes.clear()

            attr_node = nodes.new(type='ShaderNodeAttribute')
            attr_node.attribute_name = "Col"
            attr_node.location = (-300, 0)

            bsdf_node = nodes.new(type='ShaderNodeBsdfPrincipled')
            bsdf_node.location = (0, 0)
            if hasattr(bsdf_node.inputs.get('Roughness'), 'default_value'):
                bsdf_node.inputs['Roughness'].default_value = 0.5

            out_node = nodes.new(type='ShaderNodeOutputMaterial')
            out_node.location = (300, 0)

            links.new(attr_node.outputs['Color'], bsdf_node.inputs['Base Color'])
            links.new(bsdf_node.outputs['BSDF'], out_node.inputs['Surface'])

        if pc_obj.data.materials:
            pc_obj.data.materials[0] = mat
        else:
            pc_obj.data.materials.append(mat)

        mod_name = "PointCloud_Spheres"
        gn_mod = pc_obj.modifiers.get(mod_name) or pc_obj.modifiers.new(name=mod_name, type='NODES')

        gn_group_name = "Photogrammetry_PointsToSpheres"
        node_group = bpy.data.node_groups.get(gn_group_name)
        if not node_group:
            node_group = bpy.data.node_groups.new(name=gn_group_name, type='GeometryNodeTree')
            if hasattr(node_group, 'interface'):
                node_group.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
                node_group.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
            elif hasattr(node_group, 'inputs'):
                node_group.inputs.new('NodeSocketGeometry', 'Geometry')
                node_group.outputs.new('NodeSocketGeometry', 'Geometry')

            gn_nodes = node_group.nodes
            gn_links = node_group.links
            gn_nodes.clear()

            in_n = gn_nodes.new('NodeGroupInput')
            in_n.location = (-400, 0)

            m2p_n = gn_nodes.new('GeometryNodeMeshToPoints')
            m2p_n.location = (-150, 0)
            if 'Radius' in m2p_n.inputs:
                m2p_n.inputs['Radius'].default_value = 0.025

            set_mat_n = gn_nodes.new('GeometryNodeSetMaterial')
            set_mat_n.location = (150, 0)
            set_mat_n.inputs['Material'].default_value = mat

            out_n = gn_nodes.new('NodeGroupOutput')
            out_n.location = (400, 0)

            gn_links.new(in_n.outputs['Geometry'], m2p_n.inputs['Mesh'])
            gn_links.new(m2p_n.outputs['Points'], set_mat_n.inputs['Geometry'])
            gn_links.new(set_mat_n.outputs['Geometry'], out_n.inputs['Geometry'])

        gn_mod.node_group = node_group
        print("✔ Point cloud equipped with Geometry Nodes & Vertex Color Shader.")
    except Exception as e:
        print(f"Notice on Geometry Nodes setup: {{e}}")

def setup_tracked_scene():
    print("🎬 Setting up Photogrammetry Camera Track in Blender...")

    scene = bpy.context.scene
    scene.render.resolution_x = {width}
    scene.render.resolution_y = {height}
    scene.render.fps = {fps}
    scene.render.fps_base = {fps_base}
    scene.frame_start = {start_frame}
    scene.frame_end = {end_frame}

    # 1. Create Collection
    col_name = "Photogrammetry_Track"
    col = bpy.data.collections.get(col_name)
    if not col:
        col = bpy.data.collections.new(col_name)
        scene.collection.children.link(col)

    # 2. Create Tracked Camera
    cam_data_name = "Tracked_Camera_Data"
    cam_obj_name = "Tracked_Camera"

    cam_data = bpy.data.cameras.get(cam_data_name) or bpy.data.cameras.new(cam_data_name)
    cam_data.sensor_fit = 'HORIZONTAL'
    cam_data.sensor_width = {sensor_width_mm:.4f}
    cam_data.sensor_height = {sensor_height_mm:.4f}
    cam_data.lens = {lens_mm:.4f}
    cam_data.shift_x = {shift_x:.6f}
    cam_data.shift_y = {shift_y:.6f}
    cam_data.display_size = 0.5
    cam_data.show_background_images = True

    # Setup Background Video Frame Sequence
    images_folder = r"{images_dir_str}"
    if os.path.exists(images_folder):
        frame_files = sorted([f for f in os.listdir(images_folder) if f.lower().endswith(('.jpg', '.jpeg', '.png'))])
        if frame_files:
            first_frame_path = os.path.join(images_folder, frame_files[0])
            bg_img = cam_data.background_images.new()
            try:
                img_data = bpy.data.images.load(first_frame_path, check_existing=True)
                img_data.source = 'SEQUENCE'
                bg_img.image = img_data
                bg_img.image_user.frame_duration = len(frame_files)
                bg_img.image_user.frame_start = {start_frame}
                bg_img.image_user.use_auto_refresh = True
                bg_img.alpha = 1.0
                bg_img.display_depth = 'BACK'
                print(f"✔ Attached background image sequence ({{len(frame_files)}} frames).")
            except Exception as e:
                print(f"Notice: Background image sequence load: {{e}}")

    cam_obj = bpy.data.objects.get(cam_obj_name)
    if not cam_obj:
        cam_obj = bpy.data.objects.new(cam_obj_name, cam_data)
        col.objects.link(cam_obj)

    scene.camera = cam_obj

    # 3. Animate Camera Location and Rotation Keyframes
    cam_obj.animation_data_clear()
    cam_obj.rotation_mode = 'XYZ'

    frames_data = {json.dumps(frames_data)}

    for fd in frames_data:
        f = fd["frame"]
        loc = fd["loc"]
        rot = fd["rot"]

        cam_obj.location = Vector(loc)
        cam_obj.rotation_euler = Euler(rot, 'XYZ')

        cam_obj.keyframe_insert(data_path="location", frame=f)
        cam_obj.keyframe_insert(data_path="rotation_euler", frame=f)

    # Set interpolation to Linear
    set_linear_interpolation(cam_obj)
    print(f"✔ Keyframed camera over {{len(frames_data)}} frames.")

    # 4. Create 3D Sparse Point Cloud Mesh with Geometry Nodes
    pts_coords = {json.dumps(pts_blender)}
    pts_colors = {json.dumps(colors_blender)}

    if pts_coords:
        pc_mesh_name = "PointCloud_Mesh"
        pc_obj_name = "Sparse_PointCloud"

        mesh = bpy.data.meshes.get(pc_mesh_name) or bpy.data.meshes.new(pc_mesh_name)
        mesh.clear_geometry()
        mesh.from_pydata(pts_coords, [], [])
        mesh.update()

        if hasattr(mesh, "color_attributes"):
            color_attr = mesh.color_attributes.new(name="Col", type='FLOAT_COLOR', domain='POINT')
            for idx, col_val in enumerate(pts_colors):
                color_attr.data[idx].color = (col_val[0], col_val[1], col_val[2], 1.0)
        elif hasattr(mesh, "vertex_colors"):
            vcol = mesh.vertex_colors.new(name="Col")

        pc_obj = bpy.data.objects.get(pc_obj_name)
        if not pc_obj:
            pc_obj = bpy.data.objects.new(pc_obj_name, mesh)
            col.objects.link(pc_obj)
        else:
            pc_obj.data = mesh

        setup_point_cloud_geometry_nodes(pc_obj)
        print(f"✔ Created sparse point cloud mesh with {{len(pts_coords)}} renderable points.")

    # 5. RANSAC Ground Plane Object
    ground_info = {json.dumps(ground_data)}
    if ground_info:
        try:
            gp_name = "Ground_Plane"
            gp_obj = bpy.data.objects.get(gp_name)
            if not gp_obj:
                bpy.ops.mesh.primitive_plane_add(size=10.0, location=ground_info["location"])
                gp_obj = bpy.context.active_object
                gp_obj.name = gp_name
                gp_obj.display_type = 'WIRE'
                if gp_obj.name not in col.objects:
                    col.objects.link(gp_obj)
                if gp_obj.name in scene.collection.objects:
                    scene.collection.objects.unlink(gp_obj)
            print("✔ Created RANSAC Ground Plane reference.")
        except Exception as e:
            pass

    # 6. 3D Environment Surface Mesh (if available)
    mesh_path = r"{mesh_path_str}"
    if os.path.exists(mesh_path):
        try:
            if hasattr(bpy.ops.wm, "ply_import"):
                bpy.ops.wm.ply_import(filepath=mesh_path)
            elif hasattr(bpy.ops.import_mesh, "ply"):
                bpy.ops.import_mesh.ply(filepath=mesh_path)
            imported_mesh = bpy.context.selected_objects[0] if bpy.context.selected_objects else None
            if imported_mesh:
                imported_mesh.name = "Environment_Mesh"
                imported_mesh.display_type = 'WIRE'
                if imported_mesh.name not in col.objects:
                    col.objects.link(imported_mesh)
                if imported_mesh.name in scene.collection.objects:
                    scene.collection.objects.unlink(imported_mesh)
                print("✔ Loaded 3D Environment Surface Mesh.")
        except Exception as e:
            print(f"Notice on 3D Mesh import: {{e}}")

    print("\\n=======================================================")
    print("🎉 SUCCESS: Camera Track & Point Cloud loaded in Blender!")
    print("=======================================================\\n")

def export_to_alembic(filepath=None):
    if filepath is None:
        filepath = os.path.join(r"{output_script_dir_str}", "camera_track.abc")
    
    try:
        bpy.ops.wm.alembic_export(
            filepath=filepath,
            start={start_frame},
            end={end_frame},
            selected=False,
            visible_objects_only=False,
            evaluation_mode='VIEWPORT'
        )
        print(f"✔ Exported Alembic (.abc) file to: {{filepath}}")
    except Exception as e:
        print(f"Notice: Alembic export exception: {{e}}")

def save_blend_file(filepath=None):
    if filepath is None:
        filepath = os.path.join(r"{output_script_dir_str}", "camera_track.blend")
    try:
        bpy.ops.wm.save_as_mainfile(filepath=filepath)
        print(f"✔ Saved Blender (.blend) project file to: {{filepath}}")
    except Exception as e:
        pass

if __name__ == "__main__":
    setup_tracked_scene()
    export_to_alembic()
    save_blend_file()
    if getattr(bpy.app, "background", False):
        sys.exit(0)
'''

    with open(output_script_path, 'w', encoding='utf-8') as f:
        f.write(script_content)

    return True


def export_usd_scene(scene_dir, cameras, images, points, output_usd_path=None, fps=None):
    if not HAS_USD:
        return False

    scene_path = Path(scene_dir).resolve()
    if output_usd_path is None:
        output_usd_path = scene_path / "camera_track.usda"

    sorted_images = sorted(images.values(), key=lambda x: x["frame"])
    if not sorted_images or not cameras:
        return False

    first_cam = next(iter(cameras.values()))
    width = first_cam["width"]
    height = first_cam["height"]
    sensor_width_mm = 36.0
    focal_mm = (first_cam["focal_x"] * sensor_width_mm) / width

    start_frame = sorted_images[0]["frame"]
    end_frame = sorted_images[-1]["frame"]

    stage = Usd.Stage.CreateNew(str(output_usd_path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetStartTimeCode(start_frame)
    stage.SetEndTimeCode(end_frame)
    usd_fps = float(fps) if fps else 24.0
    stage.SetTimeCodesPerSecond(usd_fps)
    stage.SetFramesPerSecond(usd_fps)

    # 1. Create Camera
    cam_prim_path = Sdf.Path("/World/Tracked_Camera")
    usd_cam = UsdGeom.Camera.Define(stage, cam_prim_path)
    usd_cam.CreateFocalLengthAttr(focal_mm)
    usd_cam.CreateHorizontalApertureAttr(sensor_width_mm)
    usd_cam.CreateVerticalApertureAttr(sensor_width_mm * (height / width))
    usd_cam.CreateClippingRangeAttr(Gf.Vec2f(0.01, 10000.0))

    xform_op = usd_cam.AddTransformOp()

    T_colmap_to_usd = np.array([
        [1,  0,  0],
        [0, -1,  0],
        [0,  0, -1]
    ])
    T_world_to_usd = np.array([
        [1,  0,  0],
        [0,  0,  1],
        [0, -1,  0]
    ])

    for img in sorted_images:
        f = img["frame"]
        C = img["center"]
        R_colmap = np.array(img["R_world"])

        loc_usd = [C[0], C[2], -C[1]]
        R_usd = T_world_to_usd @ R_colmap @ T_colmap_to_usd

        mat = Gf.Matrix4d(
            R_usd[0, 0], R_usd[0, 1], R_usd[0, 2], 0.0,
            R_usd[1, 0], R_usd[1, 1], R_usd[1, 2], 0.0,
            R_usd[2, 0], R_usd[2, 1], R_usd[2, 2], 0.0,
            loc_usd[0],  loc_usd[1],  loc_usd[2],  1.0
        )
        xform_op.Set(mat, Usd.TimeCode(f))

    # 2. Create 3D Points
    if points:
        pts_path = Sdf.Path("/World/Sparse_PointCloud")
        usd_pts = UsdGeom.Points.Define(stage, pts_path)

        pts_vec = [Gf.Vec3f(p["xyz"][0], p["xyz"][2], -p["xyz"][1]) for p in points]
        colors_vec = [Gf.Vec3f(p["rgb"][0] / 255.0, p["rgb"][1] / 255.0, p["rgb"][2] / 255.0) for p in points]
        widths = [0.02] * len(points)

        usd_pts.CreatePointsAttr(pts_vec)
        usd_pts.CreateDisplayColorAttr(colors_vec)
        usd_pts.CreateWidthsAttr(widths)

    stage.GetRootLayer().Save()
    return True


def export_nuke_chan(scene_dir, cameras, images, output_chan_path=None):
    """
    Exports a clean, standard 8-column ASCII .chan camera tracking file for Nuke:
    Columns: frame  tx  ty  tz  rx  ry  rz  fov
    """
    scene_path = Path(scene_dir).resolve()
    if output_chan_path is None:
        output_chan_path = scene_path / "camera_track.chan"

    sorted_images = sorted(images.values(), key=lambda x: x["frame"])
    if not sorted_images or not cameras:
        return False

    first_cam = next(iter(cameras.values()))
    width = first_cam["width"]
    focal_x = first_cam["focal_x"]
    fov_deg = 2.0 * math.atan(width / (2.0 * focal_x)) * 180.0 / math.pi

    T_colmap_to_nuke = np.array([
        [1,  0,  0],
        [0, -1,  0],
        [0,  0, -1]
    ])
    T_world_to_nuke = np.array([
        [1,  0,  0],
        [0,  0,  1],
        [0, -1,  0]
    ])

    lines = []
    for img in sorted_images:
        f = img["frame"]
        C = img["center"]
        R_colmap = np.array(img["R_world"])

        loc = [C[0], C[2], -C[1]]
        R_nuke = T_world_to_nuke @ R_colmap @ T_colmap_to_nuke
        rx, ry, rz = rotmat2euler(R_nuke)

        rx_deg = rx * 180.0 / math.pi
        ry_deg = ry * 180.0 / math.pi
        rz_deg = rz * 180.0 / math.pi

        lines.append(f"{f}\t{loc[0]:.6f}\t{loc[1]:.6f}\t{loc[2]:.6f}\t{rx_deg:.6f}\t{ry_deg:.6f}\t{rz_deg:.6f}\t{fov_deg:.4f}\n")

    with open(output_chan_path, 'w', encoding='utf-8') as f:
        f.writelines(lines)

    return True


def export_nuke_camera_script(scene_dir, cameras, images, points, output_nk_path=None, fps=None):
    """
    Generates a full 1-Click VFX Node Graph (.nk) for Foundry Nuke:
    - Read node (Footage Sequence)
    - Camera3 node (Keyframed poses, lens, sensor aperture, win_translate)
    - ReadGeo2 node (3D Point Cloud PLY)
    - Card2 node (RANSAC Ground Plane)
    - Scene node (3D stage)
    - ScanlineRender node (Pre-connected 3D projection comp)
    - Lens distortion annotations for exact matching
    """
    scene_path = Path(scene_dir).resolve()
    if output_nk_path is None:
        output_nk_path = scene_path / "camera_track_nuke.nk"

    sorted_images = sorted(images.values(), key=lambda x: x["frame"])
    if not sorted_images or not cameras:
        return False

    first_cam = next(iter(cameras.values()))
    width = first_cam["width"]
    height = first_cam["height"]
    focal_x = first_cam["focal_x"]
    focal_y = first_cam.get("focal_y", focal_x)
    cx = first_cam.get("cx", width / 2.0)
    cy = first_cam.get("cy", height / 2.0)

    sensor_width_mm = 36.0
    lens_mm = (focal_x * sensor_width_mm) / width
    sensor_height_mm = (lens_mm * height) / focal_y

    # Window Translate (Principal Point Offset) in Nuke
    win_u = (cx - width / 2.0) / width
    win_v = (cy - height / 2.0) / height

    start_frame = sorted_images[0]["frame"]
    end_frame = sorted_images[-1]["frame"]

    # Detect distortion parameters
    params = first_cam.get("params", [])
    model = first_cam.get("model", "SIMPLE_RADIAL")
    k1, k2 = 0.0, 0.0
    if model in ("SIMPLE_RADIAL", "SIMPLE_RADIAL_FISHEYE") and len(params) >= 4:
        k1 = params[3]
    elif model in ("RADIAL", "RADIAL_FISHEYE") and len(params) >= 5:
        k1, k2 = params[3], params[4]
    elif model in ("OPENCV", "OPENCV_FISHEYE", "FULL_OPENCV") and len(params) >= 6:
        k1, k2 = params[4], params[5]

    T_colmap_to_nuke = np.array([
        [1,  0,  0],
        [0, -1,  0],
        [0,  0, -1]
    ])
    T_world_to_nuke = np.array([
        [1,  0,  0],
        [0,  0,  1],
        [0, -1,  0]
    ])

    tx_parts, ty_parts, tz_parts = [], [], []
    rx_parts, ry_parts, rz_parts = [], [], []

    for img in sorted_images:
        f = img["frame"]
        C = img["center"]
        R_colmap = np.array(img["R_world"])

        loc = [C[0], C[2], -C[1]]
        R_nuke = T_world_to_nuke @ R_colmap @ T_colmap_to_nuke
        rx, ry, rz = rotmat2euler(R_nuke)

        rx_deg = rx * 180.0 / math.pi
        ry_deg = ry * 180.0 / math.pi
        rz_deg = rz * 180.0 / math.pi

        tx_parts.append(f"x{f} {loc[0]:.6f}")
        ty_parts.append(f"x{f} {loc[1]:.6f}")
        tz_parts.append(f"x{f} {loc[2]:.6f}")

        rx_parts.append(f"x{f} {rx_deg:.6f}")
        ry_parts.append(f"x{f} {ry_deg:.6f}")
        rz_parts.append(f"x{f} {rz_deg:.6f}")

    tx_curve = " ".join(tx_parts)
    ty_curve = " ".join(ty_parts)
    tz_curve = " ".join(tz_parts)
    rx_curve = " ".join(rx_parts)
    ry_curve = " ".join(ry_parts)
    rz_curve = " ".join(rz_parts)

    ply_name = "points3D.ply"
    ply_path_str = str((Path(output_nk_path).parent / ply_name)).replace('\\', '/')

    if (scene_path / "images").exists():
        img_dir = scene_path / "images"
    elif (scene_path.parent.parent / "images").exists():
        img_dir = scene_path.parent.parent / "images"
    elif (scene_path.parent / "images").exists():
        img_dir = scene_path.parent / "images"
    else:
        img_dir = scene_path / "images"
    img_dir_str = str(img_dir).replace('\\', '/')
    img_seq_path = f"{img_dir_str}/frame_%06d.jpg"

    # RANSAC ground plane for Nuke Card
    plane_normal, plane_d, inlier_ratio = detect_ground_plane_ransac(points)
    card_node_str = ""
    extra_inputs = 0
    if plane_normal is not None and inlier_ratio > 0.15:
        C_c = -plane_d * plane_normal
        loc_nuke = [C_c[0], C_c[2], -C_c[1]]
        extra_inputs += 1
        card_node_str = f'''push $cut_paste_input
Card2 {{
 inputs 0
 translate {{{loc_nuke[0]:.4f} {loc_nuke[1]:.4f} {loc_nuke[2]:.4f}}}
 rows 10
 columns 10
 name Ground_Plane
 selected false
 xpos 320
 ypos 0
}}
'''

    # 3D Environment Mesh node
    mesh_node_str = ""
    mesh_file = scene_path / "environment_mesh.ply"
    if mesh_file.exists():
        extra_inputs += 1
        mesh_ply_str = str(mesh_file).replace('\\', '/')
        mesh_node_str = f'''push $cut_paste_input
ReadGeo2 {{
 inputs 0
 file "{mesh_ply_str}"
 name Environment_Mesh
 selected false
 xpos 480
 ypos 0
}}
'''

    scene_inputs = str(2 + extra_inputs)
    nk_fps = float(fps) if fps else 24.0

    script = f'''set cut_paste_input [stack 0]
version 14.0 v1
BackdropNode {{
 inputs 0
 name Tracker_3D_Rig
 tile_color 0x243044ff
 gl_color 0x243044ff
 label "<b>Photogrammetry 3D Tracking Rig</b>\\n\\nCamera: {lens_mm:.2f}mm | Sensor: {sensor_width_mm:.1f}x{sensor_height_mm:.1f}mm | Shift: ({win_u:.4f}, {win_v:.4f})\\nDistortion: k1={k1:.6f}, k2={k2:.6f} | Points: {len(points):,} | Frames: {start_frame}-{end_frame} @ {nk_fps:.3f} fps"
 note_font_size 14
 xpos -220
 ypos -120
 bdwidth 820
 bdheight 480
 z_order 0
}}
push $cut_paste_input
Read {{
 inputs 0
 file "{img_seq_path}"
 format "{width} {height} 0 0 {width} {height} 1.0 {width}x{height}"
 first {start_frame}
 last {end_frame}
 origfirst {start_frame}
 origlast {end_frame}
 frame_rate {nk_fps:.4f}
 name Plate_Footage
 selected false
 xpos -180
 ypos 0
}}
push $cut_paste_input
Camera3 {{
 inputs 0
 rot_order XYZ
 translate {{{{curve {tx_curve}}}}} {{{{curve {ty_curve}}}}} {{{{curve {tz_curve}}}}}
 rotate {{{{curve {rx_curve}}}}} {{{{curve {ry_curve}}}}} {{{{curve {rz_curve}}}}}
 focal {lens_mm:.4f}
 haperture {sensor_width_mm:.4f}
 vaperture {sensor_height_mm:.4f}
 win_translate {{{win_u:.6f} {win_v:.6f}}}
 name Solved_Camera
 selected true
 xpos 0
 ypos 100
}}
push $cut_paste_input
ReadGeo2 {{
 inputs 0
 file "{ply_path_str}"
 name Sparse_PointCloud
 selected false
 xpos 160
 ypos 0
}}
{card_node_str}{mesh_node_str}Scene {{
 inputs {scene_inputs}
 name Scene3D
 selected false
 xpos 160
 ypos 140
}}
ScanlineRender {{
 inputs 3
 bg Plate_Footage
 obj Scene3D
 cam Solved_Camera
 output_motion_vectors false
 name ScanlineRender_Comp
 selected false
 xpos 0
 ypos 260
}}
'''
    with open(output_nk_path, 'w', encoding='utf-8') as f:
        f.write(script)
    return True


def export_ply_pointcloud(scene_dir, points, output_ply_path=None):
    scene_path = Path(scene_dir).resolve()
    if output_ply_path is None:
        output_ply_path = scene_path / "points3D.ply"

    if not points:
        return False

    header = f"""ply
format ascii 1.0
element vertex {len(points)}
property float x
property float y
property float z
property uchar red
property uchar green
property uchar blue
end_header
"""
    # newline='\n' matters: opened in default text mode on Windows, Python turns every
    # \n into \r\n, and strict PLY readers reject carriage returns in the header.
    with open(output_ply_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(header)
        for p in points:
            x, y, z = p["xyz"][0], p["xyz"][2], -p["xyz"][1]
            r, g, b = p["rgb"]
            f.write(f"{x:.6f} {y:.6f} {z:.6f} {r} {g} {b}\n")

    return True


def find_blender_executable(custom_path=None):
    """
    Fast, instant check for Blender executable without slow recursive drive scanning.
    """
    if custom_path:
        p = Path(custom_path)
        if p.is_file() and p.name.lower().startswith("blender"):
            return p
        if p.is_dir() and (p / "blender.exe").exists():
            return p / "blender.exe"

    # Check PATH first (fastest)
    which_b = shutil.which("blender") or shutil.which("blender.exe")
    if which_b:
        return Path(which_b)

    # Check common standard locations directly (no slow recursive drive scanning)
    common_paths = [
        r"C:\Program Files\Blender Foundation\Blender 4.3\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.5\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.4\blender.exe",
        r"C:\Program Files\Blender Foundation\Blender 3.3\blender.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe",
        r"D:\SteamLibrary\steamapps\common\Blender\blender.exe",
        r"D:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
        r"D:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
        r"D:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
    ]

    for p_str in common_paths:
        p = Path(p_str)
        if p.exists() and p.is_file():
            return p

    # Check subfolders in Blender Foundation directly (non-recursive, depth 1)
    bf_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Blender Foundation"
    if bf_dir.exists() and bf_dir.is_dir():
        try:
            for sub in bf_dir.iterdir():
                if sub.is_dir() and (sub / "blender.exe").exists():
                    return sub / "blender.exe"
        except Exception:
            pass

    return None


def auto_export_alembic_via_blender(scene_dir, blender_path=None, log_callback=None):
    """
    Runs headless Blender in the background to automatically bake camera_track.abc
    and camera_track.blend from import_to_blender.py.
    """
    scene_path = Path(scene_dir).resolve()
    blender_script = scene_path / "import_to_blender.py"
    if not blender_script.exists():
        return False

    blender_exe = find_blender_executable(blender_path)
    if not blender_exe:
        if log_callback:
            log_callback("💡 Tip: Run 'import_to_blender.py' in Blender or specify Blender Path in UI to auto-bake Alembic (.abc)!", "#a0a0b0")
        return False

    if log_callback:
        log_callback(f"▶ Auto-baking Alembic (.abc) & Blender (.blend) via {blender_exe.name}...", "#00d2ff")

    try:
        cmd = [str(blender_exe), "--background", "--python", str(blender_script)]
        from core.proc import hidden_kwargs
        proc = subprocess.run(cmd, text=True, timeout=90, **hidden_kwargs(capture=True))
        abc_path = scene_path / "camera_track.abc"
        if abc_path.exists():
            if log_callback:
                log_callback(f"✔ Successfully generated Alembic: {abc_path.name}", "#00ff88")
            return True
        else:
            if log_callback:
                err_snippet = proc.stderr.strip() if proc.stderr else ""
                log_callback(f"Notice: Headless Blender finished with code {proc.returncode}. {err_snippet[:120]}", "#e0a000")
    except subprocess.TimeoutExpired:
        if log_callback:
            log_callback("Notice: Headless Blender timed out after 90s.", "#e0a000")
    except Exception as e:
        if log_callback:
            log_callback(f"Notice: Auto Alembic export failed: {e}", "#e0a000")

    return False


def _probe_scene_fps(scene_path):
    """Best-effort frame rate for a 3D_CAMERA_TRACK folder, via its source clip."""
    try:
        if not getattr(sys, 'frozen', False):
            here = str(Path(__file__).resolve().parent)
            if here not in sys.path:
                sys.path.insert(0, here)
        from core.media_info import probe_fps
    except Exception:
        return None

    shot_dir = None
    for parent in scene_path.parents:
        if parent.name == "3D_CAMERA_TRACK":
            shot_dir = parent.parent
            break
    if shot_dir is None:
        return None

    videos_dir = shot_dir.parent.parent / "02 VIDEOS"
    if not videos_dir.is_dir():
        return None
    for ext in (".mp4", ".mov", ".avi", ".mkv", ".m4v"):
        candidate = videos_dir / (shot_dir.name + ext)
        if candidate.exists():
            return probe_fps(candidate)
    return None


def export_all_formats(scene_dir, blender_path=None, log_callback=None, fps=None,
                       start_frame=None):
    """
    Parses a COLMAP scene folder and automatically generates all export formats:
    - import_to_blender.py
    - camera_track_nuke.nk (1-Click Nuke Script)
    - camera_track.chan (Nuke)
    - camera_track.abc (Alembic)
    - camera_track.blend (Blender Scene)
    - camera_track.usda (USD)
    - points3D.ply (Point Cloud)
    - camera_track.json
    """
    scene_path = Path(scene_dir).resolve()
    sparse_dir = scene_path / "sparse"

    # When no rate is supplied (batch_reconstruct.bat / direct CLI use), work the shot
    # name back out of 04 SCENES/<shot>/3D_CAMERA_TRACK/<stamp> and probe its source clip.
    if not fps:
        fps = _probe_scene_fps(scene_path)

    cameras_file = sparse_dir / "cameras.txt"
    images_file = sparse_dir / "images.txt"
    points3D_file = sparse_dir / "points3D.txt"

    if not cameras_file.exists() and (sparse_dir / "0" / "cameras.txt").exists():
        cameras_file = sparse_dir / "0" / "cameras.txt"
        images_file = sparse_dir / "0" / "images.txt"
        points3D_file = sparse_dir / "0" / "points3D.txt"

    if not cameras_file.exists() or not images_file.exists():
        return {
            "success": False,
            "error": f"COLMAP TXT files not found in {sparse_dir}."
        }

    cameras = parse_colmap_cameras(cameras_file)
    images = parse_colmap_images(images_file)
    points = parse_colmap_points3D(points3D_file)

    # Frames are numbered from the extracted filenames, which the pipeline always
    # renumbers from 1. Shift them onto the shot's real timeline so the camera
    # lands on the same frames as the plate. Every exporter below reads
    # img['frame'], so doing it once here covers all of them.
    if start_frame and images:
        offset = int(start_frame) - min(i["frame"] for i in images.values())
        if offset:
            for i in images.values():
                i["frame"] += offset
            if log_callback:
                log_callback("   Timeline start %d - camera keys shifted by %+d frames."
                             % (int(start_frame), offset), "#a0a0b0")

    exported_files = []

    # 1. Blender 1-Click Script
    blender_script = scene_path / "import_to_blender.py"
    if export_blender_script(scene_path, cameras, images, points, blender_script, fps=fps):
        exported_files.append(str(blender_script))

    # 2. Nuke 1-Click Script (.nk)
    nuke_nk_file = scene_path / "camera_track_nuke.nk"
    if export_nuke_camera_script(scene_path, cameras, images, points, nuke_nk_file, fps=fps):
        exported_files.append(str(nuke_nk_file))

    # 3. Nuke .chan Camera File
    chan_file = scene_path / "camera_track.chan"
    # .chan carries no frame-rate field, so fps is not passed here.
    if export_nuke_chan(scene_path, cameras, images, chan_file):
        exported_files.append(str(chan_file))

    # 4. Universal Scene Description (.usda)
    if HAS_USD:
        usd_file = scene_path / "camera_track.usda"
        if export_usd_scene(scene_path, cameras, images, points, usd_file, fps=fps):
            exported_files.append(str(usd_file))

    # 5. PLY Point Cloud
    ply_file = scene_path / "points3D.ply"
    if export_ply_pointcloud(scene_path, points, ply_file):
        exported_files.append(str(ply_file))

    # 6. Structured JSON
    json_file = scene_path / "camera_track.json"
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump({
            "fps": float(fps) if fps else None,
            "cameras": cameras,
            "images": images,
            "points_count": len(points)
        }, f, indent=2)
    exported_files.append(str(json_file))

    # 7. Auto-bake Alembic (.abc) via background Blender if available
    auto_export_alembic_via_blender(scene_path, blender_path=blender_path, log_callback=log_callback)
    abc_file = scene_path / "camera_track.abc"
    if abc_file.exists():
        exported_files.append(str(abc_file))
    blend_file = scene_path / "camera_track.blend"
    if blend_file.exists():
        exported_files.append(str(blend_file))

    return {
        "success": True,
        "exported_files": exported_files,
        "frames_count": len(images),
        "points_count": len(points),
        "alembic_path": str(abc_file) if abc_file.exists() else None,
        "blend_path": str(blend_file) if blend_file.exists() else None
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        target_scene = sys.argv[1]
        cli_fps = float(sys.argv[2]) if len(sys.argv) > 2 else None
        res = export_all_formats(target_scene, fps=cli_fps)
        print(json.dumps(res, indent=2))
    else:
        print("Usage: python export_tools.py <path_to_scene_directory>")
