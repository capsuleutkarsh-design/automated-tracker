"""
Tracking layers: the list, the four buttons under it, and the rules.

A layer is the unit of work on the 2D tab - its own points or grid, its own
roto, its own confidence, its own exported files - so adding, deleting,
renaming and recolouring one is a small world of its own, and it used to be
spread through the main window between a media-pool refresh and a worker
signal.

The refresh is the important part. Every handler that adds, deletes, renames,
recolours or re-points a layer ends in `refresh_layer_list`, which is therefore
the single place the shot's project file is marked dirty. Anything new that
changes a layer goes through it for the same reason.

The mode radios, the grid size and the confidence spin box live in other cards
on the same tab, because that is where the artist looks for them, but they are
properties OF the active layer - so they are read and written here, through the
widgets the tab registered.
"""

from PySide6.QtWidgets import (
    QColorDialog, QInputDialog, QListWidget, QListWidgetItem, QMessageBox,
)
from PySide6.QtGui import QColor

from core.tracking_layer import TrackingLayer
from gui.ui_kit import card, button_row, make_button


class LayerPanel:
    """
    The Tracking Layers card and everything that edits a layer.

    Built before the tab that shows it: `build_card` is called by
    build_2d_tab at the point the card belongs in the inspector.
    """

    def __init__(self, ctx):
        self.ctx = ctx

    # =====================================================================
    # THE CARD
    # =====================================================================
    def build_card(self):
        """The inspector card, with every widget registered under its old name."""
        ctx = self.ctx
        layer_card, lbody = card("Tracking Layers")

        ctx.ui.layer_list = QListWidget()
        ctx.ui.layer_list.setMinimumHeight(92)
        ctx.ui.layer_list.setMaximumHeight(150)
        ctx.ui.layer_list.setToolTip("Each layer solves independently and exports its own tracks")
        ctx.ui.layer_list.currentRowChanged.connect(self.on_layer_selected)
        lbody.addWidget(ctx.ui.layer_list)

        ctx.ui.btn_add_layer = make_button("+ Add", "Add a tracking layer")
        ctx.ui.btn_add_layer.clicked.connect(self.add_new_layer)
        ctx.ui.btn_del_layer = make_button("Delete", "Remove the active layer")
        ctx.ui.btn_del_layer.clicked.connect(self.delete_active_layer)
        ctx.ui.btn_rename_layer = make_button("Rename", "Rename the active layer")
        ctx.ui.btn_rename_layer.clicked.connect(self.rename_active_layer)
        ctx.ui.btn_color_layer = make_button("Colour", "Change the layer's overlay colour")
        ctx.ui.btn_color_layer.clicked.connect(self.change_layer_color)
        button_row(lbody, [ctx.ui.btn_add_layer, ctx.ui.btn_del_layer,
                           ctx.ui.btn_rename_layer, ctx.ui.btn_color_layer])

        ctx.ui.btn_export_roto = make_button(
            "Export Roto to Nuke",
            "Export animated rotomasks as a Foundry Nuke Roto node (.nk)")
        ctx.ui.btn_export_roto.clicked.connect(ctx.export_active_masks_to_nuke_roto)
        lbody.addWidget(ctx.ui.btn_export_roto)
        return layer_card

    # =====================================================================
    # THE LIST
    # =====================================================================
    def refresh_layer_list(self):
        ctx = self.ctx
        ctx.ui.layer_list.blockSignals(True)
        ctx.ui.layer_list.clear()
        for idx, layer in enumerate(ctx.canvas.layers):
            inc_cnt = len(layer.inclusion_masks)
            exc_cnt = len(layer.exclusion_masks)
            pt_cnt = len(layer.points) if layer.mode != "grid" else (layer.grid_size ** 2)
            mask_summary = f"{exc_cnt} exc, {inc_cnt} inc" if (exc_cnt or inc_cnt) else "No masks"
            item_text = f"■ {layer.name} ({layer.mode.upper()}: {pt_cnt} pts | {mask_summary})"
            item = QListWidgetItem(item_text)
            item.setForeground(QColor(layer.color))
            ctx.ui.layer_list.addItem(item)
        if 0 <= ctx.canvas.active_layer_idx < ctx.ui.layer_list.count():
            ctx.ui.layer_list.setCurrentRow(ctx.canvas.active_layer_idx)
        ctx.ui.layer_list.blockSignals(False)
        # Every handler that adds, deletes, renames, recolours or re-points a
        # layer ends here, so this is the one place the project needs marking.
        ctx.schedule_project_save()

    def on_layer_selected(self, row):
        ctx = self.ctx
        if row < 0 or row >= len(ctx.canvas.layers):
            return
        ctx.canvas.active_layer_idx = row
        cur_l = ctx.canvas.active_layer
        if not cur_l:
            return

        ctx.ui.radio_grid.blockSignals(True)
        ctx.ui.radio_points.blockSignals(True)
        ctx.ui.radio_cornerpin.blockSignals(True)
        if cur_l.mode == "grid":
            ctx.ui.radio_grid.setChecked(True)
            ctx.ui.grid_settings_widget.setVisible(True)
        elif cur_l.mode == "points":
            ctx.ui.radio_points.setChecked(True)
            ctx.ui.grid_settings_widget.setVisible(False)
        elif cur_l.mode == "cornerpin":
            ctx.ui.radio_cornerpin.setChecked(True)
            ctx.ui.grid_settings_widget.setVisible(False)
        ctx.ui.radio_grid.blockSignals(False)
        ctx.ui.radio_points.blockSignals(False)
        ctx.ui.radio_cornerpin.blockSignals(False)

        ctx.ui.spin_grid_size.blockSignals(True)
        ctx.ui.spin_grid_size.setValue(cur_l.grid_size)
        ctx.ui.lbl_total_pts.setText(f"{cur_l.grid_size ** 2} pts")
        ctx.ui.spin_grid_size.blockSignals(False)

        ctx.ui.spin_min_conf.blockSignals(True)
        ctx.ui.spin_min_conf.setValue(cur_l.min_confidence)
        ctx.ui.spin_min_conf.blockSignals(False)

        ctx.canvas.update()
        ctx.log_2d(f"Switched active layer to: [{cur_l.name}]", cur_l.color)

    # =====================================================================
    # ADD, DELETE, RENAME, RECOLOUR
    # =====================================================================
    def add_new_layer(self):
        ctx = self.ctx
        palette = ["#00d2ff", "#ffaa00", "#ff3388", "#00ff88", "#aa55ff", "#ffdd00", "#00e5ff"]
        idx = len(ctx.canvas.layers) + 1
        col = palette[(idx - 1) % len(palette)]
        name, ok = QInputDialog.getText(ctx.dialog_parent(), "Add Tracking Layer",
                                        "Layer Name:", text=f"Layer {idx}")
        if ok and name.strip():
            new_l = TrackingLayer(name.strip(), col, "grid")
            ctx.canvas.layers.append(new_l)
            ctx.canvas.active_layer_idx = len(ctx.canvas.layers) - 1
            self.refresh_layer_list()
            self.on_layer_selected(ctx.canvas.active_layer_idx)
            ctx.log_2d(f"➕ Added new tracking layer: [{name.strip()}]", ctx.OK)

    def delete_active_layer(self):
        ctx = self.ctx
        if len(ctx.canvas.layers) <= 1:
            QMessageBox.information(ctx.dialog_parent(), "Cannot Delete",
                                    "At least one tracking layer must remain.")
            return
        cur_idx = ctx.canvas.active_layer_idx
        del_name = ctx.canvas.layers[cur_idx].name
        del ctx.canvas.layers[cur_idx]
        ctx.canvas.active_layer_idx = max(0, cur_idx - 1)
        self.refresh_layer_list()
        self.on_layer_selected(ctx.canvas.active_layer_idx)
        ctx.log_2d(f"🗑 Deleted layer [{del_name}].", ctx.ERR)

    def rename_active_layer(self):
        ctx = self.ctx
        cur_l = ctx.canvas.active_layer
        if not cur_l:
            return
        name, ok = QInputDialog.getText(ctx.dialog_parent(), "Rename Layer",
                                        "New Name:", text=cur_l.name)
        if ok and name.strip():
            cur_l.name = name.strip()
            self.refresh_layer_list()
            ctx.log_2d(f"✏️ Renamed layer to [{cur_l.name}].", ctx.ACCENT)

    def change_layer_color(self):
        ctx = self.ctx
        cur_l = ctx.canvas.active_layer
        if not cur_l:
            return
        c = QColorDialog.getColor(QColor(cur_l.color), ctx.dialog_parent(), "Select Layer Color")
        if c.isValid():
            cur_l.color = c.name()
            self.refresh_layer_list()
            ctx.canvas.update()
            ctx.log_2d(f"🎨 Updated layer color for [{cur_l.name}] to {cur_l.color}.", cur_l.color)

    # =====================================================================
    # PROPERTIES OF THE ACTIVE LAYER THAT OTHER CARDS EDIT
    # =====================================================================
    def on_min_conf_changed(self, val):
        lay = self.ctx.canvas.active_layer
        if lay:
            lay.min_confidence = float(val)
            self.refresh_layer_list()

    def on_grid_size_changed(self, val):
        ctx = self.ctx
        ctx.ui.lbl_total_pts.setText(f"{val*val} pts")
        if ctx.canvas.active_layer:
            ctx.canvas.active_layer.grid_size = val
            self.refresh_layer_list()

    def on_2d_mode_toggled(self):
        ctx = self.ctx
        cur_l = ctx.canvas.active_layer
        if not cur_l:
            return
        if ctx.ui.radio_grid.isChecked():
            cur_l.mode = "grid"
            ctx.ui.grid_settings_widget.setVisible(True)
        elif ctx.ui.radio_points.isChecked():
            cur_l.mode = "points"
            ctx.ui.grid_settings_widget.setVisible(False)
        elif ctx.ui.radio_cornerpin.isChecked():
            cur_l.mode = "cornerpin"
            ctx.ui.grid_settings_widget.setVisible(False)
        self.refresh_layer_list()
