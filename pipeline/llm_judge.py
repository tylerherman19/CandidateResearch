"""LLM fallback judge for items the deterministic resolve() pass rejects.

Per CLAUDE.md's original disambiguation design: "a hit counts only if the
candidate's name appears and at least one require_any term appears... Then
an LLM pass makes the final call with the office and district in context."
Phase 1 deliberately deferred the LLM pass; this implements it now that
Gemini is live, specifically as a fallback for items with real evidence a
keyword rule can't see or safely encode.

Concrete motivating case: "Priorities differentiate Madison Dems running
in Assembly primary" -- no name, no district number, so no keyword rule
fires -- but a reader (or an LLM given the candidate's office) can
reasonably judge this is likely about a specific district's primary
candidates without needing every fact spelled out. A keyword list can't
safely encode "this general description plausibly refers to this specific
race" without either missing cases like this or overreaching into
wrong-candidate misattribution (see resolve.py's rejected-on-purpose
"Democratic Socialists" idea) -- that's exactly the judgment call an LLM
is suited for and a fixed rule isn't.

Only called for items resolve_with_fetch_fallback() already rejected --
this is a second-opinion fallback, not the primary gate, so normal daily
volume (a handful of near-misses) keeps this cheap. Batched like
classify.py for the same reason: batching is what keeps this within the
free tier.
"""

import json
import os
import sys

import requests

from pipeline.resolve import passes_loose_match_gates

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
BATCH_SIZE = 25
TIMEOUT_SECONDS = 30

PROMPT_INSTRUCTIONS = """You are doing a second-opinion review of news items a keyword-based filter already rejected as not being about a specific political candidate. Most of these rejections are correct -- the item is genuinely unrelated. A small number are wrong: the filter only sees the headline text, and a headline can plausibly be about the candidate's specific race without naming her or citing a district number explicitly (e.g. "Madison Dems running in Assembly primary" plausibly refers to a specific Assembly district's primary, if the candidate is running in exactly that kind of race).

CRITICAL GROUNDING RULE, read this first: the "text" field below is frequently just the headline repeated -- there is often no real article body at all. Being told which candidate you're checking is NOT evidence that candidate appears in the text. Do not reason "since we're checking about her, and this article is plausibly about someone in her position, it must be her" -- that is not what "explicitly named" means. Before answering YES, find the literal words in the title/text that support it, and quote or point to them in your reason. If you cannot point to a specific word or phrase in the given text that identifies her, answer NO, even if the topic feels like an obvious match for who she is. Getting this wrong by inventing textual evidence that isn't there is a worse failure than a missed borderline case.

Answer YES if EITHER of these is literally true in the text:
  (a) THE CANDIDATE LISTED ABOVE (the one named in "candidate=") is herself specifically involved -- named, directly quoted, or described taking a specific action. The text must contain the actual words that establish this about HER, specifically -- not about anyone else who happens to be mentioned nearby, even a prior officeholder for her seat, a party leader, or another public figure discussed in the same piece. If the article is actually about a different named person's own career, campaign, or activities, and the candidate you're checking isn't independently named/quoted/described, that is NO -- even if that other person's name is a term you'd associate with this race.
  (b) The piece is a multi-candidate roundup of the exact race she's running in (e.g. "5 Democrats running for Assembly District 76" when she's one of those 5). For THIS case specifically, the text does NOT need to name her individually -- that is the whole point of the roundup exception, and demoting a genuine roundup for "not naming her specifically" defeats it entirely. What the text DOES need to establish literally is a district number, seat name, or exact office she's running for -- an actual roundup or race-coverage frame. A prior officeholder for that seat being named, by itself, is NOT enough to satisfy (b) unless the text also frames it as coverage of the race/succession itself (not that person's own separate career or a different campaign of theirs) -- someone who used to hold the seat has their own independent newsworthiness (their own later campaigns, opinions, personal life) that has nothing to do with who succeeds them, and a mention of them is not evidence about the current race just because they once held it.
Do not require both (a) and (b) -- either one alone is sufficient. A roundup piece failing (a) is not itself a reason to answer NO if it satisfies (b).

Answer NO for "this falls under her job's general responsibilities" reasoning -- that is not evidence she is actually in this specific piece. A mayor is nominally responsible for policing, budgets, climate policy, and every other city function; that does not mean every city news story is about her. Reject reasoning like "city officials would typically be involved," "this is the kind of thing the mayor's administration handles," or "this concerns the mayor's jurisdiction" -- none of that says she is actually mentioned. If the item never actually names her, quotes her, or describes a specific action she took, answer NO even if the general subject matter falls within her office's remit.

When genuinely unsure, or when the text is too short/generic to contain any real evidence either way, answer NO. A missed borderline item is a smaller cost than confidently attributing a whole category of general city/office news to her that was never actually about her specifically.

For each item, return an object with:
- id: copy the item's id exactly
- verdict: "yes" or "no"
- reason: one short sentence. If YES, this must quote or closely paraphrase the specific word/phrase in the given text that names or identifies her -- not a restatement of the general topic.

Return ONLY a JSON object of the form {{"items": [ ... ]}}, one entry per item below, in the same order. No other text.

Items:
{items_block}
"""


