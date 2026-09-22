"""
Lens distortion and STMap generation for comp handoff (roadmap 1.5).

A solve knows k1 and k2, but comp cannot use a number in a text file: it needs
an undistorted plate to work against and a pair of STMaps to get there and back.
This module is the maths for that - the COLMAP distortion models, the two maps,
and the pinhole camera that matches the undistorted plate.

Pure numpy plus an optional EXR writer. No Qt, no COLMAP process calls.

-----------------------------------------------------------------------------
COORDINATE CONVENTIONS - read this before changing anything
-----------------------------------------------------------------------------
NORMALISED COORDINATES. Every `*_points` function here works in COLMAP's
normalised camera plane: u = (x_px - cx) / fx, v = (y_px - cy) / fy, with y
pointing DOWN like the rest of COLMAP. A "distorted" pair is what the real lens
put on the sensor; an "undistorted" pair is what an ideal pinhole would have
put there. COLMAP's own convention is that a model's Distortion() maps
undistorted -> distorted, which is why `distort_points` is closed form and
`undistort_points` has to iterate.

PIXEL CENTRES. COLMAP puts the centre of the top-left pixel at (0.5, 0.5), so
column j of a raster is sampled at x = j + 0.5 and row i at y = i + 0.5. The
principal point cx, cy is in that same convention, and so are the grids below.

STMAP LAYOUT. The returned arrays are float32, shape (H, W, 2), row 0 = the TOP
of the frame - the order an EXR scanline file stores, and the order Nuke reads
back. Channel 0 is u, channel 1 is v, both normalised 0..1 across the SOURCE
image that the map points into.

THE V FLIP, EXACTLY. Nuke's image origin is bottom-left; COLMAP's y counts down
from the top. An STMap value names a place in the source image, so the flip is
applied to the SOURCE coordinate, not to the output raster:

    u = x_src_colmap / W_src
    v = 1.0 - y_src_colmap / H_src

Sanity check with an identity map: the top output row (numpy row 0, COLMAP
y = 0.5) gets v = 1 - 0.5/H, which is just under 1 - the top of the frame in
Nuke's v-up space. Correct. The output raster itself is NOT flipped; only the
value written into it is.

Values may fall outside 0..1 where the output frame looks beyond the edge of the
source plate (which is exactly what overscan does). They are left as they are so
Nuke's STMap node can decide - clamping here would smear the border.
"""

import math
import struct
import zlib
from pathlib import Path

import numpy as np


# Models this module knows how to distort and undistort, with where their
# distortion coefficients start in COLMAP's PARAMS[] and how many there are.
# Order is COLMAP's own (src/colmap/sensor/models.h):
#   SIMPLE_RADIAL          f, cx, cy, k
#   RADIAL                 f, cx, cy, k1, k2
#   OPENCV                 fx, fy, cx, cy, k1, k2, p1, p2
#   FULL_OPENCV            fx, fy, cx, cy, k1, k2, p1, p2, k3, k4, k5, k6
#   OPENCV_FISHEYE         fx, fy, cx, cy, k1, k2, k3, k4
#   SIMPLE_RADIAL_FISHEYE  f, cx, cy, k
#   RADIAL_FISHEYE         f, cx, cy, k1, k2
#   FOV                    fx, fy, cx, cy, omega
_EXTRA_PARAMS = {
    "SIMPLE_PINHOLE":        (3, 0),
    "PINHOLE":               (4, 0),
    "SIMPLE_RADIAL":         (3, 1),
    "RADIAL":                (3, 2),
    "OPENCV":                (4, 4),
    "FULL_OPENCV":           (4, 8),
    "OPENCV_FISHEYE":        (4, 4),
    "SIMPLE_RADIAL_FISHEYE": (3, 1),
    "RADIAL_FISHEYE":        (3, 2),
    "FOV":                   (4, 1),
}

# No distortion at all: the maps are the identity and the iteration is skipped.
PINHOLE_MODELS = ("SIMPLE_PINHOLE", "PINHOLE")

# These fold the equidistant fisheye projection into their distortion, so their
# inverse is a one-dimensional solve for the ray angle rather than a 2D Newton.
FISHEYE_MODELS = ("SIMPLE_RADIAL_FISHEYE", "RADIAL_FISHEYE", "OPENCV_FISHEYE")

