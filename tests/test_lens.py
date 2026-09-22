"""
Lens distortion and STMap maths: the identity cases, the iterative inverse for
realistic barrel, pincushion and fisheye lenses, the Nuke STMap conventions
(v flip, overscan, centre alignment) and the EXR writer with its PNG fallback.
"""
import numpy as np
import pytest

from core import lens


W, H = 1920, 1080


def cam(model, params, width=W, height=H):
    """A camera dict shaped like the ones parse_colmap_cameras produces."""
    if model in ("SIMPLE_PINHOLE", "SIMPLE_RADIAL", "RADIAL",
                 "SIMPLE_RADIAL_FISHEYE", "RADIAL_FISHEYE"):
        fx = fy = params[0]
        cx, cy = params[1], params[2]
    else:
        fx, fy = params[0], params[1]
        cx, cy = params[2], params[3]
    return {"model": model, "width": width, "height": height,
            "focal_x": fx, "focal_y": fy, "cx": cx, "cy": cy,
            "params": list(params)}


PINHOLE = cam("PINHOLE", [1400.0, 1400.0, 953.0, 542.0])
# A wide lens with a strong barrel, and the same lens pushed the other way.
BARREL = cam("RADIAL", [1100.0, 960.0, 540.0, -0.32, 0.09])
PINCUSHION = cam("RADIAL", [1100.0, 960.0, 540.0, 0.30, 0.08])
OPENCV = cam("OPENCV", [1250.0, 1248.0, 951.0, 543.0, -0.24, 0.07, 0.0012, -0.0009])
FULL_OPENCV = cam("FULL_OPENCV",
                  [1250.0, 1248.0, 951.0, 543.0, -0.28, 0.11, 0.001, -0.0008,
                   -0.02, 0.004, 0.0, 0.0])
# Wide fisheyes, but short of the 90 degree wall: at f = 800 the frame corner is
# a 54 degree ray, which still has a finite pinhole image. See WIDE_FISHEYE.
FISHEYE = cam("OPENCV_FISHEYE", [800.0, 802.0, 960.0, 540.0, -0.035, 0.008, -0.0015, 0.0002])
SIMPLE_FISHEYE = cam("SIMPLE_RADIAL_FISHEYE", [800.0, 960.0, 540.0, -0.04])
RADIAL_FISHEYE = cam("RADIAL_FISHEYE", [800.0, 960.0, 540.0, -0.05, 0.006])
# A true 180 degree fisheye: its rim is at 90 degrees and has no pinhole image.
WIDE_FISHEYE = cam("OPENCV_FISHEYE", [700.0, 700.0, 960.0, 540.0, 0.0, 0.0, 0.0, 0.0])
FOV = cam("FOV", [900.0, 900.0, 960.0, 540.0, 0.85])


def frame_grid(c, step=97):
    """Normalised points on a coarse grid covering the whole frame, corners included."""
    xs = np.append(np.arange(0.5, c["width"], step), c["width"] - 0.5)
    ys = np.append(np.arange(0.5, c["height"], step), c["height"] - 0.5)
    gx, gy = np.meshgrid(xs, ys)
    return np.stack([(gx - c["cx"]) / c["focal_x"], (gy - c["cy"]) / c["focal_y"]], axis=-1)


def px_error(c, a, b):
    """Worst difference between two sets of normalised points, in pixels."""
    d = np.abs(a - b)
    return float(np.maximum(d[..., 0] * c["focal_x"], d[..., 1] * c["focal_y"]).max())


# -------------------------------------------------------------- distortion maths
@pytest.mark.parametrize("c", [PINHOLE, cam("SIMPLE_PINHOLE", [1400.0, 960.0, 540.0])])
def test_a_pinhole_camera_does_not_distort(c):
    xy = frame_grid(c)
    assert np.allclose(lens.distort_points(c, xy), xy)
    assert np.allclose(lens.undistort_points(c, xy), xy)
    assert lens.has_distortion(c) is False


def test_zero_k1_simple_radial_does_not_distort():
    c = cam("SIMPLE_RADIAL", [1400.0, 953.0, 542.0, 0.0])
    xy = frame_grid(c)
    assert np.allclose(lens.distort_points(c, xy), xy)
    assert np.allclose(lens.undistort_points(c, xy), xy)
    assert lens.has_distortion(c) is False


