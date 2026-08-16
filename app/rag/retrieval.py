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
    """Explicit section numbers are exact-match signal, not something
    semantic search should be trusted to find. A question mentioning '314.2'
    is telling us precisely what it means -- embedding similarity treats
    that citation as just another token and can easily rank generically
    related text above the literal section asked about (confirmed: querying
    'has 314.2 changed since 2023' scored 314.5 at 0.455 and never surfaced
    314.2 itself in the top 8). Extract and use directly instead of hoping
    semantic search finds it.
    """
    return _CITATION_PATTERN.findall(question)    


async def resolve(pool: asyncpg.Pool, query: str, k: int = RESOLVE_K) -> list[ResolvedChunk]:
    """Stage 1 only, standalone and testable before stage 2 exists."""
    [query_vector] = embed_texts([query])

    rows = await pool.fetch(
        """
        SELECT id, section_path, family_id, title, text, 1 - (embedding <=> $1) AS score, effective_from
        FROM current_chunk
        ORDER BY embedding <=> $1
        LIMIT $2
        """,
        str(query_vector), k,
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

    if citations:
        if result.intent is Intent.POINT_IN_TIME:
            # Real current text, directly -- no stub, no semantic search.
            rows = await pool.fetch(
                """
                SELECT id, section_path, family_id, title, text, effective_from
                FROM current_chunk
                WHERE section_path = ANY($1)
                """,
                citations,
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
                citations,
            )
            resolved = [
                ResolvedChunk(
                    chunk_id="", section_path=r["section_path"], family_id=r["family_id"],
                    title="", text="", score=1.0, effective_from="",
                )
                for r in rows
            ]

        if not resolved:
            # Citation looked real but doesn't exist in our corpus (current
            # version, for point-in-time; any version, for diachronic) --
            # fall back to semantic search rather than returning nothing.
            resolved = await resolve(pool, question, k=k)
    else:
        resolved = await resolve(pool, question, k=k)

    lineages: dict[tuple[str, str], list[VersionedChunk]] = {}
    if result.intent is Intent.DIACHRONIC:
        seen = set()
        for chunk in resolved:
            key = (chunk.family_id, chunk.section_path)
            if key in seen:
                continue
            seen.add(key)
            lineages[key] = await expand_lineage(pool, chunk.family_id, chunk.section_path)

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