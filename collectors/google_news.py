"""Google News RSS collector. Free, no key. One query per candidate.
Returns Item-shaped dicts directly (via pipeline.normalize.normalize)."""

from datetime import datetime, timezone
from urllib.parse import quote

import feedparser
import requests

from pipeline.normalize import normalize

RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
TIMEOUT_SECONDS = 15


def collect(candidate: dict, days: int = 1) -> list:
    query = f'"{candidate["name"]}" Wisconsin when:{days}d'
    url = RSS_URL.format(query=quote(query))

    resp = requests.get(
        url,
        headers={"User-Agent": "media-monitor/1.0"},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)

    items = []
    for entry in feed.entries:
        published_at = ""
        published_parsed = entry.get("published_parsed")
        if published_parsed:
            dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
            published_at = dt.isoformat(timespec="seconds")

        source_field = entry.get("source")
        source = source_field.get("title", "") if source_field else ""

        items.append(
            normalize(
                collector="google_news",
                candidate_id=candidate["id"],
                title=entry.get("title", ""),
                source=source,
                source_url=entry.get("link", ""),
                published_at=published_at,
                text=entry.get("summary", ""),
                raw={
                    "title": entry.get("title"),
                    "link": entry.get("link"),
                    "summary": entry.get("summary"),
                    "published": entry.get("published"),
                    "source_title": source,
                },
            )
        )
    return items
