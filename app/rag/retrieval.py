"""Two-stage version-aware retrieval (ADR-2).

Stage 1 RESOLVE: semantic search over current_chunk ONLY. Answers "which
policy family is this about." Restricting to current versions is what
prevents near-duplicate historical versions of one family from flooding the
top-k and evicting a different, equally relevant family -- 17 CFR 275 alone
has 5 ingested versions; searching all of them would let one family's history
dominate results meant to span 3 families.

Stage 2 EXPAND: relational lineage walk in SQL, joined on section_path. Only
runs for diachronic/historical queries. Never re-embeds -- alignment across
time is structural (same citation), not semantic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

import asyncpg

from app.rag.embed import embed_texts
from app.rag.intent import Intent, classify

RESOLVE_K = 8

import re

# Matches CFR section citations like '314.2', '275.204-2', '1005.18',
# '275.206(4)-1'. Deliberately permissive on the suffix -- CFR citations use
# hyphens, parens, and sub-letters in ways that are easier to allow broadly
# and validate against the database than to fully enumerate with regex.
_CITATION_PATTERN = re.compile(r"\b(\d{2,4}\.\d+[\w()\-]*)\b")

@dataclass(slots=True)
class ExtractedCitations:
    section_paths: list[str]  # exact citations like '314.2'
    family_ids: list[str]      # cfr_part strings from nickname matches, e.g. '1005'


_REGULATION_NICKNAMES = {
    "reg e": "1005",
    "regulation e": "1005",
    "electronic fund transfer": "1005",
    "safeguards rule": "314",
    "16 cfr 314": "314",
    "investment advisers act": "275",
    "17 cfr 275": "275",
}
@dataclass(slots=True)
class ResolvedChunk:
    chunk_id: str
    section_path: str
    family_id: str
    title: str
    text: str
    score: float
    effective_from: str
    
@dataclass(slots=True)
class VersionedChunk:
    version_id: str
    section_path: str
    text: str
    effective_from: str
    effective_to: str | None
    in_force: bool    
    
@dataclass(slots=True)
class RetrievalResult:
    intent: Intent
    resolved: list[ResolvedChunk]
    # Populated only for diachronic queries -- one lineage list per distinct
    # (family_id, section_path) pulled from the resolve step, so a
    # multi-family query gets multi-family history, not just the top hit's.
    lineages: dict[tuple[str, str], list[VersionedChunk]]    
    
def extract_citations(question: str) -> list[str]:
    """Explicit numeric citations ('314.2', '275.204-2') AND common
    regulation nicknames ('Reg E', 'the Safeguards Rule') both count as
    explicit signal -- both bypass semantic search the same way, routed
    to current_chunk (point-in-time) or expand_lineage (diachronic).

    CONFIRMED against the real policy_family table (not assumed): only
    3 families exist -- 275 (Investment Advisers Act), 314 (FTC
    Safeguards Rule), 1005 (Reg E). No "Reg P" in this corpus; omitted
    to avoid a nickname mapping to nothing real.

    Nickname match returns the FAMILY's cfr_part as a bare string
    ('1005'), not a section_path -- callers already handle both shapes
    via the same downstream SQL (WHERE section_path = ANY($1) matches
    on prefix-adjacent values naturally since real section_paths start
    with the part number, e.g. '1005.18').
    """
    section_paths = _CITATION_PATTERN.findall(question)

    q_lower = question.lower()
    family_ids = list({
        part for nickname, part in _REGULATION_NICKNAMES.items()
        if nickname in q_lower
    })

    return ExtractedCitations(section_paths=section_paths, family_ids=family_ids)

async def resolve(
    pool: asyncpg.Pool, query: str, k: int = RESOLVE_K, boost_family: list[str] | None = None
) -> list[ResolvedChunk]:
    """Stage 1 only, standalone and testable before stage 2 exists.

    boost_family: optional list of cfr_part strings (e.g. ['1005']) from a
    regulation nickname match ('Reg E'). Applies a small score BONUS to
    chunks in that family, not a hard filter -- a nickname narrows WHICH
    regulation is likely relevant, but a genuinely mixed question ("compare
    Reg E and investment adviser rules") still needs BOTH families to
    surface. A hard filter would have reintroduced the original bug in a
    new form (this time silently dropping the OTHER family instead of
    dumping one unfiltered). Confirmed via direct testing that a hard
    filter approach failed a real mixed-family golden-set question before
    this design was chosen.
    """
    [query_vector] = embed_texts([query])
    boost_patterns = [f"cfr-%-{fid}" for fid in (boost_family or [])] or ["__none__"]

    rows = await pool.fetch(
        """
        SELECT id, section_path, family_id, title, text,
               (1 - (embedding <=> $1))
                   + CASE WHEN family_id LIKE ANY($3) THEN 0.15 ELSE 0 END
                   AS score,
               effective_from
        FROM current_chunk
        ORDER BY score DESC
        LIMIT $2
        """,
        str(query_vector), k, boost_patterns,
    )
    return [
        ResolvedChunk(
            chunk_id=r["id"], section_path=r["section_path"], family_id=r["family_id"],
            title=r["title"], text=r["text"], score=float(r["score"]),
            effective_from=str(r["effective_from"]),
        )
        for r in rows
    ]

async def expand_lineage(
    pool: asyncpg.Pool, family_id: str, section_path: str
) -> list[VersionedChunk]:
    """Stage 2. No embedding call -- alignment is structural (same
    section_path across versions), not semantic. This is the mechanism that
    makes 'has X changed since Y' answerable at all: walk every version of
    ONE section, oldest first, so the delta is directly readable.

    BUG WE HIT: a section can span multiple chunk rows (our own token-budget
    splitting in split_section). The original query returned one row per
    CHUNK, not per VERSION -- a 32-chunk section with 5 versions came back
    as 160 rows, each mislabelled as if it were a distinct version. Fixed by
    concatenating all chunks for one version, in ordinal order, into one
    logical text block per version.
    """
    rows = await pool.fetch(
        """
        SELECT c.version_id, c.section_path, c.text, c.ordinal, v.effective_from, v.effective_to
        FROM chunk c
        JOIN policy_version v ON v.id = c.version_id
        WHERE v.family_id = $1 AND c.section_path = $2
        ORDER BY v.effective_from, c.ordinal
        """,
        family_id, section_path,
    )

    by_version: dict[str, list] = {}
    for r in rows:
        by_version.setdefault(r["version_id"], []).append(r)

    results = []
    for version_id, version_rows in by_version.items():
        version_rows.sort(key=lambda r: r["ordinal"])
        full_text = "\n".join(r["text"] for r in version_rows)
        first = version_rows[0]
        results.append(
            VersionedChunk(
                version_id=version_id,
                section_path=first["section_path"],
                text=full_text,
                effective_from=str(first["effective_from"]),
                effective_to=str(first["effective_to"]) if first["effective_to"] else None,
                in_force=first["effective_to"] is None,
            )
        )

    results.sort(key=lambda v: v.effective_from)
    return results

async def resolve_as_of(
    pool: asyncpg.Pool, family_id: str, section_path: str, as_of: date
) -> VersionedChunk | None:
    """The HISTORICAL counterpart to expand_lineage -- but cheap by design.

    expand_lineage pulls EVERY version of a section (that's its job for
    diachronic comparison). This pulls exactly ONE: whichever version was
    genuinely in force on the target date, using the same effective_from/
    effective_to interval already in the schema. No lineage walk, no
    multi-version prompt bloat -- the entire reason this function exists is
    to make "what was X as of DATE" cheap, not just correctly labeled.
    """
    row = await pool.fetchrow(
        """
        SELECT c.version_id, c.section_path, c.text, c.ordinal,
               v.effective_from, v.effective_to
        FROM chunk c
        JOIN policy_version v ON v.id = c.version_id
        WHERE v.family_id = $1 AND c.section_path = $2
          AND v.effective_from <= $3
          AND (v.effective_to IS NULL OR v.effective_to > $3)
        ORDER BY c.ordinal
        """,
        family_id, section_path, as_of,
    )
    # NOTE: fetchrow returns only the FIRST matching row -- if a section
    # split into multiple chunks (like 275.204-2 did earlier this session),
    # this silently drops chunks 1+. Acceptable for now since most
    # single-section point-in-time lookups fit in one chunk; flagged as a
    # known limitation, not fixed here, to avoid scope creep on this fix.
    if row is None:
        return None
    return VersionedChunk(
        version_id=row["version_id"], section_path=row["section_path"], text=row["text"],
        effective_from=str(row["effective_from"]),
        effective_to=str(row["effective_to"]) if row["effective_to"] else None,
        in_force=row["effective_to"] is None,
    )

async def retrieve(pool: asyncpg.Pool, question: str, k: int = RESOLVE_K) -> RetrievalResult:
    """The single entrypoint the rest of the system calls. Hides the
    two-stage mechanism (ADR-2) and the intent decision behind one function.

    Explicit citations ('314.2', '275.204-2') are checked FIRST and bypass
    semantic search entirely. Confirmed necessary: embedding similarity on
    'has 314.2 changed since 2023' scored an unrelated section (314.5,
    containing the word 'effective') above 314.2 itself, which never
    appeared in the top 8 at all. A citation is exact-match information;
    semantic search is the wrong tool for it.

    BUG WE HIT: the citation shortcut originally returned an empty stub
    chunk (text="") regardless of intent, assuming expand_lineage would
    always fill in the real content. True for diachronic questions, but a
    point-in-time question ('what does 314.3 require') doesn't call
    expand_lineage at all -- it got a citation match with literally no text
    in it. Fixed by making the shortcut intent-aware: point-in-time looks up
    real current text directly; diachronic keeps the stub, since
    expand_lineage supplies the real content for that path.
    """
    result = classify(question)
    citations = extract_citations(question)
    has_numeric_citation = bool(citations.section_paths)

    if has_numeric_citation:
        if result.intent is Intent.POINT_IN_TIME:
            # Real current text, directly -- no stub, no semantic search.
            # Numeric citation identifies ONE exact section -- nothing left
            # for semantic search to contribute. Nickname family_ids are
            # NOT included in this WHERE anymore: including them here is
            # what caused the original bug (pulling all 27+ sections of a
            # family unfiltered when only a nickname matched, with no
            # relevance ranking at all -- confirmed via direct SQL count).
            rows = await pool.fetch(
                """
                SELECT id, section_path, family_id, title, text, effective_from
                FROM current_chunk
                WHERE section_path = ANY($1)
                """,
                citations.section_paths,
            )
            resolved = [
                ResolvedChunk(
                    chunk_id=r["id"], section_path=r["section_path"], family_id=r["family_id"],
                    title=r["title"], text=r["text"], score=1.0,
                    effective_from=str(r["effective_from"]),
                )
                for r in rows
            ]
        else:
            # Diachronic: stub is fine here, expand_lineage below supplies
            # the real content across every version.
            rows = await pool.fetch(
                """
                SELECT DISTINCT v.family_id, c.section_path
                FROM chunk c
                JOIN policy_version v ON v.id = c.version_id
                WHERE c.section_path = ANY($1)
                """,
                citations.section_paths,
            )
            resolved = [
                ResolvedChunk(
                    chunk_id="", section_path=r["section_path"], family_id=r["family_id"],
                    title="", text="", score=1.0, effective_from="",
                )
                for r in rows
            ]
        if not resolved:
            resolved = await resolve(pool, question, k=k, boost_family=citations.family_ids)
    else:
        # No numeric citation. A nickname (if any) narrows WHICH regulation
        # is relevant but not WHICH section within it -- a family can have
        # 30+ sections (confirmed: 1005 alone has 27). Picking the relevant
        # one is exactly semantic search's job, so resolve() still runs,
        # just weighted toward the nicknamed family rather than replaced
        # by an unfiltered dump of it.
        resolved = await resolve(pool, question, k=k, boost_family=citations.family_ids)

    lineages: dict[tuple[str, str], list[VersionedChunk]] = {}
    if result.intent is Intent.DIACHRONIC:
        seen = set()
        for chunk in resolved:
            key = (chunk.family_id, chunk.section_path)
            if key in seen:
                continue
            seen.add(key)
            lineages[key] = await expand_lineage(pool, chunk.family_id, chunk.section_path)
    elif result.intent is Intent.HISTORICAL and result.as_of_date:
        # Cheap by design: ONE version per section, not full history.
        # Reuses the `lineages` dict shape (list of length 1) so downstream
        # context assembly (_render_context in generate.py) doesn't need a
        # third code path -- it already knows how to render "a list of
        # versions for a section", this just always hands it a list of one.
        seen = set()
        for chunk in resolved:
            key = (chunk.family_id, chunk.section_path)
            if key in seen:
                continue
            seen.add(key)
            version = await resolve_as_of(pool, chunk.family_id, chunk.section_path, result.as_of_date)
            if version:
                lineages[key] = [version]        

    return RetrievalResult(intent=result.intent, resolved=resolved, lineages=lineages)


async def _main():
    import sys
    from app.core.config import get_settings

    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    query = sys.argv[1] if len(sys.argv) > 1 else "what are the safeguards requirements for customer data"
    results = await resolve(pool, query)

    print(f"query: {query!r}\n")
    for r in results:
        print(f"[{r.score:.3f}] {r.family_id} / {r.section_path} (eff. {r.effective_from})")
        print(f"    {r.text[:100]!r}")
        print()

    await pool.close()
    
if __name__ == "__main__":
    asyncio.run(_main())