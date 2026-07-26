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
worth the maintenance burden for what it buys). A title search against a
public search engine resolves the same problem more generally: given the
item's own title, a DuckDuckGo HTML search can return the real canonical
URL as its top result. But DuckDuckGo soft-blocks under sustained load
(confirmed directly, repeatedly, across this build's real backfill runs --
it starts returning 202 challenge pages instead of results), so it can't
be relied on alone.

Real case that motivated a second resolution path: Google News surfaced a
Cap Times "how to vote" explainer whose body names Dina Nina Martinez
Rutherford in a ballot list, but whose title and search-snippet never
mention her at all -- Cap Times' own on-site search only indexes title/
description, not full body text, so wi_outlets.py's direct search could
never find this article either. Google News (which does full-text
indexing) was the ONLY thing that ever discovered it. With DuckDuckGo
blocked, resolving its redirect to a real URL -- and thus getting a real
shot at the full body text -- would otherwise be a dead end. But Google
News' own title carries a " - {Source}" suffix, and when that source is
one of the outlets pipeline/tncms_outlets.py already knows how to search
directly (Cap Times, WSJ, Channel 3000, WKOW), searching that outlet for
the article's own title resolves it far more reliably than a third party
that keeps getting itself blocked -- same infrastructure already proven
to work, no new failure mode. DuckDuckGo remains a fallback for outlets
outside that known set."""

import re
import sys
import time
from urllib.parse import quote, unquote

import requests

from pipeline.tncms_outlets import TNCMS_OUTLETS, search_outlet

# Short aliases Google News' own "source" label or title-suffix might use --
# terser or differently-worded than TNCMS_OUTLETS' own display names (e.g.
# Google News says "Channel 3000" or "WISC-TV", never the full "Channel
# 3000 / WISC-TV").
_OUTLET_ALIASES = {
    "captimes": ["Capital Times"],
    "wsj_madison": ["Wisconsin State Journal"],
    "channel3000": ["Channel 3000", "WISC-TV"],
    "wkow": ["WKOW"],
}

TIMEOUT_SECONDS = 10
# Was 8000 -- confirmed too low against a real case: a Cap Times "how to
# vote" explainer's real extracted text is 12,822 chars total, and Dina
# Nina Martinez Rutherford's name (in a ballot list) starts at char 10,767
# -- past the old cutoff, so resolve() never saw it despite a successful
# real-URL fetch. Same class of bug already fixed once before in
# classify.py's own truncation (300 -> 8000 chars); this is fetch_text.py's
# turn. Raised generously rather than to the bare minimum this one case
# needed, since a longer round-up/explainer article naming many
# candidates is exactly the shape of content most likely to bury a real
# mention deep in the text.
MAX_CHARS = 20000
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


def _source_outlet_domain(item: dict) -> str:
    """Returns a known TNCMS outlet domain if this item's source outlet is
    one we can search directly, else "". Checks item["source"] first (a
    real field on freshly-collected items), falling back to parsing the
    Google News title's trailing " - {Source}" suffix (needed for older
    stored rejections logged before finalize_rejections() kept `source`)."""
    source_name = item.get("source", "")
    if not source_name:
        title = item.get("title", "")
        if " - " in title:
            source_name = title.rsplit(" - ", 1)[1].strip()
    if not source_name:
        return ""

    source_lower = source_name.lower()
    for key, display_name, domain in TNCMS_OUTLETS:
        aliases = _OUTLET_ALIASES.get(key, [display_name])
        if any(alias.lower() in source_lower for alias in aliases):
            return domain
    return ""


def _resolve_via_known_outlet(item: dict) -> str:
    """Resolves a Google News redirect by searching the article's own
    source outlet for its title, when that outlet is one we already have
    working direct-search code for. Returns "" if the outlet isn't known
    or its search comes up empty -- caller falls back to DuckDuckGo."""
    domain = _source_outlet_domain(item)
    if not domain:
        return ""

    title = item.get("title", "")
    query_title = title.rsplit(" - ", 1)[0].strip() if " - " in title else title
    for entry in search_outlet(domain, query_title):
        link = entry.get("link", "")
        if link:
            return link
    return ""


def resolve_real_url(item: dict) -> str:
    """Returns a real, directly-fetchable article URL for this item, or ""
    if none could be found. Most collectors already give a real URL
    (source_url); Google News' redirect links need resolving first --
    tries the known source outlet's own search before falling back to a
    DuckDuckGo title search (see module docstring for why in that order)."""
    source_url = item.get("source_url", "")
    if item.get("collector") != "google_news":
        return source_url

    title = item.get("title", "")
    if not title:
        return ""

    known_outlet_url = _resolve_via_known_outlet(item)
    if known_outlet_url:
        return known_outlet_url

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


# Known junk patterns confirmed directly against real pages: madison.com's
# (Wisconsin State Journal) paywall serves this nav boilerplate instead of
# the article; Channel 3000/WKOW's client-rendered pages leave either a
# generic live-ticker widget or raw inline JS in the extracted text since a
# plain HTTP GET never runs their JS. Returning this junk as "real" text
# is worse than returning nothing: a downstream LLM check reasoning over
# garbage has been observed falling back to the article's TITLE alone and
# hallucinating relevance from a name mentioned there, since it has no way
# to know the "text" it was handed is meaningless. Better to report the
# fetch as having found nothing, so callers correctly treat this as still
# snippet-only rather than being misled by junk masquerading as real text.
_JUNK_MARKERS = (
    "Log In Subscribe Guest Logout",
    "Live updates all day, breaking news as it happens",
)


def _looks_like_junk(text: str) -> bool:
    if any(marker in text for marker in _JUNK_MARKERS):
        return True
    # Garbled inline JS (seen verbatim on Channel3000/WKOW pages) rather
    # than prose -- a real article extract shouldn't have several "const "
    # declarations in its first couple hundred characters.
    if text[:500].count("const ") > 2:
        return True
    return False


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

    if _looks_like_junk(text):
        return ""

    return text[:MAX_CHARS]
