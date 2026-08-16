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
from enum import Enum


class Intent(str, Enum):
    POINT_IN_TIME = "point_in_time"
    DIACHRONIC = "diachronic"


@dataclass(slots=True)
class IntentResult:
    intent: Intent
    reason: str  # which tier/rule fired -- for debugging and eval, not shown to users


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
    r"how (much|long|many)|is .* required)\b",
    re.IGNORECASE,
)


def classify(question: str) -> IntentResult:
    """Adds a real point-in-time signal instead of only detecting the
    diachronic side and defaulting everything else to diachronic by
    omission. Still conservative: an explicit date or change-keyword STILL
    wins even if current-state phrasing is also present, since a question
    can be phrased in present tense while still asking about a change
    (is it still true that it changed in 2023 -- rare, but the diachronic
    signal should win any tie).
    """
    if _EXPLICIT_DATE.search(question):
        return IntentResult(Intent.DIACHRONIC, reason="explicit_date_mentioned")

    if _CHANGE_KEYWORDS.search(question):
        return IntentResult(Intent.DIACHRONIC, reason="change_keyword_matched")

    if _CURRENT_STATE_KEYWORDS.search(question):
        return IntentResult(Intent.POINT_IN_TIME, reason="current_state_phrasing")

    # Genuinely no signal either way -- THIS is where the conservative
    # default actually earns its keep, on the residual we truly can't
    # classify, not on every simple lookup question.
    return IntentResult(Intent.DIACHRONIC, reason="default_no_signal")