# A ray at 90 degrees from the axis has no pinhole image at all - tan(pi/2) is
# infinite - so the fisheye inverse stops just short of it and says so. This is
# a property of the lens, not of the solver: a 180 degree fisheye simply cannot
# be undistorted to a flat plate near its rim.
_FISHEYE_THETA_MAX = math.pi / 2.0 - 1e-4

SUPPORTED_MODELS = tuple(_EXTRA_PARAMS)

# Newton stops when the worst residual falls below this, measured in NORMALISED
# units. A 2000 px focal length turns 1e-10 here into 2e-7 px on the sensor,
# four orders of magnitude finer than the 0.05 px the handoff is checked to.
UNDISTORT_TOL = 1e-10
UNDISTORT_MAX_ITER = 20

# COLMAP's own guard radius in the FOV model, kept identical so our maps match
# what COLMAP's undistorter produces near the optical axis.
_FOV_EPS = 1e-4


# =============================================================================
# CAMERA PARAMETERS
# =============================================================================
def _intrinsics(cam):
    """(fx, fy, cx, cy, width, height) of a parsed COLMAP camera, as floats."""
    width = float(cam["width"])
    height = float(cam["height"])
    fx = float(cam.get("focal_x", cam.get("fx", 0.0)))
    fy = float(cam.get("focal_y", cam.get("fy", fx)) or fx)
    cx = float(cam.get("cx", width / 2.0))
    cy = float(cam.get("cy", height / 2.0))
    return fx, fy, cx, cy, width, height


def _model_of(cam):
    """The camera model name, rejecting anything this module cannot do honestly."""
    model = str(cam.get("model", "PINHOLE")).upper()
    if model not in _EXTRA_PARAMS:
        raise ValueError(
            "camera model %r is not supported for distortion; supported models are: %s"
            % (model, ", ".join(SUPPORTED_MODELS))
        )
    return model


def _extra_params(cam):
    """
    The distortion coefficients of a parsed camera, padded to the model's length.

    Reads cam["params"] - the raw PARAMS[] line that parse_colmap_cameras keeps -
    and falls back to the k1/k2 keys when a caller (the GUI, a test) built the
    dict by hand without a params list. Missing trailing coefficients are zero,
    which is what a shorter cameras.txt line means anyway.
    """
    model = _model_of(cam)
    offset, count = _EXTRA_PARAMS[model]
    if count == 0:
        return np.zeros(0)

    params = cam.get("params")
    extra = np.zeros(count)
    if params is not None and len(params) > offset:
        vals = np.asarray(params[offset:offset + count], dtype=float)
        extra[:vals.shape[0]] = vals
    else:
        # Hand-built dict: k1 (and k2 where the model has one) is all we can know.
        extra[0] = float(cam.get("k1", 0.0))
        if count > 1:
            extra[1] = float(cam.get("k2", 0.0))
    return extra


def has_distortion(cam):
    """True when the model carries distortion and at least one coefficient is non-zero."""
    if _model_of(cam) in PINHOLE_MODELS:
        return False
    return bool(np.any(_extra_params(cam) != 0.0))


