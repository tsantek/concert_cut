from __future__ import annotations

import re
import shutil
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.downloader import download, fetch_playlist
from app.logging_setup import get_logger
from app.models import PlaylistEntry, PlaylistInfo, split_artist_title

log = get_logger("track_cut.playlist")


def _fmt_time(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def _sanitize(name: str) -> str:
    name = name.strip() or "Untitled"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:180] or "Untitled"


def _track_filename(index: int, entry: PlaylistEntry, ext: str) -> str:
    song = _sanitize(entry.title)
    artist = _sanitize(entry.artist) if entry.artist else ""
    if artist:
        return f"{index:02d} - {artist} - {song}{ext}"
    return f"{index:02d} - {song}{ext}"


class FetchWorker(QObject):
    finished = Signal(object)  # PlaylistInfo
    failed = Signal(str)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def run(self) -> None:
        log.info("FetchWorker: loading %r", self.url)
        try:
            info = fetch_playlist(self.url)
            log.info(
                "FetchWorker: ok title=%r tracks=%d",
                info.title,
                len(info.entries),
            )
            self.finished.emit(info)
        except Exception as exc:
            log.error("FetchWorker: failed\n%s", traceback.format_exc())
            self.failed.emit(f"{exc}\n\n(See terminal for full log)")


class BatchDownloadWorker(QObject):
    """Download checked playlist entries one-by-one."""

    progress = Signal(float, str)  # overall pct-ish message helper
    item_started = Signal(int, str)  # row index, title
    item_progress = Signal(int, float, str)  # row, pct, msg
    item_finished = Signal(int, object)  # row, Path
    item_failed = Signal(int, str)  # row, error
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self,
        items: list[tuple[int, PlaylistEntry]],
        output_dir: Path,
    ) -> None:
        super().__init__()
        self.items = items
        self.output_dir = output_dir
        self._cancel = False

    def cancel(self) -> None:
        log.info("BatchDownloadWorker: cancel requested")
        self._cancel = True

    def run(self) -> None:
        try:
            total = len(self.items)
            log.info(
                "BatchDownloadWorker: start count=%d out=%s",
                total,
                self.output_dir,
            )
            for n, (row, entry) in enumerate(self.items, start=1):
                if self._cancel:
                    log.info("BatchDownloadWorker: canceled before row=%d", row)
                    break
                log.info(
                    "BatchDownloadWorker: [%d/%d] row=%d title=%r url=%r",
                    n,
                    total,
                    row,
                    entry.title,
                    entry.url,
                )
                self.item_started.emit(row, entry.title)
                self.progress.emit(
                    (n - 1) / total * 100.0 if total else 0.0,
                    f"{n}/{total} — {entry.title}",
                )

                def on_prog(pct: float, msg: str, r: int = row) -> None:
                    self.item_progress.emit(r, pct, msg)

                try:
                    result = download(
                        entry.url,
                        audio_only=True,
                        output_dir=self.output_dir,
                        on_progress=on_prog,
                    )
                    entry.path = result.path
                    artist, song = split_artist_title(
                        result.title or entry.title,
                        fallback_artist=entry.artist
                        or result.uploader
                        or "",
                    )
                    entry.artist = artist or entry.artist
                    entry.title = song
                    entry.duration = result.duration or entry.duration
                    log.info(
                        "BatchDownloadWorker: row=%d done artist=%r song=%r path=%s",
                        row,
                        entry.artist,
                        entry.title,
                        result.path,
                    )
                    self.item_finished.emit(row, result.path)
                except Exception as exc:
                    log.error(
                        "BatchDownloadWorker: row=%d failed\n%s",
                        row,
                        traceback.format_exc(),
                    )
                    self.item_failed.emit(row, str(exc))

            self.progress.emit(100.0 if not self._cancel else 0.0, "")
            log.info("BatchDownloadWorker: finished cancel=%s", self._cancel)
            self.finished.emit()
        except Exception as exc:
            log.error("BatchDownloadWorker: fatal\n%s", traceback.format_exc())
            self.failed.emit(str(exc))


