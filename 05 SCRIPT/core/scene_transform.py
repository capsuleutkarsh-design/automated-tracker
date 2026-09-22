"""
Scene transform: scale, ground and origin for a COLMAP solve (roadmap 1.4).

COLMAP's world has an arbitrary scale, an arbitrary floor direction and an
arbitrary origin. A matchmover fixes all three with a SIMILARITY transform -
uniform scale, rotation, translation - which is the only family of transforms
that leaves every reprojection untouched. That invariant is the whole point:
the artist can set metres and level the floor and the solved camera still lines
up on the plate, pixel for pixel.

    X' = s * (Rot @ X) + t

Read in the order the artist works: SCALE first, then ROTATION, then
TRANSLATION. As a 4x4 that is [[s*Rot, t], [0, 0, 0, 1]].

Storage form (this is what lands in the per-shot project file, so it is plain
JSON types only - no numpy anywhere in the dict):

    {"scale": 1.0,
     "rotation": [[1,0,0], [0,1,0], [0,0,1]],   # 3x3, row-major
     "translation": [0.0, 0.0, 0.0]}

FRAME OF REFERENCE. Everything here works in COLMAP's own world, because that
is where `export_tools.colmap_pose_to` starts before it applies the per-DCC
basis in WORLD_BASES. COLMAP's camera y points DOWN, so in COLMAP world the sky
is on the -Y side of the floor (the same reasoning as
`detect_ground_plane_ransac`, which flips its normal to normal[1] <= 0). A
caller levelling a COLMAP floor therefore wants `up=COLMAP_UP`, i.e. (0,-1,0),
NOT the (0,1,0) default of `fit_ground`. The default is (0,1,0) because this
module is generic maths and most frames are Y-up; the pipeline caller must pass
COLMAP_UP explicitly. Getting this wrong flips the scene upside down in every
DCC, so it is spelled out again on `fit_ground` and `build`.

Pure numpy. No Qt, no file I/O - it is unit-testable on its own.
"""

import numpy as np


# COLMAP world "up". Its camera y points down, so the sky is -Y. Pass this as
# `up=` whenever the points came out of a COLMAP solve.
COLMAP_UP = (0.0, -1.0, 0.0)

# Below this, a singular value is treated as zero and the input is called
# degenerate. Relative to the largest singular value, so it is scale-free.
_DEGENERATE_RATIO = 1e-8


# =============================================================================
# REPRESENTATION
# =============================================================================
def identity():
    """The do-nothing transform, in storage form."""
    return {
        "scale": 1.0,
        "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        "translation": [0.0, 0.0, 0.0],
    }


def make(scale=1.0, rotation=None, translation=None):
    """
    Build a storage-form transform from loose parts, coercing to plain floats.

    Accepts numpy arrays for `rotation` and `translation`; the result is always
    JSON-serialisable, because it goes straight into the project file.
    """
    Rot = np.eye(3) if rotation is None else np.asarray(rotation, dtype=float).reshape(3, 3)
    t = np.zeros(3) if translation is None else np.asarray(translation, dtype=float).reshape(3)
    # + 0.0 normalises -0.0 so a saved project never reads "-0.0"
    return {
        "scale": float(scale),
        "rotation": [[float(v) + 0.0 for v in row] for row in Rot],
        "translation": [float(v) + 0.0 for v in t],
    }


def parts(T):
    """(scale, 3x3 rotation array, 3-vector translation) of a storage-form transform."""
    if T is None:
        return 1.0, np.eye(3), np.zeros(3)
    s = float(T.get("scale", 1.0))
    Rot = np.asarray(T.get("rotation") or np.eye(3), dtype=float).reshape(3, 3)
    t = np.asarray(T.get("translation") or (0.0, 0.0, 0.0), dtype=float).reshape(3)
    return s, Rot, t


def to_matrix(T):
    """
    The 4x4 homogeneous matrix of a transform, column-vector convention.

    X'_homogeneous = M @ X_homogeneous, so the 3x3 block is s*Rot and the
    translation sits in the last COLUMN (unlike USD's row-vector matrices in
    export_tools.usd_matrix_rows).
    """
    s, Rot, t = parts(T)
    M = np.eye(4)
    M[:3, :3] = s * Rot
    M[:3, 3] = t
    return M


