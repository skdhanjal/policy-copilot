"""Parse eCFR Part XML into section-level records.

Chunking unit is the SECTION (<DIV8 TYPE="SECTION">), not the (a)(1)(i)
sub-paragraph. We looked at real XML before deciding this: paragraph
numbering like (a), (1), (i) exists only as text inside sibling <P> tags,
not as real XML nesting. Reconstructing that hierarchy would be fragile and
error-prone. Section-level is both what the XML gives us for free (the N
attribute IS the CFR citation) and the unit eCFR's own amendment tracking
already operates on.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from lxml import etree


@dataclass(slots=True)
class ParsedSection:
    section_path: str   # e.g. '275.0-4', taken straight from the N attribute
    heading: str         # e.g. '§ 275.0-4 General requirements...'
    text: str            # flattened body text, tags stripped, words kept
    fr_citation: str | None  # verbatim CITA text, e.g. '[41 FR 39019, ...]'


def parse_sections(xml_text: str) -> list[ParsedSection]:
    root = etree.fromstring(xml_text.encode("utf-8"))
    sections = []

    for div8 in root.iter("DIV8"):
        if div8.get("TYPE") != "SECTION":
            continue

        section_path = div8.get("N", "")
        head_el = div8.find("HEAD")
        heading = _flatten(head_el) if head_el is not None else ""

        cita_el = div8.find("CITA")
        fr_citation = _flatten(cita_el).strip() if cita_el is not None else None

        # Walk DIRECT children in document order, once. The earlier version
        # used two separate .//P and .//NOTE findall passes -- since NOTE
        # contains its own nested <P>, that visited the same text twice and
        # lost real ordering (a NOTE belonging after paragraph (a)(3) ended
        # up appearing after (d) instead). Confirmed against real output
        # before this fix, not assumed.
        body_parts = []
        for child in div8:
            if child.tag in ("P", "NOTE"):
                body_parts.append(_flatten(child))

        text = "\n".join(p for p in body_parts if p.strip())
        
        sections.append(
            ParsedSection(
                section_path=section_path,
                heading=heading,
                text=text,
                fr_citation=fr_citation,
            )
        )

    return sections


def _flatten(el: etree._Element) -> str:
    """Turn an element and its children into plain text, dropping tags but
    keeping the words inside them. lxml decodes XML entities (&#x2014; etc)
    automatically -- that part is free.

    BUG WE HIT: itertext() yields one string per text node, split at every
    tag boundary -- including INLINE tags like <FR>1/2</FR> that sit in the
    middle of a sentence. Joining those fragments with "" looked fine until
    parse_sections joined paragraphs with "\n" one level up -- at that point
    an inline-tag edge and a real paragraph break became indistinguishable.
    Fix: join text-node fragments with a space and collapse whitespace runs,
    so "8" + "1/2" + " x 11" becomes "8 1/2 x 11" on one line, not three.
    """
    text = " ".join(el.itertext())
    return re.sub(r"\s+", " ", text).strip()


import hashlib

import tiktoken

# Same tokenizer family as most modern embedding models. Doesn't need to be
# exact -- this is a budget, not a billing calculation -- but it should be in
# the right ballpark so a "500 token" chunk doesn't silently become 2000.
_encoder = tiktoken.get_encoding("cl100k_base")

MAX_CHUNK_TOKENS = 500


@dataclass(slots=True)
class Chunk:
    section_path: str
    ordinal: int
    text: str
    token_count: int


def count_tokens(text: str) -> int:
    return len(_encoder.encode(text))


def split_section(section: ParsedSection, start_ordinal: int = 0) -> list[Chunk]:
    """One chunk per section, UNLESS it exceeds the token budget, in which
    case split at paragraph boundaries -- never mid-sentence, never mid-word.

    All resulting chunks share the same section_path and are ordered by
    ordinal. This is deliberate: section_path is the join key for diachronic
    comparison (ADR-2), and it must stay meaningful even when a section is
    long enough to need multiple chunks.
    """
    full_text = f"{section.heading}\n{section.text}".strip()
    total = count_tokens(full_text)

    if total <= MAX_CHUNK_TOKENS:
        return [Chunk(section.section_path, start_ordinal, full_text, total)]

    # Split on paragraph breaks -- our own \n-joined <P> boundaries from
    # parse_sections, which is exactly where a legal document is safe to cut.
    paragraphs = full_text.split("\n")
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    ordinal = start_ordinal

    for para in paragraphs:
        para_tokens = count_tokens(para)
        if buf and buf_tokens + para_tokens > MAX_CHUNK_TOKENS:
            text = "\n".join(buf)
            chunks.append(Chunk(section.section_path, ordinal, text, buf_tokens))
            ordinal += 1
            buf, buf_tokens = [], 0
        buf.append(para)
        buf_tokens += para_tokens

    if buf:
        text = "\n".join(buf)
        chunks.append(Chunk(section.section_path, ordinal, text, buf_tokens))

    return chunks


def content_hash(chunks: list[Chunk]) -> str:
    """One hash per SECTION (not per Part), used to skip re-embedding a
    section that is byte-identical to its previous version. Most amendments
    touch a handful of sections -- expect >90% reuse across consecutive
    snapshots."""
    joined = "\x00".join(c.text for c in chunks)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