@pytest.mark.parametrize("c, name", [
    (BARREL, "strong barrel"),
    (PINCUSHION, "strong pincushion"),
    (OPENCV, "opencv with tangential"),
    (FULL_OPENCV, "full opencv rational"),
    (cam("SIMPLE_RADIAL", [1400.0, 953.0, 542.0, -0.21]), "simple radial"),
])
def test_distort_undistort_round_trip(c, name):
    """undistort() must invert distort() to well under half a pixel, both ways."""
    xy = frame_grid(c)
    assert px_error(c, lens.undistort_points(c, lens.distort_points(c, xy)), xy) < 0.05, name
    assert px_error(c, lens.distort_points(c, lens.undistort_points(c, xy)), xy) < 0.05, name


@pytest.mark.parametrize("c", [FISHEYE, SIMPLE_FISHEYE, RADIAL_FISHEYE])
def test_fisheye_round_trip(c):
    xy = frame_grid(c)
    assert px_error(c, lens.distort_points(c, lens.undistort_points(c, xy)), xy) < 0.05
    assert px_error(c, lens.undistort_points(c, lens.distort_points(c, xy)), xy) < 0.05


def test_a_180_degree_fisheye_says_where_it_cannot_be_undistorted():
    """
    The rim of a 180 degree fisheye is a ray at 90 degrees, which has no pinhole
    image at all. The solver must report that rather than invent a number.
    """
    xy = frame_grid(WIDE_FISHEYE)
    out, ok = lens.undistort_points(WIDE_FISHEYE, xy, return_converged=True)
    assert np.isfinite(out).all()            # never NaN, never inf
    assert ok[ok.shape[0] // 2, ok.shape[1] // 2]    # the middle is fine
    assert not ok[0, 0]                      # the corner is past the wall
    # Where it did converge, it converged properly.
    good = lens.distort_points(WIDE_FISHEYE, out)
    assert float(np.abs(good - xy)[ok].max()) * WIDE_FISHEYE["focal_x"] < 0.05


def test_fov_round_trip():
    xy = frame_grid(FOV)
    assert px_error(FOV, lens.distort_points(FOV, lens.undistort_points(FOV, xy)), xy) < 0.05


def test_the_optical_axis_never_moves():
    """Every radial model leaves the principal point exactly where it is."""
    for c in (BARREL, PINCUSHION, OPENCV, FISHEYE, SIMPLE_FISHEYE, FOV, FULL_OPENCV):
        assert np.allclose(lens.distort_points(c, np.zeros(2)), np.zeros(2), atol=1e-12)
        assert np.allclose(lens.undistort_points(c, np.zeros(2)), np.zeros(2), atol=1e-12)


def test_barrel_pulls_in_and_pincushion_pushes_out():
    """A sign check, so a flipped convention cannot pass the round trips quietly."""
    edge = np.array([0.6, 0.0])
    assert lens.distort_points(BARREL, edge)[0] < edge[0]
    assert lens.distort_points(PINCUSHION, edge)[0] > edge[0]


def test_points_keep_their_shape():
    assert lens.distort_points(BARREL, np.array([0.1, 0.2])).shape == (2,)
    assert lens.distort_points(BARREL, np.zeros((5, 2))).shape == (5, 2)
    assert lens.distort_points(BARREL, np.zeros((4, 3, 2))).shape == (4, 3, 2)


def test_an_unsupported_model_is_refused_by_name():
    c = cam("THIN_PRISM_FISHEYE", [1.0, 1.0, 1.0, 1.0])
    with pytest.raises(ValueError) as err:
        lens.distort_points(c, np.zeros(2))
    assert "THIN_PRISM_FISHEYE" in str(err.value)


# ------------------------------------------------------------- the pinhole camera
def test_pinhole_of_without_overscan_is_the_same_camera():
    p = lens.pinhole_of(BARREL, 0.0)
    assert (p["width"], p["height"]) == (BARREL["width"], BARREL["height"])
    assert (p["fx"], p["fy"]) == (BARREL["focal_x"], BARREL["focal_y"])
    assert (p["cx"], p["cy"]) == (BARREL["cx"], BARREL["cy"])
    assert p["model"] == "PINHOLE" and p["k1"] == 0.0


def test_pinhole_of_overscan_grows_the_canvas_and_keeps_the_centre_aligned():
    p = lens.pinhole_of(OPENCV, 0.10)
    assert (p["width"], p["height"]) == (2112, 1188)
    # Focal length untouched: one pixel on axis is still one pixel.
    assert (p["fx"], p["fy"]) == (OPENCV["focal_x"], OPENCV["focal_y"])
    # The optical centre sits on the same physical spot: half the added pixels
    # go on each side.
    assert p["cx"] == pytest.approx(OPENCV["cx"] + (2112 - 1920) / 2.0)
    assert p["cy"] == pytest.approx(OPENCV["cy"] + (1188 - 1080) / 2.0)
    with pytest.raises(ValueError):
        lens.pinhole_of(OPENCV, -0.1)


def test_pinhole_of_is_drop_in_for_the_exporters():
    """It has to carry export_tools' spelling too, or the writers cannot use it."""
    import export_tools as et
    p = lens.pinhole_of(OPENCV, 0.05)
    mm = et.camera_intrinsics_mm(p)
    assert mm["lens_mm"] > 0.0
    u, v = et.nuke_win_translate(p)
    assert np.isfinite(u) and np.isfinite(v)


# ------------------------------------------------------------------------- STMaps
def test_pinhole_stmaps_are_the_identity():
    ident = lens.identity_stmap(W, H)
    undist = lens.undistort_stmap(PINHOLE)
    redist = lens.redistort_stmap(PINHOLE)
    assert undist.shape == (H, W, 2) and undist.dtype == np.float32
    assert np.abs(undist - ident).max() < 1e-6
    assert np.abs(redist - ident).max() < 1e-6


def test_zero_k1_simple_radial_stmap_is_the_identity():
    c = cam("SIMPLE_RADIAL", [1400.0, 953.0, 542.0, 0.0])
    assert np.abs(lens.undistort_stmap(c) - lens.identity_stmap(W, H)).max() < 1e-6
    assert np.abs(lens.redistort_stmap(c) - lens.identity_stmap(W, H)).max() < 1e-6


def test_stmap_v_counts_up_from_the_bottom():
    """
    The whole point of the flip: numpy row 0 is the top of the frame and must
    carry v near 1, because Nuke measures v up from the bottom.
    """
    m = lens.undistort_stmap(PINHOLE)
    assert m[0, 0, 0] == pytest.approx(0.5 / W)          # left column, u near 0
    assert m[0, 0, 1] == pytest.approx(1.0 - 0.5 / H)    # top row, v near 1
    assert m[-1, -1, 0] == pytest.approx(1.0 - 0.5 / W)  # right column, u near 1
    assert m[-1, -1, 1] == pytest.approx(0.5 / H)        # bottom row, v near 0
    # v decreases going down the array; u increases going right.
    assert np.all(np.diff(m[:, 0, 1]) < 0.0)
    assert np.all(np.diff(m[0, :, 0]) > 0.0)


def test_undistort_stmap_holds_the_forward_distortion():
    """
    Spot check against the maths by hand: the map at an output pixel must name
    the place the real lens put that ray on the original plate.
    """
    m = lens.undistort_stmap(BARREL)
    row, col = 200, 300
    xy = np.array([(col + 0.5 - BARREL["cx"]) / BARREL["focal_x"],
                   (row + 0.5 - BARREL["cy"]) / BARREL["focal_y"]])
    d = lens.distort_points(BARREL, xy)
    x_src = BARREL["focal_x"] * d[0] + BARREL["cx"]
    y_src = BARREL["focal_y"] * d[1] + BARREL["cy"]
    assert m[row, col, 0] == pytest.approx(x_src / W, abs=1e-6)
    assert m[row, col, 1] == pytest.approx(1.0 - y_src / H, abs=1e-6)


def test_the_two_maps_are_inverses_of_each_other():
    """
    Roadmap 1.5's own test: a point pushed through the redistort map and then
    read back through the undistort map returns where it started. Both maps are
    sampled at pixel centres, so the tolerance carries the rounding to the
    nearest sample, not the maths.

    The overscan is deliberately generous. Undistorting a barrel lens spreads the
    frame outwards, and this one is strong enough that its corners need about
    40 % more canvas before they fit - at 10 % they would fall off the
    undistorted plate and could not be looked up at all.
    """
    overscan = 0.5
    pin = lens.pinhole_of(BARREL, overscan)
    redist = lens.redistort_stmap(BARREL, overscan=overscan)
    undist = lens.undistort_stmap(BARREL, overscan=overscan)

    for row, col in [(540, 960), (120, 240), (900, 1700), (5, 5)]:
        # Where the undistorted plate holds this original pixel.
        u, v = redist[row, col]
        x_pin = u * pin["width"]
        y_pin = (1.0 - v) * pin["height"]
        # Read the undistort map at the nearest sample there...
        r = int(np.clip(round(y_pin - 0.5), 0, pin["height"] - 1))
        c = int(np.clip(round(x_pin - 0.5), 0, pin["width"] - 1))
        u2, v2 = undist[r, c]
        back = np.array([u2 * W, (1.0 - v2) * H])
        assert np.allclose(back, [col + 0.5, row + 0.5], atol=2.0)


def test_stmap_overscan_output_size_and_centre_alignment():
    overscan = 0.10
    pin = lens.pinhole_of(OPENCV, overscan)
    undist = lens.undistort_stmap(OPENCV, overscan=overscan)
    redist = lens.redistort_stmap(OPENCV, overscan=overscan)
    # The undistort map is the size of the undistorted plate; the redistort map
    # is the size of the original plate.
    assert undist.shape == (pin["height"], pin["width"], 2)
    assert redist.shape == (OPENCV["height"], OPENCV["width"], 2)

    # The optical centre of the overscanned raster still points at the optical
    # centre of the original plate - no drift from the added border.
    row = int(pin["cy"] - 0.5)
    col = int(pin["cx"] - 0.5)
    u, v = undist[row, col]
    assert u * OPENCV["width"] == pytest.approx(OPENCV["cx"], abs=0.6)
    assert (1.0 - v) * OPENCV["height"] == pytest.approx(OPENCV["cy"], abs=0.6)


def test_stmap_out_size_keeps_the_field_of_view():
    """A half-res proxy map must show the same thing, just sampled coarser."""
    full = lens.undistort_stmap(BARREL)
    half = lens.undistort_stmap(BARREL, out_size=(W // 2, H // 2))
    assert half.shape == (H // 2, W // 2, 2)
    assert np.allclose(half[0, 0], full[0, 0], atol=2e-3)
    assert np.allclose(half[-1, -1], full[-1, -1], atol=2e-3)


def test_overscan_recovers_plate_the_undistort_would_otherwise_crop():
    """
    Undistorting a barrel lens spreads the image outwards, so the corners of the
    original plate land outside an un-overscanned undistorted frame and are lost.
    Overscan buys them back: the same map now reaches further into the source.
    """
    plain = lens.undistort_stmap(BARREL, overscan=0.0)
    wide = lens.undistort_stmap(BARREL, overscan=0.35)
    # Without overscan the undistorted frame never reaches the plate corner...
    assert plain[0, 0, 0] > 0.03
    assert plain[0, 0, 1] < 0.97
    # ...with it, the same corner of the map reaches much closer to it.
    assert wide[0, 0, 0] < plain[0, 0, 0]
    assert wide[0, 0, 1] > plain[0, 0, 1]


# ------------------------------------------------------------------------ writing
def test_write_exr_reports_the_format_it_used(tmp_path):
    arr = lens.undistort_stmap(PINHOLE, out_size=(16, 9))
    written, fmt = lens.write_exr(tmp_path / "undistort.exr", arr)
    assert fmt in ("exr", "png16")
    assert lens.has_exr_support() == (fmt == "exr")

    from pathlib import Path
    out = Path(written)
    assert out.exists() and out.stat().st_size > 0

    if fmt == "exr":
        assert out.suffix == ".exr"
        back = lens.read_exr(out)
        assert np.allclose(back[..., :2], arr, atol=1e-6)
    else:
        # 16-bit quantised and clipped to 0..1: a real loss, so only the file is
        # checked. The frozen build bundles OpenEXR so this path is the warning.
        assert out.suffix == ".png"
        assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_write_exr_rejects_a_shape_no_image_format_has(tmp_path):
    with pytest.raises(ValueError):
        lens.write_exr(tmp_path / "bad.exr", np.zeros((4, 4, 7), dtype=np.float32))
