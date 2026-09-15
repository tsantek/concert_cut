from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollBar,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.downloader import download, open_local
from app.models import ConcertProject, Segment
from app.project_store import load_project, save_project, sidecar_path
from app.segmenter import build_segments, waveform_peaks
from app.setlist import parse_setlist
from app.ui.export_dialog import ExportDialog
from app.ui.playlist_page import PlaylistPage
from app.ui.waveform import WaveformWidget

PAGE_CONCERT = 0
PAGE_EDITOR = 1
PAGE_PLAYLIST = 2


def _fmt_time(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


AUDIO_VIDEO_FILTER = (
    "Media (*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.mp4 *.mkv *.webm *.mov);;"
    "All files (*)"
)

class DownloadWorker(QObject):
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, url: str, audio_only: bool, output_dir: Path) -> None:
        super().__init__()
        self.url = url
        self.audio_only = audio_only
        self.output_dir = output_dir

    def run(self) -> None:
        try:
            result = download(
                self.url,
                audio_only=self.audio_only,
                output_dir=self.output_dir,
                on_progress=lambda pct, msg: self.progress.emit(pct, msg),
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class AnalyzeWorker(QObject):
    finished = Signal(object)  # ConcertProject
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, download_result) -> None:
        super().__init__()
        self.result = download_result

    def run(self) -> None:
        try:
            duration = self.result.duration
            saved = load_project(self.result.path)

            if saved is not None:
                self.status.emit("Loaded saved cuts from sidecar…")
                segments = saved.segments
                used_chapters = saved.used_chapters
                title = saved.video_title or self.result.title
                uploader = saved.uploader or self.result.uploader
                name = saved.name or uploader or title
                source_url = saved.source_url or self.result.source_url
                video_id = saved.video_id or self.result.video_id
                chapters = saved.chapters if saved.chapters is not None else self.result.chapters
                if saved.duration > 0:
                    duration = saved.duration
                from_sidecar = True
            else:
                self.status.emit("Analyzing audio / building cuts…")
                segments, used_chapters = build_segments(
                    self.result.path,
                    duration,
                    self.result.chapters,
                )
                title = self.result.title
                uploader = self.result.uploader
                name = uploader or title
                source_url = self.result.source_url
                video_id = self.result.video_id
                chapters = self.result.chapters
                from_sidecar = False

            if duration <= 0 and segments:
                duration = max(s.end for s in segments)

            self.status.emit("Building waveform…")
            peaks, wave_dur = waveform_peaks(self.result.path, duration_hint=duration)
            if duration <= 0:
                duration = wave_dur

            project = ConcertProject(
                source_path=self.result.path,
                video_title=title,
                uploader=uploader,
                name=name,
                duration=duration,
                segments=segments,
                used_chapters=used_chapters,
                source_url=source_url,
                video_id=video_id,
                chapters=chapters,
                loaded_from_sidecar=from_sidecar,
            )
            project._peaks = peaks  # type: ignore[attr-defined]

            # Always write/update sidecar after network download so local re-open works.
            # Also write when first analyzing a local file with no sidecar yet.
            if self.result.source_url or not from_sidecar:
                self.status.emit("Saving project sidecar…")
                save_project(project)

            self.finished.emit(project)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Concert Cut")
        self.resize(1100, 760)
        self.setMinimumSize(900, 640)

        self._project: ConcertProject | None = None
        self._thread: QThread | None = None
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(1.0)
        self._player.setAudioOutput(self._audio)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_player_error)
        self._play_end: float | None = None
        self._play_start: float | None = None
        self._pending_start_ms: int | None = None

        self._play_timer = QTimer(self)
        self._play_timer.setInterval(100)
        self._play_timer.timeout.connect(self._on_play_tick)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.stack.addWidget(self._build_download_page())
        self.stack.addWidget(self._build_editor_page())
        self._playlist_page = PlaylistPage()
        self._playlist_page.switch_to_concert.connect(self._go_concert)
        self.stack.addWidget(self._playlist_page)

    def _go_concert(self) -> None:
        self.stack.setCurrentIndex(PAGE_CONCERT)

    def _go_playlist(self) -> None:
        self.stack.setCurrentIndex(PAGE_PLAYLIST)

    def _build_download_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("DownloadPage")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(48, 40, 48, 40)

        mode = QHBoxLayout()
        concert_btn = QPushButton("Concert Cut")
        concert_btn.setObjectName("ModeTabActive")
        concert_btn.setEnabled(False)
        playlist_btn = QPushButton("Playlist Cut")
        playlist_btn.setObjectName("ModeTab")
        playlist_btn.clicked.connect(self._go_playlist)
        mode.addWidget(concert_btn)
        mode.addWidget(playlist_btn)
        mode.addStretch(1)
        outer.addLayout(mode)

        outer.addStretch(1)

        card = QFrame()
        card.setObjectName("HeroCard")
        card.setMaximumWidth(720)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(36, 36, 36, 36)
        layout.setSpacing(14)

        title = QLabel("Concert Cut")
        title.setObjectName("BrandTitle")
        layout.addWidget(title)

        sub = QLabel("Download a show from the network or open one from disk. Cut songs. Export clean tracks.")
        sub.setObjectName("BrandSub")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        url_label = QLabel("Network URL")
        url_label.setObjectName("MutedLabel")
        layout.addWidget(url_label)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://…")
        self.url_edit.setMinimumHeight(40)
        layout.addWidget(self.url_edit)

        opts = QHBoxLayout()
        opts.setSpacing(12)
        self.audio_only = QCheckBox("Audio only")
        self.audio_only.setChecked(True)
        opts.addWidget(self.audio_only)
        opts.addStretch(1)
        layout.addLayout(opts)

        folder_label = QLabel("Save downloads to")
        folder_label.setObjectName("MutedLabel")
        layout.addWidget(folder_label)
        folder_row = QHBoxLayout()
        self.out_dir_edit = QLineEdit(str(Path.home() / "Downloads" / "concert_cut"))
        browse = QPushButton("Folder…")
        browse.setObjectName("GhostButton")
        browse.clicked.connect(self._browse_download_dir)
        folder_row.addWidget(self.out_dir_edit, stretch=1)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.download_btn = QPushButton("Download & Pre-cut")
        self.download_btn.setObjectName("PrimaryButton")
        self.download_btn.setMinimumHeight(42)
        self.download_btn.clicked.connect(self._start_download)
        self.local_btn = QPushButton("Open local file…")
        self.local_btn.setObjectName("GhostButton")
        self.local_btn.setMinimumHeight(42)
        self.local_btn.clicked.connect(self._open_local_file)
        actions.addWidget(self.download_btn, stretch=2)
        actions.addWidget(self.local_btn, stretch=1)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card, stretch=0)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(2)
        return page

    def _build_editor_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        top = QHBoxLayout()
        self.project_label = QLabel("")
        self.project_label.setObjectName("ProjectTitle")
        top.addWidget(self.project_label, stretch=1)
        back = QPushButton("New source")
        back.setObjectName("GhostButton")
        back.clicked.connect(self._go_new_source)
        top.addWidget(back)
        layout.addLayout(top)

        name_row = QHBoxLayout()
        name_lbl = QLabel("Name")
        name_lbl.setObjectName("MutedLabel")
        name_row.addWidget(name_lbl)
        self.name_edit = QLineEdit()
        name_row.addWidget(self.name_edit, stretch=1)
        self.source_hint = QLabel("")
        self.source_hint.setObjectName("MutedLabel")
        name_row.addWidget(self.source_hint)
        layout.addLayout(name_row)

        self.waveform = WaveformWidget()
        self.waveform.setMinimumHeight(200)
        self.waveform.segments_changed.connect(self._sync_table_from_waveform)
        self.waveform.selection_changed.connect(self._on_waveform_select)
        self.waveform.seek_play_requested.connect(self._play_from)
        self.waveform.view_changed.connect(self._on_waveform_view_changed)
        layout.addWidget(self.waveform)

        self.wave_scroll = QScrollBar(Qt.Horizontal)
        self.wave_scroll.setToolTip("Scroll left/right when zoomed")
        self.wave_scroll.valueChanged.connect(self._on_wave_scroll)
        self.wave_scroll.setEnabled(False)
        layout.addWidget(self.wave_scroll)

        zoom_row = QHBoxLayout()
        self.zoom_label = QLabel("Zoom: fit")
        self.zoom_label.setObjectName("MutedLabel")
        z_in = QPushButton("Zoom +")
        z_in.setObjectName("GhostButton")
        z_in.clicked.connect(lambda *_: self.waveform.zoom_in())
        z_out = QPushButton("Zoom −")
        z_out.setObjectName("GhostButton")
        z_out.clicked.connect(lambda *_: self.waveform.zoom_out())
        z_fit = QPushButton("Fit")
        z_fit.setObjectName("GhostButton")
        z_fit.clicked.connect(lambda *_: self.waveform.zoom_fit())
        z_sel = QPushButton("Zoom to song")
        z_sel.setObjectName("GhostButton")
        z_sel.clicked.connect(lambda *_: self.waveform.zoom_to_selection())
        zoom_row.addWidget(self.zoom_label)
        zoom_row.addStretch(1)
        zoom_row.addWidget(z_in)
        zoom_row.addWidget(z_out)
        zoom_row.addWidget(z_fit)
        zoom_row.addWidget(z_sel)
        layout.addLayout(zoom_row)

        setlist_label = QLabel(
            "Paste setlist — 0:01 Title · 0:01 - Title · "
            "1:14 - Song A 4:54 - Song B · Title 0:01"
        )
        setlist_label.setObjectName("SectionHint")
        setlist_label.setWordWrap(True)
        layout.addWidget(setlist_label)

        setlist_row = QHBoxLayout()
        self.setlist_edit = QTextEdit()
        self.setlist_edit.setPlaceholderText(
            "0:01 : Wrong ones\n"
            "6:19 - Circles\n"
            "1:14 - All The Little Lights 4:54 - Life's For The Living\n"
            "Sunflower 1:24:30"
        )
        self.setlist_edit.setMaximumHeight(100)
        setlist_row.addWidget(self.setlist_edit, stretch=1)
        apply_setlist = QPushButton("Apply setlist")
        apply_setlist.setObjectName("PrimaryButton")
        apply_setlist.setToolTip("Replace song cuts with pasted timestamps + titles")
        apply_setlist.clicked.connect(self._apply_setlist)
        setlist_row.addWidget(apply_setlist, alignment=Qt.AlignTop)
        layout.addLayout(setlist_row)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Export", "#", "Title", "Start", "End"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setColumnWidth(0, 55)
        self.table.setColumnWidth(1, 36)
        self.table.setColumnWidth(2, 340)
        self.table.setColumnWidth(3, 70)
        self.table.itemChanged.connect(self._on_table_changed)
        self.table.itemSelectionChanged.connect(self._on_table_select)
        layout.addWidget(self.table, stretch=1)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.play_btn = QPushButton("Play")
        self.play_btn.setObjectName("PrimaryButton")
        self.play_btn.clicked.connect(self._play_selected)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("GhostButton")
        self.stop_btn.clicked.connect(self._stop_playback)
        split_btn = QPushButton("Split")
        split_btn.setObjectName("GhostButton")
        split_btn.clicked.connect(self._split_selected)
        merge_prev_btn = QPushButton("Merge prev")
        merge_prev_btn.setObjectName("GhostButton")
        merge_prev_btn.clicked.connect(self._merge_with_prev)
        merge_btn = QPushButton("Merge next")
        merge_btn.setObjectName("GhostButton")
        merge_btn.clicked.connect(self._merge_with_next)
        remove_btn = QPushButton("Delete")
        remove_btn.setObjectName("GhostButton")
        remove_btn.clicked.connect(self._delete_selected)
        export_btn = QPushButton("Export…")
        export_btn.setObjectName("PrimaryButton")
        export_btn.clicked.connect(self._export)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(split_btn)
        controls.addWidget(merge_prev_btn)
        controls.addWidget(merge_btn)
        controls.addWidget(remove_btn)
        controls.addStretch(1)
        controls.addWidget(export_btn)
        layout.addLayout(controls)
        return page

    def _go_new_source(self) -> None:
        self._stop_playback()
        self._persist_project()
        self.stack.setCurrentIndex(PAGE_CONCERT)

    def _persist_project(self) -> None:
        """Write current edits to the sidecar next to the media file."""
        if not self._project:
            return
        self._project.name = self.name_edit.text().strip() or self._project.name
        self._project.segments = self.waveform.segments()
        try:
            save_project(self._project)
        except OSError:
            pass

    def _browse_download_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Download folder", self.out_dir_edit.text()
        )
        if path:
            self.out_dir_edit.setText(path)

    def _set_source_busy(self, busy: bool) -> None:
        self.download_btn.setEnabled(not busy)
        self.local_btn.setEnabled(not busy)

    def _start_download(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "URL required", "Paste a network URL first.")
            return
        self._set_source_busy(True)
        self.progress.setValue(0)
        self.status_label.setText("Starting…")

        self._thread = QThread(self)
        worker = DownloadWorker(
            url,
            self.audio_only.isChecked(),
            Path(self.out_dir_edit.text()).expanduser(),
        )
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.progress.connect(self._on_dl_progress)
        worker.finished.connect(self._on_dl_finished)
        worker.failed.connect(self._on_dl_failed)
        worker.finished.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._dl_worker = worker
        self._thread.start()

    def _open_local_file(self) -> None:
        start = str(Path.home() / "Downloads" / "concert_cut")
        path, _ = QFileDialog.getOpenFileName(
            self, "Open concert file", start, AUDIO_VIDEO_FILTER
        )
        if not path:
            return
        self._set_source_busy(True)
        self.progress.setValue(0)
        self.status_label.setText("Opening local file…")
        try:
            result = open_local(Path(path))
        except Exception as exc:
            self._set_source_busy(False)
            self.status_label.setText("")
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self.progress.setValue(30)
        self.status_label.setText("Local file ready. Analyzing…")
        self._start_analyze(result)

    def _on_dl_progress(self, pct: float, msg: str) -> None:
        self.progress.setValue(int(pct))
        self.status_label.setText(msg)

    def _on_dl_failed(self, err: str) -> None:
        self._set_source_busy(False)
        self.status_label.setText("")
        QMessageBox.critical(self, "Failed", err)

    def _on_dl_finished(self, result) -> None:
        self.progress.setValue(100)
        self.status_label.setText("Download complete. Analyzing…")
        self._start_analyze(result)

    def _start_analyze(self, result) -> None:
        self._thread = QThread(self)
        worker = AnalyzeWorker(result)
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.status.connect(self.status_label.setText)
        worker.finished.connect(self._on_analyze_finished)
        worker.failed.connect(self._on_dl_failed)
        worker.finished.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._analyze_worker = worker
        self._thread.start()

    def _on_analyze_finished(self, project: ConcertProject) -> None:
        self._set_source_busy(False)
        self._project = project
        peaks = getattr(project, "_peaks", None)
        self.name_edit.setText(project.name)
        self.project_label.setText(project.video_title)
        src = (
            "Saved cuts"
            if project.loaded_from_sidecar
            else ("Chapters" if project.used_chapters else "Energy detection")
        )
        side = sidecar_path(project.source_path).name
        self.source_hint.setText(
            f"Pre-cuts: {src}  ·  {len(project.segments)} songs  ·  "
            f"sidecar: {side}"
        )
        if peaks is not None:
            self.waveform.set_waveform(peaks, project.duration)
        self.waveform.set_segments(project.segments)
        self._on_waveform_view_changed()
        self._refill_table()
        self.stack.setCurrentIndex(PAGE_EDITOR)

    def _refill_table(self) -> None:
        if not self._project:
            return
        self.table.blockSignals(True)
        segs = self._project.segments
        self.table.setRowCount(len(segs))
        for i, seg in enumerate(segs):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked if seg.include_export else Qt.Unchecked)
            self.table.setItem(i, 0, check)

            num = QTableWidgetItem(f"{i + 1:02d}")
            num.setFlags(num.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 1, num)

            self.table.setItem(i, 2, QTableWidgetItem(seg.title))

            start = QTableWidgetItem(_fmt_time(seg.start))
            end = QTableWidgetItem(_fmt_time(seg.end))
            start.setFlags(start.flags() & ~Qt.ItemIsEditable)
            end.setFlags(end.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 3, start)
            self.table.setItem(i, 4, end)
        self.table.blockSignals(False)

    def _sync_table_from_waveform(self) -> None:
        if not self._project:
            return
        self._project.segments = self.waveform.segments()
        self._refill_table()

    def _on_table_changed(self, item: QTableWidgetItem) -> None:
        if not self._project:
            return
        row = item.row()
        if row < 0 or row >= len(self._project.segments):
            return
        seg = self._project.segments[row]
        if item.column() == 0:
            seg.include_export = item.checkState() == Qt.Checked
        elif item.column() == 2:
            seg.title = item.text().strip() or f"Track {row + 1:02d}"
            self.waveform.set_segments(self._project.segments)

    def _update_zoom_label(self) -> None:
        self.zoom_label.setText(self.waveform.view_label())

    def _on_waveform_view_changed(self) -> None:
        self._update_zoom_label()
        self._sync_wave_scroll()

    def _sync_wave_scroll(self) -> None:
        """Mirror waveform zoom window onto the horizontal scrollbar."""
        dur = self.waveform.duration()
        span = self.waveform.view_span()
        start = self.waveform.view_start()
        self.wave_scroll.blockSignals(True)
        if dur <= 0 or span >= dur - 0.05:
            self.wave_scroll.setEnabled(False)
            self.wave_scroll.setRange(0, 0)
            self.wave_scroll.setValue(0)
        else:
            # Use centiseconds for smoother dragging
            scale = 100
            maximum = max(0, int((dur - span) * scale))
            page = max(1, int(span * scale))
            self.wave_scroll.setEnabled(True)
            self.wave_scroll.setRange(0, maximum)
            self.wave_scroll.setPageStep(page)
            self.wave_scroll.setSingleStep(max(1, page // 20))
            self.wave_scroll.setValue(int(start * scale))
        self.wave_scroll.blockSignals(False)

    def _on_wave_scroll(self, value: int) -> None:
        self.waveform.set_view_start(value / 100.0, emit=False)
        self._update_zoom_label()

    def _apply_setlist(self) -> None:
        if not self._project:
            return
        text = self.setlist_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Empty setlist", "Paste a setlist with timestamps first.")
            return

        self._stop_playback()
        from PySide6.QtWidgets import QApplication

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = parse_setlist(
                text,
                self._project.duration,
                audio_path=self._project.source_path,
                refine_ends=True,
            )
        finally:
            QApplication.restoreOverrideCursor()

        if not result.segments:
            QMessageBox.warning(
                self,
                "No songs found",
                "Could not parse any timestamp lines.\n\n"
                "Try formats like:\n"
                "0:01 : Title\n"
                "6:19 - Title\n"
                "10:45 Title\n"
                "1:02:36 Title\n"
                "Title 0:01",
            )
            return

        # Count how many ends were pulled back from the next start
        tightened = 0
        for i, seg in enumerate(result.segments):
            if i + 1 < len(result.segments):
                next_start = result.segments[i + 1].start
                if seg.end < next_start - 0.75:
                    tightened += 1
            elif self._project.duration > 0 and seg.end < self._project.duration - 1.0:
                tightened += 1

        self._project.segments = result.segments
        self._project.chapters = result.chapters
        self._project.used_chapters = True
        self.waveform.set_segments(result.segments)
        self.waveform.set_selected(0)
        self._refill_table()

        skipped_note = ""
        if result.skipped_lines:
            skipped_note = f"  ·  skipped {len(result.skipped_lines)} line(s)"

        self.source_hint.setText(
            f"Pre-cuts: Setlist + energy ends  ·  {len(result.segments)} songs"
            f"{skipped_note}  ·  sidecar: {sidecar_path(self._project.source_path).name}"
        )
        self._persist_project()
        QMessageBox.information(
            self,
            "Setlist applied",
            f"Loaded {len(result.segments)} songs from setlist.\n"
            f"Refined {tightened} song ending(s) using quiet gaps before the next track."
            + (f"\nSkipped {len(result.skipped_lines)} line(s)." if result.skipped_lines else ""),
        )

    def _on_table_select(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            rows = self.table.selectionModel().selectedRows()
            if rows:
                row = rows[0].row()
        if row >= 0:
            # Avoid re-emitting selection_changed → selectRow loop noise
            if self.waveform.selected_index() != row:
                self.waveform.set_selected(row)
            else:
                self.waveform.update()

    def _on_waveform_select(self, index: int) -> None:
        self.table.blockSignals(True)
        self.table.selectRow(index)
        self.table.blockSignals(False)

    def _play_selected(self) -> None:
        if not self._project:
            return
        idx = self.waveform.selected_index()
        if idx < 0 or idx >= len(self._project.segments):
            return
        seg = self._project.segments[idx]
        self._start_playback(seg.start, seg.end)

    def _play_from(self, start: float) -> None:
        """Play from a point on the waveform until the end of that track."""
        if not self._project:
            return
        end = self._project.duration
        for i, seg in enumerate(self._project.segments):
            if seg.start <= start <= seg.end:
                end = seg.end
                self.waveform.set_selected(i)
                break
        start = max(0.0, min(start, end - 0.05))
        self._start_playback(start, end)

    def _start_playback(self, start: float, end: float) -> None:
        if not self._project:
            return
        self._stop_playback()

        self._play_start = start
        self._play_end = end
        self._pending_start_ms = int(start * 1000)

        path = str(self._project.source_path.resolve())
        url = QUrl.fromLocalFile(path)
        already = (
            self._player.source() == url
            and self._player.mediaStatus()
            in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            )
        )
        if already:
            self._seek_and_play()
        else:
            self._player.setSource(url)

    def _on_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if self._pending_start_ms is None:
            return
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            self._seek_and_play()

    def _seek_and_play(self) -> None:
        if self._pending_start_ms is None:
            return
        start_ms = self._pending_start_ms
        self._pending_start_ms = None
        self._player.setPosition(start_ms)
        self._player.play()
        self._play_timer.start()

    def _on_player_error(self, _error, message: str) -> None:
        self._pending_start_ms = None
        self._play_timer.stop()
        if message:
            QMessageBox.warning(self, "Playback error", message)

    def _stop_playback(self) -> None:
        self._pending_start_ms = None
        self._play_start = None
        self._play_end = None
        self._player.stop()
        self._play_timer.stop()
        self.waveform.set_playhead(None)

    def _on_play_tick(self) -> None:
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        t = self._player.position() / 1000.0
        # Wait until seek lands near the segment start (avoid acting on stale position 0)
        if self._play_start is not None and t + 0.5 < self._play_start:
            return
        self.waveform.set_playhead(t)
        if self._play_end is not None and t >= self._play_end:
            self._stop_playback()

    def _split_selected(self) -> None:
        if not self._project:
            return
        idx = self.waveform.selected_index()
        segs = self._project.segments
        if idx < 0 or idx >= len(segs):
            return
        seg = segs[idx]
        mid = (seg.start + seg.end) / 2
        ph = self.waveform._playhead
        cut = mid
        if ph is not None and seg.start + 1 < ph < seg.end - 1:
            cut = ph
        # Split creates a tiny gap so begin/end handles stay distinct
        gap = 0.05
        left = Segment(
            title=seg.title,
            start=seg.start,
            end=cut,
            include_export=seg.include_export,
        )
        right = Segment(
            title=f"{seg.title} (cont.)",
            start=cut + gap,
            end=seg.end,
            include_export=seg.include_export,
        )
        segs[idx : idx + 1] = [left, right]
        self.waveform.set_segments(segs)
        self._refill_table()

    def _merge_with_prev(self) -> None:
        if not self._project:
            return
        idx = self.waveform.selected_index()
        segs = self._project.segments
        if idx <= 0 or idx >= len(segs):
            return
        a, b = segs[idx - 1], segs[idx]
        merged = Segment(
            title=a.title,
            start=a.start,
            end=b.end,
            include_export=a.include_export,
        )
        segs[idx - 1 : idx + 1] = [merged]
        self.waveform.set_segments(segs)
        self.waveform.set_selected(idx - 1)
        self._refill_table()

    def _merge_with_next(self) -> None:
        if not self._project:
            return
        idx = self.waveform.selected_index()
        segs = self._project.segments
        if idx < 0 or idx >= len(segs) - 1:
            return
        a, b = segs[idx], segs[idx + 1]
        merged = Segment(
            title=a.title,
            start=a.start,
            end=b.end,
            include_export=a.include_export,
        )
        segs[idx : idx + 2] = [merged]
        self.waveform.set_segments(segs)
        self._refill_table()

    def _delete_selected(self) -> None:
        """Remove song from list; its span becomes an empty gap (pause)."""
        if not self._project:
            return
        idx = self.waveform.selected_index()
        segs = self._project.segments
        if idx < 0 or idx >= len(segs) or len(segs) <= 1:
            return
        del segs[idx]
        self.waveform.set_segments(segs)
        self.waveform.set_selected(min(idx, len(segs) - 1))
        self._refill_table()

    def _export(self) -> None:
        if not self._project:
            return
        self._stop_playback()
        self._project.name = self.name_edit.text().strip() or self._project.name
        self._project.segments = self.waveform.segments()
        self._persist_project()
        dlg = ExportDialog(self._project, self)
        dlg.exec()
        self._refill_table()
