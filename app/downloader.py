from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

from app.logging_setup import get_logger
from app.models import PlaylistEntry, PlaylistInfo, split_artist_title

try:
    import yt_dlp as _netdl
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Network downloader dependency missing. Run: pip install -r requirements.txt"
    ) from exc

log = get_logger("concert_cut.downloader")


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


def _normalize_fetch_url(url: str) -> tuple[str, bool]:
    """
    Return (url_to_fetch, single_item).

    Watch links often include &list= (mix / related). Those must NOT expand
    into a multi-page mix — treat them as one song. True playlists use
    /playlist?list=… without a primary watch video.
    """
    raw = (url or "").strip()
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    qs = parse_qs(parsed.query)

    video_id: str | None = None
    if "v" in qs and qs["v"]:
        video_id = qs["v"][0].strip()
    elif "youtu.be" in host:
        video_id = path.strip("/").split("/")[0] or None

    is_playlist_page = "/playlist" in path and "list" in qs

    if video_id and not is_playlist_page:
        clean = f"https://www.youtube.com/watch?v={video_id}"
        if clean != raw:
            log.info(
                "fetch_playlist: single-video URL (ignoring list/mix) %r -> %r",
                raw,
                clean,
            )
        return clean, True

    return raw, False


def _entry_from_info(info: dict, fallback_url: str) -> PlaylistEntry:
    entry_url = info.get("webpage_url") or fallback_url
    eid = str(info.get("id") or "")
    artist, song = split_artist_title(
        info.get("title") or "Track",
        fallback_artist=info.get("uploader") or info.get("channel") or "",
    )
    duration = float(info.get("duration") or 0.0)
    if duration <= 0 or not artist.strip() or not song.strip():
        raise RuntimeError(
            "This link has no usable song metadata (missing duration or artist). "
            "It may no longer be available."
        )
    return PlaylistEntry(
        id=eid or entry_url,
        title=song,
        artist=artist,
        duration=duration,
        url=entry_url,
    )


