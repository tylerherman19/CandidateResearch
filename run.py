"""Run the daily sweep: collect -> resolve -> store -> print a per-candidate
summary of today's (newly found) hits, plus rejection/failure counts."""

import sys
from pathlib import Path

import yaml

from collectors import gdelt, google_news
from dashboard.generate import generate as generate_dashboard
from pipeline.resolve import resolve
from store.jsonl import append_items, append_rejections

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "candidates.yaml"

COLLECTORS = {
    "google_news": google_news.collect,
    "gdelt": gdelt.collect,
}


def load_candidates() -> list:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["candidates"]


def run_sweep(candidates: list):
    """Returns (resolved_items, rejections, collector_failures)."""
    resolved_items = []
    rejections = []
    collector_failures = []

    for candidate in candidates:
        for collector_name, collect_fn in COLLECTORS.items():
            try:
                raw_items = collect_fn(candidate)
            except Exception as exc:  # one collector failing must not kill the sweep
                collector_failures.append((candidate["name"], collector_name, str(exc)))
                print(
                    f"  [warn] {collector_name} failed for {candidate['name']}: {exc}",
                    file=sys.stderr,
                )
                continue

            for item in raw_items:
                resolved, reason = resolve(item, candidate)
                if resolved is not None:
                    resolved_items.append(resolved)
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

    return resolved_items, rejections, collector_failures


def print_summary(candidates: list, new_items: list, rejections: list, collector_failures: list) -> None:
    by_candidate = {c["id"]: [] for c in candidates}
    for item in new_items:
        by_candidate.setdefault(item["candidate_id"], []).append(item)

    rejected_counts = {c["id"]: 0 for c in candidates}
    for rejection in rejections:
        rejected_counts[rejection["candidate_id"]] = rejected_counts.get(rejection["candidate_id"], 0) + 1

    for candidate in candidates:
        items = sorted(
            by_candidate.get(candidate["id"], []),
            key=lambda i: i.get("published_at", ""),
            reverse=True,
        )
        print(f"\n=== {candidate['name']} ({candidate['office']}) ===")
        if not items:
            print("  (no new hits)")
        for item in items:
            date = item.get("published_at", "")[:10] or "unknown date"
            print(f"  - {item['title']}")
            print(f"    {item.get('source') or 'unknown source'} | {date} | {item['collector']}")
            print(f"    {item['source_url']}")
        rejected = rejected_counts.get(candidate["id"], 0)
        if rejected:
            print(f"  ({rejected} rejected this run -- see data/rejections-*.jsonl)")

    if collector_failures:
        print("\n=== Collector failures this run ===")
        for candidate_name, collector_name, error in collector_failures:
            print(f"  - {collector_name} / {candidate_name}: {error}")


def main() -> None:
    candidates = load_candidates()
    print(f"Sweeping {len(candidates)} candidates...")
    resolved_items, rejections, collector_failures = run_sweep(candidates)
    new_items = append_items(resolved_items)
    append_rejections(rejections)
    print_summary(candidates, new_items, rejections, collector_failures)
    print(
        f"\n{len(new_items)} new item(s) stored this run "
        f"({len(resolved_items) - len(new_items)} already in store, "
        f"{len(rejections)} rejected)."
    )
    dashboard_path = generate_dashboard()
    print(f"Dashboard refreshed: {dashboard_path}")


if __name__ == "__main__":
    main()
