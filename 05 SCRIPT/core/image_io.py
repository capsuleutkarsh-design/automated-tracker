"""
Reading one frame of a plate as 8-bit sRGB, whatever format it was delivered in.

The 2D tracker, the viewport and the mask rasteriser all used to hand frames
straight to PIL, which reads JPEG, PNG and TIFF but not EXR or DPX - the two
formats a plate is most often delivered in. So an EXR or DPX sequence was
accepted by the media pool and then fell over the first time a frame was read.
Every frame read goes through here instead:

- EXR is read through the same OpenEXR binding core.lens writes STMaps with,
  and the scene-linear values are put through the sRGB curve and clipped to
  0..1. That is a display transform only - good enough to track and to look
  at, not a grade.
- DPX is decoded by the bundled FFmpeg (03 FFMPEG), which reads 8, 10, 12 and
  16-bit DPX. Log DPX shows up flat, the way it does in any viewer without a
  LUT; tracking does not mind.
- Everything else goes to PIL as before.

When a frame cannot be read (no FFmpeg for a DPX, a broken EXR) the error says
what the artist can do about it rather than being a PIL traceback.
"""

import io
from pathlib import Path

import numpy as np

# What a frame of a sequence may be. The media pool already listed DPX.
SEQUENCE_EXTS = {".jpg", ".jpeg", ".png", ".exr", ".tif", ".tiff", ".dpx"}

_FFMPEG_EXTS = {".dpx"}


class UnreadableImage(RuntimeError):
    """A frame that exists but cannot be decoded here, with a sentence saying why."""


def linear_to_srgb8(arr):
    """Scene-linear float (H, W, C) to 8-bit sRGB: NaN/inf to 0, clipped to 0..1."""
    a = np.nan_to_num(np.asarray(arr, dtype=np.float32), nan=0.0, posinf=1.0, neginf=0.0)
    a = np.clip(a, 0.0, 1.0)
    out = np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1.0 / 2.4) - 0.055)
    return (np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def _to_rgb(arr):
    """Any (H, W), (H, W, 1), (H, W, 2) or (H, W, >=3) array as (H, W, 3)."""
    if arr.ndim == 2:
        arr = arr[:, :, None]
    if arr.shape[2] == 1:
        return np.repeat(arr, 3, axis=2)
    if arr.shape[2] == 2:
        # Luminance + alpha: keep the luminance.
        return np.repeat(arr[:, :, :1], 3, axis=2)
    return arr[:, :, :3]


def _read_exr(path):
    from core import lens
    try:
        pix = lens.read_exr(path)
    except Exception as e:
        raise UnreadableImage(
            "Could not read the EXR frame %s (%s). Check the file opens in Nuke; if it "
            "does, send app.log in - the EXR reader may be missing from this install."
            % (Path(path).name, e)) from e
    return linear_to_srgb8(_to_rgb(np.asarray(pix)))


def _read_with_ffmpeg(path):
    from PIL import Image
    from core.app_paths import ffmpeg_exe
    from core.proc import run_hidden

    exe = ffmpeg_exe()
    name = Path(path).name
    if not exe.exists():
        raise UnreadableImage(
            "Could not read the DPX frame %s: FFmpeg was not found in 03 FFMPEG. Run "
            "SETUP.bat again, or convert the sequence to EXR, PNG or TIFF." % name)
    cmd = [str(exe), "-v", "error", "-nostdin", "-i", str(path), "-frames:v", "1",
           "-f", "image2pipe", "-vcodec", "png", "-pix_fmt", "rgb24", "pipe:1"]
    try:
        res = run_hidden(cmd, capture=True, timeout=60)
        data = res.stdout or b""
        if res.returncode != 0 or not data:
            raise RuntimeError(data[-400:].decode("utf-8", "replace").strip()
                               or "FFmpeg returned no image")
        with Image.open(io.BytesIO(data)) as im:
            return np.asarray(im.convert("RGB"))
    except UnreadableImage:
        raise
    except Exception as e:
        raise UnreadableImage(
            "Could not read the DPX frame %s (%s). Convert the sequence to EXR, PNG or "
            "TIFF and add it again." % (name, e)) from e


def read_rgb8(path):
    """One frame on disk as a (H, W, 3) uint8 sRGB array. Raises UnreadableImage."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".exr":
        return _read_exr(path)
    if ext in _FFMPEG_EXTS:
        return _read_with_ffmpeg(path)
    from PIL import Image
    try:
        with Image.open(str(path)) as im:
            return np.asarray(im.convert("RGB"))
    except Exception as e:
        raise UnreadableImage("Could not read the frame %s (%s)." % (path.name, e)) from e


def read_pil_rgb(path):
    """The same frame as a PIL RGB image, for code that resizes with PIL."""
    from PIL import Image
    return Image.fromarray(read_rgb8(path), "RGB")


def image_size(path):
    """(width, height) of a frame on disk, or None when it cannot be read."""
    path = Path(path)
    if path.suffix.lower() not in (".exr",) and path.suffix.lower() not in _FFMPEG_EXTS:
        try:
            from PIL import Image
            with Image.open(str(path)) as im:
                return int(im.size[0]), int(im.size[1])
        except Exception:
            return None
    try:
        arr = read_rgb8(path)
    except UnreadableImage:
        return None
    return int(arr.shape[1]), int(arr.shape[0])
