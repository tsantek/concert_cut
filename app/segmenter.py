from __future__ import annotations

from pathlib import Path

import numpy as np

from app.models import Segment, make_song


def segments_from_chapters(
    chapters: list[dict],
    duration: float,
) -> list[Segment]:
    segments: list[Segment] = []
    for i, ch in enumerate(chapters):
        start = float(ch.get("start_time") or 0.0)
        end = ch.get("end_time")
        if end is None:
            if i + 1 < len(chapters):
                end = float(chapters[i + 1].get("start_time") or duration)
            else:
                end = duration
        else:
            end = float(end)
        title = (ch.get("title") or "").strip() or f"Track {i + 1:02d}"
        if end <= start:
            continue
        segments.append(make_song(title, start, end))
    return segments


def _energy_curve(
    audio_path: Path,
    *,
    hop_sec: float = 0.5,
) -> tuple[np.ndarray, float, float]:
    samples, sr = _load_mono(audio_path)
    if samples.size == 0:
        return np.array([]), hop_sec, 0.0
    duration = samples.size / float(sr)
    hop = max(1, int(sr * hop_sec))
    n = samples.size // hop
    if n < 4:
        return np.array([]), hop_sec, duration
    framed = samples[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(framed.astype(np.float64) ** 2, axis=1))
    win = max(3, int(3.0 / hop_sec))
    kernel = np.ones(win) / win
    smooth = np.convolve(rms, kernel, mode="same")
    return smooth, hop_sec, duration


def _low_regions(
    smooth: np.ndarray,
    hop_sec: float,
    *,
    threshold_ratio: float = 0.38,
    min_pause_sec: float = 4.0,
) -> list[tuple[float, float]]:
    if smooth.size == 0:
        return []
    med = float(np.median(smooth)) + 1e-9
    p25 = float(np.percentile(smooth, 25)) + 1e-9
    threshold = min(med * threshold_ratio, p25 * 0.85)

    low = smooth < threshold
    min_hops = max(1, int(min_pause_sec / hop_sec))
    regions: list[tuple[float, float]] = []
    i = 0
    n = len(low)
    while i < n:
        if not low[i]:
            i += 1
            continue
        j = i
        while j < n and low[j]:
            j += 1
        if j - i >= min_hops:
            regions.append((i * hop_sec, j * hop_sec))
        i = j
    return regions


def segments_from_energy(
    audio_path: Path,
    duration: float,
    *,
    min_song_sec: float = 45.0,
    min_pause_sec: float = 4.0,
) -> list[Segment]:
    """
    Songs only. Quiet stretches become gaps between songs (not menu items).
    """
    smooth, hop_sec, wave_dur = _energy_curve(audio_path)
    if duration <= 0:
        duration = wave_dur
    if smooth.size == 0 or duration <= 0:
        return [make_song("Track 01", 0.0, max(duration, 0.0))]

    pauses = _low_regions(smooth, hop_sec, min_pause_sec=min_pause_sec)
    return _songs_between_gaps(pauses, duration, min_song_sec=min_song_sec)


def tighten_song_bounds(
    audio_path: Path,
    segments: list[Segment],
    duration: float,
    *,
    min_pause_sec: float = 4.0,
    min_song_sec: float = 20.0,
) -> list[Segment]:
    """
    Open gaps where audio is quiet. Menu stays songs-only;
    empty spans between begin/end lines are pauses on the graph.
    """
    if not segments:
        return segments_from_energy(audio_path, duration, min_pause_sec=min_pause_sec)

    smooth, hop_sec, wave_dur = _energy_curve(audio_path)
    if duration <= 0:
        duration = wave_dur or (segments[-1].end if segments else 0.0)
    if smooth.size == 0:
        return list(segments)

    pauses = _low_regions(smooth, hop_sec, min_pause_sec=min_pause_sec)
    if not pauses:
        return list(segments)

    out: list[Segment] = []
    for seg in segments:
        pieces = [(seg.start, seg.end, seg.title)]
        for ps, pe in pauses:
            next_pieces: list[tuple[float, float, str]] = []
            for a, b, title in pieces:
                if pe <= a or ps >= b:
                    next_pieces.append((a, b, title))
                    continue
                # Pause overlaps [a,b] → keep non-pause parts (gap where pause was)
                if ps > a + 0.5:
                    next_pieces.append((a, min(ps, b), title))
                if pe < b - 0.5:
                    cont = title if (ps <= a) else f"{title} (cont.)"
                    next_pieces.append((max(pe, a), b, cont))
            pieces = next_pieces

        for a, b, title in pieces:
            if b - a >= min_song_sec * 0.3:
                out.append(make_song(title, a, b))

    return _dedupe_songs(out) or list(segments)


def _songs_between_gaps(
    pauses: list[tuple[float, float]],
    duration: float,
    *,
    min_song_sec: float,
) -> list[Segment]:
    songs: list[Segment] = []
    cursor = 0.0
    song_i = 1
    for ps, pe in pauses:
        ps = max(0.0, min(ps, duration))
        pe = max(0.0, min(pe, duration))
        if pe <= ps:
            continue
        if ps - cursor >= min_song_sec * 0.4:
            songs.append(make_song(f"Track {song_i:02d}", cursor, ps))
            song_i += 1
        cursor = max(cursor, pe)
    if duration - cursor >= min_song_sec * 0.4:
        songs.append(make_song(f"Track {song_i:02d}", cursor, duration))
    elif not songs and duration > 0:
        songs.append(make_song("Track 01", 0.0, duration))
    return songs


