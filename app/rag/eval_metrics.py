"""Custom evaluation metrics that Ragas structurally cannot provide.

Ragas's faithfulness metric checks "is this claim traceable to the context
as a whole." Confirmed empirically (see scripts/check_faithfulness.py) that
this scores 1.0 on an answer that correctly quotes real context text but
attributes it to the WRONG version's date label. Faithfulness has no concept
of binding a claim to a specific entity within a multi-entity context.

This module checks that binding directly and deterministically -- same
principle as the D12 fix in generate.py: where a fact is verifiable in code,
verify it in code, don't rely on an LLM judge to notice.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.rag.retrieval import VersionedChunk

# Matches a date the answer explicitly attributes something to, in either
# ISO or common prose forms. Deliberately over-inclusive -- false positives
# here just mean an extra check we didn't strictly need, which is cheap.
_DATE_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})\b|"
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b",
    re.IGNORECASE,
)

_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}

# A correct claim that something was ABSENT from a version reads as low
# word-overlap with that version's text -- which is exactly what a WRONG
# positive claim also looks like. Without this, the check can't tell
# "correctly says the term wasn't there yet" from "hallucinated the term
# into the wrong version" (see golden_set.yaml's documented known
# limitation on diachronic-notification-event-001).
_NEGATION_CUES = re.compile(
    r"\b(not|n't|no longer|absent|lack(?:s|ing|ed)?|"
    r"did not|was not|were not|had not|has not|have not)\b",
    re.IGNORECASE,
)


def _contains_word(text_lower: str, word: str) -> bool:
    """Whole-word match, not substring -- confirmed via a live run that
    plain `in` containment let "definition" spuriously match inside
    "definitions" (the boilerplate section-header word present in every
    version), producing a false hit that had nothing to do with the
    sentence's actual claim.
    """
    return re.search(rf"\b{re.escape(word.lower())}\b", text_lower) is not None


def _normalize_date(match: re.Match) -> str:
    """Turn either regex branch into ISO format for comparison."""
    if match.group(1):
        return match.group(1)
    month, day, year = match.group(2), match.group(3), match.group(4)
    return f"{year}-{_MONTHS[month.lower()]}-{int(day):02d}"


@dataclass(slots=True)
class BindingCheck:
    claimed_date: str
    # A short excerpt of the sentence the date appeared in, for a human to
    # judge relevance -- this check finds CANDIDATE problems, it doesn't
    # replace reading them.
    sentence: str
    date_exists_in_lineage: bool
    # If the date is real but the surrounding sentence's content isn't
    # verifiable this cheaply, we say so rather than claim false confidence.
    content_verified: bool | None
    # Best-effort: which OTHER version's text the sentence's distinctive
    # words actually match, when this one doesn't. Populated only for a
    # genuine (non-negated) suspected misattribution -- lets a retry prompt
    # point at the fix instead of just flagging the error.
    likely_correct_date: str | None = None


def check_date_bindings(answer_text: str, versions: list[VersionedChunk]) -> list[BindingCheck]:
    """For every date mentioned in the answer, check two things:
      1. Does a version with that effective_from date actually exist?
      2. Best-effort: does the sentence's surrounding content overlap with
         THAT specific version's text, rather than a different version's?

    This is intentionally a coarse, sentence-level heuristic, not a full
    semantic check -- it's meant to flag candidates for human/LLM review,
    the same way a linter flags candidates rather than proving correctness.
    """
    version_by_date = {v.effective_from: v for v in versions}
    results = []

    # A word longer than 6 chars that STILL appears in every version's text
    # is section boilerplate (e.g. a definitions section's own word
    # "definition"), not evidence a sentence's content belongs to one
    # specific version -- confirmed via a live run where "definition" and
    # "contain" appeared in both an old and new 314.2 version, making a
    # correct negative claim ("the 2023 version did not contain this
    # definition") false-flag as unverified anyway. Excluded from
    # "distinctive" so only real discriminating content counts.
    all_texts_lower = [v.text.lower() for v in versions]
    # Words that appear in AT LEAST ONE version's actual text -- confirmed
    # via a live run that picking "distinctive" words by mere position
    # (first 5 words >6 chars in the sentence) grabbed the model's own
    # meta-commentary ("version", "effective", "introduced") ahead of the
    # real content word ("notification"), none of which appear literally
    # in the regulation text at all, so they can only ever miss and dilute
    # the threshold. Restricting to real corpus vocabulary fixes that.
    domain_words: set[str] = set()
    for t in all_texts_lower:
        domain_words |= set(re.findall(r"[a-z]{7,}", t))

    # Words common to EVERY version aren't distinctive to any one of them.
    # Seed candidates from the UNION of all versions, not just the first --
    # confirmed via a live run that a boilerplate word absent from the
    # first version specifically (314.2's earliest version has a different
    # structure) never got tested against the rest, so it stayed eligible
    # as "distinctive" despite actually appearing in 4 of 5 versions.
    common_words = {w for w in domain_words if all(_contains_word(t, w) for t in all_texts_lower)}

    # Split into sentences crudely -- good enough for flagging, not for
    # anything requiring real NLP. Also split on newlines: confirmed via a
    # live run that diachronic answers are routinely bulleted/multi-line.
    units = [u for u in re.split(r"(?<=[.!?])\s+|\n+", answer_text) if u.strip()]

    # Group units into blocks scoped by their nearest preceding date
    # mention: a "### effective from DATE:" heading followed by bullet
    # items describing that version belongs together, so checking the
    # heading's date against JUST the heading line (missing the bullets'
    # actual content) or against a blob that also contains the NEXT
    # version's heading (diluting both) both produce unreliable results --
    # confirmed via a live run where this merged two versions' claims into
    # one "sentence" and made a correct claim about each look unverified.
    # A unit that itself mentions a (possibly new) date starts a new block.
    blocks: list[str] = []
    current: list[str] = []
    for unit in units:
        if current and _DATE_PATTERN.search(unit):
            blocks.append(" ".join(current))
            current = [unit]
        else:
            current.append(unit)
    if current:
        blocks.append(" ".join(current))

    for sentence in blocks:
        for match in _DATE_PATTERN.finditer(sentence):
            date = _normalize_date(match)
            exists = date in version_by_date

            content_verified = None
            likely_correct_date = None
            if exists:
                version_text_lower = version_by_date[date].text.lower()
                # Pull a few distinctive words from the sentence (skip short
                # common words) and check they appear in THIS version's text
                # specifically -- not in the combined context, which is
                # exactly the check faithfulness fails to make.
                # Strip markdown formatting chars too -- confirmed via a
                # live run that "**Notification" (bold markdown, which the
                # model uses constantly for defined terms) never matched
                # domain_words because the leading "**" was never removed,
                # silently dropping the one word that actually mattered.
                words = [w.strip(".,;:()\"'*_`#") for w in sentence.split()]
                # Exclude digit-bearing tokens (dates -- long enough to
                # qualify but never real body-text content) and anything
                # not in the corpus vocabulary at all (the model's own
                # meta-commentary, e.g. "version", "introduced").
                distinctive = [
                    w for w in words
                    if len(w) > 6
                    and w.lower() not in common_words
                    and w.lower() in domain_words
                    and not any(c.isdigit() for c in w)
                ][:5]
                if distinctive:
                    hits = sum(1 for w in distinctive if _contains_word(version_text_lower, w))
                    # Strict majority, not "at least half": with only 1-2
                    # eligible words left after the filters above, a single
                    # coincidental match (a generic word that happens to
                    # also appear in this version) must not outvote the
                    # one word that actually carries the claim.
                    overlap_high = hits > len(distinctive) / 2
                    negated = bool(_NEGATION_CUES.search(sentence))
                    if negated and len(distinctive) < 2:
                        # A single word is a coin flip for a negation claim
                        # specifically: confirmed via a live run where the
                        # sole candidate ("contain") happened to reappear in
                        # a totally unrelated clause elsewhere in the same
                        # section, making a CORRECT "did not contain X"
                        # claim look contradicted by coincidence. Absence
                        # can't be confidently verified this cheaply from
                        # one word either way -- don't guess.
                        content_verified = None
                    else:
                        # Negated claim ("X was absent here") is verified by
                        # LOW overlap with this version, not high -- flip.
                        content_verified = (not overlap_high) if negated else overlap_high

                    if content_verified is False and not negated:
                        best_date, best_hits = None, 0
                        for other_date, other_version in version_by_date.items():
                            if other_date == date:
                                continue
                            other_hits = sum(
                                1 for w in distinctive if _contains_word(other_version.text.lower(), w)
                            )
                            if other_hits > best_hits:
                                best_date, best_hits = other_date, other_hits
                        if best_date and best_hits > len(distinctive) / 2:
                            likely_correct_date = best_date

            results.append(
                BindingCheck(
                    claimed_date=date,
                    sentence=sentence.strip()[:150],
                    date_exists_in_lineage=exists,
                    content_verified=content_verified,
                    likely_correct_date=likely_correct_date,
                )
            )

    return results
