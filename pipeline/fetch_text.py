"""Fetches real article body text, and the URL-resolution helpers that
feed it.

Real-world case that motivated the whole mechanism: a GDELT hit titled
"Black Lives Matter Protests Break Out in WI..." was rejected as
no_name_match because GDELT's `text` is always empty (see
collectors/gdelt.py) -- but the real article body did mention the candidate
by name. Fetching the real page and matching against its actual text fixes
that class of false negative directly.

URL RESOLUTION -- read pipeline/gnews_url.py first
--------------------------------------------------
Most collectors already hand us a real, directly-fetchable URL. Google News
does not: its RSS links are obfuscated redirect shells. Resolving those is
now pipeline/gnews_url.py's job, via Google's own endpoint, because the
title-search approach this module used to rely on was measured to fail at
essentially 100% at real volume (DuckDuckGo blocks after ~2 requests per
run, and did so *silently* -- see that module's docstring for the full
post-mortem).

What remains here are the two fallbacks, in order, for when the primary
decoder can't resolve something:

  1. resolve_via_known_outlet -- if Google News tells us the article came
     from one of the TNCMS outlets we already have working direct-search
     code for (Cap Times, Wisconsin State Journal, Channel 3000, WKOW),
     search that outlet for the article's own title. Same infrastructure
     as collectors/wi_outlets.py, no third party involved.
  2. DuckDuckGoFallback -- last resort only, and now honest about it. It
     keeps a strict budget and a circuit breaker, detects the 202
     challenge page explicitly, and logs every failure. It is expected to
     resolve a handful of items per run at most; it must never again be
     load-bearing, and if it is silently doing nothing the run summary
     will say so.

Why keep DuckDuckGo at all: it costs one request per remaining item up to
a small cap, and it's the only path that doesn't depend on Google. If
gnews_url.py's protocol breaks, this degrades the sweep instead of
zeroing it.
"""

import re
import sys
import time
from urllib.parse import quote, unquote, urlparse

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

# Politeness spacing between fetches of the SAME outlet. Article fetches
# hit hundreds of different domains, so a global sleep would be pure waste
# -- per-domain is the meaningful unit, and it's what protects the small
# local outlets we care most about not annoying.
PER_DOMAIN_INTERVAL_SECONDS = 1.0
FETCH_ATTEMPTS = 2
FETCH_RETRY_BACKOFF_SECONDS = 2

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


# ---------------------------------------------------------------- outlet


def source_outlet_domain(item: dict) -> str:
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


def resolve_via_known_outlet(item: dict) -> str:
    """Resolves a Google News redirect by searching the article's own
    source outlet for its title, when that outlet is one we already have
    working direct-search code for. Returns "" if the outlet isn't known
    or its search comes up empty.

    Worth keeping even though gnews_url.py resolves nearly everything:
    Google News (which indexes full body text) is sometimes the ONLY way
    an article is discovered at all -- confirmed against a real case, a
    Cap Times "how to vote" explainer whose body names Dina Nina Martinez
    Rutherford in a ballot list but whose title and description never
    mention her, so no on-site search query could ever surface it. This
    path resolves such an item without depending on Google twice."""
    domain = source_outlet_domain(item)
    if not domain:
        return ""

    title = item.get("title", "")
    query_title = title.rsplit(" - ", 1)[0].strip() if " - " in title else title
    if not query_title:
        return ""
    for entry in search_outlet(domain, query_title):
        link = entry.get("link", "")
        if link:
            return link
    return ""


# ------------------------------------------------------------ duckduckgo


