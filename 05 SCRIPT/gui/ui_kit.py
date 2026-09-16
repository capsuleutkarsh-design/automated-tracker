"""
Small layout primitives shared by both tabs.

The point is that a card, a form row and a button row are defined once, so the
3D and 2D tabs cannot drift apart visually.
"""

from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QSizePolicy, QScrollArea, QPushButton, QComboBox,
)
from PySide6.QtCore import Qt

from gui.theme import ROW_H

# Width of the label column in every form row, so fields line up across cards.
LABEL_W = 124


def card(title=None, parent=None):
    """
    A titled panel. Returns (frame, body_layout) - add your widgets to the body.
    """
    frame = QFrame(parent)
    frame.setObjectName("card")
    outer = QVBoxLayout(frame)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    if title is not None:
        header = QFrame()
        header.setObjectName("cardHeader")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(12, 7, 8, 7)
        hl.setSpacing(6)
        lbl = QLabel(title)
        lbl.setObjectName("sectionTitle")
        hl.addWidget(lbl)
        hl.addStretch(1)
        frame.header_layout = hl
        outer.addWidget(header)

        line = QFrame()
        line.setObjectName("divider")
        outer.addWidget(line)
    else:
        frame.header_layout = None

    body = QVBoxLayout()
    body.setContentsMargins(12, 11, 12, 12)
    body.setSpacing(8)
    outer.addLayout(body)
    frame.body = body
    return frame, body


def form_row(layout, label_text, widget, hint=None):
    """A left-aligned fixed-width label with its field. Keeps columns straight."""
    row = QHBoxLayout()
    row.setSpacing(9)
    lbl = QLabel(label_text)
    lbl.setObjectName("fieldLabel")
    lbl.setFixedWidth(LABEL_W)
    lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    row.addWidget(lbl)
    row.addWidget(widget, 1)
    if hint is not None:
        row.addWidget(hint, 0)
    layout.addLayout(row)
    return row


def button_row(layout, buttons, spacing=6, compact=True):
    """Equal-weight buttons across the full width."""
    row = QHBoxLayout()
    row.setSpacing(spacing)
    for b in buttons:
        if compact:
            b.setObjectName("compact")
        row.addWidget(b, 1)
    layout.addLayout(row)
    return row


def checkbox_grid(layout, checkboxes, columns=2):
    """Checkboxes in a grid - a single column of five wastes a lot of height."""
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(14)
    grid.setVerticalSpacing(2)
    for i, cb in enumerate(checkboxes):
        grid.addWidget(cb, i // columns, i % columns)
    for c in range(columns):
        grid.setColumnStretch(c, 1)
    layout.addLayout(grid)
    return grid


def divider(layout, vertical=False):
    line = QFrame()
    line.setObjectName("vDivider" if vertical else "divider")
    layout.addWidget(line)
    return line


def strip(spacing=8, margins=(10, 8, 10, 8)):
    """A horizontal toolbar panel, e.g. the transport bar under the viewport."""
    frame = QFrame()
    frame.setObjectName("strip")
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return frame, lay


def scrollable_strip(frame, height=46):
    """
    Wraps a toolbar strip so a narrow window scrolls it sideways rather than
    chopping the controls off the right-hand end.
    """
    area = QScrollArea()
    area.setObjectName("stripScroll")
    area.setWidget(frame)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    area.setFixedHeight(height)
    area.viewport().setObjectName("stripViewport")
    area.viewport().setAutoFillBackground(False)
    return area


def group_label(text):
    lbl = QLabel(text)
    lbl.setObjectName("sectionTitle")
    return lbl


def hint_label(text, wrap=True):
    lbl = QLabel(text)
    lbl.setObjectName("hint")
    lbl.setWordWrap(wrap)
    return lbl


def tame_combos(root, shrink=True):
    """
    Stop combo boxes from demanding the width of their longest entry.

    A QComboBox reports a size hint wide enough for its longest item, so one
    entry like "SIMPLE_RADIAL (Standard / Recommended)" drags the whole
    inspector wider than the panel and clips its neighbours.
    """
    for combo in root.findChildren(QComboBox):
        # NOT setMinimumContentsLength: that narrows the combo's *content* rect,
        # which drags the drop-down arrow in to sit on top of the text. An Ignored
        # size policy plus a plain minimum width shrinks it without that side effect.
        combo.setMinimumWidth(90)
        # A combo given an explicit width already knows how much room it wants;
        # forcing Ignored on it would let a layout squeeze it to nothing.
        has_fixed_width = combo.minimumWidth() == combo.maximumWidth() != 0
        if shrink and not has_fixed_width:
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        if not combo.toolTip():
            combo.setToolTip(combo.currentText())


def inspector_scroll(content_widget, width=430):
    """
    Wraps the left-hand inspector so a small window scrolls instead of clipping
    controls off the bottom.
    """
    tame_combos(content_widget)
    content_widget.setObjectName("inspectorBody")

    area = QScrollArea()
    area.setObjectName("inspectorScroll")
    area.setWidgetResizable(True)
    area.setWidget(content_widget)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    area.setFrameShape(QFrame.NoFrame)
    # The viewport paints with the default (light) palette brush unless told not to.
    area.viewport().setObjectName("inspectorViewport")
    area.viewport().setAutoFillBackground(False)
    area.setMinimumWidth(340)
    area.setMaximumWidth(width + 40)
    return area


def make_button(text, tooltip=None, object_name=None, checkable=False):
    b = QPushButton(text)
    if tooltip:
        b.setToolTip(tooltip)
    if object_name:
        b.setObjectName(object_name)
    if checkable:
        b.setCheckable(True)
    return b


def spacer(h=4):
    w = QWidget()
    w.setFixedHeight(h)
    w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return w