# =============================================================================
# DISTORTION MODELS
# =============================================================================
def _delta(model, extra, u, v):
    """
    COLMAP's Distortion(): the offset added to an undistorted normalised point.

    Returns (du, dv) such that the distorted point is (u + du, v + dv). Straight
    from src/colmap/sensor/models.h so a reader can diff it against upstream.
    """
    if model in PINHOLE_MODELS:
        z = np.zeros_like(u)
        return z, z

    u2 = u * u
    v2 = v * v
    r2 = u2 + v2

    if model == "SIMPLE_RADIAL":
        radial = extra[0] * r2
        return u * radial, v * radial

    if model == "RADIAL":
        radial = extra[0] * r2 + extra[1] * r2 * r2
        return u * radial, v * radial

    if model == "OPENCV":
        k1, k2, p1, p2 = extra
        uv = u * v
        radial = k1 * r2 + k2 * r2 * r2
        du = u * radial + 2.0 * p1 * uv + p2 * (r2 + 2.0 * u2)
        dv = v * radial + 2.0 * p2 * uv + p1 * (r2 + 2.0 * v2)
        return du, dv

    if model == "FULL_OPENCV":
        k1, k2, p1, p2, k3, k4, k5, k6 = extra
        uv = u * v
        r4 = r2 * r2
        r6 = r4 * r2
        # The rational radial term: a numerator and a denominator polynomial,
        # minus one because COLMAP's Distortion returns an OFFSET, not a factor.
        radial = (1.0 + k1 * r2 + k2 * r4 + k3 * r6) / (1.0 + k4 * r2 + k5 * r4 + k6 * r6) - 1.0
        du = u * radial + 2.0 * p1 * uv + p2 * (r2 + 2.0 * u2)
        dv = v * radial + 2.0 * p2 * uv + p1 * (r2 + 2.0 * v2)
        return du, dv

    if model == "FOV":
        # Division model: the image radius is atan(2 r tan(w/2)) / w. Both the
        # tiny-omega and tiny-radius limits are Taylor expansions, the same two
        # COLMAP uses, because the plain formula is 0/0 at either.
        omega = float(extra[0])
        omega2 = omega * omega
        if omega2 < _FOV_EPS:
            factor = (omega2 * r2) / 3.0 - omega2 / 12.0 + 1.0
            factor = np.broadcast_to(np.asarray(factor, dtype=float), r2.shape)
        else:
            tan_half = math.tan(omega / 2.0)
            near = r2 < _FOV_EPS
            safe_r = np.sqrt(np.where(near, 1.0, r2))
            far_factor = np.arctan(safe_r * 2.0 * tan_half) / (safe_r * omega)
            near_factor = (-2.0 * tan_half * (4.0 * r2 * tan_half * tan_half - 3.0)) / (3.0 * omega)
            factor = np.where(near, near_factor, far_factor)
        return u * factor - u, v * factor - v

    # The three fisheye models below fold the equidistant projection INTO the
    # distortion: the ray angle theta = atan(r) replaces the radius, a radial
    # polynomial bends it, and the point is put back on its original bearing.
    r = np.sqrt(r2)
    small = r < 1e-12
    safe_r = np.where(small, 1.0, r)
    theta = np.arctan(safe_r)
    t2 = theta * theta

    if model == "SIMPLE_RADIAL_FISHEYE":
        theta_d = theta * (1.0 + extra[0] * t2)
    elif model == "RADIAL_FISHEYE":
        t4 = t2 * t2
        theta_d = theta * (1.0 + extra[0] * t2 + extra[1] * t4)
    elif model == "OPENCV_FISHEYE":
        t4 = t2 * t2
        t6 = t4 * t2
        t8 = t4 * t4
        theta_d = theta * (1.0 + extra[0] * t2 + extra[1] * t4 + extra[2] * t6 + extra[3] * t8)
    else:  # unreachable: _model_of already rejected anything else
        raise ValueError("unhandled model %r" % (model,))

    scale = theta_d / safe_r
    du = np.where(small, 0.0, u * scale - u)
    dv = np.where(small, 0.0, v * scale - v)
    return du, dv


def distort_points(cam, xy_normalized):
    """
    Undistorted normalised points -> distorted normalised points (closed form).

    `xy_normalized` is any array whose last axis is 2; the shape comes back
    unchanged, so a single (2,) point works as well as an (H, W, 2) grid.
    Multiply the result by (fx, fy) and add (cx, cy) to land on sensor pixels.
    """
    model = _model_of(cam)
    extra = _extra_params(cam)
    xy = np.asarray(xy_normalized, dtype=float)
    if xy.shape[-1] != 2:
        raise ValueError("expected points with a trailing axis of 2, got shape %r" % (xy.shape,))
    if model in PINHOLE_MODELS:
        return xy.copy()
    u = xy[..., 0]
    v = xy[..., 1]
    du, dv = _delta(model, extra, u, v)
    return np.stack([u + du, v + dv], axis=-1)


def _fisheye_theta_d(model, extra, theta):
    """The bent ray angle: theta times the model's radial polynomial in theta^2."""
    t2 = theta * theta
    if model == "SIMPLE_RADIAL_FISHEYE":
        return theta * (1.0 + extra[0] * t2)
    if model == "RADIAL_FISHEYE":
        return theta * (1.0 + extra[0] * t2 + extra[1] * t2 * t2)
    t4 = t2 * t2
    return theta * (1.0 + extra[0] * t2 + extra[1] * t4 + extra[2] * t4 * t2 + extra[3] * t4 * t4)


