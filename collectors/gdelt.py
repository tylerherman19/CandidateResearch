"""GDELT DOC 2.0 API collector. Free, no key.

Note: `mode=artlist` (used here) returns title/url/domain/seendate metadata
only. Checked a live response directly -- there is no per-article tone field
in this mode (keys are: domain, language, seendate, socialimage,
sourcecountry, title, url, url_mobile). GDELT's tone data lives in
aggregate-only modes (tonechart/timelinetone), not per-article, so this
collector does not attempt to populate a tone value.
"""

import time
from datetime import datetime, timezone

import requests

from pipeline.normalize import normalize

DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
TIMEOUT_SECONDS = 20
# GDELT's own 429 message says "one every 5 seconds", but empirically the
# throttle looked like a shared token bucket rather than pure per-caller
# spacing -- the first request of a run would occasionally 429 even after
# a 10s wait. Retrying a couple times with backoff rides that out.
REQUEST_INTERVAL_SECONDS = 10
MAX_ATTEMPTS = 3


def _get(params: dict):
    resp = None
    for attempt in range(MAX_ATTEMPTS):
        resp = requests.get(
            DOC_API_URL,
            params=params,
            headers={"User-Agent": "media-monitor/1.0"},
            timeout=TIMEOUT_SECONDS,
        )
        if resp.status_code != 429:
            break
        if attempt < MAX_ATTEMPTS - 1:
            time.sleep(REQUEST_INTERVAL_SECONDS)
    resp.raise_for_status()
    return resp


def collect(candidate: dict, days: int = 1, maxrecords: int = 50) -> list:
    time.sleep(REQUEST_INTERVAL_SECONDS)
    query = f'"{candidate["name"]}" Wisconsin sourcelang:eng'
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": maxrecords,
        "format": "json",
        "timespan": f"{days}d",
    }

    resp = _get(params)

    try:
        data = resp.json()
    except ValueError:
        # GDELT returns an HTML error page (still HTTP 200) on malformed queries.
        return []

    items = []
    for article in data.get("articles", []):
        published_at = ""
        seendate = article.get("seendate", "")
        if seendate:
            try:
                dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(
                    tzinfo=timezone.utc
                )
                published_at = dt.isoformat(timespec="seconds")
            except ValueError:
                pass

        items.append(
            normalize(
                collector="gdelt",
                candidate_id=candidate["id"],
                title=article.get("title", ""),
                source=article.get("domain", ""),
                source_url=article.get("url", ""),
                published_at=published_at,
                text="",
                raw=article,
            )
        )
    return items
