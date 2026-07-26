"""Decide whether a normalized Item actually refers to our candidate.

Design (revised after real-world feedback -- see history below):
  1. exclude_any phrase present anywhere       -> reject
  2. name/alias match anywhere                 -> accept
  3. no name match, but a race_context_terms
     phrase is present                        -> accept, loose (see below)
  4. otherwise                                 -> reject

Every rejection is returned with its reason so callers can log it for audit
-- silent over-filtering is treated as worse than noise.

Why a bare name match is now sufficient on its own (no context-term/
proximity gate): a real case surfaced it -- an item titled exactly
"Juliana Bennett - Channel 3000" (her literal full name, nothing else) was
being rejected because no require_any term ("Wisconsin", "Madison", etc.)
also appeared. For names this distinctive, requiring extra context after
an exact name match rejects genuine hits for no benefit. exclude_any (empty
today) is the mechanism for the day a same-named unrelated person becomes a
real problem -- add the disambiguating term there, not by re-adding a
context gate that also rejects genuine matches.

race_context_terms (deliberately loose, added after separate real-world
feedback): Google News RSS gives us only the headline -- no real article
snippet -- so a genuine name mention buried in the article body is
invisible to us. A headline like "Candidates for [seat] explain their
views" is almost certainly about our candidate if she's one of a small
number running for that exact seat, even with her name absent from the
headline. race_context_terms are phrases specific enough to one race (e.g.
"76th Assembly District") that a false positive is implausible -- unlike
generic geography ("Madison", "Wisconsin"), which real testing showed
produces false positives (an unrelated Madison police-shooting story kept
matching on "Madison").
"""

import re
import sys

from pipeline.fetch_text import can_fetch_direct, fetch_article_text


def _find_offsets(phrase: str, text_lower: str) -> list:
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return [m.start() for m in re.finditer(pattern, text_lower)]


def resolve(item: dict, candidate: dict):
    """Returns (resolved_item, None) on accept, or (None, reason) on reject.
    resolved_item is `item` augmented with matched_alias/matched_require_any."""
    text_lower = f"{item.get('title', '')} {item.get('text', '')}".lower()

    for term in candidate.get("exclude_any", []):
        if _find_offsets(term, text_lower):
            return None, f"excluded_term:{term}"

    name_phrases = [candidate["name"]] + candidate.get("aliases", [])
    matched_name = next((phrase for phrase in name_phrases if _find_offsets(phrase, text_lower)), None)
    if matched_name:
        resolved = dict(item)
        resolved["matched_alias"] = matched_name
        resolved["matched_require_any"] = ""
        resolved["match_type"] = "name"
        return resolved, None

    race_context_terms = candidate.get("race_context_terms", [])
    race_hit = next((term for term in race_context_terms if _find_offsets(term, text_lower)), None)
    if race_hit:
        resolved = dict(item)
        resolved["matched_alias"] = ""
        resolved["matched_require_any"] = race_hit
        resolved["match_type"] = "race_context_only"
        return resolved, None

    return None, "no_name_match"


def resolve_with_fetch_fallback(item: dict, candidate: dict):
    """Returns (resolved_or_None, reason, item_for_audit).

    item_for_audit is the richest version of the item available -- if a
    full-text fetch happened (even if it didn't flip the verdict), it's the
    enriched item with real body text, not the sparse original. This
    matters downstream: a rejected item goes on to the LLM judge fallback
    (pipeline/llm_judge.py), and that judge needs the real fetched text to
    correctly recognize a mention (e.g. "the mayor") that never made it into
    the collector's own title/snippet -- previously we fetched the real
    page during this step but then discarded it on rejection, so the LLM
    judge only ever saw the same sparse title/snippet resolve() already
    couldn't match.

    Fetches the real page only if the title/snippet-only pass would reject
    AND the collector gives us a real, direct article URL (see
    fetch_text.py -- true for GDELT, not for Google News' obfuscated
    redirect links)."""
    resolved, reason = resolve(item, candidate)
    if resolved is not None:
        return resolved, reason, resolved

    if not can_fetch_direct(item.get("collector", "")):
        return None, reason, item

    fetched_text = fetch_article_text(item.get("source_url", ""))
    if not fetched_text:
        return None, reason, item

    enriched = dict(item)
    enriched["text"] = fetched_text
    resolved2, reason2 = resolve(enriched, candidate)
    if resolved2 is not None:
        resolved2["fetched_full_text"] = True
        print(
            f"  [info] rescued by full-text fetch: {item.get('title', '')[:60]!r} "
            f"(was: {reason})",
            file=sys.stderr,
        )
        return resolved2, None, resolved2

    return None, reason, enriched
