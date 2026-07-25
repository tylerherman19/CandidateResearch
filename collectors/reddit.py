"""Reddit collector via OAuth 'application only' client-credentials grant
(read-only, no user login needed) -- one registered 'script' app is enough.
Free, 100 req/min per Reddit's API terms."""

import os
import sys
import time
from datetime import datetime, timezone

import requests

from pipeline.normalize import normalize

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
SEARCH_URL = "https://oauth.reddit.com/search"
TIMEOUT_SECONDS = 15

_token_cache = {"token": None, "expires_at": 0}


def _get_token(client_id: str, client_secret: str, user_agent: str) -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 30:
        return _token_cache["token"]

    resp = requests.post(
        TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"User-Agent": user_agent},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 3600)
    return _token_cache["token"]


def collect(candidate: dict) -> list:
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user_agent = os.environ.get("REDDIT_USER_AGENT") or "media-monitor/1.0"

    if not (client_id and client_secret):
        print("  [info] REDDIT_CLIENT_ID/SECRET not set -- skipping Reddit", file=sys.stderr)
        return []

    token = _get_token(client_id, client_secret, user_agent)

    resp = requests.get(
        SEARCH_URL,
        params={
            "q": f'"{candidate["name"]}"',
            "sort": "new",
            "limit": 25,
            "restrict_sr": "false",
            "t": "day",
        },
        headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent},
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()

    items = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        published_at = ""
        created_utc = post.get("created_utc")
        if created_utc:
            published_at = datetime.fromtimestamp(created_utc, tz=timezone.utc).isoformat(timespec="seconds")

        items.append(
            normalize(
                collector="reddit",
                candidate_id=candidate["id"],
                title=post.get("title", ""),
                source=f"reddit.com/r/{post.get('subreddit', '')}",
                source_url=f"https://www.reddit.com{post.get('permalink', '')}",
                published_at=published_at,
                text=post.get("selftext", "") or post.get("title", ""),
                author=post.get("author"),
                engagement={"score": post.get("score"), "num_comments": post.get("num_comments")},
                raw=post,
            )
        )
    return items
