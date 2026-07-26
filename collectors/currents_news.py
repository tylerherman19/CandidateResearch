"""Currents API collector -- a general news search API used as a backup/
recall net alongside Google News and the direct wi_outlets.py site
searches, not a replacement for either. Free tier: 1,000 requests/day, no
credit card, commercial use permitted (checked directly against their docs
and swagger spec -- see below). No-ops entirely if CURRENTS_API_KEY isn't
set, same pattern as reddit.py/youtube.py/bluesky.py.

Auth: the key goes in an `Authorization` header, not a query param --
confirmed via api.currentsapi.services's own swagger.json
(securityDefinitions -> ApiKeyAuth -> {"type": "apiKey", "name":
"Authorization", "in": "header"}), not the query-string convention older
Currents API tutorials describe.

Base URL: https://api.currentsapi.services/v1/search
Params used: keywords, language=en, start_date/end_date (ISO 8601,
+00:00 offset) -- the API supports real server-side date filtering, unlike
wi_outlets.py's site-search endpoints, so no client-side cutoff filtering
is needed here."""

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from pipeline.normalize import normalize

SEARCH_URL = "https://api.currentsapi.services/v1/search"
TIMEOUT_SECONDS = 15


def _normalize_published(raw: str) -> str:
    """Currents API returns `published` as "YYYY-MM-DD HH:MM:SS +0000"
    (space-separated, not proper ISO 8601) per their documented examples --
    downstream code (backfill.py's _backdate, the dashboard chart) calls
    datetime.fromisoformat on published_at, which chokes on the space
    before the offset. Convert if it matches that shape; otherwise pass
    through unchanged and let the caller's own fallback handling (already
    written to tolerate unparseable dates) deal with it."""
    if not raw:
        return raw
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z")
        return dt.isoformat(timespec="seconds")
    except ValueError:
        return raw


def collect(candidate: dict, days: int = 3) -> list:
    api_key = os.environ.get("CURRENTS_API_KEY")
    if not api_key:
        print("  [info] CURRENTS_API_KEY not set -- skipping Currents API", file=sys.stderr)
        return []

    now = datetime.now(timezone.utc)
    start_date = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    end_date = now.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    try:
        resp = requests.get(
            SEARCH_URL,
            headers={"Authorization": api_key},
            params={
                "keywords": candidate["name"],
                "language": "en",
                "start_date": start_date,
                "end_date": end_date,
            },
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except Exception as exc:
        print(f"  [warn] Currents API failed for {candidate['name']}: {exc}", file=sys.stderr)
        return []

    data = resp.json()
    if data.get("status") != "ok":
        print(f"  [warn] Currents API returned non-ok status for {candidate['name']}: {data}", file=sys.stderr)
        return []

    items = []
    for entry in data.get("news", []):
        title = entry.get("title", "")
        description = entry.get("description", "")
        url = entry.get("url", "")
        if not url:
            continue

        items.append(
            normalize(
                collector="currents_api",
                candidate_id=candidate["id"],
                title=title,
                source=entry.get("author") or "Currents API",
                source_url=url,
                published_at=_normalize_published(entry.get("published", "")),
                text=description,
                raw=entry,
            )
        )
    return items
