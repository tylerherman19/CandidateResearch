"""One-off: re-runs classify_items on every already-stored accepted item, to
backfill a newly-added classification field (e.g. mention_type) onto items
classified before that field existed. Real network calls to Gemini --
not free of cost, but reuses the existing free-tier batching."""

import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from run import ENV_PATH, load_candidates, load_dotenv
from pipeline.classify import classify_items

DATA_DIR = PROJECT_ROOT / "data"


def main() -> None:
    load_dotenv(ENV_PATH)
    candidates = load_candidates()
    candidates_map = {c["id"]: {"name": c["name"], "office": c["office"]} for c in candidates}

    paths = sorted(DATA_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].jsonl"))
    all_items = []
    for path in paths:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    all_items.append(json.loads(line))

    print(f"Reclassifying {len(all_items)} items across {len(paths)} file(s)...")
    reclassified = classify_items(all_items, candidates_map)

    by_month = defaultdict(list)
    for item in reclassified:
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


if __name__ == "__main__":
    main()