def _dedupe_songs(songs: list[Segment]) -> list[Segment]:
    if not songs:
        return songs
    cleaned: list[Segment] = []
    for s in songs:
        if s.end - s.start < 1.0:
            continue
        if cleaned and s.start < cleaned[-1].end:
            # Prevent overlap: push start to previous end (creates no gap if equal)
            s.start = cleaned[-1].end
        if s.end - s.start >= 1.0:
            cleaned.append(s)
    return cleaned


def refine_ends_with_energy(
    audio_path: Path,
    segments: list[Segment],
    duration: float,
    *,
    min_pause_sec: float = 2.0,
    min_song_sec: float = 25.0,
) -> list[Segment]:
    """
    Keep setlist/chapter *start* times; pull each song's *end* back to the
    quiet stretch (applause / speak) before the next song starts.
    Leaves a gap on the timeline when a pause is found.
    """
    if len(segments) < 1:
        return segments

    smooth, hop_sec, wave_dur = _energy_curve(audio_path)
    if duration <= 0:
        duration = wave_dur or segments[-1].end
    if smooth.size == 0:
        return segments

    pauses = _low_regions(
        smooth, hop_sec, min_pause_sec=min_pause_sec, threshold_ratio=0.42
    )
    if not pauses:
        return segments

    refined: list[Segment] = []
    for i, seg in enumerate(segments):
        hard_end = (
            segments[i + 1].start if i + 1 < len(segments) else duration
        )
        hard_end = min(hard_end, duration)
        search_lo = seg.start + min_song_sec
        if hard_end - seg.start < min_song_sec + 1.0 or search_lo >= hard_end - 1.0:
            refined.append(
                make_song(seg.title, seg.start, max(seg.start + 1.0, hard_end))
            )
            continue

        # Quiet regions that begin inside this song's search window
        candidates = [
            ps
            for ps, pe in pauses
            if search_lo <= ps < hard_end - 0.5 and pe - ps >= min_pause_sec
        ]
        if candidates:
            # Prefer the last quiet stretch before the next setlist mark
            # (typical applause / talk-down before the next song).
            end = max(candidates)
            # Don't cut absurdly early: stay in the last 55% of the nominal span
            earliest = seg.start + (hard_end - seg.start) * 0.45
            end = max(end, earliest)
            end = min(end, hard_end)
        else:
            end = hard_end

        if end - seg.start < min_song_sec * 0.5:
            end = hard_end

        refined.append(
            Segment(
                title=seg.title,
                start=seg.start,
                end=end,
                include_export=seg.include_export,
            )
        )

    return refined


def build_segments(
    audio_path: Path,
    duration: float,
    chapters: list[dict] | None,
) -> tuple[list[Segment], bool]:
    if chapters:
        segs = segments_from_chapters(chapters, duration)
        if segs:
            # Open gaps for quiet stretches; menu stays song-only
            return tighten_song_bounds(audio_path, segs, duration), True
    return segments_from_energy(audio_path, duration), False


# Back-compat alias used by UI
def split_out_pauses(
    audio_path: Path,
    segments: list[Segment],
    duration: float,
    **kwargs,
) -> list[Segment]:
    return tighten_song_bounds(audio_path, segments, duration, **kwargs)


def _load_mono(path: Path, max_seconds: float | None = None) -> tuple[np.ndarray, int]:
    try:
        import soundfile as sf

        data, sr = sf.read(str(path), always_2d=True, dtype="float32")
        mono = data.mean(axis=1)
        if max_seconds is not None:
            mono = mono[: int(sr * max_seconds)]
        return mono, int(sr)
    except Exception:
        return _load_mono_ffmpeg(path, max_seconds)


def _load_mono_ffmpeg(
    path: Path, max_seconds: float | None = None
) -> tuple[np.ndarray, int]:
    import subprocess

    sr = 22050
    cmd = ["ffmpeg", "-v", "error", "-i", str(path)]
    if max_seconds is not None:
        cmd += ["-t", str(max_seconds)]
    cmd += ["-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout:
        return np.array([], dtype=np.float32), sr
    return np.frombuffer(proc.stdout, dtype=np.float32), sr


def waveform_peaks(
    path: Path,
    *,
    num_peaks: int = 4000,
    duration_hint: float | None = None,
) -> tuple[np.ndarray, float]:
    samples, sr = _load_mono(path)
    if samples.size == 0:
        return np.zeros(num_peaks, dtype=np.float32), duration_hint or 0.0
    duration = samples.size / float(sr)
    bin_size = max(1, samples.size // num_peaks)
    usable = samples[: bin_size * num_peaks]
    shaped = usable.reshape(num_peaks, bin_size)
    peaks = np.max(np.abs(shaped), axis=1).astype(np.float32)
    peak_max = float(peaks.max()) or 1.0
    peaks /= peak_max
    return peaks, duration
