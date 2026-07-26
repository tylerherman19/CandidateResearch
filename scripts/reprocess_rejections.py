"""Re-checks every stored rejection against TODAY's resolve() logic.

Root cause this fixes: resolve.py's matching rules have been tightened or
loosened multiple times over this build (race_context_terms added, the
proximity/require_any gate removed after the "Juliana Bennett - Channel
3000" bug, etc.) -- but a rejection recorded under an older ruleset is
never automatically re-evaluated once the rules improve. It just sits in
the audit log forever looking like an active miss, even long after the
bug that caused it was fixed. Confirmed directly: a real article whose
title literally contains "76th Assembly District" (one of
martinez_rutherford's own configured race_context_terms) was still on
file as a no_name_match rejection, while resolve() run fresh against that
exact title accepts it immediately -- current logic was never the
problem, staleness was.

Only works from what a rejection actually has on file. Older rejections
(recorded before finalize_rejections() was widened to keep text/source/
published_at) only have title/collector/source_url -- this can still
recover anything resolve() accepts from the title alone (race_context_terms,
name matches), but can't attempt a fetch-based rescue for those, since the
real article text was never captured and, for Google News items, the
source_url is an unresolvable redirect without a working title-search
service. Rejections logged after the finalize_rejections() fix carry full
fidelity and can be reprocessed more completely by a future pass.

Anything that now resolves gets classified and stored as a normal
accepted finding, then removed from the rejections file it currently sits
in -- a rejection that's since been fixed isn't an accurate audit record
anymore. Anything still rejected is left untouched.

Run this after any future resolve.py change, not just once."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.classify import classify_items
from pipeline.dedupe import cluster_items
from pipeline.normalize import normalize
from pipeline.resolve import resolve
from run import load_candidates
from store.jsonl import append_items

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    candidates = load_candidates()
    candidates_by_id = {c["id"]: c for c in candidates}
    candidates_map = {c["id"]: {"name": c["name"], "office": c["office"]} for c in candidates}

    promoted_by_candidate = {}
    rewritten_files = {}

    for path in sorted(DATA_DIR.glob("rejections-*.jsonl")):
        keep_lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            candidate = candidates_by_id.get(r.get("candidate_id"))
            if not candidate:
                keep_lines.append(line)
                continue

            probe = {"title": r.get("title", ""), "text": r.get("text", ""), "collector": r.get("collector", "")}
            resolved, _reason = resolve(probe, candidate)
            if resolved is None:
                keep_lines.append(line)
                continue

            item = normalize(
                collector=r.get("collector", ""),
                candidate_id=r["candidate_id"],
                title=r.get("title", ""),
                source=r.get("source", "") or r.get("collector", ""),
                source_url=r.get("source_url", ""),
                published_at=r.get("published_at", ""),
                text=r.get("text", ""),
                raw={"reprocessed_from_rejection": True, "original_reason": r.get("reason", "")},
            )
            item["matched_alias"] = resolved.get("matched_alias", "")
            item["matched_require_any"] = resolved.get("matched_require_any", "")
            item["match_type"] = resolved.get("match_type", "")
            promoted_by_candidate.setdefault(r["candidate_id"], []).append(item)

        rewritten_files[path] = keep_lines

    total_promoted = sum(len(v) for v in promoted_by_candidate.values())
    print(f"Promotable via current resolve() logic alone: {total_promoted}")
    if total_promoted == 0:
        return

    deduped = []
    for candidate in candidates:
        deduped.extend(cluster_items(promoted_by_candidate.get(candidate["id"], [])))
    print(f"After dedupe: {len(deduped)}")

    classified = classify_items(deduped, candidates_map)
    new_items = append_items(classified)
    print(f"Stored {len(new_items)} new accepted item(s)")

    for path, keep_lines in rewritten_files.items():
        content = ("\n".join(keep_lines) + "\n") if keep_lines else ""
        path.write_text(content, encoding="utf-8")
    print("Rewrote rejections files to drop promoted entries")


if __name__ == "__main__":
    main()
