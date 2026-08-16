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

RESOLVE_K = 8


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
    """
    rows = await pool.fetch(
        """
        SELECT c.version_id, c.section_path, c.text, v.effective_from, v.effective_to
        FROM chunk c
        JOIN policy_version v ON v.id = c.version_id
        WHERE v.family_id = $1 AND c.section_path = $2
        ORDER BY v.effective_from, c.ordinal
        """,
        family_id, section_path,
    )
    return [
        VersionedChunk(
            version_id=r["version_id"], section_path=r["section_path"], text=r["text"],
            effective_from=str(r["effective_from"]),
            effective_to=str(r["effective_to"]) if r["effective_to"] else None,
            in_force=r["effective_to"] is None,
        )
        for r in rows
    ]


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