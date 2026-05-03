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

# ── Große deutsche Podcast-Kanäle ────────────────────────────────────────────
CHANNELS = [
    # Business / Unternehmertum
    {"name": "Christian Wolf",        "url": "https://www.youtube.com/@ChristianWolf",        "niche": "business"},
    {"name": "OMR Podcast",           "url": "https://www.youtube.com/@OMRpodcast",            "niche": "business"},
    {"name": "Startup Insider",       "url": "https://www.youtube.com/@StartupInsider",        "niche": "business"},
    {"name": "Wirtschafts Woche",     "url": "https://www.youtube.com/@WirtschaftsWoche",      "niche": "business"},

    # Finance / Investieren
    {"name": "Finanzfluss",           "url": "https://www.youtube.com/@Finanzfluss",           "niche": "finance"},
    {"name": "Mission Money",         "url": "https://www.youtube.com/@MissionMoney",          "niche": "finance"},

    # Talk / Entertainment
    {"name": "Hotel Matze",           "url": "https://www.youtube.com/@hotelmatze",            "niche": "talk"},
    {"name": "Baywatch Berlin",       "url": "https://www.youtube.com/@BaywatchBerlin",        "niche": "entertainment"},
    {"name": "Fest & Flauschig",      "url": "https://www.youtube.com/@FestFlauschig",         "niche": "entertainment"},
    {"name": "Simplicissimus",        "url": "https://www.youtube.com/@simplicissimus",        "niche": "entertainment"},

    # News / Gesellschaft
    {"name": "WELT",                  "url": "https://www.youtube.com/@WELT",                  "niche": "politics"},
    {"name": "n-tv",                  "url": "https://www.youtube.com/@n-tv",                  "niche": "politics"},
    {"name": "Y-Kollektiv",           "url": "https://www.youtube.com/@Y-Kollektiv",           "niche": "culture"},
    {"name": "MrWissen2go",           "url": "https://www.youtube.com/@MrWissen2go",           "niche": "culture"},

    # Wissenschaft / Bildung
    {"name": "Kurzgesagt DE",         "url": "https://www.youtube.com/@KurzgesagtDE",          "niche": "science"},
    {"name": "maiLab",                "url": "https://www.youtube.com/@maiLab",                "niche": "science"},

    # True Crime / Mystery
    {"name": "Y-Kollektiv Crime",     "url": "https://www.youtube.com/@Y-Kollektiv",           "niche": "true_crime"},
]

# Gewichtung nach Viral-Potenzial
NICHE_WEIGHTS = {
    "business":     30,
    "entertainment":25,
    "finance":      20,
    "talk":         20,
    "politics":     15,
    "culture":      15,
    "true_crime":   20,
    "science":      10,
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