class DuckDuckGoFallback:
    """Last-resort title search, on a hard budget.

    The bug this class is the remediation for: the old code called
    DuckDuckGo's HTML endpoint once per rejected item with a 3-second
    sleep, called `raise_for_status()` (which does NOT raise on the 202
    challenge page DDG actually serves when it throttles you), then ran a
    result regex that simply didn't match, and returned "" -- with no log
    line. So a sweep of several hundred items would resolve maybe one,
    block, and then spend the rest of the run sleeping 3 seconds at a time
    to receive challenge pages it reported to nobody.

    Every one of those failure modes is now explicit: 202 and challenge
    bodies are detected and named, a consecutive-failure breaker stops the
    sleeping, MAX_ATTEMPTS_PER_RUN caps the damage even if it never
    formally trips, and the caller records the reason on the item."""

    MAX_ATTEMPTS_PER_RUN = 20
    CIRCUIT_BREAKER_THRESHOLD = 2  # it blocks on roughly the 2nd request
    SEARCH_INTERVAL_SECONDS = 3

    _SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"
    _RESULT_RE = re.compile(r"uddg=([^&\"]+)")
    _CHALLENGE_MARKERS = ("anomaly", "challenge", "unfortunately, bots use DuckDuckGo too")

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _USER_AGENT})
        self.attempts = 0
        self._consecutive_failures = 0
        self.tripped = False
        self.stats = {"resolved": 0, "blocked": 0, "no_result": 0, "error": 0, "skipped": 0}

    def resolve(self, title: str) -> str:
        """Returns a real URL, or "" (with the reason logged and counted)."""
        if not title:
            return ""
        if self.tripped or self.attempts >= self.MAX_ATTEMPTS_PER_RUN:
            self.stats["skipped"] += 1
            return ""

        self.attempts += 1
        time.sleep(self.SEARCH_INTERVAL_SECONDS)
        try:
            resp = self.session.get(
                self._SEARCH_URL.format(query=quote(title)),
                timeout=TIMEOUT_SECONDS,
            )
        except Exception as exc:
            self.stats["error"] += 1
            self._record_failure(f"request failed ({type(exc).__name__})", title)
            return ""

        # 202 is DuckDuckGo's soft block. It is NOT an error status, which
        # is precisely why the previous implementation never noticed it.
        blocked = resp.status_code == 202 or any(
            marker.lower() in resp.text[:4000].lower() for marker in self._CHALLENGE_MARKERS
        )
        if blocked:
            self.stats["blocked"] += 1
            self._record_failure(f"soft-blocked (HTTP {resp.status_code} challenge)", title)
            return ""

        if resp.status_code != 200:
            self.stats["error"] += 1
            self._record_failure(f"HTTP {resp.status_code}", title)
            return ""

        match = self._RESULT_RE.search(resp.text)
        if not match:
            self.stats["no_result"] += 1
            self._record_failure("no result in response", title)
            return ""

        self._consecutive_failures = 0
        self.stats["resolved"] += 1
        return unquote(match.group(1))

    def _record_failure(self, reason: str, title: str) -> None:
        self._consecutive_failures += 1
        print(f"  [info] ddg fallback: {reason} for {title[:60]!r}", file=sys.stderr)
        if self._consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD and not self.tripped:
            self.tripped = True
            print(
                f"  [warn] ddg fallback disabled for this run after "
                f"{self._consecutive_failures} consecutive failures",
                file=sys.stderr,
            )


# ------------------------------------------------------------ article text

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


class ArticleFetcher:
    """Fetches article body text, with a shared session, a per-run cache,
    and per-domain pacing.

    Returns (text, status) rather than bare text so callers can tell the
    three cases apart -- got real text / the page refused us / the page
    gave us junk. They are not the same thing and were previously
    indistinguishable, which is how a 0% success rate looked identical to
    "nothing to fetch"."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _USER_AGENT})
        self._cache = {}
        self._last_fetch_by_domain = {}
        self.stats = {"ok": 0, "junk": 0, "failed": 0, "cached": 0}

    def _pace(self, url: str) -> None:
        domain = urlparse(url).netloc
        last = self._last_fetch_by_domain.get(domain)
        if last is not None:
            wait = PER_DOMAIN_INTERVAL_SECONDS - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_fetch_by_domain[domain] = time.monotonic()

    def fetch(self, url: str):
        """Returns (text, status). status is "ok", "junk_text", or
        "fetch_failed:<reason>". Never raises.

        Real news pages carry tens of thousands of characters of nav/ad/
        related-article boilerplate before the actual article body -- a flat
        "strip all tags and take the first N chars" approach never reaches
        the real content (confirmed: 68,000+ chars of boilerplate preceded a
        real name mention on one test page). Extracting <p> tags
        specifically skips that boilerplate, since standard article
        templates put the real body in paragraph tags and nav/header/footer
        chrome in divs/spans/lists instead. Falls back to whole-page
        stripping only if a page has no <p> tags at all."""
        if not url:
            return "", "fetch_failed:no_url"
        if url in self._cache:
            self.stats["cached"] += 1
            return self._cache[url]

        # One retry: a measured live run lost 5 of 42 articles to plain
        # transient failures (ReadTimeout, a 5xx). Those are items that
        # would then be judged on a 20-word snippet for no better reason
        # than a slow server, which is the same silent-degradation failure
        # this rewrite exists to eliminate -- just at a smaller scale.
        resp = None
        last_error = "unknown"
        for attempt in range(FETCH_ATTEMPTS):
            if attempt > 0:
                time.sleep(FETCH_RETRY_BACKOFF_SECONDS)
            self._pace(url)
            try:
                resp = self.session.get(url, timeout=TIMEOUT_SECONDS)
                resp.raise_for_status()
                break
            except Exception as exc:
                last_error = type(exc).__name__
                resp = None

        if resp is None:
            print(f"  [info] full-text fetch failed for {url}: {last_error}", file=sys.stderr)
            result = ("", f"fetch_failed:{last_error}")
            self._cache[url] = result
            self.stats["failed"] += 1
            return result

        paragraphs = _PARAGRAPH_RE.findall(resp.text)
        if paragraphs:
            text = " ".join(_strip_tags(p) for p in paragraphs)
        else:
            text = _strip_tags(resp.text)

        if _looks_like_junk(text):
            result = ("", "junk_text")
            self.stats["junk"] += 1
        elif not text.strip():
            result = ("", "fetch_failed:empty_page")
            self.stats["failed"] += 1
        else:
            result = (text[:MAX_CHARS], "ok")
            self.stats["ok"] += 1

        self._cache[url] = result
        return result


def fetch_article_text(url: str) -> str:
    """Single-shot convenience wrapper. Returns extracted text or "".
    Prefer an ArticleFetcher instance for anything batch-shaped -- it
    carries the cache and the per-domain pacing."""
    return ArticleFetcher().fetch(url)[0]
