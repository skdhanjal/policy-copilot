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

    # Split into sentences crudely -- good enough for flagging, not for
    # anything requiring real NLP.
    sentences = re.split(r"(?<=[.!?])\s+", answer_text)

    for sentence in sentences:
        for match in _DATE_PATTERN.finditer(sentence):
            date = _normalize_date(match)
            exists = date in version_by_date

            content_verified = None
            if exists:
                version_text_lower = version_by_date[date].text.lower()
                # Pull a few distinctive words from the sentence (skip short
                # common words) and check they appear in THIS version's text
                # specifically -- not in the combined context, which is
                # exactly the check faithfulness fails to make.
                words = [w.strip(".,;:()\"'") for w in sentence.split()]
                distinctive = [w for w in words if len(w) > 6][:5]
                if distinctive:
                    hits = sum(1 for w in distinctive if w.lower() in version_text_lower)
                    content_verified = hits >= max(1, len(distinctive) // 2)

            results.append(
                BindingCheck(
                    claimed_date=date,
                    sentence=sentence.strip()[:150],
                    date_exists_in_lineage=exists,
                    content_verified=content_verified,
                )
            )

    return results
