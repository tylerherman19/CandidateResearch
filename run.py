"""Run the daily sweep: collect -> resolve -> dedupe -> classify -> store ->
digest -> dashboard. Prints a per-candidate terminal summary too."""

import os
import sys
from pathlib import Path

import yaml

from collectors import bluesky, currents_news, gdelt, google_news, meta_ads, reddit, wi_outlets, wispolitics, youtube
from dashboard.generate import generate as generate_dashboard
from digest.render import render_digest
from digest.send import send_digest
from pipeline.classify import classify_items
from pipeline.dedupe import cluster_items
from pipeline.enrich import Enricher
from pipeline.llm_judge import judge_rejected_items, verify_loose_matches
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
    "wi_outlets": wi_outlets.collect,  # real snippets + real URLs direct from the newsroom -- highest signal, checked first
    "google_news": google_news.collect,
    "currents_api": currents_news.collect,  # broader-recall backup; no-ops without CURRENTS_API_KEY
    "gdelt": gdelt.collect,
    "wispolitics": wispolitics.collect,
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
    """Returns (resolved_items, pending_rejections, collector_failures).

    Two phases, deliberately. Phase one is the cheap deterministic
    resolve() over everything collected -- pure, no network. Phase two
    (pipeline/enrich.py) takes ONLY what phase one rejected and fetches
    real article text for the whole set at once.

    The split is what makes the full-text path work at all: resolving a
    Google News redirect link used to be a per-item third-party search,
    which rate limiting reduced to ~0% success within seconds of a sweep
    starting. Batched, it's one RPC round trip per ~50 items. See
    pipeline/gnews_url.py.

    pending_rejections is a list of (item, reason) tuples -- not yet
    finalized, since judge_rejected_items() gets a chance to promote some
    of them before anything is logged as a final rejection. Those items
    carry whatever real body text phase two recovered, which is what the
    judge needs to see a mention the collector's snippet never had."""
    resolved_items = []
    snippet_rejections = []
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
                item["text_source"] = "collector"
                resolved, reason = resolve(item, candidate)
                if resolved is not None:
                    resolved_items.append(resolved)
                else:
                    snippet_rejections.append((item, reason))

    candidates_map = {c["id"]: c for c in candidates}
    enricher = Enricher()
    print(
        f"\nFetching full text for {len(snippet_rejections)} snippet-only rejection(s)...",
        file=sys.stderr,
    )
    rescued, pending_rejections = enricher.enrich_rejections(snippet_rejections, candidates_map)
    resolved_items.extend(rescued)

    return resolved_items, pending_rejections, collector_failures, enricher


def finalize_rejections(candidates_map: dict, still_rejected: list) -> list:
    """Keeps full item fidelity (id/text/source/published_at), not just
    what the audit-log UI displays -- discovered the hard way: an earlier
    version of this only kept candidate_id/collector/title/source_url/
    reason, which meant a rejection could never be properly re-checked
    later even after resolve.py's own matching rules improved (see
    scripts/reprocess_rejections.py). Losing `text` in particular is
    unrecoverable for any rejection whose source_url was a Google News
    redirect -- there's no way to refetch it after the fact without the
    real URL, which we never had. Keeping everything now costs nothing and
    means every future rejection stays reprocessable.

    resolved_url is kept for exactly that reason, one step further on:
    once pipeline/enrich.py has turned a Google News redirect into a real
    article URL, that URL is the durable, refetchable handle on the story.
    Persisting it means a reprocessing run skips resolution entirely, and
    that an old rejection stays reprocessable even after Google's redirect
    link rots. text_source/full_text_status are persisted so the audit log
    answers the question that started all this -- "was this judged on real
    article text, or on twenty words of headline?" -- without having to
    open the article by hand to find out."""
    return [
        {
            "id": item.get("id", ""),
            "candidate_id": item["candidate_id"],
            "candidate_name": candidates_map.get(item["candidate_id"], {}).get("name", item["candidate_id"]),
            "collector": item["collector"],
            "title": item["title"],
            "text": item.get("text", ""),
            "source": item.get("source", ""),
            "source_url": item["source_url"],
            "resolved_url": item.get("resolved_url", ""),
            "url_resolution": item.get("url_resolution", ""),
            "text_source": item.get("text_source", "collector"),
            "full_text_status": item.get("full_text_status", "not_attempted"),
            "published_at": item.get("published_at", ""),
            "reason": reason,
        }
        for item, reason in still_rejected
    ]


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


def print_summary(
    candidates: list,
    new_items: list,
    rejections: list,
    collector_failures: list,
    enrichment_lines: list = None,
) -> None:
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

    # Printed unconditionally, not only on failure. A silently-0% full-text
    # path is the exact bug this rewrite fixes; a number you see every
    # morning is what stops it recurring unnoticed.
    if enrichment_lines:
        print("\n=== Full-text coverage this run ===")
        for line in enrichment_lines:
            print(f"  {line}" if not line.startswith(" ") else line)


def main() -> None:
    load_dotenv(ENV_PATH)
    candidates = load_candidates()
    # Full candidate dicts, not just name/office -- judge_rejected_items()
    # needs race_context_exclude_any to apply the same safety gates
    # resolve() applies (see pipeline.resolve.passes_loose_match_gates).
    candidates_map = {c["id"]: c for c in candidates}

    print(f"Sweeping {len(candidates)} candidates across {len(COLLECTORS)} collectors...")
    resolved_items, pending_rejections, collector_failures, enricher = run_sweep(candidates)

    promoted, still_rejected = judge_rejected_items(pending_rejections, candidates_map)
    resolved_items.extend(promoted)

    resolved_items, demoted = verify_loose_matches(resolved_items, candidates_map)
    still_rejected.extend(demoted)
    rejections = finalize_rejections(candidates_map, still_rejected)

    deduped_items = dedupe_by_candidate(candidates, resolved_items)
    classified_items = classify_items(deduped_items, candidates_map)

    new_items = append_items(classified_items)
    append_rejections(rejections)

    velocity = check_velocity([c["id"] for c in candidates])

    enrichment_lines = enricher.summary_lines()
    print_summary(candidates, new_items, rejections, collector_failures, enrichment_lines)
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

    digest_html = render_digest(
        candidates, items_by_candidate, velocity, rejected_counts, enrichment_lines
    )
    print(send_digest(digest_html))

    dashboard_path = generate_dashboard()
    print(f"Dashboard refreshed: {dashboard_path}")


if __name__ == "__main__":
    main()