def _build_items_block(items: list, candidates_map: dict) -> str:
    lines = []
    for i, item in enumerate(items):
        info = candidates_map.get(item.get("candidate_id"), {})
        candidate_label = f"{info.get('name', item.get('candidate_id', ''))} ({info.get('office', '')})"
        lines.append(
            f"{i + 1}. id={item['id']} | candidate={candidate_label} | "
            f"title: {item.get('title', '')} | text: {(item.get('text') or '')[:8000]}"
        )
    return "\n".join(lines)


def _parse_response(text: str, expected_count: int) -> list:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    parsed = json.loads(text)
    if isinstance(parsed, dict) and "items" in parsed:
        parsed = parsed["items"]
    if not isinstance(parsed, list) or len(parsed) != expected_count:
        raise ValueError(f"expected {expected_count} verdicts, got {parsed!r}")
    return parsed


def _judge_batch_gemini(items: list, candidates_map: dict, api_key: str, model: str) -> list:
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
    return _parse_response(text, len(items))


def judge_rejected_items(rejected: list, candidates_map: dict, gemini_key=None, gemini_model=None):
    """rejected: list of (item, reason) tuples that resolve_with_fetch_fallback
    already rejected. Returns (promoted_items, still_rejected) -- promoted
    items are augmented with match_type="llm_judged" and llm_reason."""
    gemini_key = gemini_key or os.environ.get("GEMINI_API_KEY")
    if not gemini_key or not rejected:
        return [], rejected

    gemini_model = gemini_model or os.environ.get("GEMINI_MODEL") or "gemini-flash-lite-latest"

    promoted = []
    still_rejected = []

    for start in range(0, len(rejected), BATCH_SIZE):
        batch = rejected[start : start + BATCH_SIZE]
        batch_items = [item for item, _ in batch]

        try:
            verdicts = _judge_batch_gemini(batch_items, candidates_map, gemini_key, gemini_model)
        except Exception as exc:
            print(f"  [warn] LLM judge batch failed: {exc}", file=sys.stderr)
            still_rejected.extend(batch)
            continue

        for (item, reason), verdict in zip(batch, verdicts):
            if str(verdict.get("verdict", "")).lower() == "yes":
                # Same deterministic safety gates resolve() applies to its
                # own race_context_only path -- a real regression without
                # this: this LLM path never calls resolve() again, so a
                # 2016-era "Chris Taylor, Jon Rygiewicz ask for your vote
                # in Assembly District 76" article, and completely
                # unrelated Francesca-Hong-the-chef restaurant coverage,
                # both got rescued by an LLM that had no idea either the
                # recency or exclude-term constraint existed. See
                # passes_loose_match_gates's own docstring.
                candidate = candidates_map.get(item.get("candidate_id"), {})
                gate_ok, gate_reason = passes_loose_match_gates(item, candidate)
                if not gate_ok:
                    still_rejected.append((item, gate_reason))
                    continue

                resolved = dict(item)
                resolved["matched_alias"] = ""
                resolved["matched_require_any"] = ""
                resolved["match_type"] = "llm_judged"
                resolved["llm_reason"] = verdict.get("reason", "")
                promoted.append(resolved)
                print(
                    f"  [info] LLM-judged match: {item.get('title', '')[:60]!r} -- {verdict.get('reason', '')}",
                    file=sys.stderr,
                )
            else:
                still_rejected.append((item, reason))

    return promoted, still_rejected


def verify_loose_matches(items: list, candidates_map: dict, gemini_key=None, gemini_model=None):
    """Double-checks items that were accepted via the loosest deterministic
    path -- race_context_only (candidates.yaml's race_context_terms, pure
    keyword match, no LLM involved at accept time) -- against the same
    strict-grounding prompt used for rescuing rejections. This is the other
    half of the fallback/double-check design: judge_rejected_items() rescues
    real misses, this demotes real false positives that slipped past a
    keyword rule.

    Only race_context_only is checked: match_type="name" is a literal exact
    name-phrase match (high confidence, doesn't need this), and
    match_type="llm_judged" was already produced by this same strict check
    at accept time.

    Returns (still_accepted, demoted) -- demoted items are converted to
    (item, reason) rejection tuples with reason="failed_double_check"."""
    to_check = [item for item in items if item.get("match_type") == "race_context_only"]
    if not to_check:
        return items, []

    gemini_key = gemini_key or os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        return items, []

    gemini_model = gemini_model or os.environ.get("GEMINI_MODEL") or "gemini-flash-lite-latest"

    demoted = []
    demoted_ids = set()

    for start in range(0, len(to_check), BATCH_SIZE):
        batch = to_check[start : start + BATCH_SIZE]

        try:
            verdicts = _judge_batch_gemini(batch, candidates_map, gemini_key, gemini_model)
        except Exception as exc:
            print(f"  [warn] LLM double-check batch failed: {exc}", file=sys.stderr)
            continue

        for item, verdict in zip(batch, verdicts):
            if str(verdict.get("verdict", "")).lower() != "yes":
                demoted_ids.add(item["id"])
                demoted.append((item, "failed_double_check"))
                print(
                    f"  [info] double-check demoted: {item.get('title', '')[:60]!r} -- {verdict.get('reason', '')}",
                    file=sys.stderr,
                )

    still_accepted = [item for item in items if item["id"] not in demoted_ids]
    return still_accepted, demoted