def _fisheye_dtheta_d(model, extra, theta):
    """d(theta_d)/d(theta), analytic - the polynomial's derivative, term by term."""
    t2 = theta * theta
    if model == "SIMPLE_RADIAL_FISHEYE":
        return 1.0 + 3.0 * extra[0] * t2
    if model == "RADIAL_FISHEYE":
        return 1.0 + 3.0 * extra[0] * t2 + 5.0 * extra[1] * t2 * t2
    t4 = t2 * t2
    return (1.0 + 3.0 * extra[0] * t2 + 5.0 * extra[1] * t4
            + 7.0 * extra[2] * t4 * t2 + 9.0 * extra[3] * t4 * t4)


def _undistort_fisheye(model, extra, xy, max_iter, tol):
    """
    Fisheye inverse, solved in one dimension on the ray angle. Returns (xy, ok).

    A fisheye point's distorted radius IS the bent ray angle, so the inverse is:
    solve theta_d(theta) = r_d for theta (Newton, analytic derivative, monotone
    over any sane lens), then the pinhole radius is tan(theta) and the point goes
    back on its original bearing. Doing this in 1D rather than as a 2D Newton
    matters: near 90 degrees the pinhole radius runs away to infinity and a 2D
    solver wanders off instead of reporting that there is no answer.

    `ok` is False where theta hit the 90 degree wall or the solve did not
    converge, and those points come back clamped to the wall so a whole STMap is
    never poisoned by its own rim.
    """
    u = xy[:, 0]
    v = xy[:, 1]
    r_d = np.hypot(u, v)
    theta = np.clip(r_d.copy(), 0.0, _FISHEYE_THETA_MAX)

    for _ in range(int(max_iter)):
        residual = _fisheye_theta_d(model, extra, theta) - r_d
        if float(np.abs(residual).max(initial=0.0)) < tol:
            break
        deriv = _fisheye_dtheta_d(model, extra, theta)
        deriv = np.where(np.abs(deriv) < 1e-12, 1.0, deriv)
        theta = np.clip(theta - residual / deriv, 0.0, _FISHEYE_THETA_MAX)

    residual = _fisheye_theta_d(model, extra, theta) - r_d
    ok = (np.abs(residual) < max(tol * 1e3, 1e-7)) & (theta < _FISHEYE_THETA_MAX - 1e-9)

    r_u = np.tan(theta)
    # r_d == 0 is the optical axis, which maps to itself; scale would be 0/0.
    scale = np.where(r_d > 1e-12, r_u / np.where(r_d > 1e-12, r_d, 1.0), 1.0)
    return np.stack([u * scale, v * scale], axis=-1), ok