def from_matrix(M):
    """
    Storage form from a 4x4 (or 3x4) similarity matrix.

    The scale is recovered as the mean length of the three columns of the linear
    block; the rotation is that block divided by the scale. A matrix whose
    linear block is not a scaled rotation (a shear, a non-uniform scale, a
    reflection) is rejected rather than silently rounded, because such a matrix
    would break the reprojection invariant.
    """
    M = np.asarray(M, dtype=float)
    if M.shape not in ((4, 4), (3, 4)):
        raise ValueError("expected a 4x4 or 3x4 matrix, got shape %r" % (M.shape,))
    A = M[:3, :3]
    t = M[:3, 3]
    lengths = np.linalg.norm(A, axis=0)
    s = float(lengths.mean())
    if s <= 0.0 or not np.isfinite(s):
        raise ValueError("matrix has a zero or non-finite scale")
    if float(np.abs(lengths - s).max()) > 1e-6 * s:
        raise ValueError("matrix scale is not uniform - not a similarity")
    Rot = A / s
    if float(np.abs(Rot @ Rot.T - np.eye(3)).max()) > 1e-6:
        raise ValueError("matrix linear block is not a scaled rotation")
    if float(np.linalg.det(Rot)) < 0.0:
        raise ValueError("matrix contains a reflection - not a similarity")
    return make(s, Rot, t)


def compose(*transforms):
    """
    One transform equivalent to applying the arguments LEFT TO RIGHT.

        apply(compose(A, B), X) == apply(B, apply(A, X))

    Reading order, not matrix order: compose(scale, rotate, translate) is the
    transform this module builds, and equals M_translate @ M_rotate @ M_scale.
    """
    out = identity()
    for T in transforms:
        s1, R1, t1 = parts(out)
        s2, R2, t2 = parts(T)
        # X -> s1 R1 X + t1 -> s2 R2 (s1 R1 X + t1) + t2
        out = make(s1 * s2, R2 @ R1, s2 * (R2 @ t1) + t2)
    return out


def invert(T):
    """
    The transform that undoes T.

    From X' = s Rot X + t follows X = (1/s) Rot^T X' - (1/s) Rot^T t, so the
    inverse scale is 1/s, the inverse rotation is Rot^T and the inverse
    translation is -(1/s) Rot^T t.
    """
    s, Rot, t = parts(T)
    if s == 0.0:
        raise ValueError("cannot invert a transform with zero scale")
    inv_s = 1.0 / s
    return make(inv_s, Rot.T, -inv_s * (Rot.T @ t))


def is_identity(T, tol=1e-12):
    """True when T does nothing measurable (used to skip work in the exporters)."""
    s, Rot, t = parts(T)
    return (abs(s - 1.0) <= tol
            and float(np.abs(Rot - np.eye(3)).max()) <= tol
            and float(np.abs(t).max()) <= tol)


# =============================================================================
# FITTING FROM ARTIST CONSTRAINTS
# =============================================================================
def fit_scale(p_a, p_b, real_distance):
    """
    Scale factor that makes two solved points `real_distance` apart.

    The artist picks two reprojected points on the canvas and types the real
    measurement; the solve's own distance between them becomes that number.
    Units are the caller's - the GUI converts to metres before calling.
    """
    a = np.asarray(p_a, dtype=float).reshape(3)
    b = np.asarray(p_b, dtype=float).reshape(3)
    solved = float(np.linalg.norm(b - a))
    real = float(real_distance)
    if solved <= _DEGENERATE_RATIO:
        raise ValueError("the two points are the same point - pick two different points")
    if not np.isfinite(real) or real <= 0.0:
        raise ValueError("the real distance must be a positive number")
    return real / solved


