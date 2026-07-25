"""Detect a spike in daily hit volume vs. a 14-day rolling baseline, per
candidate. Reads historical counts straight from the JSONL store -- no
separate database needed at this scale. Must run after store.jsonl.append_items
so today's freshly stored items are included in the count."""

import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BASELINE_DAYS = 14
MIN_BASELINE_DAYS = 3  # below this, there's not enough history to call anything a spike


def _load_daily_counts() -> dict:
    """Returns {candidate_id: {date_str: count}}, keyed on collected_at date
    (sweep day), not published_at (which can be backdated)."""
    counts = defaultdict(lambda: defaultdict(int))
    for path in sorted(DATA_DIR.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].jsonl")):
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                day = (item.get("collected_at") or "")[:10]
                if day:
                    counts[item.get("candidate_id", "")][day] += 1
    return counts


def check_velocity(candidate_ids: list) -> dict:
    """Returns {candidate_id: {today, baseline_mean, baseline_days, spike}}."""
    counts = _load_daily_counts()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=BASELINE_DAYS)).strftime("%Y-%m-%d")

    results = {}
    for candidate_id in candidate_ids:
        daily = counts.get(candidate_id, {})
        today_count = daily.get(today, 0)
        baseline_values = [c for day, c in daily.items() if cutoff <= day < today]

        mean = statistics.mean(baseline_values) if baseline_values else 0.0
        stdev = statistics.pstdev(baseline_values) if len(baseline_values) >= 2 else 0.0

        if len(baseline_values) < MIN_BASELINE_DAYS:
            spike = False
        elif stdev > 0:
            spike = today_count > mean + 2 * stdev
        else:
            spike = today_count >= 2 * max(mean, 1)

        results[candidate_id] = {
            "today": today_count,
            "baseline_mean": round(mean, 1),
            "baseline_days": len(baseline_values),
            "spike": spike,
        }
    return results
