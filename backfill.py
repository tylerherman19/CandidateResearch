"""One-off historical backfill: runs the real collectors with a wider time
window (default 60 days) instead of fabricating any data. Every item here
is a genuine article/post returned by Google News RSS / GDELT for that
window -- nothing is synthesized.

Backdating note: we don't have a time machine, so there's no way to know
what a daily sweep would actually have found on each historical day. As an
honest approximation, each backfilled item's collected_at is set to its own
published_at (when parseable) instead of "now" -- so the JSONL history and
the dashboard/chart timeline reflect real publish dates, not one giant spike
on the day this script ran. This is documented here and in the dashboard
footer, not silently presented as if daily sweeps had been running for
2 months.

Run once: `python backfill.py [days]` (default 60).
"""

import sys
from datetime import datetime, timezone

from run import ENV_PATH, finalize_rejections, load_candidates, load_dotenv
from collectors import bluesky, currents_news, gdelt, google_news, meta_ads, reddit, wi_outlets, wispolitics, youtube
from dashboard.generate import generate as generate_dashboard
from pipeline.classify import classify_items
from pipeline.dedupe import cluster_items
from pipeline.llm_judge import judge_rejected_items, verify_loose_matches
from pipeline.resolve import resolve_with_fetch_fallback
from store.jsonl import append_items, append_rejections


def backfill_collect(candidate: dict, days: int):
    """Same collectors as the daily sweep, widened to `days`. Collectors
    that don't accept a days/maxrecords kwarg (the ones needing credentials
    we don't have) are called as-is and will just no-op as usual."""
    items = []
    for name, fn, kwargs in [
        ("wi_outlets", wi_outlets.collect, {"days": None}),  # None = full outlet history, not just `days`
        ("google_news", google_news.collect, {"days": days}),
        ("currents_api", currents_news.collect, {"days": days}),
        ("gdelt", gdelt.collect, {"days": days, "maxrecords": 250}),
        ("wispolitics", wispolitics.collect, {}),
        ("reddit", reddit.collect, {}),
        ("youtube", youtube.collect, {}),
        ("bluesky", bluesky.collect, {}),
        ("meta_ad_library", meta_ads.collect, {}),
    ]:
        try:
            items.extend(fn(candidate, **kwargs))
        except Exception as exc:
            print(f"  [warn] {name} failed for {candidate['name']}: {exc}", file=sys.stderr)
    return items


def _backdate(item: dict) -> dict:
    """Approximate collected_at with published_at for backfilled items --
    see module docstring."""
    published_at = item.get("published_at")
    if not published_at:
        return item
    try:
        datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return item
    item = dict(item)
    item["collected_at"] = published_at
    item["backfilled"] = True
    return item


def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    load_dotenv(ENV_PATH)
    candidates = load_candidates()
    # Full candidate dicts, not just name/office -- judge_rejected_items()
    # needs race_context_exclude_any to apply the same safety gates
    # resolve() applies (see pipeline.resolve.passes_loose_match_gates).
    candidates_map = {c["id"]: c for c in candidates}

    print(f"Backfilling {days} days for {len(candidates)} candidates (real APIs, wider window)...")

    resolved_items = []
    pending_rejections = []
    for candidate in candidates:
        print(f"  collecting: {candidate['name']}")
        raw_items = backfill_collect(candidate, days)
        for item in raw_items:
            resolved, reason, item_for_audit = resolve_with_fetch_fallback(item, candidate)
            if resolved is not None:
                resolved_items.append(_backdate(resolved))
            else:
                pending_rejections.append((item_for_audit, reason))

    promoted, still_rejected = judge_rejected_items(pending_rejections, candidates_map)
    resolved_items.extend(_backdate(item) for item in promoted)

    resolved_items, demoted = verify_loose_matches(resolved_items, candidates_map)
    still_rejected.extend(demoted)
    rejections = finalize_rejections(candidates_map, still_rejected)

    by_candidate = {}
    for item in resolved_items:
        by_candidate.setdefault(item["candidate_id"], []).append(item)

    deduped_items = []
    for candidate in candidates:
        deduped_items.extend(cluster_items(by_candidate.get(candidate["id"], [])))

    classified_items = classify_items(deduped_items, candidates_map)
    new_items = append_items(classified_items)
    append_rejections(rejections)

    print(
        f"\nBackfill done: {len(new_items)} new item(s) stored "
        f"({len(resolved_items) - len(deduped_items)} merged as duplicates, "
        f"{len(rejections)} rejected)."
    )

    dashboard_path = generate_dashboard()
    print(f"Dashboard refreshed: {dashboard_path}")


if __name__ == "__main__":
    main()
