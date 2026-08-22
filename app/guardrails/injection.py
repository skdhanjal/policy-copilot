"""Direct prompt-injection detection on user input. Pattern-based first
pass -- catches common injection phrasing, not a full classifier.
Indirect injection (planted in retrieved documents) is a separate
concern, handled by context.py's trust-boundary fencing (ADR-5), not
this module."""

import re

_INJECTION_PATTERNS = re.compile(
    r"\bignore (previous|prior|all|the above) instructions?\b|"
    r"\bdisregard (previous|prior|all) instructions?\b|"
    r"\byou are now\b|\bforget (your|all) (previous )?instructions?\b|"
    r"\bsystem prompt\b|\breveal your (instructions|prompt)\b|"
    r"\bact as if\b|\bpretend (you are|to be)\b|"
    r"\bnew instructions?:",
    re.IGNORECASE,
)


def detect_injection(text: str) -> bool:
    return bool(_INJECTION_PATTERNS.search(text))
