"""Query intent classification: does this question need version history?

Three tiers, cheapest checked first:
  1. Explicit date/version words -> obvious, near 100% confident, free.
  2. Keyword heuristics ("changed", "used to", "still") -> cheap, decent
     signal, still free (no model call).
  3. Everything else -> DEFAULT TO DIACHRONIC, not point-in-time.

Bias rationale: if we wrongly skip stage 2 on a question that needed
history, the user gets a confident CURRENT-ONLY answer with no signal
anything is missing -- a silent, undetectable failure. If we wrongly RUN
stage 2 when it wasn't needed, we did some harmless extra work. That
asymmetry means "unsure" should resolve to the safer failure, not a 50/50
guess. See DESIGN.md Section 6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum


class Intent(str, Enum):
    POINT_IN_TIME = "point_in_time"
    DIACHRONIC = "diachronic"
    HISTORICAL = "historical"  # as-of a specific past date, single version


@dataclass(slots=True)
class IntentResult:
    intent: Intent
    reason: str
    as_of_date: date | None = None  # populated only for HISTORICAL


# "As of 2023", "in force in 2021", "in 2021" (near "was"/"required") --
# phrasing that asks about ONE point in the past, not a comparison across
# time. Checked BEFORE the blanket _EXPLICIT_DATE catch, since without this,
# any year mention routes to diachronic regardless of phrasing -- confirmed
# bug: "What was 314.2 in force in 2021?" and "As of 2023, what were the
# recordkeeping requirements?" both incorrectly hit diachronic mode.
_HISTORICAL_PHRASING = re.compile(
    r"\b(as of|in force (in|on)|in effect (in|on)|back in|"
    r"was\s+\w+\s+required\s+in|"
    r"what (was|were))\b.*?\b(19|20)\d{2}\b",
    re.IGNORECASE,
)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")

_EXPLICIT_DATE = re.compile(r"\b(19|20)\d{2}\b")

_CHANGE_KEYWORDS = re.compile(
    r"\b(chang(e|ed|es|ing)|updat(e|ed|es)|amend(ed|ment)?|revis(e|ed|ion)|"
    r"used to|previously|before|now vs|compare[d]?|differ(ence|ent)?|"
    r"still|no longer|new(ly)? (added|required)|history|evolv(e|ed|ing)|"
    r"when (did|was))\b",
    re.IGNORECASE,
)

_CURRENT_STATE_KEYWORDS = re.compile(
    r"\b(what (is|are|does)|current(ly)?|require[sd]?|must|shall|"
    r"how (much|long|many)|is .* required|"
    # Added: real captured failures where present-tense lookup questions
    # used phrasing our original list didn't cover -- "who qualifies as a
    # service provider" and "what procedures apply to X" both wrongly hit
    # DIACHRONIC/default_no_signal before this fix (see D21).
    r"qualif(y|ies|ied) as|apply to|applies to|"
    r"who (is|are)|what (must|kind)|"
    r"does .* need to|"
    r"is (defined|considered) as)\b",
    re.IGNORECASE,
)


def classify(question: str) -> IntentResult:
    """Order matters: HISTORICAL phrasing is checked BEFORE the blanket
    change-keyword/date checks, because "what was X as of 2021" and "has X
    changed since 2021" both contain a year and superficially look similar
    to a naive check, but ask fundamentally different things -- one wants
    ONE version, the other wants a COMPARISON across versions. Checking
    historical first prevents it from being swallowed by the diachronic
    date-detection that used to catch everything with a year in it.
    """
    hist_match = _HISTORICAL_PHRASING.search(question)
    if hist_match:
        year_match = _YEAR.search(question)
        as_of = date(int(year_match.group()), 12, 31) if year_match else None
        return IntentResult(Intent.HISTORICAL, reason="as_of_phrasing_matched", as_of_date=as_of)

    if _EXPLICIT_DATE.search(question):
        return IntentResult(Intent.DIACHRONIC, reason="explicit_date_mentioned")

    if _CHANGE_KEYWORDS.search(question):
        return IntentResult(Intent.DIACHRONIC, reason="change_keyword_matched")

    if _CURRENT_STATE_KEYWORDS.search(question):
        return IntentResult(Intent.POINT_IN_TIME, reason="current_state_phrasing")

    return IntentResult(Intent.DIACHRONIC, reason="default_no_signal")