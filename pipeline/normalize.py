"""Shared helper collectors call to build the canonical Item shape (see
CLAUDE.md's `Item` definition). Keeping id-hashing/timestamping in one place
means adding or removing a collector never touches downstream code."""

import hashlib
from datetime import datetime, timezone


def _item_id(candidate_id: str, source_url: str) -> str:
    """Scoped to (candidate_id, source_url), not source_url alone.

    Root cause of a real, previously-invisible undercount: append_items()
    dedupes globally by id across the whole store, with no candidate
    awareness. A single real article relevant to multiple candidates in
    the same race (exactly the pattern race_context_terms exists to catch
    -- e.g. a "5 Democrats running for this seat" roundup) resolve()s
    independently and correctly for each of them, but the old
    source-url-only id meant only the first candidate processed ever got
    it stored -- every other candidate it was equally relevant to lost
    that finding silently, no error, no log line. Confirmed directly: the
    Isthmus "who will take over Francesca Hong's seat" roundup resolved
    correctly for both martinez_rutherford and bennett, but only bennett's
    copy was ever stored. Scoping id to the (candidate, url) pair lets the
    same real story be stored once per candidate it's genuinely relevant
    to, which is what already-correct resolve() output implies should
    happen."""
    return hashlib.sha1(f"{candidate_id}:{source_url}".encode("utf-8")).hexdigest()


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
        "id": _item_id(candidate_id, source_url),
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
