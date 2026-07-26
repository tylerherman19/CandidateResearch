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
from datetime import datetime, timedelta, timezone

from pipeline.fetch_text import fetch_article_text, resolve_real_url

# race_context_terms are office/geography identifiers ("76th Assembly
# District", "Madison mayor") that are stable across many election
# cycles -- a match on one of these alone (no candidate name present)
# says "this is about this seat," not "this is about this year's
# candidates for this seat." Confirmed as a real false-positive source,
# not theoretical: broadening collectors/wi_outlets.py's TNCMS search to
# query race_context_terms directly (previously only queried by name)
# pulled in over a decade of unrelated coverage of *prior* AD76
# officeholders (2012, 2016, 2020 articles about Chris Taylor and Jon
# Rygiewicz -- neither a current candidate) purely because "Assembly
# District 76" doesn't change year to year. A literal name match has no
# such problem -- if her name is actually in the text, it's about her,
# any date -- so this gate applies only to the race-context-only path.
RACE_CONTEXT_ONLY_MAX_AGE_DAYS = 548  # ~18 months; comfortably covers a full
# active campaign cycle without reaching into a previous election for the
# same seat. A relative window, not a hardcoded date, so this doesn't need
# manual updating next cycle.


def _find_offsets(phrase: str, text_lower: str) -> list:
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return [m.start() for m in re.finditer(pattern, text_lower)]


def _is_recent_enough(published_at: str) -> bool:
    """No published_at at all is treated as NOT recent enough -- for a
    race-context-only match (no name, so recency is the only signal that
    it's about the current cycle), an unknown date is exactly the failure
    mode this gate exists to prevent, not a reason to skip the check."""
    if not published_at:
        return False
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(days=RACE_CONTEXT_ONLY_MAX_AGE_DAYS)
    return dt >= cutoff


def passes_loose_match_gates(item: dict, candidate: dict, text_lower: str = None):
    """Deterministic safety checks for any acceptance that ISN'T a literal
    name match -- a real name match is strong evidence on its own (any
    date, any topic), but race_context_only matches and LLM-judged
    rescues have no such evidence, so both need the same two guards:
    recency (an unnamed race-context mention could be about any past
    election for the same seat) and race_context_exclude_any (a
    configured "this topic means it's not actually about the succession
    race" term, e.g. "governor" for Francesca Hong's own unrelated
    campaign coverage).

    Centralized here rather than left inline in resolve()'s
    race_context_only branch, because that was a real, confirmed bug:
    judge_rejected_items()'s LLM-rescue path doesn't call resolve() again,
    so neither gate applied to anything it promoted -- a from-2016
    "Chris Taylor, Jon Rygiewicz ask for your vote in Assembly District
    76" article, and completely unrelated Francesca-Hong-the-chef
    restaurant-review pieces, both got rescued by an LLM that was never
    told either constraint existed. Both call sites now share this one
    function instead of duplicating (or forgetting to duplicate) the
    checks. Returns (True, "") or (False, reason)."""
    if text_lower is None:
        text_lower = f"{item.get('title', '')} {item.get('text', '')}".lower()

    if not _is_recent_enough(item.get("published_at", "")):
        return False, "too_old_for_loose_match"

    exclude_hit = next(
        (term for term in candidate.get("race_context_exclude_any", []) if _find_offsets(term, text_lower)),
        None,
    )
    if exclude_hit:
        return False, f"race_context_excluded_term:{exclude_hit}"

    return True, ""


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
        gate_ok, gate_reason = passes_loose_match_gates(item, candidate, text_lower)
        if not gate_ok:
            return None, gate_reason
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

    Fetches the real page only if the title/snippet-only pass would reject.
    Most collectors already give a real, direct URL (GDELT included);
    Google News' redirect links get resolved to the real URL first via a
    title search (see fetch_text.resolve_real_url)."""
    resolved, reason = resolve(item, candidate)
    if resolved is not None:
        return resolved, reason, resolved

    real_url = resolve_real_url(item)
    if not real_url:
        return None, reason, item

    fetched_text = fetch_article_text(real_url)
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
