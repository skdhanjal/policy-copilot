"""Direct prompt-injection detection on user input. Pattern-based first
pass -- catches common injection phrasing, not a full classifier.
Indirect injection (planted in retrieved documents) is a separate
concern, handled by context.py's trust-boundary fencing (ADR-5), not
this module."""

import re

_INJECTION_PATTERNS = re.compile(
    r"\b(ignore (previous|prior|all|the above) instructions?|"
    r"disregard (previous|prior|all) instructions?|"
    r"you are now|forget (your|all) (previous )?instructions?|"
    r"system prompt|reveal your (instructions|prompt)|"
    r"act as if|pretend (you are|to be)|"
    r"new instructions?:)\b",
    re.IGNORECASE,
)


def detect_injection(text: str) -> bool:
    return bool(_INJECTION_PATTERNS.search(text))
