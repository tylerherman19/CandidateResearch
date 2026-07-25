"""Fetches real article body text for a fallback resolve() pass, when the
collector's own title/snippet caused a rejection but the collector gives us
a real, direct article URL (confirmed for GDELT: its `url` field is the
actual publisher link, not an obfuscated redirect -- unlike Google News RSS,
whose links route through a JS-redirect shell that requires decoding
Google's internal batchexecute mechanism to resolve, which isn't worth the
fragile-maintenance tradeoff).

Real-world case that motivated this: a GDELT hit titled "Black Lives Matter
Protests Break Out in WI..." was rejected as no_name_match because GDELT's
`text` is always empty (see collectors/gdelt.py) -- but the real article
body did mention the candidate by name. Fetching the real page and matching
against its actual text fixes this class of false negative directly,
instead of another yaml-level workaround.
"""

import re
import sys

import requests

TIMEOUT_SECONDS = 10
MAX_CHARS = 8000

# Collectors whose source_url is NOT a real, direct, fetchable article link
# -- fetching would either get nothing useful or a redirect shell.
NO_DIRECT_URL_COLLECTORS = {"google_news"}

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_PARAGRAPH_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _strip_tags(html: str) -> str:
    html = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", html)
    return _WHITESPACE_RE.sub(" ", text).strip()


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


def can_fetch_direct(collector: str) -> bool:
    return collector not in NO_DIRECT_URL_COLLECTORS
