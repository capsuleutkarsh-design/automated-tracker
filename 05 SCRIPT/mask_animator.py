"""
Keyframed Animated Rotomasking Engine for 2D & 3D VFX Tracking
Supports:
- Keyframe-based Rectangle & Polygon mask animation with smooth linear vertex morphing.
- Fast binary PNG mask sequence rasterization for COLMAP 3D Feature Extractor (--ImageReader.mask_path).
- Dynamic spatio-temporal trajectory filtering for 2D Point Tracking (CoTracker3).
"""

import copy
from pathlib import Path
from PIL import Image, ImageDraw


class MaskKeyframe:
    def __init__(self, frame_idx, mask_type, data):
        """
        frame_idx: int
        mask_type: 'rect' or 'poly'
        data: list of 4 floats [x1, y1, x2, y2] for rect, or list of tuples [(x,y), ...] for poly
        """
        self.frame_idx = int(frame_idx)
        self.mask_type = "poly" # Unify everything to poly for 1:1 interpolation
        if mask_type == "rect":
            # Convert rect to a 4-point polygon: Top-Left, Top-Right, Bottom-Right, Bottom-Left
            x1, y1, x2, y2 = data
            min_x, min_y = min(x1, x2), min(y1, y2)
            max_x, max_y = max(x1, x2), max(y1, y2)
            self.data = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]
        elif mask_type == "poly":
            self.data = [(float(x), float(y)) for x, y in data]
        else:
            self.data = data

    def to_dict(self):
        return {
            "frame_idx": self.frame_idx,
            "mask_type": self.mask_type,
            # A copy: the canvas edits kf.data in place while a config is in flight.
            "data": copy.deepcopy(self.data)
        }


class AnimatedMask:
    """
    Manages an animated mask shape across timeframes with keyframed interpolation.
    """
    def __init__(self, mask_id, name="Mask", category="exclusion", mask_type="poly", color="#ff3355"):
        self.id = mask_id
        self.name = name
        self.category = category  # 'exclusion' or 'inclusion'
        self.mask_type = "poly"  # forced to poly
        self.color = color
        self.is_visible = True
        self.is_locked = False
        self.keyframes = {}  # frame_idx (int) -> MaskKeyframe

    def set_keyframe(self, frame_idx, data, mask_type=None):
        kf = MaskKeyframe(frame_idx, mask_type or self.mask_type, data)
        self.keyframes[int(frame_idx)] = kf
        return kf

    def delete_keyframe(self, frame_idx):
        frame_idx = int(frame_idx)
        if frame_idx in self.keyframes:
            del self.keyframes[frame_idx]
            return True
        return False

    def has_keyframe(self, frame_idx):
        return int(frame_idx) in self.keyframes

    def get_keyframe_frames(self):
        return sorted(self.keyframes.keys())

    def get_interpolated_geometry(self, frame_idx):
        """
        Returns the mask geometry at frame_idx (exact keyframe, or linearly interpolated).
        Returns:
            dict: {"type": "poly", "points": [(x,y), ...]}
        """
        if not self.keyframes:
            return None

        frame_idx = int(frame_idx)
        if frame_idx in self.keyframes:
            kf = self.keyframes[frame_idx]
            return {"type": "poly", "points": list(kf.data)}

        sorted_frames = sorted(self.keyframes.keys())
        if frame_idx <= sorted_frames[0]:
            kf = self.keyframes[sorted_frames[0]]
            return {"type": "poly", "points": list(kf.data)}
        if frame_idx >= sorted_frames[-1]:
            kf = self.keyframes[sorted_frames[-1]]
            return {"type": "poly", "points": list(kf.data)}

        # Find surrounding keyframes f1 < frame_idx < f2
        f1 = max(f for f in sorted_frames if f < frame_idx)
        f2 = min(f for f in sorted_frames if f > frame_idx)

        kf1 = self.keyframes[f1]
        kf2 = self.keyframes[f2]

        t = (frame_idx - f1) / float(f2 - f1)

        # Polygon to Polygon Interpolation (1:1 vertex mapping)
        pts1 = kf1.data
        pts2 = kf2.data
        
        # If topology somehow mismatches (e.g. legacy data), return closest keyframe
        if len(pts1) != len(pts2):
            return {"type": "poly", "points": list(pts1 if t < 0.5 else pts2)}

        interp_poly = []
        for p1, p2 in zip(pts1, pts2):
            ix = (1.0 - t) * p1[0] + t * p2[0]
            iy = (1.0 - t) * p1[1] + t * p2[1]
            interp_poly.append((float(ix), float(iy)))

        return {"type": "poly", "points": interp_poly}

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "mask_type": self.mask_type,
            "color": self.color,
            "is_visible": self.is_visible,
            "is_locked": self.is_locked,
            "keyframes": {str(f): kf.to_dict() for f, kf in self.keyframes.items()}
        }

    @classmethod
    def from_dict(cls, d):
        mask = cls(
            d.get("id", "m_1"),
            d.get("name", "Mask"),
            d.get("category", "exclusion"),
            d.get("mask_type", "poly"),
            d.get("color", "#ff3355")
        )
        mask.is_visible = d.get("is_visible", True)
        mask.is_locked = d.get("is_locked", False)
        kfs = d.get("keyframes", {})
        for f_str, kf_data in kfs.items():
            mask.set_keyframe(int(f_str), kf_data["data"], kf_data.get("mask_type"))
        return mask


