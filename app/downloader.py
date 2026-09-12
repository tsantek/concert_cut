from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yt_dlp


ProgressCallback = Callable[[float, str], None]


@dataclass
class DownloadResult:
    path: Path
    title: str
    uploader: str
    duration: float
    chapters: list[dict] | None
    source_url: str = ""
    video_id: str = ""


def _default_work_dir() -> Path:
    return Path.home() / "Downloads" / "concert_cut"


def download(
    url: str,
    *,
    audio_only: bool = True,
    output_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> DownloadResult:
    out_dir = output_dir or _default_work_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if not on_progress:
            return
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            pct = (downloaded / total * 100.0) if total else 0.0
            speed = d.get("_speed_str") or ""
            eta = d.get("_eta_str") or ""
            msg = f"Downloading… {pct:.0f}%"
            if speed:
                msg += f"  {speed}"
            if eta:
                msg += f"  ETA {eta}"
            on_progress(min(pct, 99.0), msg)
        elif status == "finished":
            on_progress(99.0, "Processing download…")

    outtmpl = str(out_dir / "%(title).200B [%(id)s].%(ext)s")

    opts: dict = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
    }

    if audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "0",
            }
        ]
    else:
        opts["format"] = "bv*+ba/b"
        opts["merge_output_format"] = "mp4"

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info is None:
            raise RuntimeError("Failed to extract video info")

        # After audio extract, path may differ from requested template
        requested = ydl.prepare_filename(info)
        path = Path(requested)
        if audio_only:
            # Extension becomes m4a after FFmpegExtractAudio
            candidates = [
                path.with_suffix(".m4a"),
                path.with_suffix(".mp3"),
                path.with_suffix(".opus"),
                path.with_suffix(".webm"),
                path,
            ]
            path = next((p for p in candidates if p.exists()), candidates[0])
            if not path.exists():
                # Fallback: search by video id
                vid = info.get("id", "")
                matches = list(out_dir.glob(f"*[{vid}].*")) if vid else []
                if matches:
                    path = matches[0]

        if not path.exists():
            raise FileNotFoundError(f"Downloaded file not found near {requested}")

        chapters = info.get("chapters") or None
        return DownloadResult(
            path=path,
            title=info.get("title") or path.stem,
            uploader=info.get("uploader") or info.get("channel") or "",
            duration=float(info.get("duration") or 0.0),
            chapters=chapters,
            source_url=info.get("webpage_url") or url,
            video_id=str(info.get("id") or ""),
        )


def open_local(path: Path) -> DownloadResult:
    """Use an existing audio/video file from disk (no download)."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    duration = _probe_duration(path)
    return DownloadResult(
        path=path,
        title=path.stem,
        uploader="",
        duration=duration,
        chapters=None,
        source_url="",
        video_id="",
    )

def _probe_duration(path: Path) -> float:
    import json
    import subprocess

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode == 0 and proc.stdout:
        try:
            data = json.loads(proc.stdout)
            dur = data.get("format", {}).get("duration")
            if dur is not None:
                return float(dur)
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return 0.0
