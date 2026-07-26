"""Direct site-search collectors for known-good Wisconsin local outlets.

Real-world motivation: Google News RSS links resolve to an obfuscated
redirect shell (no real URL) and never include an actual article snippet
(just the headline repeated) -- see fetch_text.py/resolve.py for the
DuckDuckGo-title-search workaround that fixes that indirectly, and its own
weakness (DuckDuckGo's HTML endpoint soft-blocks a testing IP after a burst
of requests, confirmed directly: it started returning 202 challenge pages
instead of results mid-backfill). This collector sidesteps the whole
problem for outlets that support it: query the real newsroom directly and
get back a real snippet and a real URL in one request, no redirect-
resolution or third-party dependency involved at all.

captimes.com, madison.com (Wisconsin State Journal), and
www.channel3000.com are all Lee Enterprises / TownNews properties running
the same CMS (TNCMS, confirmed via each feed's own <generator> tag) with an
identical site-search RSS interface:
    https://{domain}/search/?f=rss&t=article&q={query}
Checked directly against a real missing-article case: querying captimes.com
for "democratic socialists" returned the exact Juliana Bennett DSA-
endorsement article as its top result, with a real body snippet ("Juliana
Bennett is running for Wisconsin Assembly District 76...") -- something
Google News never gave us for this item.

isthmus.com runs a different CMS and its RSS endpoint
(api/rss/content.rss) ignores query parameters entirely -- it only serves
its general recent-content feed. There's no search-RSS shortcut available,
so this collector pages through that general feed and filters client-side
by candidate name, same approach as wispolitics.py's general feed."""

import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import feedparser
import requests

from pipeline.normalize import normalize

TIMEOUT_SECONDS = 15
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# TNCMS outlets: same search-RSS interface, just a different domain/display name.
TNCMS_OUTLETS = [
    ("captimes", "The Capital Times", "captimes.com"),
    ("wsj_madison", "Wisconsin State Journal", "madison.com"),
    ("channel3000", "Channel 3000 / WISC-TV", "www.channel3000.com"),
]

ISTHMUS_FEED_URL = "https://isthmus.com/api/rss/content.rss"
ISTHMUS_MAX_PAGES = 3  # general feed, not search -- only page back far enough for recent items


def _parse_published(entry) -> str:
    published_parsed = entry.get("published_parsed")
    if not published_parsed:
        return ""
    return datetime(*published_parsed[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")


def _collect_tncms_outlet(candidate: dict, display_name: str, domain: str, cutoff) -> list:
    name_phrases = [candidate["name"]] + candidate.get("aliases", [])
    items = []
    seen_links = set()

    for phrase in name_phrases:
        url = f"https://{domain}/search/?f=rss&t=article&q={quote(phrase)}"
        try:
            resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
        except Exception as exc:
            print(f"  [info] {domain} search failed for {phrase!r}: {exc}", file=sys.stderr)
            continue

        feed = feedparser.parse(resp.content)
        for entry in feed.entries:
            link = entry.get("link", "")
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            published_at = _parse_published(entry)
            if cutoff and published_at and datetime.fromisoformat(published_at) < cutoff:
                continue

            title = entry.get("title", "")
            summary = entry.get("summary", "")
            items.append(
                normalize(
                    collector=f"wi_outlet_{TNCMS_OUTLETS_BY_DOMAIN[domain]}",
                    candidate_id=candidate["id"],
                    title=title,
                    source=display_name,
                    source_url=link,
                    published_at=published_at,
                    text=summary,
                    author=entry.get("author"),
                    raw={"title": title, "link": link, "summary": summary, "matched_query": phrase},
                )
            )
    return items


TNCMS_OUTLETS_BY_DOMAIN = {domain: key for key, _, domain in TNCMS_OUTLETS}


def _collect_isthmus(candidate: dict, cutoff) -> list:
    # Match on race_context_terms too, not just the name -- Isthmus is a
    # general feed we filter client-side (unlike the TNCMS outlets' real
    # per-candidate search), and a real "N candidates running for this seat"
    # roundup can be genuinely about this candidate without ever naming her
    # in the title/summary (confirmed: "Priorities differentiate Madison
    # Dems running in Assembly primary" mentions neither Dina Nina nor
    # Juliana by name, just "Assembly District 76" and "five Democrats").
    # resolve.py already accepts this pattern downstream (race_context_only
    # match type) -- filtering it out here before resolve() ever sees it
    # would silently drop a genuine match.
    match_phrases = (
        [candidate["name"].lower()]
        + [a.lower() for a in candidate.get("aliases", [])]
        + [t.lower() for t in candidate.get("race_context_terms", [])]
    )
    items = []
    url = ISTHMUS_FEED_URL

    for _ in range(ISTHMUS_MAX_PAGES):
        if not url:
            break
        try:
            resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
        except Exception as exc:
            print(f"  [info] isthmus.com feed page failed: {exc}", file=sys.stderr)
            break

        feed = feedparser.parse(resp.content)
        page_had_recent_item = False
        for entry in feed.entries:
            published_at = _parse_published(entry)
            if cutoff and published_at and datetime.fromisoformat(published_at) < cutoff:
                continue
            page_had_recent_item = True

            title = entry.get("title", "")
            summary = entry.get("summary", "")
            combined = f"{title} {summary}".lower()
            if not any(phrase in combined for phrase in match_phrases):
                continue

            link = entry.get("link", "")
            items.append(
                normalize(
                    collector="wi_outlet_isthmus",
                    candidate_id=candidate["id"],
                    title=title,
                    source="Isthmus",
                    source_url=link,
                    published_at=published_at,
                    text=summary,
                    author=entry.get("author"),
                    raw={"title": title, "link": link, "summary": summary},
                )
            )

        # General feed is reverse-chronological -- once a whole page is
        # older than the cutoff, later pages will be too. Stop paginating
        # instead of fetching pages we'll only throw away (matters for the
        # daily sweep's narrow window; backfill's wide/no cutoff never
        # triggers this).
        if cutoff and not page_had_recent_item:
            break

        next_links = [l.get("href") for l in feed.feed.get("links", []) if l.get("rel") == "next"]
        url = next_links[0] if next_links else None

    return items


def collect(candidate: dict, days: int = 3) -> list:
    """days=None means unbounded (full outlet history -- used by backfill).
    Default of 3, not 1, gives the twice-daily cron a buffer against a
    missed/failed run without re-classifying the outlet's entire archive
    every day -- these search endpoints have no server-side date filter, so
    every call re-fetches full search results and we filter client-side;
    an unbounded default here would re-run classify_items (paid-quota LLM
    calls) against the same ~100 historical items per candidate on every
    single sweep, for zero new information each time."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None

    items = []
    for _key, display_name, domain in TNCMS_OUTLETS:
        try:
            items.extend(_collect_tncms_outlet(candidate, display_name, domain, cutoff))
        except Exception as exc:
            print(f"  [warn] {domain} collector failed for {candidate['name']}: {exc}", file=sys.stderr)

    try:
        items.extend(_collect_isthmus(candidate, cutoff))
    except Exception as exc:
        print(f"  [warn] isthmus.com collector failed for {candidate['name']}: {exc}", file=sys.stderr)

    return items
