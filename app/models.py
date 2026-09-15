from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Segment:
    """One song with start/end. Gaps between songs are implicit pauses (not listed)."""

    title: str
    start: float  # seconds
    end: float  # seconds
    include_export: bool = True

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def make_song(title: str, start: float, end: float) -> Segment:
    return Segment(title=title, start=start, end=end, include_export=True)


@dataclass
class ConcertProject:
    source_path: Path
    video_title: str = ""
    uploader: str = ""
    name: str = ""
    duration: float = 0.0
    segments: list[Segment] = field(default_factory=list)
    used_chapters: bool = False
    source_url: str = ""
    video_id: str = ""
    chapters: list[dict] | None = None
    loaded_from_sidecar: bool = False


@dataclass
class PlaylistEntry:
    id: str
    title: str  # song name
    duration: float
    url: str
    artist: str = ""
    path: Path | None = None


@dataclass
class PlaylistInfo:
    title: str
    entries: list[PlaylistEntry] = field(default_factory=list)
    source_url: str = ""


def split_artist_title(raw_title: str, *, fallback_artist: str = "") -> tuple[str, str]:
    """Split 'Artist - Song' into (artist, song)."""
    text = (raw_title or "").strip()
    if " - " in text:
        left, right = text.split(" - ", 1)
        if left.strip() and right.strip():
            return left.strip(), right.strip()
    return (fallback_artist or "").strip(), text or "Untitled"
