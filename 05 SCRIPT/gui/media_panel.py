"""
The media pool: what is in 02 VIDEOS, and what has been solved from it.

The table on the 3D tab and the clip combo on the 2D tab are two views of the
same list, so they are filled in one pass and a row selected in one picks the
clip in the other. Keeping that in one place is the point of this file.

What this does NOT own is the two background jobs the media pool starts:
copying clips in, and unpacking a clip's frame cache. Both are QThreads the
window holds and queues, so importing is asked for through
`ctx.import_media_files` and the window does the rest.
"""

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QHeaderView, QTableWidget, QTableWidgetItem,
)

from core.media_info import detect_sequence_start
from core.media_pool import find_latest_output, scan_media_pool, thumb_path
from core.proc import run_hidden
from gui.ui_kit import card, hint_label, make_button


class MediaPanel:
    """
    The Media Pool card, and everything that reads the folder behind it.

    Built by the window before the 3D tab, which asks it for the card at the
    point it belongs in the layout.
    """

    def __init__(self, ctx):
        self.ctx = ctx

    def _register(self, name, widget):
        """Keep the widget here, and publish it under the name the app uses."""
        setattr(self, name, widget)
        setattr(self.ctx.ui, name, widget)

    # =====================================================================
    # THE CARD
    # =====================================================================
    def build_card(self):
        """The media pool table, with its three header buttons."""
        media_card, mbody = card("Media Pool")

        btn_add = make_button("+ Add Media", "Copy video files into 02 VIDEOS", "compact")
        btn_add.clicked.connect(self.add_videos)
        btn_refresh = make_button("Refresh", "Rescan the media folder", "compact")
        btn_refresh.clicked.connect(self.refresh_videos)
        btn_open_videos = make_button("Open Folder", "Show 02 VIDEOS in Explorer", "compact")
        btn_open_videos.clicked.connect(self.open_videos_folder)
        for b in (btn_add, btn_refresh, btn_open_videos):
            media_card.header_layout.addWidget(b)

        self._register("table", QTableWidget(0, 3))
        self.table.setHorizontalHeaderLabels(["Item Name", "Size / Frames", "Tracking Status"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setMinimumHeight(150)
        self.table.setToolTip(
            "Select the shots to solve. With nothing selected, every shot in the\n"
            "folder is solved. Double-click a row to open its output."
        )
        self.table.itemSelectionChanged.connect(self.on_table_row_selected)
        self.table.itemDoubleClicked.connect(self.on_table_row_double_clicked)
        mbody.addWidget(self.table)
        mbody.addWidget(hint_label(
            "Select rows to solve only those shots — select nothing to solve all."))
        return media_card

    # =====================================================================
    # WHAT IS IN THE FOLDER
    # =====================================================================
    def add_videos(self):
        files, _ = QFileDialog.getOpenFileNames(
            self.ctx.dialog_parent(), "Select Video or Image Sequence Files", "", "Video & Image Files (*.mp4 *.mov *.avi *.mkv *.m4v *.exr *.png *.jpg *.jpeg *.tif *.tiff);;All Files (*.*)"
        )
        if files:
            self.ctx.import_media_files(files, overwrite=False)

    def refresh_videos(self):
        self.ctx.videos_dir.mkdir(parents=True, exist_ok=True)
        self.table.setRowCount(0)

        # Repopulating the combo fires currentTextChanged for every addItem,
        # and each one used to start a frame extractor (B1). Fill it silently
        # and select the clip once afterwards.
        previous = self.ctx.ui.combo_2d_video.currentText() or self.ctx.last_clip
        self.ctx.ui.combo_2d_video.blockSignals(True)
        self.ctx.ui.combo_2d_video.clear()

        items = scan_media_pool(self.ctx.videos_dir)

        for v in items:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(v.name))

            if v.is_file():
                size_mb = v.stat().st_size / (1024 * 1024)
                self.table.setItem(row, 1, QTableWidgetItem(f"{size_mb:.1f} MB"))
            else:
                count = len([s for s in v.iterdir() if s.is_file()])
                self.table.setItem(row, 1, QTableWidgetItem(f"{count} Frames"))

            scene_dir = self.ctx.scenes_dir / v.stem
            solved, _ = find_latest_output(
                scene_dir, "3D_CAMERA_TRACK", "sparse/cameras.txt", legacy_subdirs=("",))
            if solved:
                status_item = QTableWidgetItem("● Solved (3D Ready)")
                status_item.setForeground(QColor(self.ctx.OK))
                status_item.setTextAlignment(Qt.AlignCenter)
            else:
                status_item = QTableWidgetItem("○ Ready to Track")
                status_item.setForeground(QColor(self.ctx.ACCENT))
                status_item.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 2, status_item)

            self.ctx.ui.combo_2d_video.addItem(v.name)

        idx = self.ctx.ui.combo_2d_video.findText(previous) if previous else -1
        if idx >= 0:
            self.ctx.ui.combo_2d_video.setCurrentIndex(idx)
        self.ctx.ui.combo_2d_video.blockSignals(False)

        if items:
            self.ctx.on_2d_video_selected(self.ctx.ui.combo_2d_video.currentText())

        self.ctx.layers.refresh_layer_list()

    def update_video_status(self, video_name, status):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.text() == video_name:
                stat_item = self.table.item(row, 2)
                if not stat_item:
                    stat_item = QTableWidgetItem()
                    self.table.setItem(row, 2, stat_item)
                stat_item.setTextAlignment(Qt.AlignCenter)
                if "✔" in status or "Completed" in status:
                    stat_item.setText("● Solved (3D Ready)")
                    stat_item.setForeground(QColor(self.ctx.OK))
                elif "✖" in status or "Failed" in status or "Error" in status:
                    stat_item.setText("✕ " + status.replace("✖", "").strip())
                    stat_item.setForeground(QColor(self.ctx.ERR))
                elif "Processing" in status or "Extracting" in status or "Matching" in status or "Tracking" in status:
                    stat_item.setText("◌ " + status)
                    stat_item.setForeground(QColor(self.ctx.WARN))
                else:
                    stat_item.setText(status)
                    stat_item.setForeground(QColor(self.ctx.ACCENT))

    def open_videos_folder(self):
        self.ctx.videos_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.ctx.videos_dir)

    def open_scenes_folder(self):
        self.ctx.scenes_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.ctx.scenes_dir)

    def open_3d_output_folder(self):
        row = self.table.currentRow()
        if row >= 0:
            v_name = self.table.item(row, 0).text()
            shot_dir = self.ctx.scenes_dir / Path(v_name).stem
            track_dir = shot_dir / "3D_CAMERA_TRACK"
            if (track_dir / "_latest").exists():
                os.startfile(track_dir / "_latest")
            elif track_dir.exists():
                os.startfile(track_dir)
            elif (shot_dir / "sparse").exists():
                os.startfile(shot_dir)
            else:
                track_dir.mkdir(parents=True, exist_ok=True)
                os.startfile(track_dir)
        else:
            self.open_scenes_folder()

    def open_2d_output_folder(self):
        v_name = self.ctx.ui.combo_2d_video.currentText()
        if v_name:
            shot_dir = self.ctx.scenes_dir / Path(v_name).stem
            point_dir = shot_dir / "2D_POINT_TRACK"
            if (point_dir / "_latest").exists():
                os.startfile(point_dir / "_latest")
            elif point_dir.exists():
                os.startfile(point_dir)
            elif (shot_dir / "cotracker_2d").exists():
                os.startfile(shot_dir / "cotracker_2d")
            else:
                point_dir.mkdir(parents=True, exist_ok=True)
                os.startfile(point_dir)
        else:
            self.open_scenes_folder()

    def on_table_row_selected(self):
        row = self.table.currentRow()
        if row >= 0 and self.table.item(row, 0):
            v_name = self.table.item(row, 0).text()
            idx = self.ctx.ui.combo_2d_video.findText(v_name)
            if idx >= 0 and self.ctx.ui.combo_2d_video.currentIndex() != idx:
                self.ctx.ui.combo_2d_video.setCurrentIndex(idx)

            # A saved project already holds the start the artist settled on;
            # only guess from the file numbering when there is nothing saved.
            if self.ctx.project_loaded and self.ctx.project_shot == Path(v_name).stem:
                return

            # An image sequence carries its own frame numbering; use it so the
            # exported camera lands on the same frames as the plate.
            detected = detect_sequence_start(self.ctx.videos_dir / v_name, default=1)
            if detected != self.ctx.ui.spin_start_frame_3d.value():
                self.ctx.ui.spin_start_frame_3d.setValue(detected)
                if detected != 1:
                    self.ctx.log_3d(
                        f"Timeline start set to {detected} from the sequence numbering "
                        f"of '{v_name}'.", self.ctx.ACCENT)

    def on_table_row_double_clicked(self, item):
        row = item.row()
        if row >= 0 and self.table.item(row, 0):
            v_name = self.table.item(row, 0).text()
            shot_dir = self.ctx.scenes_dir / Path(v_name).stem
            track_dir = shot_dir / "3D_CAMERA_TRACK" / "_latest"
            if track_dir.exists():
                os.startfile(track_dir)
            elif shot_dir.exists():
                os.startfile(shot_dir)
            else:
                self.open_scenes_folder()

    def thumbnail_for(self, video_path, frame_idx, *seek_args):
        if Path(video_path).is_dir():
            return None
        """
        Cached single frame of a clip for scrubbing before its frame cache
        exists (B12). Keyed by path + size + mtime, so a re-imported or renamed
        clip never shows another clip's frames. Returns the path or None, and
        reports an ffmpeg failure instead of hiding it (C19).
        """
        tmp_img = thumb_path(self.ctx.thumbs_dir, video_path, frame_idx)
        if tmp_img.exists():
            return tmp_img
        cmd = [str(self.ctx.ffmpeg_exe), "-y", "-loglevel", "error"]
        cmd += list(seek_args)
        cmd += ["-i", str(video_path), "-vframes", "1", "-q:v", "3", "-threads", "4", str(tmp_img)]
        try:
            r = run_hidden(cmd, capture=True, timeout=60)
            if not tmp_img.exists():
                err = (r.stdout or b"")
                if isinstance(err, bytes):
                    err = err.decode("utf-8", "replace")
                self.ctx.status("Could not read frame %d of %s: %s" % (
                    frame_idx + 1, video_path.name, err.strip()[:120] or "ffmpeg wrote nothing"),
                    error=True)
                return None
            return tmp_img
        except Exception as e:
            self.ctx.status("ffmpeg failed on %s: %s" % (video_path.name, e), error=True)
            return None

    def find_latest_overlay(self, shot_name):
        overlay, _ = find_latest_output(
            self.ctx.scenes_dir / shot_name, "2D_POINT_TRACK", "tracks_2d_overlay.mp4",
            legacy_subdirs=("cotracker_2d",))
        return overlay
