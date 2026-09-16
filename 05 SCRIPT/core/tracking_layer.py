"""
Tracking Layer Data Model and Spatial Math Utilities
"""

import numpy as np


class TrackingLayer:
    def __init__(self, name="Layer 1 (Wall)", color="#00d2ff", mode="grid"):
        self.name = name
        self.color = color
        self.mode = mode  # "grid", "points", "cornerpin"
        self.grid_size = 10
        self.min_confidence = 0.70
        self.export_cornerpin = False
        self.points = []  # list of (frame_idx, orig_x, orig_y)
        self.animated_masks = []  # list of AnimatedMask instances
        self.visible = True
        self.locked = False

    @property
    def inclusion_masks(self):
        return [m for m in self.animated_masks if m.category == "inclusion"]

    @property
    def exclusion_masks(self):
        return [m for m in self.animated_masks if m.category == "exclusion"]

    def get_masks_at_frame(self, frame_idx):
        res = []
        for m in self.animated_masks:
            geom = m.get_interpolated_geometry(frame_idx)
            if geom:
                res.append({
                    "id": m.id,
                    "name": m.name,
                    "category": m.category,
                    "type": geom["type"],
                    "coords": geom.get("coords"),
                    "points": geom.get("points"),
                    "color": m.color,
                    "is_keyframe": m.has_keyframe(frame_idx),
                    "mask_obj": m
                })
        return res

    def get_all_keyframe_frames(self):
        s = set()
        for m in self.animated_masks:
            s.update(m.get_keyframe_frames())
        return sorted(s)

    def to_config_dict(self):
        return {
            "name": self.name,
            "mode": self.mode,
            "grid_size": self.grid_size,
            "min_confidence": self.min_confidence,
            "query_points": self.points if self.mode != "grid" else None,
            "animated_masks": [m.to_dict() for m in self.animated_masks],
            "export_cornerpin": self.export_cornerpin or (self.mode == "cornerpin") or (len(self.points) == 4),
            "color": self.color
        }


def point_in_poly_canvas(x, y, poly):
    """Ray-casting algorithm for point-in-polygon hit detection on canvas."""
    n = len(poly)
    if n < 3:
        return False
    inside = False
    p1x, p1y = poly[0]
    for i in range(n + 1):
        p2x, p2y = poly[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def is_pt_in_mask_canvas(x, y, mask):
    """Checks if point (x, y) is inside mask dictionary (poly or legacy rect)."""
    if isinstance(mask, (list, tuple)) and len(mask) == 4:
        x1, y1, x2, y2 = mask
        return (min(x1, x2) <= x <= max(x1, x2)) and (min(y1, y2) <= y <= max(y1, y2))
    elif isinstance(mask, dict):
        m_type = mask.get("type", "poly")
        if m_type == "rect":
            coords = mask.get("coords", [])
            if len(coords) == 4:
                x1, y1, x2, y2 = coords
                return (min(x1, x2) <= x <= max(x1, x2)) and (min(y1, y2) <= y <= max(y1, y2))
        elif m_type == "poly":
            pts = mask.get("points", [])
            return point_in_poly_canvas(x, y, pts)
    return False
