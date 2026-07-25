"""Shared helper collectors call to build the canonical Item shape (see
CLAUDE.md's `Item` definition). Keeping id-hashing/timestamping in one place
means adding or removing a collector never touches downstream code."""

import hashlib
from datetime import datetime, timezone


def _item_id(source_url: str) -> str:
    return hashlib.sha1(source_url.encode("utf-8")).hexdigest()


def normalize(
    *,
    collector: str,
    candidate_id: str,
    title: str,
    source: str,
    source_url: str,
    published_at: str,
    text: str = "",
    author=None,
    engagement=None,
    raw=None,
) -> dict:
    return {
        "id": _item_id(source_url),
        "candidate_id": candidate_id,
        "collector": collector,
        "source": source,
        "source_url": source_url,
        "author": author,
        "published_at": published_at,
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "title": title.strip(),
        "text": text or "",
        "engagement": engagement,
        "raw": raw or {},
    }
