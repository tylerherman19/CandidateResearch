"""Google News RSS collector. Free, no key. Returns Item-shaped dicts
directly (via pipeline.normalize.normalize).

Queries multiple ways and takes the union, deduped by URL, then filters to
the desired window using each entry's own parsed published date. Checked
directly: no single query formulation reliably returns everything relevant.
The `when:Nd` operator is unreliable regardless of N -- a real, confirmed-
relevant article (a captimes.com endorsement story, ~17 days old at the
time) was present with no `when:` and with `when:90d`, but silently absent
from both `when:60d` and `when:30d` for the identical underlying query. But
dropping `when:` isn't a full fix either: it returns a *different* result
set, not a superset -- several other real, relevant articles that
`when:60d` found were absent from the no-`when:` query. The two modes
overlap only partially, so querying both and merging is what actually
maximizes recall; relying on either alone silently drops real results.
Also queries the bare name with no "Wisconsin" qualifier, which testing
showed returns the most complete single result set of the modes tried --
still filtered for relevance downstream by resolve(), so the recall
benefit doesn't cost precision.

None of this makes results *complete* -- Google News RSS is a best-effort
relevance feed, not a guaranteed-recall index, and no combination of query
phrasings changes that fact."""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import feedparser
import requests

from pipeline.normalize import normalize

RSS_URL = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
TIMEOUT_SECONDS = 15


def _fetch(query: str) -> list:
    url = RSS_URL.format(query=quote(query))
    resp = requests.get(url, headers={"User-Agent": "media-monitor/1.0"}, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    return feedparser.parse(resp.content).entries


def collect(candidate: dict, days: int = 1) -> list:
    name_query = f'"{candidate["name"]}"'
    base_query = f'"{candidate["name"]}" Wisconsin'
    queries = (name_query, base_query, f"{base_query} when:{days}d")

    entries_by_link = {}
    for query in queries:
        for entry in _fetch(query):
            link = entry.get("link", "")
            if link and link not in entries_by_link:
                entries_by_link[link] = entry

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    items = []
    for entry in entries_by_link.values():
        published_at = ""
        published_parsed = entry.get("published_parsed")
        if published_parsed:
            dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
            published_at = dt.isoformat(timespec="seconds")
            if dt < cutoff:
                continue

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
