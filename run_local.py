#!/usr/bin/env python3
"""
Podcast Bot DE — Highlight-Generator
=====================================
Lädt Segmente großer deutscher Podcasts, schneidet Highlights raus,
rendert 9:16 mit Karaoke und lädt in Bunny Queue hoch.

Usage:
  python run_local.py              # zufälliger Kanal
  python run_local.py gemischtes   # Kanal-Suche per Stichwort
  python run_local.py "" 3         # 3 Highlights generieren
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env", override=True)
sys.path.insert(0, str(ROOT / "modules"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("podcastbot")

OUTPUT_DIR = ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def _bunny_upload(video_path: Path, filename: str, meta: dict) -> str:
    """Lädt Video + JSON-Metadaten in Bunny queue/ hoch."""
    import certifi
    import requests as rq

    password = os.environ["BUNNY_STORAGE_PASSWORD"]
    zone     = os.environ.get("BUNNY_STORAGE_NAME", "syncin")
    cdn_url  = os.environ.get("BUNNY_CDN_URL", "https://syncin.b-cdn.net")
    hostname = os.environ.get("BUNNY_STORAGE_HOSTNAME", "storage.bunnycdn.com")

    with open(str(video_path), "rb") as f:
        rq.put(
            f"https://{hostname}/{zone}/queue/{filename}",
            headers={"AccessKey": password, "Content-Type": "video/mp4"},
            data=f, verify=certifi.where(), timeout=300,
        ).raise_for_status()

    meta["cdn_url"] = f"{cdn_url}/queue/{filename}"
    rq.put(
        f"https://{hostname}/{zone}/queue/{filename.replace('.mp4', '.json')}",
        headers={"AccessKey": password, "Content-Type": "application/json"},
        data=json.dumps(meta, ensure_ascii=False).encode(),
        verify=certifi.where(), timeout=30,
    ).raise_for_status()

    return meta["cdn_url"]


def generate_highlight(channel_keyword: str = None) -> bool:
    from channel_tracker import CHANNELS, pick_channel, is_used, mark_used
    from podcast_analyzer import get_latest_video, pick_best_segment
    from clip_renderer import download_segment, transcribe_segment, render_clip

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S%f")[:-3]
    segment_path = OUTPUT_DIR / f"segment_{stamp}.mp4"
    output_path  = OUTPUT_DIR / f"podcast_de_{stamp}.mp4"

    try:
        # 1. Kanal wählen
        if channel_keyword:
            kw = channel_keyword.lower()
            matches = [c for c in CHANNELS if kw in c["name"].lower() or kw in c["niche"].lower()]
            channel = matches[0] if matches else pick_channel()
        else:
            channel = pick_channel()

        logger.info(f"📻  Kanal: {channel['name']} ({channel['niche']})")

        # 2. Neuestes Video holen (nicht-verwendetes)
        used = {v for v in _load_used_videos()}
        video = get_latest_video(channel["url"], skip_ids=used)

        if not video:
            logger.warning(f"    Kein neues Video für {channel['name']} — anderer Kanal")
            # Fallback: anderen Kanal versuchen
            for _ in range(3):
                channel = pick_channel()
                video = get_latest_video(channel["url"], skip_ids=used)
                if video:
                    break
            if not video:
                logger.error("    Kein Video gefunden nach 3 Versuchen")
                return False

        logger.info(f"    → {video['title'][:70]}")
        logger.info(f"    → {video['duration']//60} Min, {len(video['chapters'])} Kapitel")

        # 3. Bestes Segment wählen (Claude analysiert Kapitel)
        logger.info("🤖  Analysiere Kapitel...")
        segment = pick_best_segment(video)
        logger.info(f"    → [{segment['start']:.0f}s–{segment['end']:.0f}s] '{segment['chapter_title']}'")
        logger.info(f"    → Grund: {segment['reason']}")

        # 4. Segment downloaden
        logger.info("⬇️   Lade Segment herunter...")
        download_segment(video["url"], segment["start"], segment["end"], segment_path)

        # 5. Whisper Transkription
        logger.info("🎙️   Transkribiere...")
        words = transcribe_segment(segment_path)

        # 6. Rendern: 9:16 + Karaoke
        logger.info("🎞️   Rendere 9:16 Clip...")
        render_clip(
            segment_path=segment_path,
            words=words,
            channel_name=channel["name"],
            chapter_title=segment["chapter_title"],
            output_path=output_path,
        )

        # 7. Metadaten + Upload
        yt_title = _generate_viral_title(video, segment, channel)
        caption  = _build_caption(video, segment, channel, yt_title)
        meta = {
            "title":        yt_title,
            "caption":      caption,
            "sport":        "podcast",
            "player":       channel["name"],
            "source_video": video["url"],
            "channel":      channel["name"],
        }

        filename = f"podcast_de_{stamp}.mp4"
        cdn = _bunny_upload(output_path, filename, meta)
        logger.info(f"✅  In Queue: {filename}")
        logger.info(f"    CDN: {cdn}")

        # 8. Video als verwendet markieren
        mark_used(video["id"])
        return True

    except Exception as e:
        logger.error(f"❌  Fehler: {e}", exc_info=True)
        return False

    finally:
        segment_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def _generate_viral_title(video: dict, segment: dict, channel: dict) -> str:
    """Generiert einen viral-optimierten YouTube/TikTok Titel mit Claude."""
    import anthropic
    chapter = segment.get("chapter_title", "")
    podcast_title = video.get("title", "")
    channel_name  = channel["name"]
    niche         = channel.get("niche", "")

    prompt = f"""Du bist ein Social-Media-Experte für TikTok und YouTube Shorts in Deutschland.

