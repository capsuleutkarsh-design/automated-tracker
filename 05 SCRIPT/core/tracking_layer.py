"""
Tracking Layer Data Model and Spatial Math Utilities
"""

import copy


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
        # Copies, not references: the canvas keeps editing these lists while the
        # worker thread reads the config.
        return {
            "name": self.name,
            "mode": self.mode,
            "grid_size": self.grid_size,
            "min_confidence": self.min_confidence,
            "query_points": copy.deepcopy(self.points) if self.mode != "grid" else None,
            # query_points is what the engine tracks, so a grid layer has none.
            # The project file needs the clicks back whatever the mode is, or
            # switching a layer to grid and saving would throw them away.
            "points": copy.deepcopy(self.points),
            "animated_masks": [m.to_dict() for m in self.animated_masks],
            # Only a layer the user explicitly put in corner-pin mode gets corner-pin
            # exports. "Has exactly 4 points" is not a corner pin - a 2x2 grid has 4.
            "export_cornerpin": bool(self.export_cornerpin or self.mode == "cornerpin"),
            "color": self.color
        }

    @classmethod
    def from_config_dict(cls, d):
        """
        Rebuild a layer from to_config_dict(), for the per-shot project file.

        Missing keys fall back to the constructor's defaults, so a layer saved
        by an older build still loads. AnimatedMask is imported here rather
        than at module scope: this module is otherwise pure data and is
        imported by code that has no business pulling in PIL.
        """
        from mask_animator import AnimatedMask

        layer = cls(
            d.get("name", "Layer 1 (Wall)"),
            d.get("color", "#00d2ff"),
            d.get("mode", "grid"),
        )
        try:
            layer.grid_size = int(d.get("grid_size", layer.grid_size))
        except (TypeError, ValueError):
            pass
        try:
            layer.min_confidence = float(d.get("min_confidence", layer.min_confidence))
        except (TypeError, ValueError):
            pass
        layer.export_cornerpin = bool(d.get("export_cornerpin", False))

        # JSON has no tuples; the canvas and the engine both expect (frame, x, y).
        raw_points = d.get("points")
        if raw_points is None:
            raw_points = d.get("query_points") or []
        layer.points = [(int(p[0]), float(p[1]), float(p[2]))
                        for p in raw_points if p is not None and len(p) >= 3]

        layer.animated_masks = [AnimatedMask.from_dict(m)
                                for m in (d.get("animated_masks") or [])]
        return layer


def point_in_poly(x, y, poly):
    """
    Ray-casting point-in-polygon test.
    poly: list of [px, py] or (px, py) vertices.
    """
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


def is_point_in_mask(x, y, mask):
    """
    Checks if coordinate (x, y) is inside mask dict or bounding box list.
    Supports rectangle: {"type": "rect", "coords": [x1, y1, x2, y2]}
    Supports polygon:   {"type": "poly", "points": [(x1, y1), (x2, y2), ...]}
    Supports legacy:    [x1, y1, x2, y2]
    """
    if isinstance(mask, (list, tuple)):
        if len(mask) == 4:
            x1, y1, x2, y2 = mask
            return (min(x1, x2) <= x <= max(x1, x2)) and (min(y1, y2) <= y <= max(y1, y2))
        return False
    elif isinstance(mask, dict):
        m_type = mask.get("type", "rect")
        if m_type == "rect":
            coords = mask.get("coords", [])
            if len(coords) == 4:
                x1, y1, x2, y2 = coords
                return (min(x1, x2) <= x <= max(x1, x2)) and (min(y1, y2) <= y <= max(y1, y2))
        elif m_type == "poly":
            pts = mask.get("points", [])
            return point_in_poly(x, y, pts)
    return False
