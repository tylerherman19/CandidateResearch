"""Append-only JSONL store, one file per month. Dedupe by item id
(hash of source URL) across the whole store, per CLAUDE.md."""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load_existing_ids(path: Path) -> set:
    ids = set()
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ids.add(json.loads(line)["id"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return ids


def append_items(items: list) -> list:
    """Appends items not already present (by id), bucketed by each item's
    own collected_at into that month's file -- a normal daily sweep only
    ever touches this month's file (collected_at is always "now"), but a
    backfill run can hand this a batch spanning several historical months
    at once. Dedupe is checked against every existing file, not just the
    target month, since a batch can span months in one call. Returns only
    the items that were actually new."""
    if not items:
        return []

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing_ids = set()
    for path in DATA_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].jsonl"):
        existing_ids |= _load_existing_ids(path)

    by_month = defaultdict(list)
    seen_in_batch = set()
    for item in items:
        item_id = item["id"]
        if item_id in existing_ids or item_id in seen_in_batch:
            continue
        seen_in_batch.add(item_id)
        collected_at = item.get("collected_at") or datetime.now(timezone.utc).isoformat()
        by_month[collected_at[:7]].append(item)

    new_items = []
    for month, month_items in by_month.items():
        path = DATA_DIR / f"{month}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            for item in month_items:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
                new_items.append(item)

    return new_items


def _rejections_file_path(dt=None) -> Path:
    dt = dt or datetime.now(timezone.utc)
    return DATA_DIR / f"rejections-{dt.strftime('%Y-%m')}.jsonl"


def append_rejections(rejections: list) -> None:
    """Appends every rejection this run produced, with its reason, for
    audit. Not deduped -- the same story can be (correctly) re-rejected
    on a later day and that's worth seeing, not hiding."""
    if not rejections:
        return

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _rejections_file_path()
    with path.open("a", encoding="utf-8") as f:
        for rejection in rejections:
            f.write(json.dumps(rejection, ensure_ascii=False) + "\n")