def rotation_between(a, b):
    """
    Shortest-arc rotation taking unit vector `a` onto unit vector `b`.

    Rodrigues' formula on the axis a x b. "Shortest arc" means the axis is
    perpendicular to both vectors, so the rotation adds no spin about `b`
    itself - exactly the "no roll beyond what is needed" the ground fit wants.
    The half-turn case (a == -b) has no unique shortest arc, so a deterministic
    perpendicular axis is chosen: the world axis least aligned with `a`.
    """
    a = np.asarray(a, dtype=float).reshape(3)
    b = np.asarray(b, dtype=float).reshape(3)
    a = a / (np.linalg.norm(a) or 1.0)
    b = b / (np.linalg.norm(b) or 1.0)
    v = np.cross(a, b)
    s = float(np.linalg.norm(v))
    c = float(np.dot(a, b))
    if s < 1e-12:
        if c > 0.0:
            return np.eye(3)
        # 180 degrees: any perpendicular axis is "shortest"; pick one that is
        # stable and well conditioned instead of whatever cross() returned.
        axis = np.zeros(3)
        axis[int(np.argmin(np.abs(a)))] = 1.0
        axis = np.cross(a, axis)
        axis /= np.linalg.norm(axis)
        K = np.array([[0.0, -axis[2], axis[1]],
                      [axis[2], 0.0, -axis[0]],
                      [-axis[1], axis[0], 0.0]])
        return np.eye(3) + 2.0 * (K @ K)  # Rodrigues at theta = pi
    K = np.array([[0.0, -v[2], v[1]],
                  [v[2], 0.0, -v[0]],
                  [-v[1], v[0], 0.0]])
    return np.eye(3) + K + K @ K * ((1.0 - c) / (s * s))


def fit_ground(points, up=(0.0, 1.0, 0.0), normal_hint=None):
    """
    Rotation that levels the best-fit plane through `points`, plus a reason.

    Returns (rotation, reason): a 3x3 array and None when the fit worked, or
    (identity, "plain English reason") when the points cannot define a plane.
    The caller shows the reason to the artist rather than silently doing
    nothing - the rule in the roadmap is that the artist never has to guess.

    METHOD. The plane is the total-least-squares fit: subtract the centroid and
    take the singular vector of the smallest singular value as the normal. The
    rotation is then the SHORTEST-ARC rotation taking that normal onto `up`, so
    det is +1 and no roll is introduced about the up axis - the scene keeps the
    heading the artist is already looking at, and only the tilt changes.

    SIGN. A fitted plane normal has an arbitrary sign; it is flipped to have a
    non-negative dot product with `up` (or with `normal_hint` when given), i.e.
    the input is assumed to be roughly level already, which it is for a plate
    shot by a human. Pass `normal_hint` when it is not, for instance the normal
    that `detect_ground_plane_ransac` already agreed on.

    UP. For points straight out of a COLMAP solve pass `up=COLMAP_UP`
    (0, -1, 0): COLMAP's camera y points down, so the sky is -Y and the
    per-DCC bases in export_tools.WORLD_BASES map COLMAP -Y onto the DCC's up.
    The (0, 1, 0) default is for callers already working in a Y-up frame.

    DEGENERATE INPUT. Fewer than three points, points that are all the same, and
    points along a single line have no unique plane; each returns the identity
    with a reason instead of an arbitrary rotation.
    """
    P = np.asarray(points, dtype=float).reshape(-1, 3)
    if P.shape[0] < 3:
        return np.eye(3), "need at least three points to fit a ground plane, got %d" % P.shape[0]

    centred = P - P.mean(axis=0)
    # Singular values of the centred cloud: how far it spreads along each of its
    # own axes. Two large and one small means a plane; one large and two small
    # means a line; all small means one point repeated.
    try:
        _u, sv, vt = np.linalg.svd(centred, full_matrices=False)
    except np.linalg.LinAlgError:
        return np.eye(3), "the ground fit did not converge on these points"
    if not np.all(np.isfinite(sv)) or sv[0] <= _DEGENERATE_RATIO:
        return np.eye(3), "the selected points are all in the same place"
    if sv[1] <= _DEGENERATE_RATIO * sv[0]:
        return np.eye(3), "the selected points lie on a straight line, which does not define a plane"
    if sv[2] > 0.999 * sv[1]:
        # The cloud is a ball, not a slab: the "smallest" direction is noise.
        return np.eye(3), "the selected points do not lie near a plane"

    normal = vt[2]
    normal = normal / (np.linalg.norm(normal) or 1.0)
    reference = np.asarray(normal_hint if normal_hint is not None else up, dtype=float).reshape(3)
    if float(np.dot(normal, reference)) < 0.0:
        normal = -normal
    return rotation_between(normal, np.asarray(up, dtype=float).reshape(3)), None


def fit_origin(point):
    """
    Translation that moves `point` to the world origin.

    Because translation is applied LAST, `point` must already carry the scale
    and rotation of the transform being built - otherwise the origin lands
    somewhere else entirely. `build` does that for you; a direct caller should
    pass `apply_to_points(scale_and_rotation, p)`.
    """
    p = np.asarray(point, dtype=float).reshape(3)
    return -p