Podcast-Kanal: {channel_name}
Podcast-Folge: {podcast_title}
Thema des Clips: {chapter or podcast_title}
Nische: {niche}

Erstelle einen **kurzen, viralen Titel** (max. 60 Zeichen) für diesen Podcast-Clip auf TikTok/YouTube Shorts.

Regeln:
- Kein "Podcast" oder Kanalname im Titel
- Neugier wecken, Frage oder starke Aussage
- Umgangssprache OK, gerne Großbuchstaben für Betonung
- 1 passendes Emoji am Ende
- KEIN Clickbait — der Clip muss halten was der Titel verspricht
- Maximal 60 Zeichen

Antworte NUR mit dem Titel, ohne Anführungszeichen."""

    try:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=80,
            messages=[{"role": "user", "content": prompt}],
        )
        title = msg.content[0].text.strip().strip('"').strip("'")
        logger.info(f"    Titel: {title}")
        return title[:100]
    except Exception as e:
        logger.warning(f"    Titel-Generierung fehlgeschlagen: {e} — Fallback")
        return chapter or podcast_title[:60] or channel_name


def _load_used_videos() -> set:
    used_file = OUTPUT_DIR / "used_videos.json"
    try:
        return set(json.loads(used_file.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _build_caption(video: dict, segment: dict, channel: dict, viral_title: str = "") -> str:
    name  = channel["name"]
    niche = channel.get("niche", "")

    niche_tags = {
        "business":      "#business #unternehmertum #entrepreneur #erfolg",
        "finance":       "#finanzen #investieren #geld #reich",
        "entertainment": "#podcast #unterhaltung #comedy #lustig",
        "talk":          "#interview #talk #podcast #persönlichkeit",
        "tech":          "#tech #ki #zukunft #technologie",
        "politics":      "#politik #gesellschaft #news #diskussion",
        "true_crime":    "#truecrime #verbrechen #krimi #mystery",
        "health":        "#gesundheit #fitness #lifestyle #wohlbefinden",
        "culture":       "#kultur #philosophie #leben #gedanken",
        "science":       "#wissenschaft #bildung #lernen #interessant",
    }
    tags = niche_tags.get(niche, "#podcast #deutsch #viral")

    lines = [
        viral_title or name,
        "",
        f"🎙 aus dem {name} Podcast",
        f"➡️ Ganzen Podcast ansehen — Link in Bio",
        "",
        tags,
        "#podcast #deutscherpodcast #fyp #fypシ #viral",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    keyword = sys.argv[1] if len(sys.argv) > 1 else None
    count   = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    done = 0
    for i in range(count):
        if count > 1:
            logger.info(f"\n{'='*50}\nDurchlauf {i+1}/{count}\n{'='*50}")
        if generate_highlight(keyword if keyword else None):
            done += 1
        if i < count - 1:
            time.sleep(5)

    logger.info(f"\n🏁  Fertig: {done}/{count} Highlights generiert")
