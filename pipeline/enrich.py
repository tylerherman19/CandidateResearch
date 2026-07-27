"""Full-text enrichment: turn snippet-only items into real-article-text
items, in one batched pass, with honest per-item provenance.

WHY THIS IS A SEPARATE PHASE
-----------------------------
It used to be inline: resolve_with_fetch_fallback() did resolve -> resolve
URL -> fetch page -> re-resolve, one item at a time, inside the collector
loop. That structure forced the expensive part to be per-item, which is
what made a per-item third-party search look like a reasonable primitive
in the first place -- and a per-item third-party search is exactly what
rate limiting kills.

Doing it as a distinct phase over the whole set of rejected items instead:

  * lets URL resolution batch (pipeline/gnews_url.py resolves ~50 articles
    in a single RPC round trip rather than one search per item),
  * gives article fetches a shared session, a cache, and per-domain
    pacing instead of hammering one outlet in a tight loop,
  * produces run-level statistics, so "the full-text path resolved 0 of
    312 items" is a number printed in the summary rather than something
    you only discover by manually opening articles during an audit.

That last point is the actual fix for how the previous failure went
unnoticed. The resolution mechanism was replaced because it didn't work;
this phase exists so that if the replacement stops working, you find out
the next morning.

PROVENANCE STAMPED ON EVERY ITEM
--------------------------------
  url_resolution -- "direct" (collector gave a real URL), "gnews_decode",
      "outlet_search", "ddg", or "unresolved:<reason>"
  resolved_url   -- the real article URL, when we got one. Persisted on
      rejection records too, so a reprocessing run never has to resolve
      the same link twice (and so a rejection stays reprocessable even if
      Google's redirect later dies -- see run.finalize_rejections, which
      already learned this lesson once about `text`).
  text_source    -- "collector" (title/snippet only) or "full_text"
  full_text_status -- "ok", "junk_text", "fetch_failed:<reason>",
      "unresolved_url", or "not_attempted"

Anything judged on `text_source == "collector"` was judged on ~20 words.
That is a fact worth carrying downstream rather than losing.
"""

import sys

from pipeline.fetch_text import ArticleFetcher, DuckDuckGoFallback, resolve_via_known_outlet
from pipeline.gnews_url import GoogleNewsResolver, is_google_news_url
from pipeline.resolve import resolve


