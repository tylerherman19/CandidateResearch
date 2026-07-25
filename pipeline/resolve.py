"""Decide whether a normalized Item actually refers to our candidate.

Design (per CLAUDE.md, approved before implementation):
  1. exclude_any phrase present anywhere       -> reject
  2. no name/alias match anywhere:
     - a race_context_terms phrase present     -> accept, loose (see below)
     - otherwise                               -> reject
  3. require_any term present, but not within  -> reject
     ~200 words of a name/alias match
  4. name/alias match + require_any within 200
     words of it                               -> accept

Every rejection is returned with its reason so callers can log it for audit
-- silent over-filtering is treated as worse than noise.

race_context_terms (deliberately loose, added after real-world feedback):
Google News RSS gives us only the headline -- no real article snippet -- so
a genuine name mention buried in the article body is invisible to us. A
headline like "Candidates for [seat] explain their views" is almost
certainly about our candidate if she's one of a small number running for
that exact seat, even with her name absent from the headline. race_context_
terms are phrases specific enough to one race (e.g. "76th Assembly
District") that a false positive is implausible -- unlike generic geography
("Madison", "Wisconsin"), which real testing showed produces false positives
(an unrelated Madison police-shooting story kept matching on "Madison").
"""

import re

PROXIMITY_WORDS = 200


def _find_offsets(phrase: str, text_lower: str) -> list:
    pattern = r"\b" + re.escape(phrase.lower()) + r"\b"
    return [m.start() for m in re.finditer(pattern, text_lower)]


def _word_index(text_lower: str, char_offset: int) -> int:
    return len(text_lower[:char_offset].split())


def resolve(item: dict, candidate: dict):
    """Returns (resolved_item, None) on accept, or (None, reason) on reject.
    resolved_item is `item` augmented with matched_alias/matched_require_any."""
    text_lower = f"{item.get('title', '')} {item.get('text', '')}".lower()

    for term in candidate.get("exclude_any", []):
        if _find_offsets(term, text_lower):
            return None, f"excluded_term:{term}"

    name_phrases = [candidate["name"]] + candidate.get("aliases", [])
    name_hits = [
        (offset, phrase)
        for phrase in name_phrases
        for offset in _find_offsets(phrase, text_lower)
    ]
    if not name_hits:
        race_context_terms = candidate.get("race_context_terms", [])
        race_hit = next((term for term in race_context_terms if _find_offsets(term, text_lower)), None)
        if race_hit:
            resolved = dict(item)
            resolved["matched_alias"] = ""
            resolved["matched_require_any"] = race_hit
            resolved["match_type"] = "race_context_only"
            return resolved, None
        return None, "no_name_match"

    require_any = candidate.get("require_any", [])
    require_hits = [
        (offset, term)
        for term in require_any
        for offset in _find_offsets(term, text_lower)
    ]
    if not require_hits:
        return None, "no_require_any_term_present"

    for name_offset, name_phrase in name_hits:
        name_word = _word_index(text_lower, name_offset)
        for req_offset, req_term in require_hits:
            req_word = _word_index(text_lower, req_offset)
            if abs(name_word - req_word) <= PROXIMITY_WORDS:
                resolved = dict(item)
                resolved["matched_alias"] = name_phrase
                resolved["matched_require_any"] = req_term
                resolved["match_type"] = "name"
                return resolved, None

    return None, "require_any_present_but_outside_200_words"
