"""One-off: re-clusters already-stored accepted items in place using the
current (fixed) dedupe.cluster_items logic, without re-fetching anything
from any network source. Used after fixing a dedupe bug, to correct
already-committed data that predates the fix -- safer than re-running
backfill.py, which depends on live, occasionally-rate-limited APIs and can
silently produce a worse dataset than what's already stored.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.dedupe import cluster_items

DATA_DIR = PROJECT_ROOT / "data"


def main() -> None:
    paths = sorted(DATA_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].jsonl"))
    all_items = []
    for path in paths:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    all_items.append(json.loads(line))

    print(f"Loaded {len(all_items)} items across {len(paths)} file(s).")

    by_candidate = defaultdict(list)
    for item in all_items:
        by_candidate[item["candidate_id"]].append(item)

    deduped = []
    for candidate_id, items in by_candidate.items():
        clustered = cluster_items(items)
        print(f"  {candidate_id}: {len(items)} -> {len(clustered)} after re-clustering")
        deduped.extend(clustered)

    by_month = defaultdict(list)
    for item in deduped:
        month = (item.get("collected_at") or "")[:7]
        by_month[month].append(item)

    for path in paths:
        path.unlink()

    for month, items in by_month.items():
        path = DATA_DIR / f"{month}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for item in items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"Wrote {len(items)} items to {path}")

    print(f"\nTotal: {len(all_items)} -> {len(deduped)} after re-dedup.")


if __name__ == "__main__":
    main()
