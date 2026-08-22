"""Regex-based PII redaction. Deterministic, no model call -- runs before
embedding per ADR-9 (redaction must precede embedding, not just
classification). Escalate to Presidio only if measured recall is
insufficient."""

import re

_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone": re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "credit_card": re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b"),
}


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Returns (redacted_text, list of PII types found)."""
    found = []
    for label, pattern in _PATTERNS.items():
        if pattern.search(text):
            found.append(label)
            text = pattern.sub(f"[REDACTED_{label.upper()}]", text)
    return text, found
