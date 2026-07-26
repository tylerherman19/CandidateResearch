"""Fetches real article body text for a fallback resolve() pass, when the
collector's own title/snippet caused a rejection.

Real-world case that motivated this: a GDELT hit titled "Black Lives Matter
Protests Break Out in WI..." was rejected as no_name_match because GDELT's
`text` is always empty (see collectors/gdelt.py) -- but the real article
body did mention the candidate by name. Fetching the real page and matching
against its actual text fixes this class of false negative directly,
instead of another yaml-level workaround.

Google News RSS links route through an obfuscated JS-redirect shell, not a
real article URL. Decoding Google's internal batchexecute protocol directly
was ruled out (fragile, breaks silently whenever Google changes it -- not
worth the maintenance burden for what it buys). But a title search against
a public search engine resolves the same problem far more robustly: given
the item's own title (which we already have), a DuckDuckGo HTML search
reliably returns the real canonical URL as its top result -- checked
directly, the true captimes.com URL for a real test article came back
verbatim with no site restriction needed, just the title text. This is the
fix for the Google News dead-end that earlier passes correctly declined to
solve via URL decoding."""

import re
import sys
import time
from urllib.parse import quote, unquote

import requests

TIMEOUT_SECONDS = 10
MAX_CHARS = 8000
# DuckDuckGo's HTML endpoint 429/202-challenges under rapid repeated
# requests (confirmed directly -- same throttling pattern already seen from
# GDELT). Only matters for our own back-to-back rejected items in one run;
# a small spacing keeps this from tripping under normal volume.
SEARCH_INTERVAL_SECONDS = 3

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_DDG_RESULT_RE = re.compile(r"uddg=([^&\"]+)")
_DDG_SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _strip_tags(html: str) -> str:
    html = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", html)
    return _WHITESPACE_RE.sub(" ", text).strip()


def resolve_real_url(item: dict) -> str:
    """Returns a real, directly-fetchable article URL for this item, or ""
    if none could be found. Most collectors already give a real URL
    (source_url); Google News' redirect links need resolving via a title
    search first."""
    source_url = item.get("source_url", "")
    if item.get("collector") != "google_news":
        return source_url

    title = item.get("title", "")
    if not title:
        return ""

    time.sleep(SEARCH_INTERVAL_SECONDS)
    try:
        resp = requests.get(
            _DDG_SEARCH_URL.format(query=quote(title)),
            headers={"User-Agent": _USER_AGENT},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"  [info] title search failed for {title[:60]!r}: {exc}", file=sys.stderr)
        return ""

    match = _DDG_RESULT_RE.search(resp.text)
    if not match:
        return ""
    return unquote(match.group(1))


def fetch_article_text(url: str) -> str:
    """Returns extracted plain text, or "" on any failure. Never raises.

    Real news pages carry tens of thousands of characters of nav/ad/related-
    article boilerplate before the actual article body -- a flat "strip all
    tags and take the first N chars" approach never reaches the real
    content (confirmed: 68,000+ chars of boilerplate preceded a real name
    mention on one test page). Extracting <p> tags specifically skips that
    boilerplate, since standard article templates put the real body in
    paragraph tags and nav/header/footer chrome in divs/spans/lists instead.
    Falls back to whole-page stripping only if a page has no <p> tags at all."""
    if not url:
        return ""
    try:
        resp = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
    except Exception as exc:
        print(f"  [info] full-text fetch failed for {url}: {exc}", file=sys.stderr)
        return ""

    paragraphs = _PARAGRAPH_RE.findall(resp.text)
    if paragraphs:
        text = " ".join(_strip_tags(p) for p in paragraphs)
    else:
        text = _strip_tags(resp.text)

    return text[:MAX_CHARS]
