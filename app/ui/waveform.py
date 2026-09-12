from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QWheelEvent
from PySide6.QtWidgets import QWidget

from app.models import Segment

HANDLE_W = 10
HANDLE_H = 12
HIT_TOL = 8
MIN_VIEW_SEC = 5.0


def _fmt_clock(seconds: float) -> str:
    s = int(max(0, seconds))
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


class WaveformWidget(QWidget):
    """
    Waveform with per-song begin/end lines.
    - Begin handle sits at the TOP of the line
    - End handle sits at the BOTTOM
    - Scroll wheel: zoom toward cursor
    - Middle-drag or Alt+left-drag: pan when zoomed
    Gaps between songs are empty (pause) — not listed as tracks.
    """

    segments_changed = Signal()
    selection_changed = Signal(int)
    seek_play_requested = Signal(float)
    view_changed = Signal()  # zoom/pan updated

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self._peaks = None
        self._duration = 0.0
        self._segments: list[Segment] = []
        self._selected = 0
        self._drag: tuple[str, int] | None = None
        self._playhead: float | None = None
        self._view_start = 0.0
        self._view_end = 0.0
        self._panning = False
        self._pan_anchor_x = 0.0
        self._pan_anchor_start = 0.0

    def set_waveform(self, peaks, duration: float) -> None:
        self._peaks = peaks
        self._duration = duration
        self._view_start = 0.0
        self._view_end = duration
        self.update()
        self.view_changed.emit()

    def set_segments(self, segments: list[Segment]) -> None:
        self._segments = segments
        if self._selected >= len(segments):
            self._selected = max(0, len(segments) - 1)
        self.update()

    def segments(self) -> list[Segment]:
        return self._segments

    def selected_index(self) -> int:
        return self._selected

    def set_selected(self, index: int) -> None:
        if 0 <= index < len(self._segments):
            self._selected = index
            self.update()
            self.selection_changed.emit(index)

    def set_playhead(self, t: float | None) -> None:
        self._playhead = t
        self.update()

    def view_label(self) -> str:
        if self._duration <= 0:
            return "Zoom: —"
        span = self._view_end - self._view_start
        if span >= self._duration - 0.01:
            return "Zoom: fit"
        return f"Zoom: {span:.0f}s  ·  {_fmt_clock(self._view_start)}–{_fmt_clock(self._view_end)}"

    def view_start(self) -> float:
        return self._view_start

    def duration(self) -> float:
        return self._duration

    def view_span(self) -> float:
        return self._view_span()

    def is_zoomed(self) -> bool:
        return self._duration > 0 and self._view_span() < self._duration - 0.05

    def set_view_start(self, start: float, *, emit: bool = True) -> None:
        """Pan so the visible window starts at `start` (keeps current zoom span)."""
        span = self._view_span()
        self._view_start = float(start)
        self._view_end = self._view_start + span
        self._clamp_view()
        self.update()
        if emit:
            self.view_changed.emit()

    def zoom_in(self, factor: float = 0.7) -> None:
        # Guard: Qt clicked(bool) must not be treated as factor (bool is an int subclass)
        if isinstance(factor, bool) or not isinstance(factor, (int, float)):
            factor = 0.7
        mid = (self._view_start + self._view_end) / 2
        self._zoom_at(mid, float(factor))

    def zoom_out(self, factor: float = 1.4) -> None:
        if isinstance(factor, bool) or not isinstance(factor, (int, float)):
            factor = 1.4
        mid = (self._view_start + self._view_end) / 2
        self._zoom_at(mid, float(factor))

    def zoom_fit(self) -> None:
        self._view_start = 0.0
        self._view_end = self._duration
        self.update()
        self.view_changed.emit()

    def zoom_to_selection(self, pad_ratio: float = 0.15) -> None:
        if not self._segments or not (0 <= self._selected < len(self._segments)):
            return
        seg = self._segments[self._selected]
        pad = max(1.0, (seg.end - seg.start) * pad_ratio)
        self._view_start = max(0.0, seg.start - pad)
        self._view_end = min(self._duration, seg.end + pad)
        if self._view_end - self._view_start < MIN_VIEW_SEC:
            mid = (seg.start + seg.end) / 2
            half = MIN_VIEW_SEC / 2
            self._view_start = max(0.0, mid - half)
            self._view_end = min(self._duration, mid + half)
        self._clamp_view()
        self.update()
        self.view_changed.emit()

    def _view_span(self) -> float:
        return max(0.001, self._view_end - self._view_start)

    def _clamp_view(self) -> None:
        span = self._view_span()
        if span > self._duration:
            self._view_start = 0.0
            self._view_end = self._duration
            return
        if self._view_start < 0:
            self._view_end -= self._view_start
            self._view_start = 0.0
        if self._view_end > self._duration:
            self._view_start -= self._view_end - self._duration
            self._view_end = self._duration
        self._view_start = max(0.0, self._view_start)

    def _zoom_at(self, anchor_t: float, factor: float) -> None:
        if self._duration <= 0:
            return
        old = self._view_span()
        new = max(MIN_VIEW_SEC, min(self._duration, old * factor))
        if abs(new - old) < 1e-6:
            self._clamp_view()
            self.update()
            self.view_changed.emit()
            return
        frac = (anchor_t - self._view_start) / old
        frac = max(0.0, min(1.0, frac))
        self._view_start = anchor_t - frac * new
        self._view_end = self._view_start + new
        self._clamp_view()
        self.update()
        self.view_changed.emit()

    def _x_to_time(self, x: float) -> float:
        w = max(1, self.width())
        t = self._view_start + (x / w) * self._view_span()
        return max(0.0, min(self._duration, t))

    def _time_to_x(self, t: float) -> float:
        span = self._view_span()
        if span <= 0:
            return 0.0
        return (t - self._view_start) / span * self.width()

    def _gap_regions(self) -> list[tuple[float, float]]:
        if self._duration <= 0:
            return []
        songs = sorted(self._segments, key=lambda s: s.start)
        gaps: list[tuple[float, float]] = []
        cursor = 0.0
        for seg in songs:
            if seg.start > cursor + 0.05:
                gaps.append((cursor, seg.start))
            cursor = max(cursor, seg.end)
        if self._duration > cursor + 0.05:
            gaps.append((cursor, self._duration))
        return gaps

    def _segment_at(self, t: float) -> int | None:
        for i, seg in enumerate(self._segments):
            if seg.start <= t <= seg.end:
                return i
        return None

    def _hit_handle(self, x: float, y: float) -> tuple[str, int] | None:
        h = self.height()
        top_zone = y <= h * 0.45
        bottom_zone = y >= h * 0.55

        start_hits: list[tuple[float, int]] = []
        end_hits: list[tuple[float, int]] = []
        for i, seg in enumerate(self._segments):
            sx = self._time_to_x(seg.start)
            ex = self._time_to_x(seg.end)
            if -HIT_TOL <= sx <= self.width() + HIT_TOL and abs(sx - x) <= HIT_TOL:
                start_hits.append((abs(sx - x), i))
            if -HIT_TOL <= ex <= self.width() + HIT_TOL and abs(ex - x) <= HIT_TOL:
                end_hits.append((abs(ex - x), i))

        start_hits.sort()
        end_hits.sort()

        if top_zone and start_hits:
            return ("start", start_hits[0][1])
        if bottom_zone and end_hits:
            return ("end", end_hits[0][1])

        candidates: list[tuple[float, str, int]] = []
        for dist, i in start_hits:
            candidates.append((dist, "start", i))
        for dist, i in end_hits:
            candidates.append((dist, "end", i))
        if not candidates:
            return None
        candidates.sort(key=lambda c: c[0])
        best = candidates[0]
        if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) < 1.0:
            if y < h / 2:
                for c in candidates:
                    if c[1] == "start":
                        return ("start", c[2])
            else:
                for c in candidates:
                    if c[1] == "end":
                        return ("end", c[2])
        return (best[1], best[2])

    def _draw_handle(self, painter: QPainter, x: int, kind: str, active: bool) -> None:
        if x < -HANDLE_W or x > self.width() + HANDLE_W:
            return
        color = QColor("#3d9b8f") if kind == "start" else QColor("#d4a24c")
        if active:
            color = QColor("#e8e4dc")
        painter.setBrush(color)
        painter.setPen(QPen(QColor("#0c0e14"), 1))
        if kind == "start":
            poly = QPolygonF(
                [
                    QPointF(x, HANDLE_H),
                    QPointF(x - HANDLE_W / 2, 1),
                    QPointF(x + HANDLE_W / 2, 1),
                ]
            )
        else:
            y0 = self.height() - 1
            poly = QPolygonF(
                [
                    QPointF(x, y0 - HANDLE_H),
                    QPointF(x - HANDLE_W / 2, y0),
                    QPointF(x + HANDLE_W / 2, y0),
                ]
            )
        painter.drawPolygon(poly)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor("#0c0e14"))
        # subtle top edge
        painter.fillRect(0, 0, rect.width(), 1, QColor("#2c3344"))
        w = rect.width()

        for gs, ge in self._gap_regions():
            if ge < self._view_start or gs > self._view_end:
                continue
            x1 = int(self._time_to_x(gs))
            x2 = int(self._time_to_x(ge))
            painter.fillRect(
                x1, 0, max(1, x2 - x1), rect.height(), QColor(60, 52, 40, 50)
            )

        for i, seg in enumerate(self._segments):
            if seg.end < self._view_start or seg.start > self._view_end:
                continue
            x1 = int(self._time_to_x(seg.start))
            x2 = int(self._time_to_x(seg.end))
            ww = max(2, x2 - x1)
            if i == self._selected:
                painter.fillRect(x1, 0, ww, rect.height(), QColor(61, 155, 143, 55))
                painter.setPen(QPen(QColor("#3d9b8f"), 2))
                painter.drawRect(x1, 1, ww - 1, rect.height() - 2)
            else:
                painter.fillRect(x1, 0, ww, rect.height(), QColor(61, 155, 143, 22))

        if self._peaks is not None and len(self._peaks) and self._duration > 0:
            mid = rect.height() / 2
            amp = mid * 0.75
            n = len(self._peaks)
            painter.setPen(QPen(QColor("#6a8f9e"), 1))
            i0 = max(0, int(self._view_start / self._duration * n) - 1)
            i1 = min(n, int(self._view_end / self._duration * n) + 2)
            for i in range(i0, i1):
                t = (i + 0.5) / n * self._duration
                x = int(self._time_to_x(t))
                if x < -1 or x > w + 1:
                    continue
                h = float(self._peaks[i]) * amp
                painter.drawLine(x, int(mid - h), x, int(mid + h))

        for i, seg in enumerate(self._segments):
            sx = int(self._time_to_x(seg.start))
            ex = int(self._time_to_x(seg.end))
            active = i == self._selected

            if -2 <= sx <= w + 2:
                painter.setPen(QPen(QColor("#3d9b8f"), 2 if active else 1))
                painter.drawLine(sx, 0, sx, rect.height())
                self._draw_handle(painter, sx, "start", active)

            if -2 <= ex <= w + 2:
                painter.setPen(QPen(QColor("#d4a24c"), 2 if active else 1))
                painter.drawLine(ex, 0, ex, rect.height())
                self._draw_handle(painter, ex, "end", active)

        if self._playhead is not None and self._duration > 0:
            if self._view_start <= self._playhead <= self._view_end:
                painter.setPen(QPen(QColor("#e07a6a"), 2))
                x = int(self._time_to_x(self._playhead))
                painter.drawLine(x, 0, x, rect.height())

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self._duration <= 0:
            return
        # Shift + wheel = pan left/right when zoomed
        if event.modifiers() & Qt.ShiftModifier:
            delta = event.angleDelta().y() or event.angleDelta().x()
            if delta == 0:
                return
            step = self._view_span() * 0.15 * (-1 if delta > 0 else 1)
            self.set_view_start(self._view_start + step)
            event.accept()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 0.8 if delta > 0 else 1.25
        anchor = self._x_to_time(event.position().x())
        self._zoom_at(anchor, factor)
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        y = event.position().y()

        if event.button() == Qt.MiddleButton or (
            event.button() == Qt.LeftButton and event.modifiers() & Qt.AltModifier
        ):
            self._panning = True
            self._pan_anchor_x = x
            self._pan_anchor_start = self._view_start
            self.setCursor(Qt.ClosedHandCursor)
            return

        if event.button() != Qt.LeftButton:
            return
        hit = self._hit_handle(x, y)
        if hit is not None:
            self._drag = hit
            self.set_selected(hit[1])
            return
        t = self._x_to_time(x)
        idx = self._segment_at(t)
        if idx is not None:
            self.set_selected(idx)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        t = self._x_to_time(event.position().x())
        idx = self._segment_at(t)
        if idx is not None:
            self.set_selected(idx)
        self.seek_play_requested.emit(t)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        y = event.position().y()

        if self._panning:
            dx = x - self._pan_anchor_x
            dt = -(dx / max(1, self.width())) * self._view_span()
            span = self._view_span()
            self._view_start = self._pan_anchor_start + dt
            self._view_end = self._view_start + span
            self._clamp_view()
            self.update()
            self.view_changed.emit()
            return

        if self._drag is not None:
            kind, i = self._drag
            t = self._x_to_time(x)
            seg = self._segments[i]
            min_len = 1.0
            prev_end = self._segments[i - 1].end if i > 0 else 0.0
            next_start = (
                self._segments[i + 1].start if i + 1 < len(self._segments) else self._duration
            )
            if kind == "start":
                lo = prev_end
                hi = seg.end - min_len
                seg.start = max(lo, min(hi, t))
            else:
                lo = seg.start + min_len
                hi = next_start
                seg.end = max(lo, min(hi, t))
            self.update()
            self.segments_changed.emit()
            return

        if self._hit_handle(x, y) is not None:
            self.setCursor(Qt.SizeHorCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None
        if self._panning:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
