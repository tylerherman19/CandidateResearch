"""WisPolitics.com RSS collector. Free, no key -- "Wisconsin's Premiere
Political News Service", includes a real "Press Releases" category and
actual article-body previews in the RSS <description> (unlike Google News,
which only repeats the title).

Known limitation, checked directly: wispolitics.com 403s requests from at
least one datacenter/cloud IP range (confirmed via curl with a real browser
User-Agent -- still blocked -- while the exact same URL loads fine from a
real residential browser). This looks like an IP-range-level anti-scraping
block, not a header check, so it may or may not work from GitHub Actions'
runners depending on whether their IP range is also blocked. This collector
is written to degrade gracefully (logs and returns []) if blocked, rather
than fail the whole sweep -- verify empirically whether it works in CI."""

import sys

import feedparser
import requests

from pipeline.normalize import normalize

FEED_URL = "https://www.wispolitics.com/feed/"
TIMEOUT_SECONDS = 15


def collect(candidate: dict) -> list:
    try:
        resp = requests.get(
            FEED_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            },
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"  [info] wispolitics feed unavailable ({exc}) -- skipping", file=sys.stderr)
        return []

    feed = feedparser.parse(resp.content)
    print(f"  [info] wispolitics: fetched {len(feed.entries)} feed entries for {candidate['name']}", file=sys.stderr)

    items = []
    for entry in feed.entries:
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        combined = f"{title} {summary}"

        # This is a general WisPolitics feed (all WI political news), not a
        # per-candidate search -- filter client-side to this candidate's name.
        name_hit = candidate["name"].lower() in combined.lower() or any(
            alias.lower() in combined.lower() for alias in candidate.get("aliases", [])
        )
        if not name_hit:
            continue

        published_at = ""
        published_parsed = entry.get("published_parsed")
        if published_parsed:
            from datetime import datetime, timezone

            published_at = datetime(*published_parsed[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")

        categories = [tag.get("term", "") for tag in entry.get("tags", [])]

        items.append(
            normalize(
                collector="wispolitics",
                candidate_id=candidate["id"],
                title=title,
                source="WisPolitics",
                source_url=entry.get("link", ""),
                published_at=published_at,
                text=summary,
                author=entry.get("author"),
                raw={"title": title, "link": entry.get("link"), "summary": summary, "categories": categories},
            )
        )
    return items
