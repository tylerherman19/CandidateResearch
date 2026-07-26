"""Cluster near-duplicate items (the same wire story running in many
outlets) via simhash on the title. Keeps one canonical item per cluster
(earliest published_at) and stores cluster_size as the reach signal -- a
story in 300 outlets matters more than one in 3.

Title-only, not title+body: real-world data showed wire-syndicated copies
of the identical headline (verbatim, same story) failing to cluster once
resolve_with_fetch_fallback started populating real per-outlet article text
-- each outlet's page has enough of its own boilerplate/framing/byline
noise that body-text similarity dropped below threshold even though the
headline was byte-for-byte the same. The title is the far more reliable
duplicate signal for wire content; outlets routinely rewrite or pad the
body but rarely touch a syndicated headline.

Pure stdlib (hashlib) -- no new dependency for a technique this small.
"""

import hashlib
import re

HASH_BITS = 64
DEFAULT_THRESHOLD_BITS = 10  # ~0.85 similarity at 64 bits (1 - 10/64 ~= 0.84)


def _hash_token(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big")


def simhash(text: str) -> int:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return 0

    weights = [0] * HASH_BITS
    for token in tokens:
        h = _hash_token(token)
        for bit in range(HASH_BITS):
            weights[bit] += 1 if (h >> bit) & 1 else -1

    fingerprint = 0
    for bit in range(HASH_BITS):
        if weights[bit] > 0:
            fingerprint |= 1 << bit
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def cluster_items(items: list, threshold_bits: int = DEFAULT_THRESHOLD_BITS) -> list:
    """Items should already be filtered to one candidate (a cluster from
    one candidate's coverage shouldn't merge with another's). Returns one
    canonical item per cluster, augmented with cluster_size/cluster_members."""
    if not items:
        return []

    signatures = [simhash(item.get("title", "")) for item in items]

    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    for i in range(n):
        for j in range(i + 1, n):
            if hamming_distance(signatures[i], signatures[j]) <= threshold_bits:
                union(i, j)

    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(items[i])

    canonical_items = []
    for members in clusters.values():
        members_sorted = sorted(members, key=lambda it: it.get("published_at") or "9999")
        canonical = dict(members_sorted[0])
        canonical["cluster_size"] = len(members_sorted)
        canonical["cluster_members"] = [m.get("source_url", "") for m in members_sorted]
        canonical_items.append(canonical)

    return canonical_items