def build(points_for_ground=None, scale_pair=None, real_distance=None,
          origin_point=None, up=(0.0, 1.0, 0.0), notes=None):
    """
    The single transform for everything the artist set, in storage form.

    Order of application, which is also the order the panel presents it:

        1. uniform SCALE      from `scale_pair` + `real_distance`
        2. ROTATION           from `points_for_ground` (levels the floor)
        3. TRANSLATION        from `origin_point` (moves it to 0, 0, 0)

        X' = s * (Rot @ X) + t

    Every constraint is optional; leaving one out leaves that part at identity,
    so the artist can set scale today and the floor tomorrow.

    `origin_point` is given in the ORIGINAL solve coordinates - the same space
    as the picked 3D point - and is carried through the scale and rotation here,
    so the result really does land on the origin.

    `up` is the direction the floor normal should end up along; pass COLMAP_UP
    for points from a COLMAP solve (see the module docstring).

    `notes` is an optional list; any plain-English reason a constraint could not
    be met is appended to it for the log, and that part stays at identity.
    """
    if notes is None:
        notes = []

    scale = 1.0
    if scale_pair is not None and real_distance is not None:
        p_a, p_b = scale_pair
        scale = fit_scale(p_a, p_b, real_distance)

    Rot = np.eye(3)
    if points_for_ground is not None and len(points_for_ground):
        Rot, reason = fit_ground(points_for_ground, up=up)
        if reason:
            notes.append("Ground not levelled: %s." % reason)

    t = np.zeros(3)
    if origin_point is not None:
        p = np.asarray(origin_point, dtype=float).reshape(3)
        t = fit_origin(scale * (Rot @ p))

    return make(scale, Rot, t)


# =============================================================================
# APPLYING
# =============================================================================
def apply_to_points(T, points):
    """
    (N, 3) points through the transform: X' = s * (Rot @ X) + t.

    Accepts a single (3,) point as well and gives back the same shape, so the
    GUI can transform one picked point without reshaping.
    """
    s, Rot, t = parts(T)
    P = np.asarray(points, dtype=float)
    single = (P.ndim == 1)
    P = P.reshape(-1, 3)
    out = s * (P @ Rot.T) + t
    return out.reshape(3) if single else out


def apply_to_camera(T, R_world_to_cam, camera_center):
    """
    A camera through the transform: returns (R_world_to_cam', camera_center').

    The camera centre is a point, so it scales, rotates and translates like any
    other. The ORIENTATION only rotates - a uniform scale does not turn a camera
    - and because the stored matrix maps world INTO the camera, the rotation
    composes on the right and transposed:

        C'    = s * (Rot @ C) + t
        R_wc' = R_wc @ Rot^T

    WHY REPROJECTION IS UNCHANGED. A point in camera space is x = R_wc (X - C).
    After the transform:

        R_wc' (X' - C') = R_wc Rot^T (s Rot X + t - s Rot C - t)
                        = R_wc Rot^T s Rot (X - C)
                        = s * R_wc (X - C)
                        = s * x

    The camera-space point is scaled, so the DEPTH changes by s, but the
    projection divides x and y by z and the s cancels: the pixel is identical.
    That is the invariant the tests pin down, and it is why only a similarity is
    allowed here.

    COLMAP's parsed images store the camera-TO-world rotation in img["R_world"],
    which is R_wc^T. For those the identity above simplifies to a plain left
    multiply:  R_world' = Rot @ R_world.
    """
    s, Rot, t = parts(T)
    R_wc = np.asarray(R_world_to_cam, dtype=float).reshape(3, 3)
    C = np.asarray(camera_center, dtype=float).reshape(3)
    return R_wc @ Rot.T, s * (Rot @ C) + t


def apply_to_colmap_image(T, img):
    """
    (center, R_world) of a parsed COLMAP image after the transform.

    A convenience for export_tools, whose `img` dicts hold "center" and
    "R_world" (camera-to-world). Returns plain lists in the same layout so the
    caller can drop them straight back into a copy of the dict.
    """
    s, Rot, t = parts(T)
    C = np.asarray(img["center"], dtype=float).reshape(3)
    R_world = np.asarray(img["R_world"], dtype=float).reshape(3, 3)
    return (s * (Rot @ C) + t).tolist(), (Rot @ R_world).tolist()