def undistort_points(cam, xy_normalized, max_iter=UNDISTORT_MAX_ITER, tol=UNDISTORT_TOL,
                     return_converged=False):
    """
    Distorted normalised points -> undistorted normalised points (iterative).

    None of COLMAP's distortion models has a closed-form inverse, so this solves
    distort(x) = x_d for x with Newton's method: the 2x2 Jacobian is taken by
    central differences (the models are cheap, and a hand-derived Jacobian for
    FULL_OPENCV would be a bug farm), and the step is the exact 2x2 solve.

    It stops when the worst residual is below `tol` = 1e-10 in normalised units
    - about 2e-7 px on a 2000 px lens - or after `max_iter` = 20 passes.
    Twenty is generous: a realistic k1 converges in three or four.

    Two guards keep a pathological corner from poisoning a whole STMap: a step
    through a near-singular Jacobian, or one that produces a non-finite or
    absurd value, falls back to the plain fixed-point step x <- x_d - delta(x),
    which cannot blow up even where Newton would.

    The fisheye models take a different route - see `_undistort_fisheye` - since
    a ray at 90 degrees from the axis has NO pinhole image and a 2D solver would
    happily return nonsense rather than admit it.

    With `return_converged=True` the result is (points, mask) where the mask is
    False for any point still above `tol`, or, for a fisheye, beyond the 90
    degree wall. Worth checking before delivering the maps of a very wide lens.
    """
    model = _model_of(cam)
    xy = np.asarray(xy_normalized, dtype=float)
    if xy.shape[-1] != 2:
        raise ValueError("expected points with a trailing axis of 2, got shape %r" % (xy.shape,))
    if model in PINHOLE_MODELS:
        out = xy.copy()
        return (out, np.ones(out.shape[:-1], dtype=bool)) if return_converged else out

    extra = _extra_params(cam)
    target = xy.reshape(-1, 2)

    if model in FISHEYE_MODELS:
        solved, ok = _undistort_fisheye(model, extra, target, max_iter, tol)
        out = solved.reshape(xy.shape)
        return (out, ok.reshape(xy.shape[:-1])) if return_converged else out

    x = target.copy()          # the distorted point is the obvious first guess
    h = 1e-6                   # central-difference step, normalised units

    def forward(p):
        du, dv = _delta(model, extra, p[:, 0], p[:, 1])
        return np.stack([p[:, 0] + du, p[:, 1] + dv], axis=-1)

    residual = forward(x) - target
    for _ in range(int(max_iter)):
        if float(np.abs(residual).max(initial=0.0)) < tol:
            break

        # Central differences give the columns of J = d(distort)/d(x).
        dx = np.zeros_like(x)
        dx[:, 0] = h
        dy = np.zeros_like(x)
        dy[:, 1] = h
        j0 = (forward(x + dx) - forward(x - dx)) / (2.0 * h)   # d/du
        j1 = (forward(x + dy) - forward(x - dy)) / (2.0 * h)   # d/dv
        a, c = j0[:, 0], j0[:, 1]
        b, d = j1[:, 0], j1[:, 1]
        det = a * d - b * c

        safe = np.abs(det) > 1e-12
        safe_det = np.where(safe, det, 1.0)
        # Newton step: -J^-1 @ residual, written out for a 2x2.
        step_u = -(d * residual[:, 0] - b * residual[:, 1]) / safe_det
        step_v = -(-c * residual[:, 0] + a * residual[:, 1]) / safe_det
        step = np.stack([step_u, step_v], axis=-1)
        # Where the Jacobian is degenerate, take the fixed-point step instead.
        step = np.where(safe[:, None], step, -residual)

        candidate = x + step
        bad = ~np.isfinite(candidate).all(axis=1) | (np.abs(candidate).max(axis=1) > 1e6)
        if bad.any():
            candidate[bad] = x[bad] - residual[bad]
        x = candidate
        residual = forward(x) - target

    out = x.reshape(xy.shape)
    if return_converged:
        ok = (np.abs(residual).max(axis=1) < max(tol * 1e3, 1e-7)).reshape(xy.shape[:-1])
        return out, ok
    return out


# =============================================================================
# THE UNDISTORTED PINHOLE CAMERA
# =============================================================================
def pinhole_of(cam, overscan=0.0):
    """
    The pinhole camera that matches the undistorted plate, for a given overscan.

    CONVENTION. The focal lengths are kept exactly as solved, so one pixel at the
    optical axis stays one pixel and the field of view only grows outwards; the
    raster is scaled by (1 + overscan) in both axes; and the principal point
    moves by half of the pixels added on each axis, which keeps the optical
    centre on the same physical spot in the plate. Overscan exists because
    undistorting a barrel lens pushes the corners of the image outside the
    original frame, and without the extra canvas comp loses them.

        width'  = round(width  * (1 + overscan))
        height' = round(height * (1 + overscan))
        fx', fy' = fx, fy
        cx' = cx + (width'  - width)  / 2
        cy' = cy + (height' - height) / 2

    This is OUR convention, and it is the one the STMaps here are built on. It
    is not bit-for-bit what `colmap image_undistorter` picks: COLMAP sizes the
    output from the undistorted region of interest and can also rescale the
    focal length. When the plate really does come out of `image_undistorter`,
    export the camera COLMAP wrote in its own sparse/cameras.txt rather than
    this; use this one when the undistorted plate is produced from these maps.

    HOW MUCH OVERSCAN. Undistorting a barrel lens spreads the frame outwards, so
    the corners of the plate land outside an un-overscanned undistorted frame and
    are simply lost. The 5 and 10 % the panel offers cover an ordinary lens; a
    strong wide-angle barrel (k1 around -0.3) needs nearer 40 % before its corners
    fit. And a fisheye near 180 degrees cannot be undistorted to a flat plate at
    any overscan - its rim is a ray at 90 degrees, whose pinhole image is at
    infinity - so those need a cropped pinhole with a longer focal length instead.

    The returned dict carries both the fx/fy names asked for here and the
    focal_x/focal_y/model/params names that export_tools' parsed cameras use, so
    it drops straight into camera_intrinsics_mm and nuke_win_translate.
    """
    fx, fy, cx, cy, width, height = _intrinsics(cam)
    overscan = float(overscan)
    if overscan < 0.0:
        raise ValueError("overscan must be zero or positive, got %r" % (overscan,))

    out_w = int(round(width * (1.0 + overscan)))
    out_h = int(round(height * (1.0 + overscan)))
    cx_out = cx + (out_w - width) / 2.0
    cy_out = cy + (out_h - height) / 2.0

    return {
        "model": "PINHOLE",
        "width": out_w,
        "height": out_h,
        "fx": fx,
        "fy": fy,
        "cx": cx_out,
        "cy": cy_out,
        # export_tools' spelling of the same numbers
        "focal_x": fx,
        "focal_y": fy,
        "k1": 0.0,
        "k2": 0.0,
        "fisheye": False,
        "params": [fx, fy, cx_out, cy_out],
        "overscan": overscan,
        "source_model": str(cam.get("model", "PINHOLE")).upper(),
    }


