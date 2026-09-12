from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from app.exporter import export_segments
from app.models import ConcertProject, Segment


class ExportWorker(QObject):
    progress = Signal(int, int, str)  # current, total, title
    finished = Signal(object, object)  # written paths, dest folder
    failed = Signal(str)

    def __init__(
        self,
        source: Path,
        segments: list[Segment],
        name: str,
        output_dir: Path,
        fmt: str,
    ) -> None:
        super().__init__()
        self.source = source
        self.segments = segments
        self.name = name
        self.output_dir = output_dir
        self.fmt = fmt

    def run(self) -> None:
        try:
            written = export_segments(
                self.source,
                self.segments,
                name=self.name,
                output_dir=self.output_dir,
                fmt=self.fmt,
                on_progress=lambda i, total, title: self.progress.emit(i, total, title),
            )
            album = self.name.strip() or "Untitled"
            dest = self.output_dir / album
            self.finished.emit(written, dest)
        except Exception as exc:
            self.failed.emit(str(exc))


class ExportDialog(QDialog):
    def __init__(self, project: ConcertProject, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export tracks")
        self.setMinimumWidth(560)
        self._project = project
        self._thread: QThread | None = None
        self._busy = False

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit(project.name or project.uploader or project.video_title)
        form.addRow("Name (artist / album)", self.name_edit)

        self.format_combo = QComboBox()
        self.format_combo.addItems(["mp3", "m4a", "wav"])
        form.addRow("Format", self.format_combo)

        default_out = Path.home() / "Downloads" / "concert_cut" / "exports"
        self.out_edit = QLineEdit(str(default_out))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        out_row = QHBoxLayout()
        out_row.addWidget(self.out_edit)
        out_row.addWidget(browse)
        form.addRow("Output folder", out_row)
        layout.addLayout(form)

        layout.addWidget(QLabel("Check songs to export:"))
        self.table = QTableWidget(len(project.segments), 3)
        self.table.setHorizontalHeaderLabels(["Export", "Title", "Duration"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 320)
        for i, seg in enumerate(project.segments):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check.setCheckState(Qt.Checked if seg.include_export else Qt.Unchecked)
            self.table.setItem(i, 0, check)
            self.table.setItem(i, 1, QTableWidgetItem(seg.title))
            dur = QTableWidgetItem(f"{seg.duration:.1f}s")
            dur.setFlags(dur.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 2, dur)
        layout.addWidget(self.table)

        self.status_label = QLabel("Ready to export.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        layout.addWidget(self.progress)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        export_btn = self.buttons.button(QDialogButtonBox.Ok)
        export_btn.setText("Export")
        export_btn.setObjectName("PrimaryButton")
        self.buttons.accepted.connect(self._do_export)
        self.buttons.rejected.connect(self._on_cancel)
        layout.addWidget(self.buttons)

        self._written: list[Path] = []

    def _browse(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path = QFileDialog.getExistingDirectory(self, "Output folder", self.out_edit.text())
        if path:
            self.out_edit.setText(path)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.name_edit.setEnabled(not busy)
        self.format_combo.setEnabled(not busy)
        self.out_edit.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(not busy)
        # Keep Cancel available to close after finish; during busy it just waits message
        self.buttons.button(QDialogButtonBox.Cancel).setText(
            "Please wait…" if busy else "Cancel"
        )
        self.buttons.button(QDialogButtonBox.Cancel).setEnabled(not busy)

    def _on_cancel(self) -> None:
        if self._busy:
            return
        self.reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._busy:
            event.ignore()
            return
        super().closeEvent(event)

    def _do_export(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Please enter a Name for the export.")
            return

        selected = []
        for i, seg in enumerate(self._project.segments):
            check = self.table.item(i, 0)
            title_item = self.table.item(i, 1)
            if title_item:
                seg.title = title_item.text().strip() or seg.title
            include = bool(check and check.checkState() == Qt.Checked)
            seg.include_export = include
            if include:
                selected.append(seg)

        if not selected:
            QMessageBox.warning(
                self, "Nothing selected", "Check at least one song to export."
            )
            return

        out_dir = Path(self.out_edit.text()).expanduser()
        fmt = self.format_combo.currentText()

        self._set_busy(True)
        self.progress.setValue(0)
        self.progress.setFormat("Starting…")
        self.status_label.setText(f"Exporting {len(selected)} tracks…")

        self._thread = QThread(self)
        worker = ExportWorker(
            self._project.source_path,
            selected,
            name,
            out_dir,
            fmt,
        )
        worker.moveToThread(self._thread)
        self._thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._thread.quit)
        worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(worker.deleteLater)
        self._export_worker = worker
        self._export_name = name
        self._thread.start()

    def _on_progress(self, current: int, total: int, title: str) -> None:
        pct = int((current - 1) / max(1, total) * 100)
        self.progress.setValue(pct)
        self.progress.setFormat(f"{current}/{total}  (%p%)")
        self.status_label.setText(f"Exporting {current}/{total}: {title}")

    def _on_finished(self, written, dest) -> None:
        self._written = list(written)
        self.progress.setValue(100)
        self.progress.setFormat("Done")
        self.status_label.setText(f"Exported {len(self._written)} tracks.")
        self._set_busy(False)
        self._project.name = self._export_name
        QMessageBox.information(
            self,
            "Export complete",
            f"Exported {len(self._written)} tracks to:\n{dest}",
        )
        self.accept()

    def _on_failed(self, err: str) -> None:
        self._set_busy(False)
        self.progress.setFormat("Failed")
        self.status_label.setText("Export failed.")
        QMessageBox.critical(self, "Export failed", err)

    @property
    def written_paths(self) -> list[Path]:
        return self._written