def _is_unavailable_error(message: str) -> bool:
    text = (message or "").lower()
    markers = (
        "video unavailable",
        "private video",
        "has been removed",
        "not available",
        "is unavailable",
        "copyright",
        "account associated with this video has been terminated",
        "this video is not available",
        "sign in to confirm your age",
    )
    return any(m in text for m in markers)


class ProgressPopup(QDialog):
    """Modal loading / download progress dialog."""

    stop_clicked = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.status = QLabel("Starting…")
        self.status.setObjectName("MutedLabel")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        layout.addWidget(self.bar)

        row = QHBoxLayout()
        row.addStretch(1)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("GhostButton")
        self.stop_btn.clicked.connect(self.stop_clicked.emit)
        row.addWidget(self.stop_btn)
        layout.addLayout(row)

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def set_progress(self, pct: int) -> None:
        self.bar.setValue(int(min(100, max(0, pct))))

    def set_indeterminate(self, on: bool) -> None:
        if on:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 100)

    def set_stop_enabled(self, enabled: bool) -> None:
        self.stop_btn.setEnabled(enabled)


class PlaylistPage(QWidget):
    """Download individual tracks from a network playlist and save checked ones."""

    switch_to_concert = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._info: PlaylistInfo | None = None
        self._entries: list[PlaylistEntry] = []
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._batch: BatchDownloadWorker | None = None
        self._busy = False
        self._save_dir: Path | None = None
        self._pending_rows: list[int] = []
        self._unavailable_rows: set[int] = set()
        self._progress_popup: ProgressPopup | None = None
        self._total_download: int = 0
        self._done_download: int = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        mode = QHBoxLayout()
        concert_btn = QPushButton("Concert")
        concert_btn.setObjectName("ModeTab")
        concert_btn.clicked.connect(self.switch_to_concert.emit)
        playlist_btn = QPushButton("Playlist")
        playlist_btn.setObjectName("ModeTabActive")
        playlist_btn.setEnabled(False)
        mode.addWidget(concert_btn)
        mode.addWidget(playlist_btn)
        mode.addStretch(1)
        root.addLayout(mode)

        title = QLabel("Playlist")
        title.setObjectName("ProjectTitle")
        root.addWidget(title)

        sub = QLabel(
            "Paste a network playlist URL, download tracks one by one, "
            "then save the ones you want."
        )
        sub.setObjectName("MutedLabel")
        sub.setWordWrap(True)
        root.addWidget(sub)

        url_label = QLabel("Playlist URL")
        url_label.setObjectName("MutedLabel")
        root.addWidget(url_label)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://…")
        self.url_edit.setMinimumHeight(40)
        root.addWidget(self.url_edit)

        folder_label = QLabel("Save to")
        folder_label.setObjectName("MutedLabel")
        root.addWidget(folder_label)
        folder_row = QHBoxLayout()
        self.out_dir_edit = QLineEdit(
            str(Path.home() / "Downloads" / "track_cut" / "playlists")
        )
        browse = QPushButton("Folder…")
        browse.setObjectName("GhostButton")
        browse.clicked.connect(self._browse_dir)
        folder_row.addWidget(self.out_dir_edit, stretch=1)
        folder_row.addWidget(browse)
        root.addLayout(folder_row)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.load_btn = QPushButton("Fetch songs")
        self.load_btn.setObjectName("PrimaryButton")
        self.load_btn.setMinimumHeight(40)
        self.load_btn.clicked.connect(self._load_playlist)
        self.select_all_btn = QPushButton("Check all")
        self.select_all_btn.setObjectName("GhostButton")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        self.select_none_btn = QPushButton("Uncheck all")
        self.select_none_btn.setObjectName("GhostButton")
        self.select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        actions.addWidget(self.load_btn)
        actions.addWidget(self.select_all_btn)
        actions.addWidget(self.select_none_btn)
        actions.addStretch(1)
        root.addLayout(actions)

        self.playlist_title = QLabel("")
        self.playlist_title.setObjectName("MutedLabel")
        root.addWidget(self.playlist_title)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["", "#", "Artist", "Song name", "Duration", "Status"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 48)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.setColumnWidth(2, 180)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.cellClicked.connect(self._on_cell_clicked)
        root.addWidget(self.table, stretch=1)

        dl_row = QHBoxLayout()
        self.download_btn = QPushButton("Save selected")
        self.download_btn.setObjectName("PrimaryButton")
        self.download_btn.setMinimumHeight(40)
        self.download_btn.setToolTip("Download checked songs into the Save to folder")
        self.download_btn.clicked.connect(self._download_and_save)
        self.cancel_btn = QPushButton("Stop")
        self.cancel_btn.setObjectName("GhostButton")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_download)
        dl_row.addWidget(self.download_btn, stretch=2)
        dl_row.addWidget(self.cancel_btn)
        root.addLayout(dl_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)

        self.status_label = QLabel("")
        self.status_label.setObjectName("MutedLabel")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

    def _browse_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Save to folder", self.out_dir_edit.text()
        )
        if path:
            self.out_dir_edit.setText(path)

    def _open_progress_popup(
        self,
        title: str,
        *,
        allow_stop: bool,
        indeterminate: bool = False,
    ) -> None:
        self._close_progress_popup()
        popup = ProgressPopup(title, self)
        popup.set_stop_enabled(allow_stop)
        if allow_stop:
            popup.stop_clicked.connect(self._cancel_download)
        popup.set_indeterminate(indeterminate)
        self._progress_popup = popup
        popup.show()

    def _close_progress_popup(self) -> None:
        if self._progress_popup is not None:
            self._progress_popup.close()
            self._progress_popup.deleteLater()
            self._progress_popup = None

    def _update_progress_popup(self, pct: int | None = None, status: str | None = None) -> None:
        if self._progress_popup is None:
            return
        if status is not None:
            self._progress_popup.set_status(status)
        if pct is not None:
            self._progress_popup.set_progress(pct)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.load_btn.setEnabled(not busy)
        self.download_btn.setEnabled(not busy)
        self.select_all_btn.setEnabled(not busy)
        self.select_none_btn.setEnabled(not busy)
        self.url_edit.setEnabled(not busy)
        self.out_dir_edit.setEnabled(not busy)
        # Stop lives on the popup during downloads; keep page Stop in sync
        self.cancel_btn.setEnabled(busy and self._batch is not None)
        if not busy:
            self._close_progress_popup()

    def _clear_thread(self) -> None:
        self._thread = None
        self._worker = None
        self._batch = None

    def _load_playlist(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "URL required", "Paste a playlist URL first.")
            return
        log.info("UI: Load playlist clicked url=%r", url)
        self._set_busy(True)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("Fetching songs…")
        self.progress.setValue(0)
        self._open_progress_popup(
            "Fetching songs",
            allow_stop=False,
            indeterminate=True,
        )
        self._update_progress_popup(status="Loading playlist…")

        thread = QThread(self)
        worker = FetchWorker(url)
        # Keep Python refs — otherwise GC kills the worker before the thread runs.
        self._thread = thread
        self._worker = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_fetch_done)
        worker.failed.connect(self._on_fetch_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._clear_thread)
        thread.start()
        log.info("UI: fetch thread started")

    def _make_row_check(self, checked: bool = True) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("CheckCell")
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setAlignment(Qt.AlignCenter)
        box = QCheckBox()
        box.setObjectName("PlaylistCheck")
        box.setChecked(checked)
        box.setCursor(Qt.PointingHandCursor)
        layout.addWidget(box)
        wrap._check = box  # type: ignore[attr-defined]
        return wrap

    def _row_check(self, row: int) -> QCheckBox | None:
        cell = self.table.cellWidget(row, 0)
        if cell is None:
            return None
        return getattr(cell, "_check", None)

    def _on_cell_clicked(self, row: int, column: int) -> None:
        if column == 0:
            return
        box = self._row_check(row)
        if box is not None and box.isEnabled():
            box.toggle()

    def _on_fetch_done(self, info: PlaylistInfo) -> None:
        log.info("UI: playlist loaded title=%r n=%d", info.title, len(info.entries))
        self._info = info
        self._entries = list(info.entries)
        self.playlist_title.setText(f"{info.title} — {len(self._entries)} songs")
        self.table.setRowCount(0)
        for i, entry in enumerate(self._entries):
            self.table.insertRow(i)
            self.table.setCellWidget(i, 0, self._make_row_check(True))
            num = QTableWidgetItem(str(i + 1))
            num.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(i, 1, num)
            self.table.setItem(i, 2, QTableWidgetItem(entry.artist))
            self.table.setItem(i, 3, QTableWidgetItem(entry.title))
            self.table.setItem(i, 4, QTableWidgetItem(_fmt_time(entry.duration)))
            self.table.setItem(i, 5, QTableWidgetItem("Pending"))
        self.status_label.setText(
            f"Loaded {len(self._entries)} available songs. Check some, then Save selected."
        )
        self._set_busy(False)

    def _on_fetch_failed(self, message: str) -> None:
        log.error("UI: playlist load failed: %s", message)
        self._set_busy(False)
        self.status_label.setText("Load failed — see terminal logs.")
        QMessageBox.critical(self, "Playlist failed", message)

    def _set_all_checked(self, checked: bool) -> None:
        for row in range(self.table.rowCount()):
            box = self._row_check(row)
            if box is not None:
                box.setChecked(checked)

    def _checked_rows(self) -> list[int]:
        rows: list[int] = []
        for row in range(self.table.rowCount()):
            box = self._row_check(row)
            if box is not None and box.isChecked():
                rows.append(row)
        return rows

    def _download_and_save(self) -> None:
        if not self._entries:
            QMessageBox.warning(self, "No playlist", "Fetch songs first.")
            return
        rows = self._checked_rows()
        if not rows:
            QMessageBox.warning(self, "Nothing selected", "Check at least one track.")
            return

        save_dir = Path(self.out_dir_edit.text().strip()).expanduser()
        if not self.out_dir_edit.text().strip():
            QMessageBox.warning(self, "Folder required", "Choose a Save to folder first.")
            return
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Folder error", str(exc))
            return

        self._save_dir = save_dir
        self._pending_rows = rows
        self._unavailable_rows = set()
        staging = save_dir / ".staging"
        staging.mkdir(parents=True, exist_ok=True)

        items = [(r, self._entries[r]) for r in rows]
        self._total_download = len(items)
        self._done_download = 0
        self._set_busy(True)
        self.status_label.setText(f"Saving to {self._save_dir}…")
        self.progress.setValue(0)
        self._open_progress_popup("Downloading", allow_stop=True)
        self._update_progress_popup(
            pct=0,
            status=f"0 / {self._total_download} — starting…",
        )

        thread = QThread(self)
        worker = BatchDownloadWorker(items, staging)
        self._thread = thread
        self._worker = worker
        self._batch = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.item_started.connect(self._on_item_started)
        worker.item_progress.connect(self._on_item_progress)
        worker.item_finished.connect(self._on_item_finished)
        worker.item_failed.connect(self._on_item_failed)
        worker.progress.connect(self._on_batch_progress)
        worker.finished.connect(self._on_batch_finished)
        worker.failed.connect(self._on_batch_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._clear_thread)
        self.cancel_btn.setEnabled(True)
        thread.start()
        log.info(
            "UI: save selected started count=%d dest=%s",
            len(items),
            self._save_dir,
        )

    def _cancel_download(self) -> None:
        if self._batch:
            self._batch.cancel()
            self.status_label.setText("Stopping after current song…")
            self.cancel_btn.setEnabled(False)
            self._update_progress_popup(status="Stopping after current song…")
            if self._progress_popup is not None:
                self._progress_popup.set_stop_enabled(False)

    def _on_item_started(self, row: int, title: str) -> None:
        status = self.table.item(row, 5)
        if status:
            status.setText("Downloading")
        msg = f"Downloading — {title}"
        self.status_label.setText(msg)
        overall = int(self._done_download / max(1, self._total_download) * 100)
        self._update_progress_popup(
            pct=overall,
            status=f"{self._done_download} / {self._total_download} — {title}",
        )

    def _on_item_progress(self, row: int, pct: float, msg: str) -> None:
        status = self.table.item(row, 5)
        if status:
            status.setText(f"{pct:.0f}%")
        if msg:
            self.status_label.setText(msg)
        # Blend per-file progress into overall bar
        base = self._done_download / max(1, self._total_download)
        piece = pct / 100.0 / max(1, self._total_download)
        overall = int((base + piece) * 100)
        title = ""
        if row < len(self._entries):
            title = self._entries[row].title
        self._update_progress_popup(
            pct=overall,
            status=f"{self._done_download + 1} / {self._total_download} — {title} ({pct:.0f}%)",
        )

    def _on_item_finished(self, row: int, path: Path) -> None:
        self._done_download += 1
        status = self.table.item(row, 5)
        if status:
            status.setText("Done")
        if row < len(self._entries):
            entry = self._entries[row]
            artist_item = self.table.item(row, 2)
            if artist_item:
                artist_item.setText(entry.artist or "—")
            song_item = self.table.item(row, 3)
            if song_item:
                song_item.setText(entry.title)
            dur_item = self.table.item(row, 4)
            if dur_item and entry.duration > 0:
                dur_item.setText(_fmt_time(entry.duration))
        overall = int(self._done_download / max(1, self._total_download) * 100)
        self._update_progress_popup(pct=overall)

    def _on_item_failed(self, row: int, message: str) -> None:
        self._done_download += 1
        if _is_unavailable_error(message):
            log.info("UI: mark unavailable row=%d: %s", row, message)
            self._unavailable_rows.add(row)
            status = self.table.item(row, 5)
            if status:
                status.setText("Unavailable")
            self.status_label.setText("Skipping unavailable song…")
            self._update_progress_popup(
                pct=int(self._done_download / max(1, self._total_download) * 100),
                status="Skipping unavailable song…",
            )
            return
        status = self.table.item(row, 5)
        if status:
            status.setText("Failed")
        self.status_label.setText(f"Failed: {message}")
        self._update_progress_popup(
            pct=int(self._done_download / max(1, self._total_download) * 100),
            status=f"Failed: {message}",
        )
    def _purge_unavailable_rows(self) -> int:
        removed = 0
        for row in sorted(self._unavailable_rows, reverse=True):
            self._remove_row(row)
            removed += 1
        self._unavailable_rows.clear()
        return removed

    def _remove_row(self, row: int) -> None:
        if row < 0 or row >= self.table.rowCount():
            return
        self.table.removeRow(row)
        if 0 <= row < len(self._entries):
            del self._entries[row]
        self._pending_rows = [
            r if r < row else r - 1 for r in self._pending_rows if r != row
        ]
        for i in range(self.table.rowCount()):
            num = self.table.item(i, 1)
            if num:
                num.setText(str(i + 1))
        if self._info is not None:
            self.playlist_title.setText(
                f"{self._info.title} — {len(self._entries)} songs"
            )

    def _on_batch_progress(self, pct: float, msg: str) -> None:
        self.progress.setValue(int(min(100, max(0, pct))))
        if msg:
            self.status_label.setText(msg)
            self._update_progress_popup(status=msg)

    def _copy_to_save_dir(self) -> list[Path]:
        if not self._save_dir:
            return []
        album = _sanitize(self._info.title if self._info else "playlist")
        out_root = self._save_dir / album
        out_root.mkdir(parents=True, exist_ok=True)

        written: list[Path] = []
        n = 0
        for row in self._pending_rows:
            if row in self._unavailable_rows:
                continue
            if row >= len(self._entries):
                continue
            entry = self._entries[row]
            if not entry.path or not entry.path.is_file():
                continue
            n += 1
            ext = entry.path.suffix or ".m4a"
            target = out_root / _track_filename(n, entry, ext)
            shutil.copy2(entry.path, target)
            written.append(target)
            status = self.table.item(row, 5)
            if status:
                status.setText("Saved")
        return written

    def _on_batch_finished(self) -> None:
        written = self._copy_to_save_dir()
        dropped = self._purge_unavailable_rows()
        self.progress.setValue(100)
        self._set_busy(False)
        parts: list[str] = []
        if written and self._save_dir:
            out_root = written[0].parent
            parts.append(f"Saved {len(written)} song(s) to {out_root}")
            QMessageBox.information(
                self,
                "Saved",
                f"Downloaded and saved {len(written)} song(s) to:\n{out_root}",
            )
        else:
            parts.append("No files were saved.")
        if dropped:
            parts.append(f"Removed {dropped} unavailable.")
        self.status_label.setText(" ".join(parts))

    def _on_batch_failed(self, message: str) -> None:
        self._set_busy(False)
        QMessageBox.critical(self, "Download failed", message)
