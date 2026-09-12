from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.models import Segment
from app.segmenter import refine_ends_with_energy, segments_from_chapters

# One or more "TIMESTAMP [-:|] Title" chunks (also works for a full single-line setlist)
_INLINE_CHUNKS = re.compile(
    r"""
    (?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})
    (?:
        \s*[\-\–\—\:\|]\s*   # 0:01 - Title  /  0:01 : Title
      | \s+                   # 0:01 Title
    )
    (?P<title>.+?)
    (?=
        \s+(?:\d{1,2}:)?\d{1,2}:\d{2}\b   # next timestamp
      | $
    )
    """,
    re.VERBOSE,
)

# Single line: timestamp at start
_LINE_START_TS = re.compile(
    r"""
    ^\s*
    (?:\#?\d+[\.\)\:\-]\s+)?
    [\[\(]?
    (?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})
    [\]\)]?
    \s*
    (?:[\-\–\—\:\|·•\.]+|\s+)?
    \s*
    (?P<title>.+?)
    \s*$
    """,
    re.VERBOSE,
)

# Single line: title first, timestamp at end
_LINE_END_TS = re.compile(
    r"""
    ^\s*
    (?P<title>.+?)
    \s*
    (?:[\-\–\—\:\|]\s*)?
    [\[\(]?
    (?P<ts>(?:\d{1,2}:)?\d{1,2}:\d{2})
    [\]\)]?
    \s*$
    """,
    re.VERBOSE,
)


@dataclass
class SetlistParseResult:
    chapters: list[dict]
    segments: list[Segment]
    skipped_lines: list[str]


def parse_timestamp(ts: str) -> float:
    """Parse M:SS or H:MM:SS (also accepts MM:SS with leading zeros)."""
    parts = [int(p) for p in ts.strip().split(":")]
    if len(parts) == 2:
        m, s = parts
        if s >= 60:
            raise ValueError(f"Invalid seconds in timestamp: {ts}")
        return float(m * 60 + s)
    if len(parts) == 3:
        h, m, s = parts
        if m >= 60 or s >= 60:
            raise ValueError(f"Invalid timestamp: {ts}")
        return float(h * 3600 + m * 60 + s)
    raise ValueError(f"Unsupported timestamp: {ts}")


def _clean_title(title: str) -> str:
    title = title.strip()
    title = re.sub(r"^[\-\–\—\:\|·•\.]+\s*", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    # Drop trailing separators left by chunking
    title = re.sub(r"[\-\–\—\:\|·•\.]+$", "", title).strip()
    return title


def parse_setlist_entries(line: str) -> list[tuple[float, str]]:
    """
    Parse one or more songs from a line.

    Supports:
      0:01 : Wrong ones
      0:01 - Circles
      6:19 Circles
      1:24:30 Sunflower
      [0:01] Wrong ones
      1. 0:01 Wrong ones
      Wrong ones 0:01
      # inline multi-song line:
      1:14 - All The Little Lights 4:54 - Life's For The Living 9:30 - Riding To New York
    """
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return []
    if not re.search(r"\d:\d{2}", raw):
        return []

    # Prefer splitting every timestamp…title chunk (handles single- and multi-song lines)
    inline: list[tuple[float, str]] = []
    for m in _INLINE_CHUNKS.finditer(raw):
        title = _clean_title(m.group("title"))
        if not title:
            continue
        try:
            inline.append((parse_timestamp(m.group("ts")), title))
        except ValueError:
            continue
    if len(inline) >= 2:
        return inline
    if len(inline) == 1:
        # One chunk is fine for normal single-song lines
        return inline

    m = _LINE_START_TS.match(raw)
    if m:
        title = _clean_title(m.group("title"))
        if title:
            return [(parse_timestamp(m.group("ts")), title)]

    m = _LINE_END_TS.match(raw)
    if m:
        title = _clean_title(m.group("title"))
        if title and not re.fullmatch(r"[\d:\[\]\(\)\s\-\–\—\:\|]+", title):
            return [(parse_timestamp(m.group("ts")), title)]

    return []


def parse_setlist(
    text: str,
    duration: float,
    *,
    audio_path: Path | None = None,
    refine_ends: bool = True,
) -> SetlistParseResult:
    chapters: list[dict] = []
    skipped: list[str] = []
    seen_starts: set[float] = set()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            entries = parse_setlist_entries(stripped)
        except ValueError:
            skipped.append(stripped)
            continue
        if not entries:
            skipped.append(stripped)
            continue

        added_any = False
        for start, title in entries:
            if start in seen_starts:
                continue
            if duration > 0 and start >= duration + 1.0:
                continue
            seen_starts.add(start)
            chapters.append({"start_time": start, "title": title})
            added_any = True
        if not added_any:
            skipped.append(stripped)

    chapters.sort(key=lambda c: float(c["start_time"]))
    if duration > 0:
        for ch in chapters:
            ch["start_time"] = max(
                0.0, min(float(ch["start_time"]), max(0.0, duration - 0.05))
            )

    segments = segments_from_chapters(chapters, duration) if chapters else []

    # Pull ends back to quiet gaps before the next setlist mark
    if refine_ends and audio_path is not None and segments:
        segments = refine_ends_with_energy(audio_path, segments, duration)
        # Keep chapter end_times in sync for the sidecar
        for i, seg in enumerate(segments):
            if i < len(chapters):
                chapters[i]["end_time"] = seg.end
                chapters[i]["title"] = seg.title

    return SetlistParseResult(chapters=chapters, segments=segments, skipped_lines=skipped)
