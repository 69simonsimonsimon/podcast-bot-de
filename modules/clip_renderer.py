"""
Clip Renderer — Podcast Bot DE
================================
1. Lädt nur das gewählte Segment herunter (yt-dlp --download-sections)
2. Transkribiert mit Whisper (für Karaoke)
3. Rendert: 9:16 mit Blur-Hintergrund + Wort-Karaoke + Podcast-Label
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

import openai

logger = logging.getLogger("podcastbot")

# ── Render-Konfiguration ──────────────────────────────────────────────────────
WIDTH  = 1080
HEIGHT = 1920
FONT   = "Arial-Bold"

# Karaoke-Farben
COLOR_DEFAULT  = "&H00FFFFFF"   # Weiß
COLOR_ACTIVE   = "&H0000FFFF"   # Gelb (hervorgehobenes Wort)
COLOR_OUTLINE  = "&H00000000"   # Schwarz
FONT_SIZE      = 68


def _get_cookies_file() -> str | None:
    """Schreibt YouTube-Cookies aus Env-Variable in Temp-Datei. Gibt Pfad zurück oder None."""
    cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not cookies:
        return None
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, prefix="yt_cookies_")
    tmp.write(cookies)
    tmp.flush()
    tmp.close()
    return tmp.name


def download_segment(video_url: str, start: float, end: float,
                     output_path: Path) -> Path:
    """
    Lädt nur das angegebene Segment herunter.
    Versucht mehrere Player-Clients um GitHub Actions Bot-Detection zu umgehen.
    """
    start_str = _sec_to_hhmmss(start)
    end_str   = _sec_to_hhmmss(end)

    logger.info(f"[renderer] Download Segment {start_str}–{end_str}...")

    cookies_file = _get_cookies_file()
    if cookies_file:
        logger.info(f"[renderer] Cookies geladen ({Path(cookies_file).stat().st_size} Bytes)")

    # Verschiedene Strategien — erste funktionierende wird genommen
    strategies = [
        # 1. iOS-Client (oft am besten für nicht-öffentliche IPs)
        {"player_client": "ios", "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]"},
        # 2. TV-Embedded (umgeht viele Bot-Checks)
        {"player_client": "tv_embedded", "format": "bestvideo[height<=720]+bestaudio/best[height<=720]"},
        # 3. Android-Client
        {"player_client": "android", "format": "bestvideo[height<=720]+bestaudio/best[height<=720]"},
        # 4. Fallback: web mit einfachstem Format
        {"player_client": "web", "format": "best[ext=mp4]/best"},
    ]

    tmp = output_path.with_suffix(".tmp.%(ext)s")
    last_error = "Unbekannter Fehler"

    for i, strategy in enumerate(strategies):
        # Alte temp-Dateien löschen
        for f in output_path.parent.glob("*.tmp.*"):
            f.unlink(missing_ok=True)

        cmd = [
            "yt-dlp",
            "--download-sections", f"*{start_str}-{end_str}",
            "--force-keyframes-at-cuts",
            "-f", strategy["format"],
            "--merge-output-format", "mp4",
            "--extractor-args", f"youtube:player_client={strategy['player_client']}",
            "-o", str(tmp),
            "--no-playlist",
            "--quiet",
            "--no-warnings",
        ]
        if cookies_file:
            cmd += ["--cookies", cookies_file]
        cmd.append(video_url)

        logger.info(f"[renderer] Versuch {i+1}/4: player_client={strategy['player_client']}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        # Output-Datei finden
        parent = output_path.parent
        found = False
        for f in parent.glob("*.tmp.mp4"):
            f.rename(output_path)
            found = True
            break
        if not found and not output_path.exists():
            for f in parent.glob("*.mp4"):
                if f != output_path:
                    f.rename(output_path)
                    found = True
                    break

        if output_path.exists() and output_path.stat().st_size > 10_000:
            mb = output_path.stat().st_size / 1024 / 1024
            logger.info(f"[renderer] ✅ Segment geladen mit {strategy['player_client']}: {mb:.1f} MB")
            break

        last_error = result.stderr[:300] if result.stderr else "Unbekannt"
        logger.warning(f"[renderer] Versuch {i+1} fehlgeschlagen: {last_error[:100]}")

    # Cookies aufräumen
    if cookies_file:
        try:
            Path(cookies_file).unlink(missing_ok=True)
        except Exception:
            pass

    if not output_path.exists() or output_path.stat().st_size < 10_000:
        raise RuntimeError(f"yt-dlp Download fehlgeschlagen (alle {len(strategies)} Strategien):\n{last_error}")

    return output_path


def transcribe_segment(video_path: Path) -> list[dict]:
    """
    Transkribiert das Video-Segment mit Whisper.
    Gibt Liste von {word, start, end} zurück.
    """
    logger.info("[renderer] Whisper-Transkription...")
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    # Audio extrahieren für Whisper (schneller als ganzes Video)
    audio_path = video_path.with_suffix(".wav")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ar", "16000", "-ac", "1",
        str(audio_path),
    ], capture_output=True, timeout=60)

    try:
        with open(str(audio_path), "rb") as af:
            tr = client.audio.transcriptions.create(
                model="whisper-1",
                file=af,
                response_format="verbose_json",
                timestamp_granularities=["word"],
                language="de",
            )
        words = [
            {"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)}
            for w in (tr.words or [])
        ]
        logger.info(f"[renderer] {len(words)} Wörter transkribiert")
        return words
    finally:
        audio_path.unlink(missing_ok=True)


def render_clip(segment_path: Path, words: list[dict],
                channel_name: str, chapter_title: str,
                output_path: Path, hook: str = "") -> Path:
    """
    Rendert das finale 9:16-Video:
    - Blur-Hintergrund (gestrecktes Original)
    - Original zentriert (letterboxed)
    - Wort-für-Wort Karaoke
    - Podcast-Label oben + CTA unten
    """
    logger.info("[renderer] Render 9:16 Video mit Karaoke...")

    ass_path = segment_path.with_suffix(".ass")
    _write_ass(words, ass_path, channel_name, chapter_title, hook)

    try:
        filter_graph = (
            # Hintergrund: Original strecken + stark bluuren
            "[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            "crop={w}:{h},boxblur=25:5[bg];"
            # Vordergrund: Original auf Breite skalieren, Höhe proportional
            "[0:v]scale={w}:-2[fg];"
            # Übereinanderlegen: fg zentriert auf bg
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[composed];"
            # ASS-Untertitel einbrennen
            "[composed]ass='{ass}'[out]"
        ).format(w=WIDTH, h=HEIGHT, ass=str(ass_path).replace("'", "\\'").replace("\\", "/"))

        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(segment_path),
            "-filter_complex", filter_graph,
            "-map", "[out]",
            "-map", "0:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(output_path),
        ], capture_output=True, timeout=300)

        if result.returncode != 0:
            logger.error(f"[renderer] ffmpeg Fehler:\n{result.stderr[-500:]}")
            raise RuntimeError("ffmpeg fehlgeschlagen")

        mb = output_path.stat().st_size / 1024 / 1024
        logger.info(f"[renderer] ✅ {output_path.name} ({mb:.1f} MB)")
        return output_path

    finally:
        ass_path.unlink(missing_ok=True)


# ── ASS-Subtitle Generator ────────────────────────────────────────────────────

def _write_ass(words: list[dict], path: Path, channel_name: str, chapter_title: str, hook: str = ""):
    """Erstellt eine ASS-Subtitle-Datei mit Wort-für-Wort Highlighting."""

    # Gruppiere Wörter in Zeilen (max. 4 Wörter pro Zeile)
    groups = _group_words(words, max_words=4)

    events = []

    # Karaoke-Events: jede Gruppe mit Wort-Highlighting
    for group in groups:
        g_start = group[0]["start"]
        g_end   = group[-1]["end"]
        line_text = _build_karaoke_line(group)
        events.append(
            f"Dialogue: 0,{_ass_time(g_start)},{_ass_time(g_end)},"
            f"Karaoke,,0,0,0,,{line_text}"
        )

    # Podcast-Name oben (gesamte Clip-Dauer)
    if words:
        clip_start = words[0]["start"]
        clip_end   = words[-1]["end"]
        label = _escape_ass(f"🎙 {channel_name}")
        events.insert(0,
            f"Dialogue: 0,{_ass_time(clip_start)},{_ass_time(clip_end)},"
            f"Label,,0,0,0,,{label}"
        )
        # Kapitel-Titel unter Podcast-Name (erste 3 Sekunden)
        if chapter_title:
            chapter_short = chapter_title[:50] + ("…" if len(chapter_title) > 50 else "")
            events.insert(1,
                f"Dialogue: 0,{_ass_time(clip_start)},{_ass_time(min(clip_start+4, clip_end))},"
                f"Chapter,,0,0,0,,{_escape_ass(chapter_short)}"
            )
        # Hook-Text in der Mitte (erste 3 Sekunden, groß und auffällig)
        if hook:
            events.insert(2,
                f"Dialogue: 0,{_ass_time(clip_start)},{_ass_time(min(clip_start+3, clip_end))},"
                f"Hook,,0,0,0,,{_escape_ass(hook)}"
            )
        # CTA unten — Stitch + Kommentar-Bait
        _cta_options = [
            "💬 Kommentiert eure Meinung 👇",
            "🎭 Stitch mit deiner Reaktion!",
            "💬 Stimmt ihr dem zu? Kommentiert!",
            "👇 Ganzen Podcast in Bio",
        ]
        import random as _r
        _cta_text = _r.choice(_cta_options)
        events.append(
            f"Dialogue: 0,{_ass_time(max(clip_end-3, clip_start))},{_ass_time(clip_end)},"
            f"CTA,,0,0,0,,{{\\an2}}{_escape_ass(_cta_text)}"
        )

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{FONT},{FONT_SIZE},{COLOR_DEFAULT},&H00FFFF00,{COLOR_OUTLINE},&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,80,80,120,1
Style: Label,{FONT},42,&H00FFFFFF,&H00FFFFFF,{COLOR_OUTLINE},&H90000000,-1,0,0,0,100,100,2,0,1,2,1,8,40,40,60,1
Style: Chapter,{FONT},36,&H00E0E0E0,&H00E0E0E0,{COLOR_OUTLINE},&H90000000,0,0,0,0,100,100,1,0,1,2,0,8,40,40,110,1
Style: CTA,{FONT},44,&H00FFFF00,&H00FFFF00,{COLOR_OUTLINE},&H90000000,-1,0,0,0,100,100,0,0,1,2,1,2,60,60,80,1
Style: Hook,{FONT},72,&H00FFFFFF,&H00FFFFFF,{COLOR_OUTLINE},&HA0000000,-1,0,0,0,100,100,0,0,1,4,2,5,60,60,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def _group_words(words: list[dict], max_words: int = 4) -> list[list[dict]]:
    """Gruppiert Wörter in Zeilen für Karaoke-Anzeige."""
    groups, current = [], []
    for w in words:
        current.append(w)
        if len(current) >= max_words:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _build_karaoke_line(group: list[dict]) -> str:
    r"""Erstellt Karaoke-Text mit {\k}-Tags für Wort-Highlighting."""
    parts = []
    for w in group:
        duration_cs = max(1, int((w["end"] - w["start"]) * 100))
        parts.append(f"{{\\kf{duration_cs}}}{_escape_ass(w['word'])} ")
    return "".join(parts).rstrip()


def _ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape_ass(text: str) -> str:
    return text.replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def _sec_to_hhmmss(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
