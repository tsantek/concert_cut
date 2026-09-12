from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from app.models import Segment

ProgressCallback = Callable[[int, int, str], None]  # index (1-based), total, title


def _sanitize(name: str) -> str:
    name = name.strip() or "Untitled"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:180] or "Untitled"


def export_segments(
    source: Path,
    segments: list[Segment],
    *,
    name: str,
    output_dir: Path,
    fmt: str = "mp3",
    on_progress: ProgressCallback | None = None,
) -> list[Path]:
    """
    Export each segment with embedded metadata.
    Name → artist + album; Title → title; track number set.
    """
    album_name = _sanitize(name)
    dest_root = output_dir / album_name
    dest_root.mkdir(parents=True, exist_ok=True)

    total = len(segments)
    written: list[Path] = []
    for i, seg in enumerate(segments, start=1):
        if on_progress:
            on_progress(i, total, seg.title)
        out_path = _export_one(
            source,
            seg,
            index=i,
            total=total,
            name=name,
            album_name=album_name,
            dest_root=dest_root,
            fmt=fmt,
        )
        written.append(out_path)

    return written


def _export_one(
    source: Path,
    seg: Segment,
    *,
    index: int,
    total: int,
    name: str,
    album_name: str,
    dest_root: Path,
    fmt: str,
) -> Path:
    title = _sanitize(seg.title)
    filename = f"{index:02d} - {title}.{fmt}"
    out_path = dest_root / filename

    duration = max(0.05, seg.end - seg.start)
    # -ss before -i: fast input seek; accurate enough for song cuts
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-ss",
        f"{seg.start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-metadata",
        f"title={seg.title.strip() or title}",
        "-metadata",
        f"artist={name.strip() or album_name}",
        "-metadata",
        f"album={name.strip() or album_name}",
        "-metadata",
        f"track={index}/{total}",
    ]

    if fmt == "mp3":
        cmd += ["-codec:a", "libmp3lame", "-q:a", "2"]
    elif fmt == "m4a":
        cmd += ["-codec:a", "aac", "-b:a", "256k"]
    elif fmt == "wav":
        cmd += ["-codec:a", "pcm_s16le"]
    else:
        cmd += ["-codec:a", "copy"]

    cmd.append(str(out_path))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"ffmpeg failed for {filename}")
    return out_path
