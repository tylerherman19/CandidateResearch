"""Shared TNCMS site-search primitive, used two ways:
  - collectors/wi_outlets.py: search each outlet by candidate name/
    race_context_terms to discover new items directly.
  - pipeline/fetch_text.py: resolve a Google News redirect link to a real
    URL by searching the ORIGINAL article's own title against its known
    source outlet, when Google News tells us which outlet it came from.

Why the second use case matters, concretely: Google News sometimes finds
an article (via Google's own full-text index) that our direct per-outlet
search never would, because TNCMS's own on-site search only indexes
title/description, not full body text -- confirmed directly against a
real case (a Cap Times "how to vote" explainer whose body names Dina Nina
Martinez Rutherford in a ballot list, but whose title/description don't
mention her at all, so no name or race_context_terms query ever surfaces
it). Google News is often the ONLY discovery path for that class of
article. Previously, resolving its redirect link depended entirely on
DuckDuckGo's title search, which soft-blocks under load (see fetch_text.py
history). But when the source outlet is one we already have working
direct-search code for, there's a much more reliable option: search THAT
outlet for the article's own title. Same infra, no third party involved.

captimes.com, madison.com (Wisconsin State Journal), www.channel3000.com,
and www.wkow.com are all Lee Enterprises / TownNews properties running
the same CMS (TNCMS, confirmed via each feed's own <generator> tag) with
an identical site-search RSS interface:
    https://{domain}/search/?f=rss&t=article&q={query}
isthmus.com is NOT included here -- different CMS, no working search
endpoint (see collectors/wi_outlets.py for how it's handled instead, via
its general content feed)."""

import sys
import time

import feedparser
import requests
from urllib.parse import quote

TIMEOUT_SECONDS = 20
RETRY_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 3
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# (key, display_name, domain) -- key is used to build collector names
# (wi_outlet_{key}) and is also how fetch_text.py maps a Google-News
# "source" display name or title suffix back to one of these outlets.
TNCMS_OUTLETS = [
    ("captimes", "The Capital Times", "captimes.com"),
    ("wsj_madison", "Wisconsin State Journal", "madison.com"),
    ("channel3000", "Channel 3000 / WISC-TV", "www.channel3000.com"),
    ("wkow", "WKOW 27", "www.wkow.com"),
]

TNCMS_OUTLETS_BY_DOMAIN = {domain: key for key, _, domain in TNCMS_OUTLETS}


def get_with_retry(url: str):
    last_exc = None
    for attempt in range(RETRY_ATTEMPTS):
        if attempt > 0:
            time.sleep(RETRY_BACKOFF_SECONDS)
        try:
            resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            last_exc = exc
    raise last_exc


def search_outlet(domain: str, query: str) -> list:
    """Returns feedparser entries from domain's TNCMS search-RSS for
    query, or [] on any failure. Never raises."""
    url = f"https://{domain}/search/?f=rss&t=article&q={quote(query)}"
    try:
        resp = get_with_retry(url)
    except Exception as exc:
        print(f"  [info] {domain} search failed for {query!r}: {exc}", file=sys.stderr)
        return []
    return feedparser.parse(resp.content).entries
