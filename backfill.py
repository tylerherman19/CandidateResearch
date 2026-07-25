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

from run import ENV_PATH, load_candidates, load_dotenv
from collectors import bluesky, gdelt, google_news, meta_ads, reddit, wispolitics, youtube
from dashboard.generate import generate as generate_dashboard
from pipeline.classify import classify_items
from pipeline.dedupe import cluster_items
from pipeline.resolve import resolve
from store.jsonl import append_items, append_rejections


def backfill_collect(candidate: dict, days: int):
    """Same collectors as the daily sweep, widened to `days`. Collectors
    that don't accept a days/maxrecords kwarg (the ones needing credentials
    we don't have) are called as-is and will just no-op as usual."""
    items = []
    for name, fn, kwargs in [
        ("google_news", google_news.collect, {"days": days}),
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
    candidates_map = {c["id"]: {"name": c["name"], "office": c["office"]} for c in candidates}

    print(f"Backfilling {days} days for {len(candidates)} candidates (real APIs, wider window)...")

    resolved_items = []
    rejections = []
    for candidate in candidates:
        print(f"  collecting: {candidate['name']}")
        raw_items = backfill_collect(candidate, days)
        for item in raw_items:
            resolved, reason = resolve(item, candidate)
            if resolved is not None:
                resolved_items.append(_backdate(resolved))
            else:
                rejections.append(
                    {
                        "candidate_id": candidate["id"],
                        "candidate_name": candidate["name"],
                        "collector": item["collector"],
                        "title": item["title"],
                        "source_url": item["source_url"],
                        "reason": reason,
                    }
                )

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
