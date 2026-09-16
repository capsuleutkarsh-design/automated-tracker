"""
Professional VFX Studio Theme & Style System
Authentic desktop workstation styling inspired by Foundry Nuke, 3DEqualizer & Mocha Pro.
Flat seamless inspector panels, native menu & status bars, zero SaaS card boxes.
"""

DARK_STUDIO_QSS = """
/* ==============================================================================
   WORKSPACE & TYPOGRAPHY
   ============================================================================== */
QMainWindow {
    background-color: #14151a;
}

QWidget {
    color: #cfd2d9;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    font-size: 11.5px;
}

/* Ensure ALL labels and text are 100% transparent */
QLabel {
    background-color: transparent;
    color: #c0c4cc;
}

QCheckBox, QRadioButton {
    background-color: transparent;
    spacing: 7px;
    color: #b8bcc6;
    font-size: 11px;
}

/* Tooltips */
QToolTip {
    background-color: #21242d;
    color: #f3f4f6;
    border: 1px solid #3b3f4d;
    border-radius: 3px;
    padding: 4px 8px;
    font-size: 11px;
}

/* ==============================================================================
   NATIVE MENU BAR & POPUP MENUS
   ============================================================================== */
QMenuBar {
    background-color: #14151a;
    color: #b0b5be;
    border-bottom: 1px solid #232530;
    padding: 2px 4px;
    font-size: 11.5px;
}

QMenuBar::item {
    background: transparent;
    padding: 4px 10px;
    border-radius: 3px;
}

QMenuBar::item:selected {
    background-color: #222530;
    color: #ffffff;
}

QMenuBar::item:pressed {
    background-color: #1c1e27;
}

QMenu {
    background-color: #191b22;
    color: #d1d5db;
    border: 1px solid #2c2f3c;
    border-radius: 4px;
    padding: 4px 0px;
}

QMenu::item {
    padding: 5px 24px 5px 16px;
}

QMenu::item:selected {
    background-color: #262936;
    color: #38bdf8;
}

QMenu::separator {
    height: 1px;
    background-color: #262833;
    margin: 4px 8px;
}

/* ==============================================================================
   BOTTOM STATUS BAR
   ============================================================================== */
QStatusBar {
    background-color: #131418;
    color: #7d8390;
    border-top: 1px solid #21232d;
    font-size: 11px;
    min-height: 22px;
}

QStatusBar::item {
    border: none;
}

QLabel#statusChip {
    background-color: #1a1c24;
    color: #8b929e;
    border: 1px solid #262833;
    border-radius: 3px;
    padding: 1px 8px;
    font-size: 10.5px;
    font-weight: 600;
}

/* ==============================================================================
   STUDIO WORKSPACE SWITCHER (TABS)
   ============================================================================== */
QTabWidget::pane {
    border: 1px solid #232530;
    background-color: rgba(22, 23, 30, 0.88);
    top: -1px;
}

QTabBar {
    background: transparent;
    qproperty-drawBase: 0;
}

QTabBar::tab {
    background-color: rgba(17, 18, 23, 0.92);
    color: #7b818d;
    padding: 6px 18px;
    border-top-left-radius: 3px;
    border-top-right-radius: 3px;
    font-weight: 600;
    font-size: 11.5px;
    letter-spacing: 0.3px;
    margin-right: 2px;
    border: 1px solid #1f2129;
    border-bottom: none;
}

QTabBar::tab:selected {
    background-color: rgba(22, 23, 30, 0.94);
    color: #38bdf8;
    border: 1px solid #232530;
    border-top: 2px solid #0ea5e9;
    border-bottom: 1px solid rgba(22, 23, 30, 0.94);
    font-weight: 700;
}

QTabBar::tab:hover:!selected {
    background-color: #1a1b23;
    color: #d1d5db;
    border-color: #282b37;
}

/* ==============================================================================
   FLAT INSPECTOR SECTIONS (NUKE STYLE - NO CARD BOXES)
   ============================================================================== */
QGroupBox {
    border: none;
    border-top: 1px solid #282b38;
    margin-top: 16px;
    padding-top: 12px;
    font-weight: 700;
    font-size: 10px;
    color: #6b7080;
    background-color: transparent;
    letter-spacing: 0.8px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0px;
    top: -2px;
    color: #0ea5e9;
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.8px;
    background-color: transparent;
    border: none;
}

/* ==============================================================================
   BUTTONS & ACTIONS (SOLID & DIGNIFIED)
   ============================================================================== */
QPushButton {
    background-color: #21232d;
    color: #d1d5db;
    border: 1px solid #2e3140;
    border-radius: 3px;
    padding: 3px 10px;
    font-weight: 500;
    font-size: 11px;
    min-height: 22px;
}

QPushButton:hover {
    background-color: #292d3a;
    border-color: #42475a;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #171820;
    border-color: #20222b;
}

QPushButton:checked {
    background-color: #202838;
    border: 1px solid #0284c7;
    color: #38bdf8;
    font-weight: 600;
}

QPushButton:disabled {
    background-color: #16171d;
    color: #434752;
    border-color: #1b1d24;
}

/* Primary Execution CTA */
QPushButton#primaryBtn {
    background-color: #0369a1;
    border: 1px solid #0284c7;
    border-radius: 3px;
    color: #ffffff;
    font-weight: 700;
    font-size: 11.5px;
    letter-spacing: 0.4px;
}

QPushButton#primaryBtn:hover {
    background-color: #0284c7;
    border-color: #38bdf8;
}

QPushButton#primaryBtn:pressed {
    background-color: #075985;
}

QPushButton#primaryBtn:disabled {
    background-color: #161a22;
    color: #3e4856;
    border-color: #1c212a;
}

/* Stop / Cancel Action */
QPushButton#stopBtn {
    background-color: #261619;
    border: 1px solid #4a1920;
    border-radius: 3px;
    color: #fca5a5;
    font-weight: 600;
    font-size: 11px;
}

QPushButton#stopBtn:hover {
    background-color: #991b1b;
    border-color: #dc2626;
    color: #ffffff;
}

QPushButton#stopBtn:disabled {
    background-color: #161416;
    color: #3d292c;
    border-color: #1c1719;
}

/* Export: Blender */
QPushButton#blenderBtn {
    background-color: #221812;
    border: 1px solid #4d2612;
    border-radius: 3px;
    color: #fdba74;
    font-weight: 600;
    font-size: 11px;
}

QPushButton#blenderBtn:hover {
    background-color: #9a3412;
    border-color: #ea580c;
    color: #ffffff;
}

/* Export: Nuke */
QPushButton#nukeBtn {
    background-color: #221d10;
    border: 1px solid #4d3d12;
    border-radius: 3px;
    color: #fde047;
    font-weight: 600;
    font-size: 11px;
}

QPushButton#nukeBtn:hover {
    background-color: #854d0e;
    border-color: #ca8a04;
    color: #ffffff;
}

/* ==============================================================================
   FORM CONTROLS & DATA INPUTS
   ============================================================================== */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background-color: #14151b;
    border: 1px solid #282b37;
    border-radius: 3px;
    padding: 3px 7px;
    min-height: 20px;
    color: #f3f4f6;
    selection-background-color: #0284c7;
    selection-color: #ffffff;
}

QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
    border-color: #3b4050;
    background-color: #16171f;
}

QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border: 1px solid #0284c7;
    background-color: #16171f;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid #282b37;
    background-color: #191b23;
}

QComboBox::down-arrow {
    width: 0;
    height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid #7c8290;
}

QComboBox QAbstractItemView {
    background-color: #15161d;
    border: 1px solid #2b2e3c;
    border-radius: 3px;
    color: #e5e7eb;
    selection-background-color: #1e2230;
    selection-color: #38bdf8;
    padding: 2px;
    outline: none;
}

/* SpinBox arrows */
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 15px;
    border-left: 1px solid #282b37;
    background-color: #191b23;
}

QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 15px;
    border-left: 1px solid #282b37;
    background-color: #191b23;
}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #262936;
}

/* CheckBoxes & RadioButtons */
QCheckBox:hover, QRadioButton:hover {
    color: #ffffff;
}

QCheckBox::indicator {
    width: 13px;
    height: 13px;
    border: 1px solid #333744;
    border-radius: 2px;
    background-color: #14151b;
}

QCheckBox::indicator:hover {
    border-color: #0284c7;
}

QCheckBox::indicator:checked {
    background-color: #0284c7;
    border-color: #38bdf8;
}

QRadioButton::indicator {
    width: 13px;
    height: 13px;
    border: 1px solid #333744;
    border-radius: 6px;
    background-color: #14151b;
}

QRadioButton::indicator:checked {
    background-color: #0284c7;
    border: 3px solid #14151b;
}

/* ==============================================================================
   TABLE & SPREADSHEET
   ============================================================================== */
QTableWidget {
    background-color: #14151b;
    border: 1px solid #232530;
    border-radius: 3px;
    gridline-color: #191a22;
    color: #d1d5db;
    selection-background-color: #1c2130;
    selection-color: #38bdf8;
    outline: none;
}

QTableWidget::item {
    padding: 4px 6px;
    border-bottom: 1px solid #181920;
}

QTableWidget::item:selected {
    background-color: #1c2130;
    color: #38bdf8;
    font-weight: 600;
}

QHeaderView {
    background-color: #14151b;
    border: none;
}

QHeaderView::section {
    background-color: #171820;
    color: #7b818e;
    padding: 4px 6px;
    border: none;
    border-bottom: 1px solid #232530;
    border-right: 1px solid #1c1d25;
    font-weight: 700;
    font-size: 10px;
    letter-spacing: 0.4px;
    text-transform: uppercase;
}

QHeaderView::section:vertical {
    background-color: #14151b;
    border-right: 1px solid #232530;
    border-bottom: 1px solid #181920;
    color: #555963;
}

QTableCornerButton::section {
    background-color: #171820;
    border: none;
    border-bottom: 1px solid #232530;
    border-right: 1px solid #1c1d25;
}

/* ==============================================================================
   DIAGNOSTICS & LOG CONSOLE
   ============================================================================== */
QTextEdit {
    background-color: #111217;
    border: 1px solid #20222b;
    border-radius: 3px;
    font-family: 'Consolas', 'Cascadia Mono', 'Courier New', monospace;
    font-size: 11px;
    color: #9297a2;
    padding: 5px;
    line-height: 1.35;
}

/* ==============================================================================
   PROGRESS BAR
   ============================================================================== */
QProgressBar {
    border: 1px solid #232530;
    border-radius: 3px;
    text-align: center;
    background-color: #111217;
    color: #f3f4f6;
    font-weight: 600;
    font-size: 10.5px;
    height: 18px;
}

QProgressBar::chunk {
    background-color: #0284c7;
    border-radius: 2px;
}

/* ==============================================================================
   SLIDERS & TRANSPORT
   ============================================================================== */
QSlider::groove:horizontal {
    height: 3px;
    background: #232530;
    border-radius: 1px;
}

QSlider::sub-page:horizontal {
    background: #0284c7;
    border-radius: 1px;
}

QSlider::handle:horizontal {
    background: #d1d5db;
    border: 1px solid #0284c7;
    width: 10px;
    margin-top: -3px;
    margin-bottom: -3px;
    border-radius: 5px;
}

QSlider::handle:horizontal:hover {
    background: #38bdf8;
}

/* ==============================================================================
   SPLITTER & SCROLLBARS
   ============================================================================== */
QSplitter::handle {
    background-color: #181920;
    width: 3px;
}

QSplitter::handle:hover {
    background-color: #0284c7;
}

QScrollBar:vertical {
    border: none;
    background: #111216;
    width: 6px;
    margin: 0px;
}

QScrollBar::handle:vertical {
    background: #262834;
    min-height: 20px;
    border-radius: 3px;
}

QScrollBar::handle:vertical:hover {
    background: #3e4254;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    border: none;
    background: #111216;
    height: 6px;
    margin: 0px;
}

QScrollBar::handle:horizontal {
    background: #262834;
    min-width: 20px;
    border-radius: 3px;
}

QScrollBar::handle:horizontal:hover {
    background: #3e4254;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
"""
