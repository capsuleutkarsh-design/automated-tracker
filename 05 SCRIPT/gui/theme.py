"""
Studio theme and design tokens.

One accent, four surface levels, three text weights, a 4px spacing grid and a
single 28px control height. Everything in the app is built from these, so the
two tabs look like one program instead of two.
"""

# ---------------------------------------------------------------------------
# Design tokens
# ---------------------------------------------------------------------------
BG_APP = "#0d0f13"       # window behind everything
BG_PANEL = "#14171d"     # panel / card body
BG_RAISED = "#1a1e25"    # header strips, toolbars, hovered rows
BG_INPUT = "#0f1217"     # inputs, lists, consoles
BG_HOVER = "#20252e"

BORDER = "#242a33"       # default hairline
BORDER_SOFT = "#1c2129"  # dividers inside a card
BORDER_FOCUS = "#3d8fd6"

TEXT = "#e3e7ee"         # primary
TEXT_DIM = "#98a1b0"     # labels, secondary
TEXT_MUTED = "#646d7c"   # hints, disabled

ACCENT = "#38bdf8"
ACCENT_DIM = "#1e7fb0"
ACCENT_WASH = "#132735"

OK = "#3ddc84"
WARN = "#f2b544"
ERR = "#ff6161"

ROW_H = 28               # every input, every button
RADIUS = 5

FONT_STACK = "'Segoe UI', 'Inter', system-ui, sans-serif"
MONO_STACK = "'Cascadia Mono', 'Consolas', 'JetBrains Mono', monospace"


# ---------------------------------------------------------------------------
# Arrow glyphs
#
# Qt hands rendering of a sub-control over to the stylesheet as soon as the
# stylesheet mentions it, and it cannot build a triangle out of CSS borders the
# way a browser can. So the chevrons are drawn once as small PNGs and referenced
# by path. They are regenerated if missing or if the colour changes.
# ---------------------------------------------------------------------------
import os
from pathlib import Path

try:
    from core.app_paths import bundled_dir, writable_cache_dir
except Exception:  # theme.py imported before the package is on sys.path
    bundled_dir = lambda: Path(__file__).resolve().parent.parent
    writable_cache_dir = lambda: Path(__file__).resolve().parent / "assets"

# Prefer icons shipped with the build; fall back to a writable cache when the
# install directory is read-only (Program Files).
_ICON_DIR = bundled_dir() / "gui" / "assets" / "_ui"