def export_to_nuke_roto_script(animated_masks, width, height, total_frames, output_path, timeline_start=1):
    """
    Exports keyframed animated rotomasks directly as a native Foundry Nuke Roto / Bezier setup.
    Can be pasted directly into Nuke's Node Graph (Ctrl+V).

    Keyframes are stored on 0-based source frames; `timeline_start` is the frame the
    clip's first frame sits on in the comp (1, or 1001 for a numbered plate), the
    same offset the 2D tracker exports use. `total_frames` is unused and kept only
    so existing callers' positional arguments still line up.
    """
    timeline_start = int(timeline_start)
    out_file = Path(output_path)
    
    script_lines = [
        "set cut_paste_input [stack 0]",
        "version 14.0 v1",
        "push $cut_paste_input",
        "Roto {",
        " inputs 0",
        f" format \"{width} {height} 0 0 {width} {height} 1.0 {width}x{height}\"",
        " curves {",
        "  {",
        "   {f 0}"
    ]

    for mask_idx, mask in enumerate(animated_masks):
        if not mask.keyframes:
            continue
        
        shape_name = f"Roto_{mask.name.replace(' ', '_')}"
        script_lines.append(f"   {{shape {shape_name} {{")
        script_lines.append("    {f 0}")

        # Extract all keyframed vertices
        sorted_kfs = sorted(mask.keyframes.keys())
        first_kf_pts = mask.keyframes[sorted_kfs[0]].data
        num_pts = len(first_kf_pts)

        # Write each control point with its animation curve across keyframes
        for pt_idx in range(num_pts):
            x_curve_parts = []
            y_curve_parts = []
            for f in sorted_kfs:
                pts = mask.keyframes[f].data
                if pt_idx < len(pts):
                    # Invert Y for Nuke (Nuke's origin (0,0) is bottom-left, screen is top-left)
                    nx = pts[pt_idx][0]
                    ny = height - pts[pt_idx][1]
                    x_curve_parts.append(f"x{f + timeline_start} {nx:.2f}")
                    y_curve_parts.append(f"x{f + timeline_start} {ny:.2f}")

            x_curve_str = " ".join(x_curve_parts)
            y_curve_str = " ".join(y_curve_parts)

            script_lines.append(f"    {{pt {{{{curve {x_curve_str}}}}} {{{{curve {y_curve_str}}}}} 0 0 0 0}}")

        script_lines.append("   }}")

    script_lines.extend([
        "  }",
        " }",
        " name Roto_AutoTracked_Masks",
        " selected true",
        " xpos 0",
        " ypos 0",
        "}"
    ])

    out_file.write_text("\n".join(script_lines), encoding="utf-8")
    return True


def rasterize_masks_to_png(animated_masks, width, height, out_masks_dir, total_frames,
                           frame_filenames=None, progress_callback=None, frame_step=1):
    """
    Renders binary PNG mask sequence for COLMAP 3D Feature Extractor.
    In COLMAP:
    - Pure White (255) = Track static background
    - Pure Black (0) = Excluded region (moving actors/dynamic foreground)
    
    out_masks_dir: Path to output directory (e.g. 04 SCENES/<shot>/masks)
    total_frames: Total number of frames in video
    frame_filenames: Optional list of image filenames (e.g. ["frame_000001.jpg", ...])
    frame_step: Frame-skip used when the images were extracted. Mask keyframes are set
                against the full-rate timeline, so rendered frame t must be looked up at
                source frame t * frame_step or the shapes slide off over time.
    """
    out_path = Path(out_masks_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    if not animated_masks:
        return []

    generated_files = []
    step = max(1, int(frame_step))

    for t in range(total_frames):
        # Map the rendered frame back onto the timeline the keyframes were set on.
        src_t = t * step
        # 1. Default base: pure white (255)
        # Mode 'L' (8-bit grayscale)
        mask_img = Image.new('L', (width, height), color=255)
        draw = ImageDraw.Draw(mask_img)

        # Check if there are inclusion masks
        inc_masks = [m for m in animated_masks if m.category == "inclusion"]
        exc_masks = [m for m in animated_masks if m.category == "exclusion"]

        # If inclusion masks exist, base becomes black (0), and inclusion shapes are drawn white (255)
        if inc_masks:
            draw.rectangle([0, 0, width, height], fill=0)
            for m in inc_masks:
                geom = m.get_interpolated_geometry(src_t)
                if not geom or geom.get("type") != "poly":
                    continue
                pts = geom.get("points", [])
                if len(pts) >= 3:
                    draw.polygon(pts, fill=255)

        # Exclusion masks: carved out as black (0)
        for m in exc_masks:
            geom = m.get_interpolated_geometry(src_t)
            if not geom or geom.get("type") != "poly":
                continue
            pts = geom.get("points", [])
            if len(pts) >= 3:
                draw.polygon(pts, fill=0)

        # Save mask image matching COLMAP's expected naming:
        # COLMAP looks for <image_name>.png in --ImageReader.mask_path
        if frame_filenames and t < len(frame_filenames):
            base_fname = frame_filenames[t]
            mask_file = out_path / f"{base_fname}.png"
            mask_img.save(mask_file, format="PNG")
        else:
            fname = f"frame_{t+1:06d}.jpg.png"
            mask_file = out_path / fname
            mask_img.save(mask_file, format="PNG")

        generated_files.append(mask_file)

        if progress_callback and (t % 10 == 0 or t == total_frames - 1):
            progress_callback(t + 1, total_frames)

    return generated_files
