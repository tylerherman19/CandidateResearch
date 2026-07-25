"""Meta Ad Library API collector -- political/issue ads only (NOT the Meta
Content Library, which is gated to vetted academic researchers). Requires
an identity-verified access token with ads_read permission."""

import os
import sys

import requests

from pipeline.normalize import normalize

ADS_ARCHIVE_URL = "https://graph.facebook.com/v19.0/ads_archive"
TIMEOUT_SECONDS = 20


def collect(candidate: dict) -> list:
    access_token = os.environ.get("META_AD_LIBRARY_TOKEN")
    if not access_token:
        print("  [info] META_AD_LIBRARY_TOKEN not set -- skipping Meta Ad Library", file=sys.stderr)
        return []

    resp = requests.get(
        ADS_ARCHIVE_URL,
        params={
            "access_token": access_token,
            "search_terms": candidate["name"],
            "ad_type": "POLITICAL_AND_ISSUE_ADS",
            "ad_reached_countries": "US",
            "ad_active_status": "ALL",
            "fields": "id,ad_creative_bodies,ad_creative_link_titles,page_name,ad_delivery_start_time,ad_snapshot_url",
            "limit": 25,
        },
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()

    items = []
    for ad in data.get("data", []):
        bodies = ad.get("ad_creative_bodies") or []
        titles = ad.get("ad_creative_link_titles") or []
        source_url = ad.get("ad_snapshot_url") or f"https://www.facebook.com/ads/library/?id={ad.get('id', '')}"

        items.append(
            normalize(
                collector="meta_ad_library",
                candidate_id=candidate["id"],
                title=(titles[0] if titles else (bodies[0][:120] if bodies else "Political ad")),
                source=ad.get("page_name", "Meta Ad Library"),
                source_url=source_url,
                published_at=ad.get("ad_delivery_start_time", ""),
                text=" ".join(bodies),
                author=ad.get("page_name"),
                engagement=None,
                raw=ad,
            )
        )
    return items
