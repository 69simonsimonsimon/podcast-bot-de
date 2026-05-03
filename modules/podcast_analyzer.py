"""
Podcast Analyzer — Podcast Bot DE
===================================
1. Holt neuestes Video eines Kanals via yt-dlp
2. Liest YouTube-Kapitel aus
3. Claude wählt das viral-stärkste Kapitel
4. Gibt Segment-Timestamps zurück
"""

import json
import logging
import os
import subprocess
import tempfile
import random
from datetime import timedelta
from pathlib import Path

import anthropic

logger = logging.getLogger("podcastbot")


def _cookies_args() -> list:
    """Gibt yt-dlp Cookie-Argumente zurück falls YOUTUBE_COOKIES gesetzt."""
    cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not cookies:
        return []
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, prefix="yt_cookies_")
    tmp.write(cookies)
    tmp.flush()
    tmp.close()
    return ["--cookies", tmp.name]

# Mindest- und Maximal-Länge eines Highlights in Sekunden
HIGHLIGHT_MIN_SEC = 55
HIGHLIGHT_MAX_SEC = 88   # TikTok-optimiert


def _run_ytdlp(args: list, timeout: int = 60) -> str:
    result = subprocess.run(
        ["yt-dlp"] + args,
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip()


def get_latest_video(channel_url: str, skip_ids: set = None) -> dict | None:
    """
    Holt die neuesten Videos eines Kanals und gibt das erste nicht-verwendete zurück.
    Gibt {id, title, url, duration, chapters} zurück oder None.
    """
    skip_ids = skip_ids or set()
    logger.info(f"[analyzer] Fetche Kanal: {channel_url}")

    raw = _run_ytdlp([
        "--flat-playlist",
        "--playlist-end", "10",
        "--print", '%(id)s\t%(title)s\t%(duration)s',
        "--no-warnings",
    ] + _cookies_args() + [channel_url], timeout=45)

    if not raw:
        logger.warning("[analyzer] Keine Videos gefunden")
        return None

    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        vid_id, title = parts[0], parts[1]
        duration = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0

        if vid_id in skip_ids:
            continue
        # Nur überspringen wenn Dauer bekannt UND unter 10 Minuten
        if duration > 0 and duration < 600:
            continue

        video_url = f"https://www.youtube.com/watch?v={vid_id}"
        duration, chapters = _get_meta(video_url)

        # Nochmals prüfen mit echten Metadaten
        if duration > 0 and duration < 600:
            logger.debug(f"[analyzer] Skip (zu kurz {duration}s): {title[:50]}")
            continue

        logger.info(f"[analyzer] Video: {title[:60]}  ({duration//60} min, {len(chapters)} Kapitel)")
        return {
            "id":       vid_id,
            "title":    title,
            "url":      video_url,
            "duration": duration,
            "chapters": chapters,
        }

    return None


def _get_meta(video_url: str) -> tuple[int, list[dict]]:
    """Holt Dauer + Kapitel eines Videos (ohne Download). Gibt (duration_sec, chapters) zurück."""
    raw = _run_ytdlp([
        "--print", "%(duration)s\t%(chapters)j",
        "--no-warnings",
        "--skip-download",
    ] + _cookies_args() + [video_url], timeout=20)

    duration = 0
    chapters = []
    try:
        first_line = raw.splitlines()[0] if raw else ""
        parts = first_line.split("\t", 1)
        if parts[0].isdigit():
            duration = int(parts[0])
        if len(parts) > 1:
            raw_chapters = json.loads(parts[1])
            if isinstance(raw_chapters, list) and raw_chapters:
                chapters = [
                    {
                        "title":      c.get("title", ""),
                        "start_time": float(c.get("start_time", 0)),
                        "end_time":   float(c.get("end_time", 0)),
                    }
                    for c in raw_chapters
                    if c.get("end_time", 0) - c.get("start_time", 0) >= 60
                ]
    except Exception:
        pass
    return duration, chapters


def pick_best_segment(video: dict) -> dict:
    """
    Wählt den besten Highlight-Moment:
    - Mit Kapiteln: Claude analysiert Titel → bestes Kapitel → 88s-Fenster
    - Ohne Kapitel: Zufälliges 88s-Fenster aus der Mitte des Videos
    Gibt {start, end, chapter_title, reason} zurück.
    """
    chapters = video.get("chapters", [])

    if chapters:
        return _pick_chapter_with_claude(video, chapters)
    else:
        return _pick_middle_segment(video)


def _pick_chapter_with_claude(video: dict, chapters: list) -> dict:
    """Claude wählt das viral-stärkste Kapitel."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    chapters_text = "\n".join(
        f"{i+1}. [{_fmt_time(c['start_time'])}–{_fmt_time(c['end_time'])}] {c['title']}"
        for i, c in enumerate(chapters)
    )

    prompt = f"""Du bist ein Social-Media-Experte für TikTok und YouTube Shorts.

Podcast: "{video['title']}"

Kapitel:
{chapters_text}

Welches Kapitel hat das höchste Viral-Potenzial für TikTok/Reels?
Kriterien: Kontroverses Thema, emotionale Aussage, überraschendes Statement, starke Meinung.

Antworte NUR mit JSON:
{{"chapter_index": <1-basierter Index>, "reason": "<1 Satz warum viral>"}}"""

    try:
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Strip markdown code blocks if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        idx = int(result["chapter_index"]) - 1
        idx = max(0, min(idx, len(chapters) - 1))
        chapter = chapters[idx]
        reason = result.get("reason", "")
        logger.info(f"[analyzer] Claude wählt Kapitel {idx+1}: '{chapter['title']}' — {reason}")
    except Exception as e:
        logger.warning(f"[analyzer] Claude-Fehler: {e} — zufälliges Kapitel")
        chapter = random.choice(chapters)
        reason = "Zufällig ausgewählt"

    # 88s-Fenster aus dem Kapitel schneiden (Anfang bevorzugt)
    c_start = chapter["start_time"]
    c_end   = chapter["end_time"]
    c_len   = c_end - c_start

    if c_len <= HIGHLIGHT_MAX_SEC:
        start, end = c_start, c_end
    else:
        # Erste 88s des Kapitels (Hook ist meist am Anfang)
        start = c_start + 5   # kurzer Puffer nach Kapitelstart
        end   = start + HIGHLIGHT_MAX_SEC

    return {
        "start":         start,
        "end":           end,
        "chapter_title": chapter["title"],
        "reason":        reason,
    }


def _pick_middle_segment(video: dict) -> dict:
    """Fallback ohne Kapitel: zufälliges 88s-Fenster aus dem mittleren Drittel."""
    duration = video["duration"] or 3600   # Fallback 1h wenn unbekannt
    third    = duration // 3
    window   = max(HIGHLIGHT_MAX_SEC + 1, 2 * third - third)
    start    = random.randint(third, third + window - HIGHLIGHT_MAX_SEC)
    end      = start + HIGHLIGHT_MAX_SEC
    logger.info(f"[analyzer] Kein Kapitel — Segment {_fmt_time(start)}–{_fmt_time(end)}")
    return {
        "start":         float(start),
        "end":           float(end),
        "chapter_title": "",
        "reason":        "Kein Kapitel verfügbar",
    }


def _fmt_time(seconds: float) -> str:
    return str(timedelta(seconds=int(seconds)))[2:] if seconds < 3600 else str(timedelta(seconds=int(seconds)))
