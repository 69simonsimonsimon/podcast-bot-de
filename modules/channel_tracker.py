"""
Channel Tracker — Podcast Bot DE
=================================
Liste großer deutscher Podcast-Kanäle.
Trackt welche Videos bereits verarbeitet wurden (max. 500).
"""

import json
import logging
import os
import random
from pathlib import Path

logger = logging.getLogger("podcastbot")

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "output")))
USED_FILE = OUTPUT_DIR / "used_videos.json"

# ── Echte deutsche Podcast-Kanäle (nur Long-Form Podcast-Formate) ────────────
CHANNELS = [
    # Talk / Interview Podcasts
    {"name": "Hotel Matze",           "url": "https://www.youtube.com/@hotelmatze",            "niche": "talk"},
    {"name": "Baywatch Berlin",       "url": "https://www.youtube.com/@BaywatchBerlin",        "niche": "talk"},
    {"name": "Fest & Flauschig",      "url": "https://www.youtube.com/@FestFlauschig",         "niche": "talk"},
    {"name": "Gemischtes Hack",       "url": "https://www.youtube.com/@GemischtesHack",        "niche": "talk"},
    {"name": "Jung & Naiv",           "url": "https://www.youtube.com/@JungNaiv",              "niche": "talk"},
    {"name": "Cui Bono WTF",          "url": "https://www.youtube.com/@CuiBono",               "niche": "talk"},

    # Business / Unternehmertum Podcasts
    {"name": "OMR Podcast",           "url": "https://www.youtube.com/@OMRpodcast",            "niche": "business"},
    {"name": "Startup Insider",       "url": "https://www.youtube.com/@StartupInsider",        "niche": "business"},
    {"name": "Doppelgänger Tech Talk","url": "https://www.youtube.com/@Doppelgaenger",         "niche": "business"},
    {"name": "Bits und so",           "url": "https://www.youtube.com/@bitsundso",             "niche": "business"},

    # Finance / Geld Podcasts
    {"name": "Mission Money",         "url": "https://www.youtube.com/@MissionMoney",          "niche": "finance"},
    {"name": "Geldmacher Podcast",    "url": "https://www.youtube.com/@Geldmacher",            "niche": "finance"},
    {"name": "Aktien mit Kopf",       "url": "https://www.youtube.com/@AktienMitKopf",         "niche": "finance"},

    # Gesellschaft / Politik Podcasts
    {"name": "Lage der Nation",       "url": "https://www.youtube.com/@lagedernation",         "niche": "politics"},
    {"name": "Die Macherinnen",       "url": "https://www.youtube.com/@DieMacherinnen",        "niche": "politics"},
    {"name": "Tonspur Wissen",        "url": "https://www.youtube.com/@TonspurWissen",         "niche": "politics"},

    # True Crime Podcasts
    {"name": "Cold Case Files DE",    "url": "https://www.youtube.com/@ColdCaseFilesDE",       "niche": "true_crime"},
    {"name": "Mordlust",              "url": "https://www.youtube.com/@MordlustPodcast",       "niche": "true_crime"},
]

# Gewichtung nach Viral-Potenzial
NICHE_WEIGHTS = {
    "talk":         35,
    "business":     25,
    "finance":      20,
    "politics":     15,
    "true_crime":   25,
}


def pick_channel() -> dict:
    """Wählt zufällig einen Kanal, gewichtet nach Nische."""
    weights = [NICHE_WEIGHTS.get(c["niche"], 10) for c in CHANNELS]
    return random.choices(CHANNELS, weights=weights, k=1)[0]


def load_used() -> set:
    try:
        return set(json.loads(USED_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def mark_used(video_id: str):
    used = load_used()
    used.add(video_id)
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    entries = list(used)[-500:]
    USED_FILE.write_text(json.dumps(entries), encoding="utf-8")


def is_used(video_id: str) -> bool:
    return video_id in load_used()