def _raster(intr, out_size):
    """
    (fx, fy, cx, cy, W, H) for a raster of `out_size`, rescaled from `intr`.

    Rendering an STMap at a different resolution than the camera it describes
    (a half-res proxy, say) must keep the same field of view, so every
    intrinsic scales with the raster - a plain resize, not a crop.
    """
    fx, fy, cx, cy, width, height = intr
    if out_size is None:
        return fx, fy, cx, cy, int(round(width)), int(round(height))
    out_w, out_h = int(out_size[0]), int(out_size[1])
    if out_w <= 0 or out_h <= 0:
        raise ValueError("out_size must be positive, got %r" % (out_size,))
    sx = out_w / width
    sy = out_h / height
    return fx * sx, fy * sy, cx * sx, cy * sy, out_w, out_h


def _pixel_grid(width, height):
    """(x, y) pixel-centre grids for a raster, COLMAP convention (centre at +0.5)."""
    xs = np.arange(width, dtype=float) + 0.5
    ys = np.arange(height, dtype=float) + 0.5
    return np.meshgrid(xs, ys)   # both (height, width), row 0 = top


def _to_stmap(x_src, y_src, src_w, src_h):
    """
    Source pixel coordinates -> an STMap tile, applying the Nuke v flip.

    u counts right from the left edge; v counts UP from the bottom, which is
    where the 1.0 - y/H comes from (see the module docstring).
    """
    u = x_src / float(src_w)
    v = 1.0 - y_src / float(src_h)
    return np.stack([u, v], axis=-1).astype(np.float32)


def undistort_stmap(cam, out_size=None, overscan=0.0):
    """
    The map that turns the ORIGINAL plate into an UNDISTORTED plate.

    Wire it into Nuke as: Read(original plate) -> STMap(stmap = this map). The
    output raster is the undistorted pinhole camera from `pinhole_of(cam,
    overscan)` - override the resolution with `out_size` = (width, height) and
    the field of view is preserved. Every value points into the ORIGINAL plate,
    normalised by the original plate's width and height.

    No iteration is needed here: for each output pixel we know the undistorted
    point and only have to ask the lens where it landed, which is the forward
    `distort_points`.

    Returns float32, shape (H, W, 2), row 0 = top of frame.
    """
    fx, fy, cx, cy, src_w, src_h = _intrinsics(cam)
    pin = pinhole_of(cam, overscan)
    px, py, pcx, pcy, out_w, out_h = _raster(
        (pin["fx"], pin["fy"], pin["cx"], pin["cy"], pin["width"], pin["height"]), out_size)

    gx, gy = _pixel_grid(out_w, out_h)
    # Output pixel -> ideal pinhole ray, as a normalised point.
    xy = np.stack([(gx - pcx) / px, (gy - pcy) / py], axis=-1)
    distorted = distort_points(cam, xy)
    # ...and where that ray actually hit the real sensor.
    x_src = fx * distorted[..., 0] + cx
    y_src = fy * distorted[..., 1] + cy
    return _to_stmap(x_src, y_src, src_w, src_h)


