"""Cluster near-duplicate items (the same wire story running in many
outlets) via simhash on title + first 500 chars of text. Keeps one
canonical item per cluster (earliest published_at) and stores cluster_size
as the reach signal -- a story in 300 outlets matters more than one in 3.

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

    signatures = [simhash(f"{item.get('title', '')} {(item.get('text') or '')[:500]}") for item in items]

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
