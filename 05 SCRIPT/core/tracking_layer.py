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
        # Hand corrections to the solved result (roadmap 2.1): one per
        # (tracked point, frame) as {"point", "frame", "x", "y"}, where frame
        # is the clip's own frame index - the same numbering self.points uses -
        # and x, y are plate pixels. They are the artist's decisions about a
        # drifting track, so they live in the project file and come back with it.
        self.corrections = []

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

    def set_correction(self, point_index, frame, x, y):
        """
        Record where the artist put a tracked point on a frame.

        One correction per (point, frame): dragging the same marker twice on
        the same frame is one decision revised, not two. Returns the stored dict.
        """
        point_index, frame = int(point_index), int(frame)
        entry = {"point": point_index, "frame": frame, "x": float(x), "y": float(y)}
        for i, c in enumerate(self.corrections):
            if int(c.get("point", -1)) == point_index and int(c.get("frame", -1)) == frame:
                self.corrections[i] = entry
                return entry
        self.corrections.append(entry)
        return entry

    def clear_correction(self, point_index, frame):
        """Forget the correction on one point and frame. True if there was one."""
        point_index, frame = int(point_index), int(frame)
        before = len(self.corrections)
        self.corrections = [c for c in self.corrections
                            if not (int(c.get("point", -1)) == point_index
                                    and int(c.get("frame", -1)) == frame)]
        return len(self.corrections) != before

    def corrected_frames(self, point_index=None):
        """
        Sorted frames this layer carries corrections on - all points, or one.

        The canvas draws these differently and the timeline marks them, so the
        artist can see at a glance which frames they have already fixed.
        """
        return sorted({int(c.get("frame", -1)) for c in self.corrections
                       if point_index is None or int(c.get("point", -1)) == int(point_index)}
                      - {-1})

    def correction_at(self, frame, point_index=None):
        """The correction on this frame (optionally for one point), or None."""
        for c in self.corrections:
            if int(c.get("frame", -1)) != int(frame):
                continue
            if point_index is None or int(c.get("point", -1)) == int(point_index):
                return c
        return None

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
            # Hand corrections to the solved track are the artist's work too,
            # so they survive a clip switch and a restart like the roto does.
            "corrections": copy.deepcopy(self.corrections),
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

        # A correction that lost a field is dropped rather than half-restored:
        # a marker put back on the wrong frame is worse than one the artist has
        # to place again.
        layer.corrections = []
        for c in (d.get("corrections") or []):
            try:
                layer.corrections.append({
                    "point": int(c["point"]),
                    "frame": int(c["frame"]),
                    "x": float(c["x"]),
                    "y": float(c["y"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
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
