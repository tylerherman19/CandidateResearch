"""Resolve Google News RSS redirect links to real article URLs, using
Google's own endpoint rather than a third-party search engine.

WHY THIS EXISTS (the bug it replaces)
-------------------------------------
Google News RSS links point at an obfuscated redirect shell
(news.google.com/rss/articles/CBMi...), not a real article URL. Everything
downstream that needs the real page -- the full-text fallback in
pipeline/enrich.py, and through it the LLM judge -- is blocked until that
link is turned into a real URL.

The previous implementation resolved it by running a DuckDuckGo HTML search
for the article's title. That was measured, live, to fail essentially
100% of the time at real sweep volume: DuckDuckGo soft-blocks after
roughly the *second* request, serving an HTTP 202 challenge page instead of
results, and stays blocked for the rest of the run. Worse, it failed
silently -- 202 is not an error status, so `raise_for_status()` passed, the
result regex simply didn't match, and the function returned "" with no log
line at all. Net effect: for any outlet outside the four-site TNCMS
allowlist (i.e. most of them -- PBS Wisconsin, AOL, WPR, CNN, ABC...), the
full-text fetch never happened, and both resolve() and the LLM judge were
deciding on a ~20-word headline snippet while believing they'd tried.

That is not a tuning problem. Spacing requests further apart does not fix a
service that rate-limits us to single digits per run when we need hundreds.
It needed a resolution path we aren't rationed on.

HOW THIS WORKS
--------------
Google News' own web UI resolves these links through an internal RPC
(`Fbv4je` on the DotsSplashUi batchexecute endpoint). Each article page
carries a per-article signature (`data-n-a-sg`) and timestamp
(`data-n-a-ts`); posting those back with the article ID returns the real
canonical URL.

Two properties make this the right primitive rather than a different
flavour of the same fragility:

  1. It is not meaningfully rate-limited at our volume. Measured live:
     166 consecutive signature fetches, back to back with no sleep at all,
     166/166 succeeded (~0.24s each). Compare: DuckDuckGo blocked at
     request two.
  2. The expensive half batches. The signature fetch is one GET per
     article, but the RPC accepts many articles in a single POST --
     measured: 60 articles resolved in one 0.1s request. So a sweep of
     several hundred items costs a few hundred cheap GETs and a handful of
     POSTs, not one fragile third-party search per item.

A prior version of this codebase ruled out "decoding Google's internal
batchexecute protocol" as fragile. That judgement was right about the
risk and wrong about the alternative: the fallback it chose instead
doesn't work at all. Fragile-but-working beats robust-in-principle-but-
0%-in-practice. The fragility is handled explicitly instead of wished
away -- see the failure contract below.

FAILURE CONTRACT
----------------
This module never raises and never fails silently. Every URL either
resolves or comes back with a machine-readable reason
(`unresolved:<why>`), which callers record on the item and surface in the
run summary. If Google changes the protocol tomorrow, the next run says
so out loud -- in the terminal summary and the digest -- instead of
quietly reverting to headline-only judgement, which is exactly how the
previous failure went unnoticed for as long as it did.

The circuit breaker exists for the same reason: if signature fetches start
failing consecutively (protocol change, IP block, outage), we stop after
CIRCUIT_BREAKER_THRESHOLD rather than spending twenty minutes of a sweep
re-confirming that Google is saying no.
"""

import base64
import json
import re
import sys
import time
from urllib.parse import urlparse

import requests

BATCH_SIZE = 50  # 60 verified working in one POST; 50 leaves headroom
TIMEOUT_SECONDS = 20
# Measured: 166 back-to-back signature fetches, zero failures, no sleep.
# A small default spacing anyway, because "it didn't throttle us today" is
# not a guarantee, and a daily batch job has no reason to be in a hurry.
SIGNATURE_INTERVAL_SECONDS = 0.2
RETRY_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 2
# Consecutive signature-fetch failures before we give up on Google for the
# rest of this run. Isolated failures (one dead article) shouldn't trip it;
# a protocol change or an IP block will, immediately.
CIRCUIT_BREAKER_THRESHOLD = 5

_BATCHEXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_ARTICLE_URL = "https://news.google.com/rss/articles/{article_id}"
_RPC_ID = "Fbv4je"

_SIGNATURE_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_TIMESTAMP_RE = re.compile(r'data-n-a-ts="([^"]+)"')

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def is_google_news_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.netloc.endswith("news.google.com") and "/articles/" in parsed.path


