"""
VFX Studio Dark Theme & Style System
Provides unified QSS stylesheets, color palettes, and UI tokens.
Inspired by modern high-end VFX & 3D workstations (DaVinci Resolve, Unreal Engine 5, Nuke).
"""

DARK_STUDIO_QSS = """
/* ==============================================================================
   GLOBAL WORKSPACE & TYPOGRAPHY
   ============================================================================== */
QMainWindow, QWidget {
    background-color: #0c0e14;
    color: #e2e8f0;
    font-family: 'Segoe UI Variable Display', 'Segoe UI', -apple-system, BlinkMacSystemFont, 'SF Pro Display', Roboto, sans-serif;
    font-size: 12.5px;
}

/* Tooltips */
QToolTip {
    background-color: #141926;
    color: #f8fafc;
    border: 1px solid #2e3b52;
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 11.5px;
}

/* ==============================================================================
   TAB SYSTEM — FLOATING SEGMENTED PILLS
   ============================================================================== */
QTabWidget::pane {
    border: 1px solid #1a2234;
    background-color: #0f131c;
    border-radius: 10px;
    top: -1px;
}

QTabBar {
    background: transparent;
    qproperty-drawBase: 0;
}

QTabBar::tab {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #161c28, stop:1 #111622);
    color: #94a3b8;
    padding: 9px 24px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 600;
    font-size: 12.5px;
    margin-right: 4px;
    border: 1px solid #1e2638;
    border-bottom: none;
    min-height: 20px;
}

QTabBar::tab:selected {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1e283d, stop:1 #0f131c);
    color: #38bdf8;
    border: 1px solid #2b3952;
    border-top: 2px solid #38bdf8;
    border-bottom: 1px solid #0f131c;
    font-weight: 700;
}

QTabBar::tab:hover:!selected {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1c2434, stop:1 #141a26);
    color: #f1f5f9;
    border-color: #273349;
}

/* ==============================================================================
   PANEL CARDS (QGroupBox)
   ============================================================================== */
QGroupBox {
    border: 1px solid #1c2436;
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
    font-weight: 600;
    font-size: 11.5px;
    color: #94a3b8;
    background-color: #121622;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 3px 10px;
    background-color: #1a2234;
    border: 1px solid #28354c;
    border-radius: 5px;
    color: #38bdf8;
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.4px;
}

/* ==============================================================================
   BUTTONS & CTAs
   ============================================================================== */
QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1c2436, stop:1 #141a27);
    color: #e2e8f0;
    border: 1px solid #28354c;
    border-radius: 6px;
    padding: 5px 12px;
    font-weight: 500;
    min-height: 24px;
}

QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #253147, stop:1 #1b2334);
    border-color: #38bdf8;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #0f141f;
    border-color: #1a2232;
}

QPushButton:checked {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0284c7, stop:1 #0369a1);
    border-color: #38bdf8;
    color: #ffffff;
    font-weight: bold;
}

QPushButton:disabled {
    background-color: #10131b;
    color: #475569;
    border-color: #181f2c;
}

/* Primary Execution Action */
QPushButton#primaryBtn {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:0.5 #2563eb, stop:1 #4f46e5);
    border: 1px solid #38bdf8;
    border-top: 1px solid #7dd3fc;
    border-radius: 8px;
    color: #ffffff;
    font-weight: 700;
    font-size: 12.5px;
    letter-spacing: 0.5px;
}

QPushButton#primaryBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0369a1, stop:0.5 #1d4ed8, stop:1 #4338ca);
    border-color: #93c5fd;
    border-top-color: #bae6fd;
}

QPushButton#primaryBtn:pressed {
    background-color: #1e3a8a;
    border-color: #2563eb;
}

QPushButton#primaryBtn:disabled {
    background: #141b2b;
    color: #475569;
    border-color: #1e283d;
}

/* Cancel / Abort Action */
QPushButton#stopBtn {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2b1318, stop:1 #1c0d10);
    border: 1px solid #881337;
    border-radius: 8px;
    color: #fda4af;
    font-weight: 700;
    font-size: 12px;
}

QPushButton#stopBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #991b1b, stop:1 #7f1d1d);
    border-color: #f43f5e;
    color: #ffffff;
}

QPushButton#stopBtn:disabled {
    background-color: #130e10;
    color: #4c2229;
    border-color: #1e1316;
}

/* Studio Export: Blender */
QPushButton#blenderBtn {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2a1608, stop:1 #1a0e05);
    border: 1px solid #9a3412;
    border-radius: 6px;
    color: #fdba74;
    font-weight: 600;
    font-size: 11.5px;
}

QPushButton#blenderBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ea580c, stop:1 #c2410c);
    border-color: #fed7aa;
    color: #ffffff;
}

/* Studio Export: Nuke */
QPushButton#nukeBtn {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #261e06, stop:1 #171203);
    border: 1px solid #854d0e;
    border-radius: 6px;
    color: #fde047;
    font-weight: 600;
    font-size: 11.5px;
}

QPushButton#nukeBtn:hover {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #ca8a04, stop:1 #a16207);
    border-color: #fef08a;
    color: #ffffff;
}

/* ==============================================================================
   FORM CONTROLS & INPUTS
   ============================================================================== */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #131722;
    border: 1px solid #232d40;
    border-radius: 6px;
    padding: 5px 10px;
    min-height: 22px;
    color: #f8fafc;
    selection-background-color: #0284c7;
    selection-color: #ffffff;
}

QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
    border-color: #334155;
    background-color: #161b28;
}

QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border: 1px solid #38bdf8;
    background-color: #161d2c;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid #232d40;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
    background-color: #171c2a;
}

QComboBox::down-arrow {
    width: 0;
    height: 0;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #94a3b8;
    margin-right: 2px;
}

QComboBox::down-arrow:hover {
    border-top: 5px solid #38bdf8;
}

QComboBox QAbstractItemView {
    background-color: #121622;
    border: 1px solid #28354c;
    border-radius: 6px;
    color: #e2e8f0;
    selection-background-color: #1e283d;
    selection-color: #38bdf8;
    padding: 4px;
    outline: none;
}

/* SpinBox arrows */
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid #232d40;
    background-color: #171c2a;
    border-top-right-radius: 5px;
}

QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    border-left: 1px solid #232d40;
    background-color: #171c2a;
    border-bottom-right-radius: 5px;
}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #243046;
}

/* CheckBoxes & RadioButtons */
QCheckBox, QRadioButton {
    spacing: 8px;
    color: #cbd5e1;
    font-size: 12px;
}

QCheckBox:hover, QRadioButton:hover {
    color: #ffffff;
}

QCheckBox::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #2e3b52;
    border-radius: 4px;
    background-color: #131722;
}

QCheckBox::indicator:hover {
    border-color: #38bdf8;
    background-color: #171d2b;
}

QCheckBox::indicator:checked {
    background-color: #0284c7;
    border-color: #38bdf8;
}

QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border: 1px solid #2e3b52;
    border-radius: 8px;
    background-color: #131722;
}

QRadioButton::indicator:hover {
    border-color: #38bdf8;
}

QRadioButton::indicator:checked {
    background-color: #0284c7;
    border: 3px solid #131722;
    outline: 1px solid #38bdf8;
}

/* ==============================================================================
   TABLE & DATA VIEWS
   ============================================================================== */
QTableWidget {
    background-color: #0e1118;
    border: 1px solid #1c2436;
    border-radius: 8px;
    gridline-color: #161b28;
    color: #cbd5e1;
    selection-background-color: #1a273f;
    selection-color: #38bdf8;
    outline: none;
}

QTableWidget::item {
    padding: 6px 8px;
    border-bottom: 1px solid #141924;
}

QTableWidget::item:selected {
    background-color: #162438;
    color: #38bdf8;
    font-weight: 600;
}

QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #171c2a, stop:1 #121622);
    color: #94a3b8;
    padding: 7px 10px;
    border: none;
    border-bottom: 1px solid #222c3f;
    border-right: 1px solid #1a2232;
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 0.3px;
}

QTableCornerButton::section {
    background-color: #141925;
    border: none;
    border-bottom: 1px solid #222c3f;
}

/* ==============================================================================
   DIAGNOSTICS & LOG CONSOLE
   ============================================================================== */
QTextEdit {
    background-color: #090c12;
    border: 1px solid #1a2234;
    border-radius: 8px;
    font-family: 'Consolas', 'Cascadia Code', 'JetBrains Mono', 'Courier New', monospace;
    font-size: 11.5px;
    color: #94a3b8;
    padding: 8px;
    line-height: 1.4;
}

/* ==============================================================================
   PROGRESS BAR
   ============================================================================== */
QProgressBar {
    border: 1px solid #1c2538;
    border-radius: 7px;
    text-align: center;
    background-color: #0b0e14;
    color: #f8fafc;
    font-weight: 700;
    font-size: 11.5px;
    height: 22px;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:0.5 #2563eb, stop:1 #38bdf8);
    border-radius: 6px;
}

/* ==============================================================================
   SLIDERS & TRANSPORT CONTROLS
   ============================================================================== */
QSlider::groove:horizontal {
    height: 5px;
    background: #192233;
    border-radius: 2.5px;
}

QSlider::sub-page:horizontal {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0284c7, stop:1 #38bdf8);
    border-radius: 2.5px;
}

QSlider::handle:horizontal {
    background: #ffffff;
    border: 2px solid #38bdf8;
    width: 14px;
    margin-top: -5px;
    margin-bottom: -5px;
    border-radius: 7px;
}

QSlider::handle:horizontal:hover {
    background: #38bdf8;
    border: 2px solid #ffffff;
    width: 16px;
    margin-top: -6px;
    margin-bottom: -6px;
    border-radius: 8px;
}

/* ==============================================================================
   SPLITTER & SCROLLBARS
   ============================================================================== */
QSplitter::handle {
    background-color: #151a26;
    width: 4px;
}

QSplitter::handle:hover {
    background-color: #38bdf8;
}

QScrollBar:vertical {
    border: none;
    background: #0b0e14;
    width: 6px;
    margin: 0px;
    border-radius: 3px;
}

QScrollBar::handle:vertical {
    background: #243046;
    min-height: 24px;
    border-radius: 3px;
}

QScrollBar::handle:vertical:hover {
    background: #38bdf8;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    border: none;
    background: #0b0e14;
    height: 6px;
    margin: 0px;
    border-radius: 3px;
}

QScrollBar::handle:horizontal {
    background: #243046;
    min-width: 24px;
    border-radius: 3px;
}

QScrollBar::handle:horizontal:hover {
    background: #38bdf8;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
"""