class _YtdlLogger:
    """Bridge yt-dlp messages into our logger."""

    def debug(self, msg: str) -> None:
        # yt-dlp dumps a lot at debug; keep noise down
        if msg.startswith("[debug] "):
            log.debug(msg)
        else:
            log.info(msg)

    def info(self, msg: str) -> None:
        log.info(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def error(self, msg: str) -> None:
        log.error(msg)


def _entry_url(entry: dict) -> str | None:
    for key in ("webpage_url", "original_url"):
        val = entry.get(key)
        if isinstance(val, str) and val.strip().startswith("http"):
            return val.strip()

    url = entry.get("url")
    if isinstance(url, str) and url.strip():
        url = url.strip()
        if url.startswith("http"):
            return url
        # Flat playlist extracts often give a bare id, not a full URL.
        eid = str(entry.get("id") or url)
        ie = str(entry.get("ie_key") or entry.get("extractor_key") or "").lower()
        if "youtube" in ie or (len(eid) == 11 and "/" not in eid and "://" not in eid):
            built = f"https://www.youtube.com/watch?v={eid}"
            log.debug("Built watch URL from bare id %s -> %s", eid, built)
            return built
        return url

    eid = entry.get("id")
    if eid:
        eid_s = str(eid)
        if len(eid_s) == 11 and "/" not in eid_s:
            return f"https://www.youtube.com/watch?v={eid_s}"
        return eid_s
    return None


_UNAVAILABLE_TITLE_MARKERS = (
    "[deleted video]",
    "[private video]",
    "[unavailable",
    "video unavailable",
    "this video is unavailable",
    "this video isn't available",
    "has been removed",
)


def _is_unavailable_entry(raw: dict) -> str | None:
    """Return a reason string if the entry should be hidden, else None."""
    title = str(raw.get("title") or "").strip()
    title_l = title.lower()
    for marker in _UNAVAILABLE_TITLE_MARKERS:
        if marker in title_l or title_l == marker.strip("[]"):
            return f"title={title!r}"

    availability = str(raw.get("availability") or "").lower()
    if availability in {
        "private",
        "premium_only",
        "subscriber_only",
        "needs_auth",
        "unavailable",
        "blocked",
    }:
        return f"availability={availability}"

    # Flat extracts for dead videos often omit duration.
    dur = raw.get("duration")
    try:
        dur_f = float(dur) if dur is not None else 0.0
    except (TypeError, ValueError):
        dur_f = 0.0
    if dur_f <= 0:
        return "missing duration"

    # Flat entries for dead videos sometimes have no id / no duration and a placeholder title
    if not raw.get("id") and not raw.get("url") and not raw.get("webpage_url"):
        return "missing id/url"

    return None


def fetch_playlist(url: str) -> PlaylistInfo:
    """List tracks in a network playlist without downloading media.

    A watch/video URL (even with &list= mix params) loads only that one song.
    A /playlist?list=… URL loads the full playlist.
    """
    fetch_url, single_item = _normalize_fetch_url(url)
    log.info(
        "fetch_playlist: start url=%r fetch_url=%r single=%s",
        url,
        fetch_url,
        single_item,
    )

    if single_item:
        opts: dict = {
            "quiet": True,
            "no_warnings": False,
            "skip_download": True,
            "noplaylist": True,
            "ignoreerrors": False,
            "logger": _YtdlLogger(),
        }
        with _netdl.YoutubeDL(opts) as client:
            try:
                info = client.extract_info(fetch_url, download=False)
            except Exception:
                log.exception("fetch_playlist: single extract failed for %r", fetch_url)
                raise
            if info is None:
                raise RuntimeError("Failed to load song info")
            # Nested playlist leftover — still take the video itself
            if info.get("_type") == "playlist" and info.get("entries"):
                log.warning("fetch_playlist: unexpected playlist for single URL; taking first entry")
                first = next((e for e in info["entries"] if e), None)
                if not first:
                    raise RuntimeError("No song found for this link")
                info = first
            entry = _entry_from_info(info, fetch_url)
            log.info(
                "fetch_playlist: single song artist=%r title=%r duration=%s",
                entry.artist,
                entry.title,
                entry.duration,
            )
            return PlaylistInfo(
                title=entry.title,
                source_url=entry.url,
                entries=[entry],
            )

    opts = {
        "quiet": True,
        "no_warnings": False,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "noplaylist": False,
        "ignoreerrors": True,
        "logger": _YtdlLogger(),
    }
    with _netdl.YoutubeDL(opts) as client:
        try:
            info = client.extract_info(fetch_url, download=False)
        except Exception:
            log.exception("fetch_playlist: extract_info failed for %r", fetch_url)
            raise

        if info is None:
            log.error("fetch_playlist: extract_info returned None")
            raise RuntimeError("Failed to load playlist info")

        log.info(
            "fetch_playlist: type=%r title=%r id=%r keys=%s",
            info.get("_type"),
            info.get("title"),
            info.get("id"),
            sorted(info.keys()),
        )

        raw_entries = info.get("entries")
        if raw_entries is None:
            log.info("fetch_playlist: no entries — single item fallback")
            entry = _entry_from_info(info, fetch_url)
            return PlaylistInfo(
                title=info.get("title") or entry.title,
                source_url=info.get("webpage_url") or fetch_url,
                entries=[entry],
            )

        # Generators must be materialized once.
        try:
            raw_list = list(raw_entries)
        except Exception:
            log.exception("fetch_playlist: failed to materialize entries")
            raise

        log.info("fetch_playlist: raw entry count=%d", len(raw_list))

        entries: list[PlaylistEntry] = []
        skipped = 0
        for i, raw in enumerate(raw_list):
            if not raw:
                skipped += 1
                log.debug("fetch_playlist: skip empty entry index=%d", i)
                continue
            if not isinstance(raw, dict):
                skipped += 1
                log.warning(
                    "fetch_playlist: skip non-dict entry index=%d type=%s",
                    i,
                    type(raw).__name__,
                )
                continue

            entry_url = _entry_url(raw)
            if not entry_url:
                skipped += 1
                log.warning(
                    "fetch_playlist: skip entry index=%d — no url keys=%s",
                    i,
                    sorted(raw.keys()),
                )
                continue

            reason = _is_unavailable_entry(raw)
            if reason:
                skipped += 1
                log.info(
                    "fetch_playlist: skip unavailable index=%d (%s) title=%r",
                    i,
                    reason,
                    raw.get("title"),
                )
                continue

            eid = str(raw.get("id") or entry_url or i)
            raw_title = (raw.get("title") or f"Track {i + 1}").strip()
            artist, song = split_artist_title(
                raw_title,
                fallback_artist=str(
                    raw.get("uploader") or raw.get("channel") or raw.get("artist") or ""
                ),
            )
            if not artist.strip():
                skipped += 1
                log.info(
                    "fetch_playlist: skip offline/incomplete index=%d — no artist title=%r",
                    i,
                    raw_title,
                )
                continue
            if not song.strip():
                skipped += 1
                log.info(
                    "fetch_playlist: skip offline/incomplete index=%d — no song title=%r",
                    i,
                    raw_title,
                )
                continue
            dur = raw.get("duration")
            duration = float(dur) if dur is not None else 0.0
            if duration <= 0:
                skipped += 1
                log.info(
                    "fetch_playlist: skip offline index=%d — no duration title=%r",
                    i,
                    raw_title,
                )
                continue
            entries.append(
                PlaylistEntry(
                    id=eid,
                    title=song,
                    artist=artist,
                    duration=duration,
                    url=entry_url,
                )
            )
            if len(entries) <= 3:
                log.info(
                    "fetch_playlist: sample[%d] artist=%r song=%r url=%r duration=%s",
                    len(entries) - 1,
                    artist,
                    song,
                    entry_url,
                    duration,
                )

        log.info(
            "fetch_playlist: kept=%d skipped=%d title=%r",
            len(entries),
            skipped,
            info.get("title"),
        )

        if not entries:
            raise RuntimeError(
                "Playlist has no downloadable tracks "
                f"(raw={len(raw_list)}, skipped={skipped}). Check terminal logs."
            )

        return PlaylistInfo(
            title=info.get("title") or "Playlist",
            entries=entries,
            source_url=info.get("webpage_url") or fetch_url,
        )


def download(
    url: str,
    *,
    audio_only: bool = True,
    output_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> DownloadResult:
    """Download media from a network URL into output_dir."""
    out_dir = output_dir or _default_work_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("download: start url=%r audio_only=%s out=%s", url, audio_only, out_dir)

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
        "no_warnings": False,
        "logger": _YtdlLogger(),
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

    with _netdl.YoutubeDL(opts) as client:
        try:
            info = client.extract_info(url, download=True)
        except Exception:
            log.exception("download: extract_info failed for %r", url)
            raise
        if info is None:
            raise RuntimeError("Failed to extract media info")

        requested = client.prepare_filename(info)
        path = Path(requested)
        if audio_only:
            candidates = [
                path.with_suffix(".m4a"),
                path.with_suffix(".mp3"),
                path.with_suffix(".opus"),
                path.with_suffix(".webm"),
                path,
            ]
            path = next((p for p in candidates if p.exists()), candidates[0])
            if not path.exists():
                vid = info.get("id", "")
                matches = list(out_dir.glob(f"*[{vid}].*")) if vid else []
                if matches:
                    path = matches[0]

        if not path.exists():
            log.error("download: file missing near %s", requested)
            raise FileNotFoundError(f"Downloaded file not found near {requested}")

        log.info("download: ok path=%s title=%r", path, info.get("title"))
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
