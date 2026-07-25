"""YouTube Data API v3 collector. Free quota: 10,000 units/day.
search.list costs 100 units/call; videos.list (for engagement stats) costs
1 unit per call -- cheap enough to always fetch real engagement numbers."""

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from pipeline.normalize import normalize

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
TIMEOUT_SECONDS = 15


def collect(candidate: dict) -> list:
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("  [info] YOUTUBE_API_KEY not set -- skipping YouTube", file=sys.stderr)
        return []

    published_after = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    resp = requests.get(
        SEARCH_URL,
        params={
            "key": api_key,
            "part": "snippet",
            "q": candidate["name"],
            "type": "video",
            "order": "date",
            "maxResults": 10,
            "publishedAfter": published_after,
        },
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    search_data = resp.json()

    video_ids = [
        item["id"]["videoId"] for item in search_data.get("items", []) if item.get("id", {}).get("videoId")
    ]
    stats_by_id = {}
    if video_ids:
        stats_resp = requests.get(
            VIDEOS_URL,
            params={"key": api_key, "part": "statistics", "id": ",".join(video_ids)},
            timeout=TIMEOUT_SECONDS,
        )
        stats_resp.raise_for_status()
        for video in stats_resp.json().get("items", []):
            stats_by_id[video["id"]] = video.get("statistics", {})

    items = []
    for result in search_data.get("items", []):
        video_id = result.get("id", {}).get("videoId")
        if not video_id:
            continue
        snippet = result.get("snippet", {})
        stats = stats_by_id.get(video_id, {})

        items.append(
            normalize(
                collector="youtube",
                candidate_id=candidate["id"],
                title=snippet.get("title", ""),
                source=snippet.get("channelTitle", "YouTube"),
                source_url=f"https://www.youtube.com/watch?v={video_id}",
                published_at=snippet.get("publishedAt", ""),
                text=snippet.get("description", ""),
                author=snippet.get("channelTitle"),
                engagement={
                    "views": stats.get("viewCount"),
                    "likes": stats.get("likeCount"),
                    "comments": stats.get("commentCount"),
                },
                raw={"snippet": snippet, "statistics": stats},
            )
        )
    return items
