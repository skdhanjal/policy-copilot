"""Context compression. Deterministic, safe only -- no truncation.
Truncation tested and confirmed unsafe: dropped substantive legal
conditions from a real chunk, not just filler."""

import re


def compress_chunk_text(text: str) -> str:
    """Whitespace collapse + boilerplate strip only. Never truncates --
    every word in a legal chunk may be load-bearing."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\bfor purposes of this (section|part)\b,?\s*", "", text, flags=re.I)
    return text