def redistort_stmap(cam, out_size=None, overscan=0.0):
    """
    The map that turns an UNDISTORTED plate back into the ORIGINAL geometry.

    The other half of the handoff: comp works on the undistorted plate, then
    Read(comp result) -> STMap(stmap = this map) puts the distortion back so the
    render sits on the original plate. The output raster is the ORIGINAL plate's
    size (override with `out_size`), and every value points into the UNDISTORTED
    plate described by `pinhole_of(cam, overscan)` - so the same overscan must
    be used for both maps or the two will not line up.

    This direction needs the iterative `undistort_points`, once per output pixel.
    Where a pixel has no pinhole image at all - the rim of a very wide fisheye -
    the value is clamped rather than left as infinity; call `undistort_points`
    with `return_converged=True` on the frame corners first if that is a risk,
    and warn the artist rather than shipping a map with a broken edge.

    Returns float32, shape (H, W, 2), row 0 = top of frame.
    """
    intr = _intrinsics(cam)
    fx, fy, cx, cy, out_w, out_h = _raster(intr, out_size)
    pin = pinhole_of(cam, overscan)

    gx, gy = _pixel_grid(out_w, out_h)
    # Output pixel -> the distorted normalised point the real lens produced...
    xy = np.stack([(gx - cx) / fx, (gy - cy) / fy], axis=-1)
    undistorted = undistort_points(cam, xy)
    # ...and where the ideal pinhole would have put it on the undistorted plate.
    x_src = pin["fx"] * undistorted[..., 0] + pin["cx"]
    y_src = pin["fy"] * undistorted[..., 1] + pin["cy"]
    return _to_stmap(x_src, y_src, pin["width"], pin["height"])


def identity_stmap(width, height):
    """
    The STMap that changes nothing, for tests and for a lens with no distortion.

    Written out rather than special-cased so the expected values are visible:
    pixel centres over the same raster, with the v flip.
    """
    gx, gy = _pixel_grid(int(width), int(height))
    return _to_stmap(gx, gy, int(width), int(height))


# =============================================================================
# WRITING
# =============================================================================
_EXR_BACKEND_CACHE = []


def exr_backend(recheck=False):
    """
    Which 32-bit float EXR writer actually works here, or None.

    Returns "openexr", "imageio" or None. The OpenEXR binding is trusted on
    import; imageio is not, because it reports EXR as a known extension while
    only being able to write one through an optional backend (FreeImage,
    OpenCV) that may not be installed. The only honest test for that is to write
    a 1x1 EXR to a temp file, so that is what happens - once, cached, since the
    answer cannot change inside a run. Pass recheck=True in a test that
    manipulates the environment.
    """
    if _EXR_BACKEND_CACHE and not recheck:
        return _EXR_BACKEND_CACHE[0]

    backend = None
    try:
        import OpenEXR  # noqa: F401
        backend = "openexr"
    except Exception:
        backend = None

    if backend is None:
        try:
            import tempfile
            import imageio.v2 as iio
            probe = np.zeros((1, 1, 3), dtype=np.float32)
            with tempfile.TemporaryDirectory() as tmp:
                iio.imwrite(str(Path(tmp) / "probe.exr"), probe)
            backend = "imageio"
        except Exception:
            backend = None

    del _EXR_BACKEND_CACHE[:]
    _EXR_BACKEND_CACHE.append(backend)
    return backend


def has_exr_support():
    """True when write_exr will actually produce an EXR rather than fall back."""
    return exr_backend() is not None


def write_exr(path, array):
    """
    Write a float image, preferring 32-bit float EXR.

    Returns (written_path, fmt) where fmt is "exr" when a real EXR was written
    and "png16" when the fallback ran. On the fallback the suffix of the written
    path is .png, which is why the path comes back instead of being assumed.

    THE FALLBACK IS LOSSY. A 16-bit PNG holds 0..1 in 65535 steps and clips
    anything outside, so an overscan STMap loses the values that point off the
    edge of the source plate. It is a warning-and-carry-on path, not a
    delivery format; the roadmap bundles an OpenEXR wheel in the frozen build
    precisely so this is never hit there.

    Channels are named R, G, B, A in order. A two-channel STMap is padded with a
    zero blue channel, which every EXR reader and Nuke's STMap node are happy
    with and which keeps the file viewable.
    """
    path = Path(path)
    arr = np.asarray(array, dtype=np.float32)
    if arr.ndim == 2:
        arr = arr[:, :, None]
    if arr.ndim != 3 or arr.shape[2] > 4:
        raise ValueError("expected an (H, W) or (H, W, C<=4) array, got shape %r" % (arr.shape,))

    backend = exr_backend()
    if backend is not None:
        try:
            _write_exr_file(path, arr, backend)
            return str(path), "exr"
        except Exception:
            # A broken or half-installed EXR binding must not lose the map.
            pass

    png_path = path.with_suffix(".png")
    _write_png16(png_path, arr)
    return str(png_path), "png16"


