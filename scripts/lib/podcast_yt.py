"""YouTube podcast discovery via transcript scanning.

Discovers podcast content by fetching auto-captions from LLM-resolved
YouTube podcast channels and grepping for the search topic. Finds content
invisible to title-based search — e.g., Acquired's "The NFL" episode
mentions Taylor Swift 18 times, ESPN 117 times, Netflix 102 times.

Uses yt-dlp for channel playlist fetch + caption download. No API keys.
Reuses transcript highlight extraction from youtube_yt.
"""

import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from . import log

# How many recent episodes to scan per channel, by depth
EPISODES_PER_CHANNEL = {
    "quick": 2,
    "default": 3,
    "deep": 4,
}

# Minimum topic mentions in captions to count as a hit
MENTION_THRESHOLD = 5

# Max total results to return
RESULTS_CAP = {
    "quick": 4,
    "default": 8,
    "deep": 20,
}

# Min duration in seconds to qualify as a podcast episode
MIN_DURATION = 1200  # 20 minutes


def _log(msg: str):
    log.source_log("Podcasts", msg, tty_only=False)


def is_available() -> bool:
    """Podcast source is available when yt-dlp is installed."""
    return shutil.which("yt-dlp") is not None


def resolve_channel(handle: str) -> Optional[str]:
    """Resolve a YouTube @handle to a channel URL.

    Tries the @handle directly first (fast, ~92% success rate).
    Falls back to ytsearch1 if the handle doesn't resolve.

    Returns the channel URL (https://www.youtube.com/channel/...) or None.
    """
    # Try @handle directly - use the channel/videos URL format
    # yt-dlp can fetch from @handle URLs directly for playlist operations
    direct_url = f"https://www.youtube.com/@{handle}/videos"
    try:
        result = subprocess.run(
            ["yt-dlp", "--playlist-end", "1",
             "--print", "%(channel_url)s",
             "--no-download", "--no-warnings", "--ignore-config", "--no-cookies-from-browser",
             direct_url],
            capture_output=True, text=True, timeout=20,
        )
        channel_url = result.stdout.strip().split("\n")[0].strip()
        if channel_url and channel_url.startswith("http"):
            _log(f"Resolved @{handle} -> {channel_url}")
            return channel_url
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Fallback: search for the podcast
    _log(f"@{handle} not found, trying search fallback")
    try:
        result = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--playlist-end", "1",
             "--print", "%(channel_url)s",
             f'ytsearch1:"{handle}" podcast full episode'],
            capture_output=True, text=True, timeout=20,
        )
        channel_url = result.stdout.strip()
        if channel_url and channel_url.startswith("http"):
            _log(f"Search fallback resolved {handle} -> {channel_url}")
            return channel_url
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    _log(f"Could not resolve channel: {handle}")
    return None


