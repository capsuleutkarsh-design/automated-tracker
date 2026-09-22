"""
Render the SVG brand sources into the PNG / ICO files the README, the GitHub
Pages site and the installer use.

    "00 PYTHON\python.exe" branding\render_assets.py

Outputs (all git-tracked so the site and README work without a build):
    branding/png/mark_<size>.png          16 ... 1024
    branding/png/logo.png, logo@2x.png    horizontal lockup
    branding/png/banner.png, banner@2x.png
    branding/app_icon.ico                 multi-size Windows icon
    docs/assets/*                         copies the site references
"""
import os
import shutil
import sys
from pathlib import Path

# The default (windows) platform is used on purpose: the "offscreen" platform
# has no font database here, so every glyph in the wordmark renders as a box.
# No window is ever shown.

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PNG = HERE / "png"
SITE_ASSETS = ROOT / "docs" / "assets"


def render(svg, w, h, out):
    r = QSvgRenderer(str(svg))
    if not r.isValid():
        sys.exit("bad svg: %s" % svg)
    img = QImage(QSize(w, h), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    r.render(p)
    p.end()
    out.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(out)):
        sys.exit("could not write %s" % out)
    print("  %-28s %dx%d" % (out.relative_to(ROOT), w, h))


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841  (needed for font rendering)
    print("rendering brand assets")

    for s in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
        render(HERE / "mark.svg", s, s, PNG / ("mark_%d.png" % s))
    render(HERE / "logo.svg", 1040, 256, PNG / "logo.png")
    render(HERE / "logo.svg", 2080, 512, PNG / "logo@2x.png")
    if (HERE / "banner.svg").exists():
        render(HERE / "banner.svg", 1280, 640, PNG / "banner.png")
        render(HERE / "banner.svg", 2560, 1280, PNG / "banner@2x.png")

    # Windows icon from the rendered marks
    try:
        from PIL import Image
        frames = [Image.open(PNG / ("mark_%d.png" % s)).convert("RGBA")
                  for s in (16, 24, 32, 48, 64, 128, 256)]
        ico = HERE / "app_icon.ico"
        frames[-1].save(ico, format="ICO", sizes=[(f.width, f.height) for f in frames])
        print("  %-28s %d sizes" % (ico.relative_to(ROOT), len(frames)))
    except ImportError:
        print("  Pillow missing - app_icon.ico not written")

    # copies for the GitHub Pages site
    SITE_ASSETS.mkdir(parents=True, exist_ok=True)
    for name in ("mark.svg", "logo.svg", "banner.svg"):
        if (HERE / name).exists():
            shutil.copy2(HERE / name, SITE_ASSETS / name)
    for name in ("mark_32.png", "mark_64.png", "mark_256.png", "mark_512.png",
                 "logo.png", "banner.png", "banner@2x.png"):
        if (PNG / name).exists():
            shutil.copy2(PNG / name, SITE_ASSETS / name)
    if (HERE / "app_icon.ico").exists():
        shutil.copy2(HERE / "app_icon.ico", SITE_ASSETS.parent / "favicon.ico")
    print("  copied site assets -> %s" % SITE_ASSETS.relative_to(ROOT))


if __name__ == "__main__":
    main()
