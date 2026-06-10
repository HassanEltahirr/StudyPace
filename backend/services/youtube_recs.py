from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from services.gemini_ai import clean_academic_topic

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass


SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
PREFERRED_CHANNELS = ("khan academy", "mit", "professor leonard")
CORRUPTED_TITLE_MARKERS = ("clause", "fee", "municipal")
MAX_RESULTS = 3


def youtube_available() -> bool:
    return bool(os.getenv("YOUTUBE_API_KEY"))


def recommend_videos(title: str, fallback_topics: list[str] | None = None) -> list[dict]:
    """Return up to 3 lecture videos for a deck: thumbnail, title, channel, duration.

    Irrelevant results (titles sharing no keywords with the topic) are hidden
    rather than shown, and preferred teaching channels rank first.
    """
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        return []

    topic = _study_topic(title, fallback_topics or [])
    if not topic:
        return []

    query = f"{topic} lecture explained"
    items = _search(api_key, query)
    if not items:
        return []

    keywords = _topic_keywords(topic)
    relevant = [item for item in items if _is_relevant(item, keywords)]
    ranked = sorted(relevant, key=_channel_rank)[:MAX_RESULTS]
    durations = _durations(api_key, [item["video_id"] for item in ranked])

    return [
        {
            "video_id": item["video_id"],
            "title": item["title"],
            "channel": item["channel"],
            "thumbnail_url": item["thumbnail_url"],
            "duration": durations.get(item["video_id"], ""),
            "url": f"https://www.youtube.com/watch?v={item['video_id']}",
        }
        for item in ranked
    ]


def _study_topic(title: str, fallback_topics: list[str]) -> str:
    cleaned = re.sub(r"\s+", " ", title or "").strip()
    if cleaned and not _looks_corrupted(cleaned):
        return cleaned

    recovered = clean_academic_topic(cleaned) if cleaned else None
    if recovered:
        return recovered

    for fallback in fallback_topics:
        fallback = re.sub(r"\s+", " ", fallback or "").strip()
        if fallback and not _looks_corrupted(fallback):
            return fallback
    return ""


def _looks_corrupted(title: str) -> bool:
    lower = title.lower()
    if title.startswith("*") or title.startswith("•"):
        return True
    if any(marker in lower for marker in CORRUPTED_TITLE_MARKERS):
        return True
    letters = sum(1 for ch in title if ch.isalpha())
    return letters < max(3, len(title) * 0.5)


def _topic_keywords(topic: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]{4,}", topic.lower())}


def _is_relevant(item: dict, keywords: set[str]) -> bool:
    if not keywords:
        return True
    title_words = set(re.findall(r"[a-z0-9]{4,}", item["title"].lower()))
    return bool(keywords & title_words)


def _channel_rank(item: dict) -> int:
    channel = item["channel"].lower()
    for rank, preferred in enumerate(PREFERRED_CHANNELS):
        if preferred in channel:
            return rank
    return len(PREFERRED_CHANNELS)


def _search(api_key: str, query: str) -> list[dict]:
    params = urllib.parse.urlencode({
        "part": "snippet",
        "type": "video",
        "maxResults": 10,
        "q": query,
        "key": api_key,
        "safeSearch": "strict",
        "videoEmbeddable": "true",
    })
    body = _get_json(f"{SEARCH_URL}?{params}")
    if not body:
        return []

    items: list[dict] = []
    for entry in body.get("items", []):
        video_id = (entry.get("id") or {}).get("videoId")
        snippet = entry.get("snippet") or {}
        if not video_id or not snippet:
            continue
        thumbnails = snippet.get("thumbnails") or {}
        thumbnail = (thumbnails.get("medium") or thumbnails.get("default") or {}).get("url", "")
        items.append({
            "video_id": video_id,
            "title": _decode_entities(snippet.get("title", "")),
            "channel": _decode_entities(snippet.get("channelTitle", "")),
            "thumbnail_url": thumbnail,
        })
    return items


def _durations(api_key: str, video_ids: list[str]) -> dict[str, str]:
    if not video_ids:
        return {}
    params = urllib.parse.urlencode({
        "part": "contentDetails",
        "id": ",".join(video_ids),
        "key": api_key,
    })
    body = _get_json(f"{VIDEOS_URL}?{params}")
    if not body:
        return {}

    durations: dict[str, str] = {}
    for entry in body.get("items", []):
        video_id = entry.get("id")
        raw = (entry.get("contentDetails") or {}).get("duration", "")
        if video_id and raw:
            durations[video_id] = _format_duration(raw)
    return durations


def _format_duration(iso_duration: str) -> str:
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso_duration)
    if not match:
        return ""
    hours, minutes, seconds = (int(value or 0) for value in match.groups())
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _decode_entities(value: str) -> str:
    return (
        value.replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def _get_json(url: str) -> dict | None:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