def _fetch_recent_episodes(
    channel_url: str,
    limit: int,
    from_date: str,
    to_date: str,
) -> List[Dict[str, Any]]:
    """Fetch recent long-form episodes from a channel.

    Returns list of dicts with video_id, title, channel, duration, date, views, likes.
    Filters to episodes with duration >= MIN_DURATION.
    """
    import json as _json
    try:
        result = subprocess.run(
            ["yt-dlp", f"--playlist-end={limit + 2}",
             "--dump-json", "--no-download", "--no-warnings", "--ignore-config", "--no-cookies-from-browser",
             f"{channel_url}/videos"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    episodes = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            video = _json.loads(line)
        except _json.JSONDecodeError:
            continue

        video_id = video.get("id", "")
        title = video.get("title", "")
        channel = video.get("channel", video.get("uploader", ""))
        duration = video.get("duration") or 0
        upload_date_raw = video.get("upload_date", "")
        views = video.get("view_count") or 0
        likes = video.get("like_count") or 0

        # Convert YYYYMMDD to YYYY-MM-DD
        date_str = None
        if upload_date_raw and len(upload_date_raw) >= 8:
            date_str = f"{upload_date_raw[:4]}-{upload_date_raw[4:6]}-{upload_date_raw[6:8]}"

        # Filter: duration >= MIN_DURATION
        if duration < MIN_DURATION:
            continue

        # Filter: within date range (soft - keep if no date available)
        if date_str and (date_str < from_date or date_str > to_date):
            continue

        episodes.append({
            "video_id": video_id,
            "title": title,
            "channel_name": channel,
            "duration": duration,
            "date": date_str,
            "views": views,
            "likes": likes,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })

    return episodes[:limit]


def _fetch_captions(video_id: str, temp_dir: str) -> Optional[str]:
    """Fetch auto-captions for a video. Returns caption text or None."""
    out_template = os.path.join(temp_dir, f"cap_{video_id}")
    try:
        subprocess.run(
            ["yt-dlp", "--write-auto-sub", "--sub-lang", "en",
             "--skip-download", "--sub-format", "vtt",
             "-o", out_template,
             f"https://www.youtube.com/watch?v={video_id}"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    vtt_path = f"{out_template}.en.vtt"
    if not os.path.exists(vtt_path):
        return None

    try:
        with open(vtt_path, "r", encoding="utf-8") as f:
            text = f.read()
        os.remove(vtt_path)
        # Strip VTT formatting: timestamps, alignment, tags, duplicate lines
        # VTT auto-captions repeat lines as they scroll, so deduplicate
        lines = []
        prev_line = ""
        for line in text.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
                continue
            if re.match(r"^\d{2}:\d{2}:", line):
                continue
            if re.match(r"^NOTE\b", line):
                continue
            if "align:" in line or "position:" in line:
                continue
            # Strip inline VTT tags like <c>, </c>, timestamps
            cleaned = re.sub(r"<[^>]+>", "", line)
            cleaned = cleaned.strip()
            if cleaned and not re.match(r"^\d+$", cleaned) and cleaned != prev_line:
                lines.append(cleaned)
                prev_line = cleaned
        return " ".join(lines)
    except Exception:
        return None


def _count_mentions(text: str, topic: str) -> int:
    """Count case-insensitive topic mentions in text."""
    # Build a regex pattern from the topic words
    # For multi-word topics like "Taylor Swift", search for the full phrase
    pattern = re.escape(topic.strip())
    return len(re.findall(pattern, text, re.IGNORECASE))


def _extract_mention_context(text: str, topic: str, max_excerpts: int = 3) -> List[str]:
    """Extract text snippets around topic mentions for highlights."""
    words = text.split()
    topic_lower = topic.lower()
    excerpts = []

    for i, word in enumerate(words):
        # Check if we're near a mention
        window = " ".join(words[max(0, i - 5):i + 15]).lower()
        if topic_lower in window and len(excerpts) < max_excerpts:
            start = max(0, i - 10)
            end = min(len(words), i + 30)
            excerpt = " ".join(words[start:end])
            # Avoid duplicate excerpts
            if not any(excerpt[:50] in e for e in excerpts):
                excerpts.append(excerpt)

    return excerpts


def _scan_channel(
    handle: str,
    topic: str,
    from_date: str,
    to_date: str,
    episodes_limit: int,
) -> List[Dict[str, Any]]:
    """Scan a single channel's recent episodes for topic mentions.

    Returns list of hit items with mention_count and transcript data.
    """
    # Step 1: Resolve channel handle to URL
    channel_url = resolve_channel(handle)
    if not channel_url:
        return []

    # Step 2: Fetch recent long-form episodes
    episodes = _fetch_recent_episodes(channel_url, episodes_limit, from_date, to_date)
    if not episodes:
        _log(f"No recent long-form episodes from {handle}")
        return []

    _log(f"Scanning {len(episodes)} episodes from {handle}")

    # Step 3: Fetch captions and grep for topic
    hits = []
    with tempfile.TemporaryDirectory() as temp_dir:
        for ep in episodes:
            caption_text = _fetch_captions(ep["video_id"], temp_dir)
            if not caption_text:
                continue

            mention_count = _count_mentions(caption_text, topic)
            if mention_count < MENTION_THRESHOLD:
                continue

            # Extract highlights around the mentions
            from .youtube_yt import extract_transcript_highlights
            highlights = extract_transcript_highlights(caption_text, topic, limit=5)
            mention_excerpts = _extract_mention_context(caption_text, topic)

            # Cap transcript for storage
            words = caption_text.split()
            transcript_snippet = " ".join(words[:5000]) if len(words) > 5000 else caption_text

            hits.append({
                "video_id": ep["video_id"],
                "title": ep["title"],
                "channel_name": ep["channel_name"],
                "url": ep["url"],
                "date": ep["date"],
                "duration": ep["duration"],
                "engagement": {
                    "views": ep["views"],
                    "likes": ep["likes"],
                },
                "mention_count": mention_count,
                "transcript_snippet": transcript_snippet,
                "transcript_highlights": highlights,
                "mention_excerpts": mention_excerpts,
                "relevance": min(1.0, mention_count / 50),
                "why_relevant": f"Podcast: {ep['channel_name']} - {ep['title'][:60]} ({mention_count} mentions)",
            })

            _log(f"  HIT: {ep['title'][:60]} ({mention_count} mentions)")

    return hits


def search_podcast_youtube(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    channels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Discover podcast content by scanning transcripts of resolved channels.

    Args:
        topic: Search topic
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        depth: 'quick', 'default', or 'deep'
        channels: List of YouTube @handles to scan

    Returns:
        Dict with 'items' list. Each item has transcript and mention data.
    """
    if not is_available():
        _log("yt-dlp not installed")
        return {"items": [], "error": "yt-dlp not installed"}

    if not channels:
        _log("No podcast channels provided")
        return {"items": []}

    episodes_limit = EPISODES_PER_CHANNEL.get(depth, EPISODES_PER_CHANNEL["default"])
    results_cap = RESULTS_CAP.get(depth, RESULTS_CAP["default"])

    _log(f"Scanning {len(channels)} podcast channels for '{topic}' (depth={depth}, {episodes_limit} eps/channel)")

    # Scan channels in parallel
    all_hits: List[Dict[str, Any]] = []
    max_workers = min(4, len(channels))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _scan_channel, handle, topic, from_date, to_date, episodes_limit,
            ): handle
            for handle in channels
        }
        for future in as_completed(futures):
            handle = futures[future]
            try:
                hits = future.result()
                all_hits.extend(hits)
            except Exception as exc:
                _log(f"Error scanning {handle}: {type(exc).__name__}: {exc}")

    # Deduplicate by video_id
    seen = set()
    unique_hits = []
    for hit in all_hits:
        vid = hit["video_id"]
        if vid not in seen:
            seen.add(vid)
            unique_hits.append(hit)

    # Score: mention_count * log(views + 1)
    for hit in unique_hits:
        views = hit["engagement"].get("views", 0)
        hit["_score"] = hit["mention_count"] * math.log(views + 1)

    # Sort by score descending
    unique_hits.sort(key=lambda x: x["_score"], reverse=True)

    # Cap results
    results = unique_hits[:results_cap]

    # Clean up internal scoring field
    for hit in results:
        hit.pop("_score", None)

    _log(f"Found {len(results)} podcast hits across {len(channels)} channels")
    return {"items": results}
