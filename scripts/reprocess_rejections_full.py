"""Full-chain reprocessing of stored rejections: fetch + LLM rescue, not
just the cheap pure-resolve() pass scripts/reprocess_rejections.py does.

Motivation: scripts/reprocess_rejections.py only re-checks resolve()
against whatever text is already stored on the rejection record, which is
often empty/sparse for older entries -- it can't discover anything that
needed a real fetch or an LLM judgment call to find, which is most of
what recent fixes (known-outlet redirect resolution, MAX_CHARS increases,
junk-text filtering, unified safety gates) actually improved. This
reruns the SAME resolve_with_fetch_fallback -> judge_rejected_items ->
verify_loose_matches chain used for live sweeps, against the stored
backlog, deduped by (candidate_id, source_url) first since the same real
rejection has been logged repeatedly across many sweep runs."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run import load_dotenv, ENV_PATH, load_candidates, finalize_rejections
from pipeline.normalize import normalize
from pipeline.enrich import Enricher
from pipeline.resolve import resolve
from pipeline.llm_judge import judge_rejected_items, verify_loose_matches
from pipeline.dedupe import cluster_items
from pipeline.classify import classify_items
from store.jsonl import append_items, append_rejections

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    load_dotenv(ENV_PATH)
    candidates = load_candidates()
    candidates_by_id = {c["id"]: c for c in candidates}
    candidates_map = {c["id"]: c for c in candidates}

    seen = set()
    to_reprocess = []
    for path in sorted(DATA_DIR.glob("rejections-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("reason") != "no_name_match":
                continue
            key = (r.get("candidate_id"), r.get("source_url"))
            if key in seen or not r.get("source_url"):
                continue
            seen.add(key)
            to_reprocess.append(r)

    print(f"Reprocessing {len(to_reprocess)} unique no_name_match rejections through full chain...")

    resolved_items = []
    snippet_rejections = []
    for r in to_reprocess:
        candidate = candidates_by_id.get(r.get("candidate_id"))
        if not candidate:
            continue
        item = normalize(
            collector=r.get("collector", ""),
            candidate_id=r["candidate_id"],
            title=r.get("title", ""),
            source=r.get("source", ""),
            source_url=r.get("source_url", ""),
            published_at=r.get("published_at", ""),
            text=r.get("text", ""),
        )
        # A previously-resolved real URL on the stored record is reused
        # rather than re-derived -- resolution is the expensive half, and
        # a Google News redirect link can rot while the real URL doesn't.
        if r.get("resolved_url"):
            item["resolved_url"] = r["resolved_url"]
            item["url_resolution"] = r.get("url_resolution", "")
        item["text_source"] = "collector"
        resolved, reason = resolve(item, candidate)
        if resolved is not None:
            resolved_items.append(resolved)
        else:
            snippet_rejections.append((item, reason))

    enricher = Enricher()
    print(f"Fetching full text for {len(snippet_rejections)} snippet-only rejection(s)...")
    rescued, pending_rejections = enricher.enrich_rejections(snippet_rejections, candidates_map)
    resolved_items.extend(rescued)
    for line in enricher.summary_lines():
        print(line)

    print(f"Direct resolve (with fetch): {len(resolved_items)} promoted, {len(pending_rejections)} still rejected")

    promoted, still_rejected = judge_rejected_items(pending_rejections, candidates_map)
    resolved_items.extend(promoted)
    resolved_items, demoted = verify_loose_matches(resolved_items, candidates_map)
    still_rejected.extend(demoted)

    total_promoted = len(resolved_items)
    print(f"After LLM rescue + double-check: {total_promoted} total promoted")

    if total_promoted == 0:
        return

    by_candidate = {}
    for item in resolved_items:
        by_candidate.setdefault(item["candidate_id"], []).append(item)

    deduped = []
    for c in candidates:
        deduped.extend(cluster_items(by_candidate.get(c["id"], [])))
    print(f"After dedupe: {len(deduped)}")

    classified = classify_items(deduped, candidates_map)
    new_items = append_items(classified)
    print(f"Stored {len(new_items)} new accepted item(s)")
    for it in new_items:
        print(" -", it["candidate_id"], "|", it.get("match_type"), "|", it["title"][:70])


if __name__ == "__main__":
    main()
