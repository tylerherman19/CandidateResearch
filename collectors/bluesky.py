"""Bluesky AT Protocol collector.

Checked empirically: public.api.bsky.app's searchPosts is 403'd at the CDN
for unauthenticated requests (confirmed with multiple User-Agents), even
though other public read endpoints like getProfile return 200 fine. So in
practice this collector needs BLUESKY_HANDLE/BLUESKY_APP_PASSWORD (an app
password, not your real password) to do anything -- it still tries the
public endpoint first in case that changes, but expect it to no-op without
credentials."""

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from pipeline.normalize import normalize

PUBLIC_BASE = "https://public.api.bsky.app"
AUTHENTICATED_BASE = "https://bsky.social"
SEARCH_PATH = "/xrpc/app.bsky.feed.searchPosts"
SESSION_PATH = "/xrpc/com.atproto.server.createSession"
TIMEOUT_SECONDS = 15


def _get_auth_headers(handle: str, app_password: str) -> dict:
    resp = requests.post(
        AUTHENTICATED_BASE + SESSION_PATH,
        json={"identifier": handle, "password": app_password},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    token = resp.json()["accessJwt"]
    return {"Authorization": f"Bearer {token}"}


def collect(candidate: dict) -> list:
    handle = os.environ.get("BLUESKY_HANDLE")
    app_password = os.environ.get("BLUESKY_APP_PASSWORD")

    base = PUBLIC_BASE
    headers = {}
    if handle and app_password:
        try:
            headers = _get_auth_headers(handle, app_password)
            base = AUTHENTICATED_BASE
        except Exception as exc:
            print(f"  [warn] Bluesky auth failed, falling back to public search: {exc}", file=sys.stderr)

    since = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    resp = requests.get(
        base + SEARCH_PATH,
        params={"q": f'"{candidate["name"]}"', "since": since, "limit": 25, "sort": "latest"},
        headers=headers,
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()

    items = []
    for post in data.get("posts", []):
        record = post.get("record", {})
        author = post.get("author", {})
        uri = post.get("uri", "")
        post_id = uri.rsplit("/", 1)[-1] if uri else ""
        handle_for_url = author.get("handle", "")
        source_url = (
            f"https://bsky.app/profile/{handle_for_url}/post/{post_id}"
            if handle_for_url and post_id
            else uri
        )

        items.append(
            normalize(
                collector="bluesky",
                candidate_id=candidate["id"],
                title=(record.get("text", "") or "")[:120],
                source="bsky.app",
                source_url=source_url,
                published_at=record.get("createdAt", ""),
                text=record.get("text", ""),
                author=handle_for_url,
                engagement={
                    "likes": post.get("likeCount"),
                    "reposts": post.get("repostCount"),
                    "replies": post.get("replyCount"),
                },
                raw=post,
            )
        )
    return items