def article_id_from_url(url: str) -> str:
    """The trailing path segment, minus any query string."""
    if not is_google_news_url(url):
        return ""
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def decode_legacy_id(article_id: str) -> str:
    """Older Google News article IDs embed the real URL directly in a
    base64'd protobuf; newer ones (the `AU_yqL...` family, which is
    everything current) are opaque and need the RPC. Free to try, no
    network, and it matters for the stored rejection backlog, which
    contains links old enough to still be the legacy format.

    Returns "" if this isn't a legacy ID."""
    try:
        raw = base64.urlsafe_b64decode(article_id + "=" * (-len(article_id) % 4))
    except Exception:
        return ""
    # Field 2 (0x22) is a length-delimited string; on legacy IDs it is the
    # URL itself, on new ones it's the opaque AU_yqL... blob.
    for match in re.finditer(rb"\x22(.)(https?://[^\x00-\x1f]+)", raw):
        candidate = match.group(2)
        expected_length = match.group(1)[0]
        if len(candidate) >= expected_length:
            candidate = candidate[:expected_length]
        try:
            return candidate.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return ""


class GoogleNewsResolver:
    """Resolves a batch of Google News redirect links to real URLs.

    Stateful on purpose: it holds one HTTP session (connection reuse across
    hundreds of requests), a per-run resolution cache, and the circuit
    breaker. One instance per sweep."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": _USER_AGENT})
        self._cache = {}
        self._consecutive_failures = 0
        self.tripped = False
        self.stats = {"legacy": 0, "rpc": 0, "cached": 0, "failed": 0}

    # -- signature phase ------------------------------------------------

    def _fetch_signature(self, article_id: str):
        """Returns (signature, timestamp) or (None, reason)."""
        url = _ARTICLE_URL.format(article_id=article_id)
        last_error = "unknown"
        for attempt in range(RETRY_ATTEMPTS):
            if attempt > 0:
                time.sleep(RETRY_BACKOFF_SECONDS)
            try:
                resp = self.session.get(url, timeout=TIMEOUT_SECONDS)
            except Exception as exc:
                last_error = f"request_failed:{type(exc).__name__}"
                continue
            if resp.status_code != 200:
                last_error = f"http_{resp.status_code}"
                continue
            signature = _SIGNATURE_RE.search(resp.text)
            timestamp = _TIMESTAMP_RE.search(resp.text)
            if not signature or not timestamp:
                # The page loaded but carries no signature -- this is the
                # shape a Google-side protocol change would take, so name
                # it distinctly rather than lumping it in with network
                # errors.
                return None, "no_signature_in_page"
            return (signature.group(1), timestamp.group(1)), ""
        return None, last_error

    # -- RPC phase ------------------------------------------------------

    @staticmethod
    def _build_rpc_entry(article_id: str, timestamp: str, signature: str, index: int) -> list:
        payload = [
            "garturlreq",
            [
                ["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
                "X",
                "X",
                1,
                [1, 1, 1],
                1,
                1,
                None,
                0,
                0,
                None,
                0,
            ],
            article_id,
            int(timestamp),
            signature,
        ]
        return [_RPC_ID, json.dumps(payload, separators=(",", ":")), None, str(index)]

    @staticmethod
    def _parse_rpc_response(text: str) -> dict:
        """Returns {index_string: real_url} for whatever resolved.

        The response is Google's anti-JSON-hijacking `)]}'` preamble
        followed by a JSON array of RPC envelopes. Entries whose payload is
        null failed individually (a bad signature, a dead article) and are
        simply absent from the result -- the caller marks those unresolved
        rather than the whole batch."""
        body = text.split("\n", 1)[1] if text.startswith(")]}'") else text
        rows = None
        try:
            rows = json.loads(body)
        except json.JSONDecodeError:
            # Very large responses can arrive length-prefixed in chunks
            # instead of as one array; recover what parses.
            rows = []
            for line in body.splitlines():
                line = line.strip()
                if not line.startswith("[["):
                    continue
                try:
                    rows.extend(json.loads(line))
                except json.JSONDecodeError:
                    continue

        resolved = {}
        for row in rows or []:
            if not isinstance(row, list) or len(row) < 7:
                continue
            if row[0] != "wrb.fr" or not row[2]:
                continue
            try:
                inner = json.loads(row[2])
            except json.JSONDecodeError:
                continue
            if isinstance(inner, list) and len(inner) > 1 and inner[0] == "garturlres":
                resolved[str(row[6])] = inner[1]
        return resolved

    def _resolve_rpc_batch(self, signed: list) -> dict:
        """signed: list of (article_id, signature, timestamp).
        Returns {article_id: real_url} for those that resolved."""
        if not signed:
            return {}

        entries = [
            self._build_rpc_entry(article_id, timestamp, signature, index + 1)
            for index, (article_id, signature, timestamp) in enumerate(signed)
        ]
        form = {"f.req": json.dumps([entries], separators=(",", ":"))}

        for attempt in range(RETRY_ATTEMPTS):
            if attempt > 0:
                time.sleep(RETRY_BACKOFF_SECONDS)
            try:
                resp = self.session.post(
                    _BATCHEXECUTE_URL,
                    headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                    data=form,
                    timeout=TIMEOUT_SECONDS,
                )
            except Exception as exc:
                print(f"  [warn] google news url rpc failed: {exc}", file=sys.stderr)
                continue
            if resp.status_code != 200:
                print(f"  [warn] google news url rpc returned {resp.status_code}", file=sys.stderr)
                continue

            by_index = self._parse_rpc_response(resp.text)
            return {
                signed[int(index) - 1][0]: url
                for index, url in by_index.items()
                if index.isdigit() and 0 < int(index) <= len(signed)
            }
        return {}

    # -- public API -----------------------------------------------------

    def resolve_batch(self, urls: list) -> dict:
        """Returns {google_news_url: real_url_or_"unresolved:<reason>"}.

        Never raises. Non-Google-News URLs are returned unchanged, so
        callers can hand this a mixed list without pre-filtering."""
        results = {}
        needs_signature = []  # (url, article_id)

        for url in dict.fromkeys(urls):
            if not is_google_news_url(url):
                results[url] = url
                continue
            if url in self._cache:
                results[url] = self._cache[url]
                self.stats["cached"] += 1
                continue

            article_id = article_id_from_url(url)
            if not article_id:
                results[url] = "unresolved:no_article_id"
                continue

            legacy = decode_legacy_id(article_id)
            if legacy:
                results[url] = legacy
                self._cache[url] = legacy
                self.stats["legacy"] += 1
                continue

            needs_signature.append((url, article_id))

        if not needs_signature:
            return results

        if self.tripped:
            for url, _ in needs_signature:
                results[url] = "unresolved:circuit_breaker_open"
                self.stats["failed"] += 1
            return results

        signed = []  # (article_id, signature, timestamp)
        url_by_article_id = {}
        for url, article_id in needs_signature:
            if self.tripped:
                results[url] = "unresolved:circuit_breaker_open"
                self.stats["failed"] += 1
                continue

            if SIGNATURE_INTERVAL_SECONDS:
                time.sleep(SIGNATURE_INTERVAL_SECONDS)
            signature_pair, reason = self._fetch_signature(article_id)
            if signature_pair is None:
                results[url] = f"unresolved:{reason}"
                self.stats["failed"] += 1
                self._consecutive_failures += 1
                if self._consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                    self.tripped = True
                    print(
                        f"  [warn] google news url resolution circuit breaker tripped after "
                        f"{self._consecutive_failures} consecutive failures (last: {reason}); "
                        f"remaining items this run will be judged on their collector snippet only",
                        file=sys.stderr,
                    )
                continue

            self._consecutive_failures = 0
            signature, timestamp = signature_pair
            signed.append((article_id, signature, timestamp))
            url_by_article_id[article_id] = url

        for start in range(0, len(signed), BATCH_SIZE):
            chunk = signed[start : start + BATCH_SIZE]
            resolved = self._resolve_rpc_batch(chunk)
            for article_id, _, _ in chunk:
                url = url_by_article_id[article_id]
                real_url = resolved.get(article_id)
                if real_url:
                    results[url] = real_url
                    self._cache[url] = real_url
                    self.stats["rpc"] += 1
                else:
                    results[url] = "unresolved:rpc_no_result"
                    self.stats["failed"] += 1

        return results

    def resolve_one(self, url: str) -> str:
        """Convenience for the single-item path (scripts, backfill).
        Prefer resolve_batch -- one POST per item wastes the batching that
        makes this cheap."""
        return self.resolve_batch([url]).get(url, "unresolved:unknown")