def _write_exr_file(path, arr, backend):
    """Write `arr` as scanline 32-bit float EXR through whichever binding exists."""
    if arr.shape[2] == 2:
        # A bare two-channel EXR is legal but several viewers show nothing; a
        # zero blue costs a third of the file and makes it openable anywhere.
        arr = np.dstack([arr, np.zeros_like(arr[:, :, :1])])
    height, width, channels = arr.shape
    names = ["R", "G", "B", "A"][:channels]

    if backend == "openexr":
        import OpenEXR
        pixels = {name: np.ascontiguousarray(arr[:, :, i]) for i, name in enumerate(names)}
        # OpenEXR 3.2+ ships a numpy-native File API; older wheels only have the
        # header/OutputFile pair, which needs Imath for the pixel type. Try the
        # modern one first and fall through on anything at all.
        if hasattr(OpenEXR, "File"):
            try:
                with OpenEXR.File({}, pixels) as handle:
                    handle.write(str(path))
                return
            except Exception:
                pass
        import Imath
        header = OpenEXR.Header(width, height)
        pt = Imath.PixelType(Imath.PixelType.FLOAT)
        header["channels"] = {name: Imath.Channel(pt) for name in names}
        out = OpenEXR.OutputFile(str(path), header)
        out.writePixels({name: pixels[name].tobytes() for name in names})
        out.close()
        return

    import imageio.v2 as iio
    iio.imwrite(str(path), arr)


def read_exr(path):
    """
    Read an EXR back as a float32 (H, W, C) array, or raise when no backend exists.

    Only used by the tests and by the verification scripts; the pipeline itself
    never reads its own maps back.
    """
    backend = exr_backend()
    if backend is None:
        raise RuntimeError("no EXR backend is importable in this interpreter")
    if backend == "openexr":
        import OpenEXR
        if hasattr(OpenEXR, "File"):
            # OpenEXR 3.x hands back whatever grouping the writer used: an RGB
            # image comes back as one "RGB" entry already shaped (H, W, 3),
            # while separately written planes come back as "R", "G", "B". Both
            # are valid files, so accept either rather than assuming.
            with OpenEXR.File(str(path)) as handle:
                chans = handle.channels()
                if not isinstance(chans, dict):
                    chans = dict(chans)
                for name in ("RGBA", "RGB"):
                    if name in chans:
                        pix = np.asarray(chans[name].pixels, dtype=np.float32)
                        return pix if pix.ndim == 3 else pix[:, :, None]
                order = [n for n in ("R", "G", "B", "A") if n in chans]
                if not order:
                    order = sorted(chans)
                planes = []
                for n in order:
                    pix = np.asarray(chans[n].pixels, dtype=np.float32)
                    planes.append(pix if pix.ndim == 2 else pix[:, :, 0])
                return np.dstack(planes)
        import Imath
        src = OpenEXR.InputFile(str(path))
        header = src.header()
        win = header["dataWindow"]
        width = win.max.x - win.min.x + 1
        height = win.max.y - win.min.y + 1
        pt = Imath.PixelType(Imath.PixelType.FLOAT)
        order = [n for n in ("R", "G", "B", "A") if n in header["channels"]]
        planes = [np.frombuffer(src.channel(n, pt), dtype=np.float32).reshape(height, width)
                  for n in order]
        src.close()
        return np.dstack(planes)

    import imageio.v2 as iio
    return np.asarray(iio.imread(str(path)), dtype=np.float32)


def _write_png16(path, arr):
    """
    Write a 16-bit PNG by hand (zlib plus four chunks).

    Pillow refuses 16-bit RGB - it only does 16-bit single channel - and there is
    no other image library in the bundled interpreter, so the encoder is here.
    PNG is big-endian and every scanline is prefixed with its filter byte; filter
    0 (none) keeps this short and the maps compress well enough regardless.
    """
    height, width, channels = arr.shape
    if channels == 2:
        arr = np.dstack([arr, np.zeros_like(arr[:, :, :1])])
        channels = 3
    color_type = {1: 0, 2: 4, 3: 2, 4: 6}[channels]

    data = np.clip(arr, 0.0, 1.0)
    quantised = np.rint(data * 65535.0).astype(">u2")
    raw = b"".join(b"\x00" + quantised[row].tobytes() for row in range(height))

    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 16, color_type, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))
