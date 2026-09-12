from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models import ConcertProject, Segment

SIDECAR_SUFFIX = ".concertcut.json"
STORE_VERSION = 1


def sidecar_path(media_path: Path) -> Path:
    """`concert.m4a` → `concert.m4a.concertcut.json` (stays next to the file)."""
    return Path(str(media_path) + SIDECAR_SUFFIX)


def save_project(project: ConcertProject) -> Path:
    """Persist songs, metadata, and chapters beside the media file."""
    path = sidecar_path(project.source_path)
    payload: dict[str, Any] = {
        "version": STORE_VERSION,
        "source_path": str(project.source_path),
        "video_title": project.video_title,
        "uploader": project.uploader,
        "name": project.name,
        "duration": project.duration,
        "used_chapters": project.used_chapters,
        "source_url": project.source_url,
        "video_id": project.video_id,
        "chapters": project.chapters,
        "segments": [
            {
                "title": s.title,
                "start": s.start,
                "end": s.end,
                "include_export": s.include_export,
            }
            for s in project.segments
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_project(media_path: Path) -> ConcertProject | None:
    """Load saved cuts/metadata for a local media file, if the sidecar exists."""
    path = sidecar_path(media_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    segments_raw = data.get("segments") or []
    segments = [
        Segment(
            title=str(s.get("title") or f"Track {i + 1:02d}"),
            start=float(s.get("start") or 0.0),
            end=float(s.get("end") or 0.0),
            include_export=bool(s.get("include_export", True)),
        )
        for i, s in enumerate(segments_raw)
        if float(s.get("end") or 0) > float(s.get("start") or 0)
    ]
    if not segments:
        return None

    return ConcertProject(
        source_path=media_path.resolve(),
        video_title=str(data.get("video_title") or media_path.stem),
        uploader=str(data.get("uploader") or ""),
        name=str(data.get("name") or data.get("uploader") or data.get("video_title") or ""),
        duration=float(data.get("duration") or 0.0),
        segments=segments,
        used_chapters=bool(data.get("used_chapters")),
        source_url=str(data.get("source_url") or ""),
        video_id=str(data.get("video_id") or ""),
        chapters=data.get("chapters"),
        loaded_from_sidecar=True,
    )
