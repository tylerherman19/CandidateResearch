"""Classify deduped items on multiple axes via a fallback chain:
Gemini Flash-Lite -> Groq -> rules-based. Batched 25 items/call -- one call
per item exhausts the free daily quota before 8am. Logs which tier produced
each result (`classified_by`).

Explicitly NOT using VADER/TextBlob: generic sentiment misreads political
framing ("attacked the bill" can be favorable framing from the candidate's
own side). The rules-tier fallback below is honest about that limit -- it
does not attempt stance/claim_type/narrative_frame, it flags them "unknown"
rather than guessing wrong.
"""

import json
import os
import sys

import requests

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
BATCH_SIZE = 25
TIMEOUT_SECONDS = 30

CLASSIFICATION_FIELDS = [
    "stance_toward_candidate",
    "topic",
    "content_type",
    "claim_type",
    "risk_level",
    "narrative_frame",
    "summary",
    "mention_type",
]

PROMPT_INSTRUCTIONS = """You are classifying news/social hits about specific political candidates for a campaign monitoring digest.

For each item, produce an object with these fields:
- id: copy the item's id exactly
- stance_toward_candidate: one of "favorable", "unfavorable", "neutral", "mixed" -- judge from the candidate's own perspective, not generic sentiment. Do not use generic sentiment analysis: "attacked the bill" can be *favorable* framing if it's the candidate's own attack on an opponent's position.
- topic: a short label, e.g. "housing", "public safety", "campaign", "budget", "endorsement", "general"
- content_type: one of "news", "opinion", "press_release", "social_post", "other"
- claim_type: one of "factual_report", "opinion_commentary", "allegation", "endorsement", "other"
- risk_level: one of "low", "elevated", "high" -- elevated/high for scandal, controversy, legal issues, or a major attack line
- narrative_frame: a short free-text label (a few words) describing the story's angle
- summary: one or two plain-English sentences summarizing what the item actually says (not the headline restated -- the substance)
- mention_type: a short free-text label (2-4 words) describing HOW the candidate is actually mentioned or involved in this specific piece -- not a fixed category, use whatever phrase actually fits, e.g. "directly quoted", "described taking action", "named as subject", "brief mention in passing", "one of several candidates profiled", "opinion piece target", "co-signer of letter". Be specific to what's actually happening in the text, not generic.

Return ONLY a JSON object of the form {{"items": [ ... ]}}, one entry per item below, in the same order. No other text.

Items:
{items_block}
"""


TEXT_CHARS_FOR_CLASSIFICATION = 8000  # matches fetch_text.py's own cap -- don't waste a fetch we already paid for


def _build_items_block(items: list, candidates_map: dict) -> str:
    lines = []
    for i, item in enumerate(items):
        info = candidates_map.get(item.get("candidate_id"), {})
        candidate_label = f"{info.get('name', item.get('candidate_id', ''))} ({info.get('office', '')})"
        lines.append(
            f"{i + 1}. id={item['id']} | candidate={candidate_label} | "
            f"title: {item.get('title', '')} | text: {(item.get('text') or '')[:TEXT_CHARS_FOR_CLASSIFICATION]}"
        )
    return "\n".join(lines)


def _parse_classification_response(text: str, expected_count: int) -> list:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    parsed = json.loads(text)
    if isinstance(parsed, dict) and "items" in parsed:
        parsed = parsed["items"]
    if not isinstance(parsed, list) or len(parsed) != expected_count:
        raise ValueError(f"expected {expected_count} classifications, got {parsed!r}")
    return parsed


def _classify_batch_gemini(items: list, candidates_map: dict, api_key: str, model: str) -> list:
    prompt = PROMPT_INSTRUCTIONS.format(items_block=_build_items_block(items, candidates_map))
    resp = requests.post(
        GEMINI_URL.format(model=model),
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        },
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_classification_response(text, len(items))


def _classify_batch_groq(items: list, candidates_map: dict, api_key: str, model: str) -> list:
    prompt = PROMPT_INSTRUCTIONS.format(items_block=_build_items_block(items, candidates_map))
    resp = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    return _parse_classification_response(text, len(items))


TOPIC_KEYWORDS = {
    "housing": ["housing", "rent", "eviction", "affordable housing"],
    "public_safety": ["police", "shooting", "crime", "arrest"],
    "budget": ["budget", "tax", "spending", "levy"],
    "campaign": ["campaign", "endorse", "election", "primary", "run for", "announces run"],
    "education": ["school", "education", "teacher"],
}
RISK_KEYWORDS = ["scandal", "controversy", "lawsuit", "investigation", "arrested", "indicted", "resign"]


def _rules_classify_one(item: dict) -> dict:
    text = f"{item.get('title', '')} {item.get('text', '')}".lower()

    topic = "general"
    for label, keywords in TOPIC_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            topic = label
            break

    risk_level = "elevated" if any(kw in text for kw in RISK_KEYWORDS) else "low"
    content_type = "opinion" if any(w in text for w in ["opinion:", "editorial", "column"]) else "news"

    return {
        "id": item["id"],
        "stance_toward_candidate": "unknown",
        "topic": topic,
        "content_type": content_type,
        "claim_type": "unknown",
        "risk_level": risk_level,
        "narrative_frame": "",
        "summary": "",  # rules tier can't reliably summarize without an LLM
        "mention_type": "",
    }


def _rules_classify_batch(items: list, candidates_map: dict) -> list:
    return [_rules_classify_one(item) for item in items]


def classify_items(
    items: list,
    candidates_map: dict,
    gemini_key=None,
    groq_key=None,
    gemini_model=None,
    groq_model=None,
) -> list:
    gemini_key = gemini_key or os.environ.get("GEMINI_API_KEY")
    groq_key = groq_key or os.environ.get("GROQ_API_KEY")
    # .get(key, default) isn't enough here: GitHub Actions' `${{ vars.X }}`
    # sets the env var to "" (not absent) when the repo Variable is unset,
    # and .get()'s default only kicks in when the key is missing entirely.
    gemini_model = gemini_model or os.environ.get("GEMINI_MODEL") or "gemini-flash-lite-latest"
    groq_model = groq_model or os.environ.get("GROQ_MODEL") or "llama-3.1-8b-instant"

    enriched = []
    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start : start + BATCH_SIZE]
        classifications, tier = None, None

        if gemini_key:
            try:
                classifications = _classify_batch_gemini(batch, candidates_map, gemini_key, gemini_model)
                tier = "gemini"
            except Exception as exc:
                print(f"  [warn] Gemini classification failed: {exc}", file=sys.stderr)

        if classifications is None and groq_key:
            try:
                classifications = _classify_batch_groq(batch, candidates_map, groq_key, groq_model)
                tier = "groq"
            except Exception as exc:
                print(f"  [warn] Groq classification failed: {exc}", file=sys.stderr)

        if classifications is None:
            classifications = _rules_classify_batch(batch, candidates_map)
            tier = "rules"

        for item, classification in zip(batch, classifications):
            merged = dict(item)
            for field in CLASSIFICATION_FIELDS:
                merged[field] = classification.get(field, "")
            merged["classified_by"] = tier
            enriched.append(merged)

    return enriched
