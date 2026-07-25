"""Run the daily sweep: collect -> resolve -> dedupe -> classify -> store ->
digest -> dashboard. Prints a per-candidate terminal summary too."""

import os
import sys
from pathlib import Path

import yaml

from collectors import bluesky, gdelt, google_news, meta_ads, reddit, youtube
from dashboard.generate import generate as generate_dashboard
from digest.render import render_digest
from digest.send import send_digest
from pipeline.classify import classify_items
from pipeline.dedupe import cluster_items
from pipeline.resolve import resolve
from pipeline.velocity import check_velocity
from store.jsonl import append_items, append_rejections

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "candidates.yaml"
ENV_PATH = Path(__file__).resolve().parent / ".env"


def load_dotenv(path: Path) -> None:
    """Minimal stdlib .env loader for local dev -- avoids adding
    python-dotenv as a dependency for something this small. Doesn't
    override real env vars already set (GitHub Actions injects secrets
    that way directly, no .env file involved there)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value

COLLECTORS = {
    "google_news": google_news.collect,
    "gdelt": gdelt.collect,
    "reddit": reddit.collect,
    "youtube": youtube.collect,
    "bluesky": bluesky.collect,
    "meta_ad_library": meta_ads.collect,
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


def dedupe_by_candidate(candidates: list, resolved_items: list) -> list:
    """Clusters near-duplicate items per candidate (a cluster from one
    candidate's coverage shouldn't merge with another's)."""
    by_candidate = {}
    for item in resolved_items:
        by_candidate.setdefault(item["candidate_id"], []).append(item)

    deduped = []
    for candidate in candidates:
        deduped.extend(cluster_items(by_candidate.get(candidate["id"], [])))
    return deduped


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
            cluster_note = f" [{item['cluster_size']} outlets]" if item.get("cluster_size", 1) > 1 else ""
            classification_note = ""
            if item.get("classified_by"):
                classification_note = (
                    f" -- {item.get('topic')}/{item.get('stance_toward_candidate')}/"
                    f"{item.get('risk_level')} (via {item.get('classified_by')})"
                )
            print(f"  - {item['title']}{cluster_note}")
            print(f"    {item.get('source') or 'unknown source'} | {date} | {item['collector']}{classification_note}")
            print(f"    {item['source_url']}")
        rejected = rejected_counts.get(candidate["id"], 0)
        if rejected:
            print(f"  ({rejected} rejected this run -- see data/rejections-*.jsonl)")

    if collector_failures:
        print("\n=== Collector failures this run ===")
        for candidate_name, collector_name, error in collector_failures:
            print(f"  - {collector_name} / {candidate_name}: {error}")


def main() -> None:
    load_dotenv(ENV_PATH)
    candidates = load_candidates()
    candidates_map = {c["id"]: {"name": c["name"], "office": c["office"]} for c in candidates}

    print(f"Sweeping {len(candidates)} candidates across {len(COLLECTORS)} collectors...")
    resolved_items, rejections, collector_failures = run_sweep(candidates)

    deduped_items = dedupe_by_candidate(candidates, resolved_items)
    classified_items = classify_items(deduped_items, candidates_map)

    new_items = append_items(classified_items)
    append_rejections(rejections)

    velocity = check_velocity([c["id"] for c in candidates])

    print_summary(candidates, new_items, rejections, collector_failures)
    print(
        f"\n{len(new_items)} new item(s) stored this run "
        f"({len(classified_items) - len(new_items)} already in store, "
        f"{len(rejections)} rejected, {len(resolved_items) - len(deduped_items)} merged as duplicates)."
    )
    for candidate_id, v in velocity.items():
        if v["spike"]:
            name = candidates_map[candidate_id]["name"]
            print(f"  [SPIKE] {name}: {v['today']} today vs {v['baseline_mean']} avg over {v['baseline_days']}d")

    items_by_candidate = {}
    for item in new_items:
        items_by_candidate.setdefault(item["candidate_id"], []).append(item)
    rejected_counts = {}
    for rejection in rejections:
        rejected_counts[rejection["candidate_id"]] = rejected_counts.get(rejection["candidate_id"], 0) + 1

    digest_html = render_digest(candidates, items_by_candidate, velocity, rejected_counts)
    print(send_digest(digest_html))

    dashboard_path = generate_dashboard()
    print(f"Dashboard refreshed: {dashboard_path}")


if __name__ == "__main__":
    main()