def _make_chevron(path, colour, up=False, size=18, thickness=2):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rgb = tuple(int(colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
    pad = size * 0.28
    mid = size / 2.0
    if up:
        pts = [(pad, mid + pad * 0.55), (mid, mid - pad * 0.55), (size - pad, mid + pad * 0.55)]
    else:
        pts = [(pad, mid - pad * 0.55), (mid, mid + pad * 0.55), (size - pad, mid - pad * 0.55)]
    d.line(pts, fill=rgb, width=thickness, joint="curve")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _chevrons():
    """Returns QSS-safe paths for (down, up, down_accent)."""
    wanted = {
        "chev_down.png": (TEXT_DIM, False),
        "chev_up.png": (TEXT_DIM, True),
        "chev_down_accent.png": (ACCENT, False),
    }
    out = {}
    for name, (colour, up) in wanted.items():
        f = _ICON_DIR / name
        if f.exists():
            out[name] = f.as_posix()
            continue
        for target in (f, writable_cache_dir() / "ui" / name):
            try:
                _make_chevron(target, colour, up=up)
                out[name] = target.as_posix()
                break
            except Exception:
                continue
        else:
            out[name] = ""
    return out


_CHEV = _chevrons()
CHEV_DOWN = _CHEV["chev_down.png"]
CHEV_UP = _CHEV["chev_up.png"]
CHEV_DOWN_ACCENT = _CHEV["chev_down_accent.png"]


DARK_STUDIO_QSS = f"""
/* ========================================================================
   BASE
   ======================================================================== */
QMainWindow, QDialog {{
    background-color: {BG_APP};
}}

/* The workspace sits on top of the background plate in a StackAll layout, so it
   must not paint - but only this widget, which is why it is targeted by id. */
QWidget#workspaceRoot {{
    background: transparent;
}}

QLabel#bgPlate {{
    background-color: {BG_APP};
}}

QWidget {{
    color: {TEXT};
    font-family: {FONT_STACK};
    font-size: 12px;
}}

QLabel {{
    background: transparent;
    color: {TEXT_DIM};
}}

QLabel#h1 {{
    color: {TEXT};
    font-size: 13px;
    font-weight: 600;
}}

/* Card heading: small, spaced, quiet. Reads as structure, not decoration. */
QLabel#sectionTitle {{
    color: {TEXT_DIM};
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.1px;
    text-transform: uppercase;
    background: transparent;
}}

QLabel#hint {{
    color: {TEXT_MUTED};
    font-size: 11px;
}}

QLabel#fieldLabel {{
    color: {TEXT_DIM};
    font-size: 12px;
}}

QLabel#valueChip {{
    color: {ACCENT};
    font-family: {MONO_STACK};
    font-size: 11px;
    background: {ACCENT_WASH};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 2px 7px;
}}

QLabel#statusChip {{
    color: {TEXT_DIM};
    font-size: 11px;
    padding: 2px 9px;
    margin-left: 5px;
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
}}

/* Chip states, set from code via setProperty("state", ...). Keeping the colours
   here means handlers never hand-write a stylesheet. */
QLabel#valueChip[state="key"], QLabel#statusChip[state="ok"] {{
    color: {OK};
    background: #11261d;
    border-color: #1f5137;
}}

QLabel#valueChip[state="interp"] {{
    color: {ACCENT};
    background: {ACCENT_WASH};
    border-color: {ACCENT_DIM};
}}

QLabel#valueChip[state="idle"], QLabel#statusChip[state="idle"] {{
    color: {TEXT_MUTED};
    background: {BG_INPUT};
    border-color: {BORDER_SOFT};
}}

QLabel#statusChip[state="busy"] {{
    color: {ACCENT};
    border-color: {ACCENT_DIM};
}}

QLabel#statusChip[state="bad"] {{
    color: {ERR};
    border-color: #45262b;
}}

QToolTip {{
    background-color: {BG_RAISED};
    color: {TEXT};
    border: 1px solid {BORDER_FOCUS};
    border-radius: 4px;
    padding: 6px 9px;
}}

/* ========================================================================
   CARDS  -  the one container shape used everywhere
   ======================================================================== */
QFrame#card {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS + 1}px;
}}

QFrame#cardHeader {{
    background-color: {BG_RAISED};
    border: none;
    border-top-left-radius: {RADIUS}px;
    border-top-right-radius: {RADIUS}px;
}}

QFrame#divider {{
    background-color: {BORDER_SOFT};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

QFrame#vDivider {{
    background-color: {BORDER_SOFT};
    border: none;
    max-width: 1px;
    min-width: 1px;
}}

/* Toolbars / strips that sit under a viewport */
QFrame#strip {{
    background-color: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}

/* ========================================================================
   TABS
   ======================================================================== */
QTabWidget::pane {{
    border: none;
    background: transparent;
    top: -1px;
}}

QTabBar {{
    qproperty-drawBase: 0;
    background: transparent;
}}

QTabBar::tab {{
    background: transparent;
    color: {TEXT_MUTED};
    padding: 8px 20px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
    font-size: 12px;
    font-weight: 600;
}}

QTabBar::tab:hover {{
    color: {TEXT_DIM};
}}

QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
}}

/* ========================================================================
   INPUTS  -  all exactly ROW_H tall so rows line up across cards
   ======================================================================== */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background-color: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 0 9px;
    min-height: {ROW_H}px;
    max-height: {ROW_H}px;
    selection-background-color: {ACCENT_DIM};
}}

QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {BORDER_FOCUS};
}}

QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {ACCENT};
    background-color: {BG_PANEL};
}}

QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {TEXT_MUTED};
    background-color: {BG_APP};
    border-color: {BORDER_SOFT};
}}

QLineEdit {{
    font-family: {MONO_STACK};
    font-size: 11px;
}}

QSpinBox, QDoubleSpinBox {{
    font-family: {MONO_STACK};
    font-size: 12px;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    background: transparent;
    width: 22px;
}}

QComboBox::down-arrow {{
    image: url("{CHEV_DOWN}");
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 11px;
    height: 11px;
}}

QComboBox QAbstractItemView {{
    background-color: {BG_RAISED};
    color: {TEXT};
    border: 1px solid {BORDER_FOCUS};
    border-radius: {RADIUS}px;
    padding: 4px;
    outline: none;
    selection-background-color: {ACCENT_DIM};
    selection-color: #ffffff;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    background: {BG_RAISED};
    border: none;
    border-left: 1px solid {BORDER};
    border-top-right-radius: {RADIUS}px;
    width: 17px;
    height: {ROW_H // 2 - 1}px;
}}

QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    background: {BG_RAISED};
    border: none;
    border-left: 1px solid {BORDER};
    border-bottom-right-radius: {RADIUS}px;
    width: 17px;
    height: {ROW_H // 2 - 1}px;
}}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {ACCENT_DIM};
}}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{CHEV_UP}");
    width: 9px;
    height: 9px;
}}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{CHEV_DOWN}");
    width: 9px;
    height: 9px;
}}

/* ========================================================================
   BUTTONS
   Default = quiet secondary. Loud variants are opt-in by objectName.
   ======================================================================== */
QPushButton {{
    background-color: {BG_RAISED};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 0 12px;
    min-height: {ROW_H}px;
    max-height: {ROW_H}px;
    font-size: 12px;
}}

QPushButton:hover {{
    background-color: {BG_HOVER};
    border-color: {BORDER_FOCUS};
}}

QPushButton:pressed {{
    background-color: {BG_INPUT};
}}

QPushButton:disabled {{
    color: {TEXT_MUTED};
    background-color: {BG_APP};
    border-color: {BORDER_SOFT};
}}

/* Primary call to action - one per screen */
QPushButton#primary {{
    background-color: {ACCENT};
    color: #06121c;
    border: 1px solid {ACCENT};
    font-weight: 700;
    font-size: 13px;
    min-height: 38px;
    max-height: 38px;
    letter-spacing: 0.4px;
}}

QPushButton#primary:hover {{
    background-color: #55caff;
    border-color: #55caff;
}}

QPushButton#primary:pressed {{
    background-color: {ACCENT_DIM};
}}

QPushButton#primary:disabled {{
    background-color: {BG_RAISED};
    color: {TEXT_MUTED};
    border-color: {BORDER};
}}

/* Destructive / stop */
QPushButton#danger {{
    background-color: transparent;
    color: {ERR};
    border: 1px solid #45262b;
    min-height: 38px;
    max-height: 38px;
    font-weight: 600;
}}

QPushButton#danger:hover {{
    background-color: #2a1619;
    border-color: {ERR};
}}

QPushButton#danger:disabled {{
    color: {TEXT_MUTED};
    border-color: {BORDER_SOFT};
    background: transparent;
}}

/* Export row - accent outline, calmer than primary */
QPushButton#export {{
    background-color: transparent;
    color: {ACCENT};
    border: 1px solid {ACCENT_DIM};
    font-weight: 600;
    min-height: 30px;
    max-height: 30px;
}}

QPushButton#export:hover {{
    background-color: {ACCENT_WASH};
    border-color: {ACCENT};
}}

/* Small icon-ish buttons in dense rows */
QPushButton#compact {{
    padding: 0 8px;
    font-size: 11px;
    min-height: 26px;
    max-height: 26px;
}}

/* Transport buttons */
QPushButton#transport {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    font-family: {MONO_STACK};
    font-size: 13px;
    min-width: 34px;
    min-height: 30px;
    max-height: 30px;
    padding: 0 6px;
}}

QPushButton#transport:hover {{
    background-color: {BG_HOVER};
    border-color: {ACCENT_DIM};
}}

QPushButton#transportPlay {{
    background-color: {ACCENT_WASH};
    color: {ACCENT};
    border: 1px solid {ACCENT_DIM};
    font-weight: 700;
    font-size: 12px;
    min-width: 78px;
    min-height: 30px;
    max-height: 30px;
}}

QPushButton#transportPlay:hover {{
    background-color: {ACCENT_DIM};
    color: #ffffff;
}}

/* Toggle chips (Matte / Alpha / Loupe) */
QPushButton#toggle {{
    background-color: transparent;
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    font-size: 11px;
    font-weight: 600;
    padding: 0 12px;
    min-height: 26px;
    max-height: 26px;
}}

QPushButton#toggle:hover {{
    border-color: {BORDER_FOCUS};
    color: {TEXT};
}}

QPushButton#toggle:checked {{
    background-color: {ACCENT_WASH};
    color: {ACCENT};
    border-color: {ACCENT};
}}

/* ========================================================================
   LISTS, TABLES, CONSOLE
   ======================================================================== */
QListWidget, QTableWidget, QTreeWidget {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    outline: none;
    color: {TEXT};
}}

QListWidget::item {{
    padding: 6px 9px;
    border-radius: 3px;
    margin: 1px 3px;
}}

QListWidget::item:hover {{
    background-color: {BG_RAISED};
}}

QListWidget::item:selected {{
    background-color: {ACCENT_WASH};
    color: {ACCENT};
}}

QTableWidget {{
    gridline-color: transparent;
}}

QTableWidget::item {{
    padding: 7px 9px;
    border: none;
}}

QTableWidget::item:selected {{
    background-color: {ACCENT_WASH};
    color: {TEXT};
}}

QHeaderView::section {{
    background-color: {BG_RAISED};
    color: {TEXT_MUTED};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 7px 9px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
}}

QTableCornerButton::section {{
    background-color: {BG_RAISED};
    border: none;
}}

QTextEdit {{
    background-color: {BG_INPUT};
    color: {TEXT_DIM};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    padding: 8px 10px;
    font-family: {MONO_STACK};
    font-size: 11px;
    selection-background-color: {ACCENT_DIM};
}}

/* ========================================================================
   CHECK / RADIO
   ======================================================================== */
QCheckBox, QRadioButton {{
    background: transparent;
    color: {TEXT_DIM};
    spacing: 8px;
    padding: 3px 0;
    font-size: 12px;
}}

QCheckBox:hover, QRadioButton:hover {{
    color: {TEXT};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px;
    height: 15px;
    background-color: {BG_INPUT};
    border: 1px solid {BORDER_SOFT};
}}

QCheckBox::indicator {{
    border-radius: 3px;
}}

QRadioButton::indicator {{
    border-radius: 8px;
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {ACCENT};
}}

QCheckBox::indicator:checked {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

QRadioButton::indicator:checked {{
    background-color: {ACCENT};
    border: 4px solid {BG_INPUT};
}}

QCheckBox:disabled, QRadioButton:disabled {{
    color: {TEXT_MUTED};
}}

/* ========================================================================
   SLIDER  -  the timeline
   ======================================================================== */
QSlider::groove:horizontal {{
    background: {BG_INPUT};
    height: 5px;
    border-radius: 2px;
    border: 1px solid {BORDER_SOFT};
}}

QSlider::sub-page:horizontal {{
    background: {ACCENT_DIM};
    height: 5px;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background: {ACCENT};
    border: none;
    width: 4px;
    height: 17px;
    margin: -7px 0;
    border-radius: 1px;
}}

QSlider::handle:horizontal:hover {{
    background: #7fd8ff;
    width: 6px;
    margin: -7px -1px;
}}

/* ========================================================================
   PROGRESS
   ======================================================================== */
QProgressBar {{
    background-color: {BG_INPUT};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    text-align: center;
    color: {TEXT_DIM};
    font-size: 11px;
    min-height: 22px;
    max-height: 22px;
}}

QProgressBar::chunk {{
    background-color: {ACCENT_DIM};
    border-radius: 4px;
}}

/* ========================================================================
   SPLITTER / SCROLL
   ======================================================================== */
QSplitter::handle {{
    background-color: transparent;
}}

QSplitter::handle:horizontal {{
    width: 7px;
}}

QSplitter::handle:vertical {{
    height: 7px;
}}

QSplitter::handle:hover {{
    background-color: {ACCENT_DIM};
}}

QScrollArea, QScrollArea#inspectorScroll {{
    background: transparent;
    border: none;
}}

/* The scroll viewport and the widget inside it both default to the palette
   base brush, which is light. Both have to be made transparent explicitly. */
QWidget#inspectorViewport, QWidget#inspectorBody, QWidget#stripViewport {{
    background: transparent;
}}

QScrollArea#stripScroll {{
    background: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {BORDER};
    border-radius: 5px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background: {ACCENT_DIM};
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {BORDER};
    border-radius: 5px;
    min-width: 30px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {ACCENT_DIM};
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0; border: none; background: none;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* ========================================================================
   MENU / STATUS BAR
   ======================================================================== */
QMenuBar {{
    background-color: {BG_APP};
    color: {TEXT_DIM};
    border-bottom: 1px solid {BORDER};
    padding: 2px 4px;
}}

QMenuBar::item {{
    background: transparent;
    padding: 5px 11px;
    border-radius: 4px;
}}

QMenuBar::item:selected {{
    background-color: {BG_RAISED};
    color: {TEXT};
}}

QMenu {{
    background-color: {BG_RAISED};
    color: {TEXT};
    border: 1px solid {BORDER_FOCUS};
    border-radius: {RADIUS}px;
    padding: 5px;
}}

QMenu::item {{
    padding: 7px 26px 7px 14px;
    border-radius: 4px;
}}

QMenu::item:selected {{
    background-color: {ACCENT_DIM};
    color: #ffffff;
}}

QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 5px 8px;
}}

QStatusBar {{
    background-color: {BG_APP};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER};
}}

QStatusBar::item {{
    border: none;
}}

QMessageBox, QInputDialog, QColorDialog, QFileDialog {{
    background-color: {BG_PANEL};
}}

QMessageBox QLabel, QInputDialog QLabel {{
    color: {TEXT};
}}
"""