class Enricher:
    """One instance per run. Holds the HTTP sessions, caches, circuit
    breakers and counters shared across the whole batch."""

    def __init__(self, use_ddg_fallback: bool = True):
        self.gnews = GoogleNewsResolver()
        self.fetcher = ArticleFetcher()
        self.ddg = DuckDuckGoFallback() if use_ddg_fallback else None
        self.stats = {
            "considered": 0,
            "url_direct": 0,
            "url_gnews_decode": 0,
            "url_outlet_search": 0,
            "url_ddg": 0,
            "url_unresolved": 0,
            "text_ok": 0,
            "text_junk": 0,
            "text_fetch_failed": 0,
            "rescued": 0,
        }

    # -- phase 1: URLs --------------------------------------------------

    def resolve_urls(self, items: list) -> None:
        """Stamps resolved_url / url_resolution on every item, in place.

        Batched deliberately: every Google News link in the batch is
        resolved through one shared set of RPC calls, and only the
        leftovers fall through to the per-item fallbacks."""
        google_news_urls = [
            item.get("source_url", "")
            for item in items
            if is_google_news_url(item.get("source_url", ""))
        ]
        decoded = self.gnews.resolve_batch(google_news_urls) if google_news_urls else {}

        for item in items:
            source_url = item.get("source_url", "")

            # Already resolved on a previous run and carried on the stored
            # record -- don't spend a round trip re-deriving it.
            cached = item.get("resolved_url", "")
            if cached and not cached.startswith("unresolved:"):
                item["url_resolution"] = item.get("url_resolution") or "cached"
                continue

            if not is_google_news_url(source_url):
                # Every other collector hands us a real URL already.
                item["resolved_url"] = source_url
                item["url_resolution"] = "direct" if source_url else "unresolved:no_source_url"
                if source_url:
                    self.stats["url_direct"] += 1
                else:
                    self.stats["url_unresolved"] += 1
                continue

            real_url = decoded.get(source_url, "unresolved:not_attempted")
            if not real_url.startswith("unresolved:"):
                item["resolved_url"] = real_url
                item["url_resolution"] = "gnews_decode"
                self.stats["url_gnews_decode"] += 1
                continue

            decode_failure = real_url

            outlet_url = resolve_via_known_outlet(item)
            if outlet_url:
                item["resolved_url"] = outlet_url
                item["url_resolution"] = "outlet_search"
                self.stats["url_outlet_search"] += 1
                continue

            ddg_url = self.ddg.resolve(item.get("title", "")) if self.ddg else ""
            if ddg_url:
                item["resolved_url"] = ddg_url
                item["url_resolution"] = "ddg"
                self.stats["url_ddg"] += 1
                continue

            item["resolved_url"] = ""
            item["url_resolution"] = decode_failure
            self.stats["url_unresolved"] += 1

    # -- phase 2: text --------------------------------------------------

    def fetch_texts(self, items: list) -> None:
        """Stamps text / text_source / full_text_status, in place. Items
        whose URL never resolved are marked, not silently skipped."""
        for item in items:
            url = item.get("resolved_url", "")
            if not url:
                item["text_source"] = "collector"
                item["full_text_status"] = "unresolved_url"
                continue

            text, status = self.fetcher.fetch(url)
            if status == "ok":
                item["text"] = text
                item["text_source"] = "full_text"
                item["full_text_status"] = "ok"
                self.stats["text_ok"] += 1
                continue

            # Junk (paywall nav, un-run JS) is worse than nothing -- see
            # fetch_text._looks_like_junk. Leave the collector's own text
            # in place and say so, rather than overwriting it with garbage
            # the LLM judge would then reason over.
            item["text_source"] = "collector"
            item["full_text_status"] = status
            if status == "junk_text":
                self.stats["text_junk"] += 1
            else:
                self.stats["text_fetch_failed"] += 1

    # -- public ---------------------------------------------------------

    def enrich_rejections(self, pending: list, candidates_map: dict) -> tuple:
        """pending: list of (item, reason) that the snippet-only resolve()
        pass rejected. Returns (rescued_items, still_rejected).

        Every item comes back enriched whether or not it was rescued --
        a rejection that got real body text still carries it, because the
        LLM judge downstream needs that text to see a mention the
        collector's snippet never contained. (Handing the judge the same
        20 words resolve() already failed on, and calling it a second
        opinion, is theatre.)"""
        if not pending:
            return [], []

        items = [item for item, _ in pending]
        reasons = {id(item): reason for item, reason in pending}
        self.stats["considered"] += len(items)

        self.resolve_urls(items)
        self.fetch_texts(items)

        rescued = []
        still_rejected = []
        for item in items:
            reason = reasons[id(item)]
            if item.get("text_source") != "full_text":
                still_rejected.append((item, reason))
                continue

            candidate = candidates_map.get(item.get("candidate_id"), {})
            resolved_item, _ = resolve(item, candidate)
            if resolved_item is None:
                still_rejected.append((item, reason))
                continue

            resolved_item["fetched_full_text"] = True
            self.stats["rescued"] += 1
            print(
                f"  [info] rescued by full-text fetch: {item.get('title', '')[:60]!r} (was: {reason})",
                file=sys.stderr,
            )
            rescued.append(resolved_item)

        return rescued, still_rejected

    # -- reporting ------------------------------------------------------

    def summary_lines(self) -> list:
        """Human-readable run report. Printed by run.py and surfaced in the
        digest -- the whole point is that a broken full-text path is
        visible without an audit."""
        s = self.stats
        if not s["considered"]:
            return []

        resolved_count = (
            s["url_direct"] + s["url_gnews_decode"] + s["url_outlet_search"] + s["url_ddg"]
        )
        lines = [
            f"Full-text enrichment: {s['considered']} snippet-only item(s) needed a real article.",
            f"  URLs resolved: {resolved_count}/{s['considered']} "
            f"(direct {s['url_direct']}, google-news decode {s['url_gnews_decode']}, "
            f"outlet search {s['url_outlet_search']}, ddg {s['url_ddg']}, "
            f"unresolved {s['url_unresolved']})",
            f"  Body text: {s['text_ok']} fetched, {s['text_junk']} junk/paywalled, "
            f"{s['text_fetch_failed']} fetch failed",
            f"  Rescued into the digest by full text: {s['rescued']}",
        ]

        judged_on_snippet = s["considered"] - s["text_ok"]
        if judged_on_snippet:
            lines.append(
                f"  [!] {judged_on_snippet} item(s) still judged on their headline snippet alone"
            )
        if self.gnews.tripped:
            lines.append("  [!] google news URL resolution CIRCUIT BREAKER TRIPPED this run")
        if self.ddg is not None and self.ddg.tripped:
            lines.append("  [!] duckduckgo fallback was blocked and disabled this run (expected)")
        if s["considered"] and resolved_count == 0:
            lines.append(
                "  [!] ZERO URLs resolved -- the full-text path is broken, not merely unlucky"
            )
        return lines


def enrich_one(item: dict, candidate: dict, enricher: Enricher = None):
    """Single-item convenience for scripts and backfill paths that aren't
    batch-shaped. Returns (resolved_or_None, reason, item_for_audit) --
    the same contract the old resolve_with_fetch_fallback() had, so
    callers don't have to change shape to get the new resolution chain.

    Pass a shared Enricher when looping, or every call gets its own
    session, cache and circuit breaker, which defeats most of the point."""
    resolved_item, reason = resolve(item, candidate)
    if resolved_item is not None:
        return resolved_item, reason, resolved_item

    enricher = enricher or Enricher()
    rescued, still_rejected = enricher.enrich_rejections([(item, reason)], {candidate["id"]: candidate})
    if rescued:
        return rescued[0], None, rescued[0]
    enriched_item = still_rejected[0][0]
    return None, reason, enriched_item
