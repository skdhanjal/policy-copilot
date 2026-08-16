"""Phase 1 ingest: walk bounded amendment history for the 3 confirmed
families, parse each snapshot, and write versions + chunks to Postgres.

Scope decision: most recent 5 amendment dates per family (or fewer if a
family has fewer total), not full history. Full history for 17 CFR 275 alone
is 33 snapshots -- most of which are near-duplicates of their neighbor and
would be caught by content_hash AFTER an expensive fetch+parse anyway. Bounded
depth still exercises everything Phase 1 needs to prove: multi-family
retrieval competition (ADR-2) and genuine diachronic depth on real history.
"""

from __future__ import annotations
from datetime import date
import asyncio
import hashlib
import sys

import asyncpg
import httpx

from app.core.config import get_settings
from app.rag.ecfr_client import BASE, fetch_snapshot_xml
from app.rag.ecfr_parse import content_hash, parse_sections, split_section
from app.rag.embed import embed_texts

MAX_DATES_PER_FAMILY = 5

# (family_id, cfr_title, cfr_part)
FAMILIES = [
    ("cfr-17-275", 17, "275"),
    ("cfr-16-314", 16, "314"),
    ("cfr-12-1005", 12, "1005"),
]


async def fetch_amendment_dates(client: httpx.AsyncClient, title: int, part: str) -> list[str]:
    """Reuses the same versions endpoint from scripts/probe_corpus.py --
    we already validated this shape by hand, so no new parsing risk here."""
    url = f"{BASE}/api/versioner/v1/versions/title-{title}.json"
    r = await client.get(url, params={"part": part}, timeout=60.0)
    r.raise_for_status()
    payload = r.json()
    entries = payload.get("content_versions", payload if isinstance(payload, list) else [])
    dates = sorted({e.get("amendment_date") or e.get("date") for e in entries if e.get("amendment_date") or e.get("date")})
    return dates


async def ingest_family(
    client: httpx.AsyncClient, pool: asyncpg.Pool, family_id: str, title: int, part: str
) -> None:
    all_dates_str = await fetch_amendment_dates(client, title, part)
    dates_str = all_dates_str[-MAX_DATES_PER_FAMILY:]
    dates = [date.fromisoformat(d) for d in dates_str]
    print(f"[{family_id}] {len(all_dates_str)} total dates, ingesting most recent {len(dates)}: {dates}", file=sys.stderr)

    prev_version_id: str | None = None

    for i, eff_from in enumerate(dates):
        eff_to = dates[i + 1] if i + 1 < len(dates) else None
        version_id = f"{family_id}-{eff_from}"

        xml = await fetch_snapshot_xml(client, title, part, eff_from)
        sections = parse_sections(xml)

        # Chunks and their source section pair up 2-at-a-time from extend above;
        # rebuild cleanly rather than rely on that fragile interleaving.
        all_chunks = []
        for section in sections:
            all_chunks.extend(split_section(section))

        snapshot_hash = hashlib.sha256(
            "\x00".join(c.text for c in all_chunks).encode()
        ).hexdigest()

        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO policy_version
                        (id, family_id, version_label, effective_from, effective_to,
                         supersedes, source_uri, content_hash)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    version_id, family_id, f"v-{eff_from}", eff_from, eff_to,
                    prev_version_id,
                    f"{BASE}/api/versioner/v1/full/{eff_from}/title-{title}.xml?part={part}",
                    snapshot_hash,
                )

                # Batch-embed all chunks for this snapshot in one call --
                # much faster than one model.encode() per chunk.
                vectors = embed_texts([c.text for c in all_chunks])

                for chunk, vector in zip(all_chunks, vectors):
                    chunk_id = f"{version_id}#{chunk.section_path}#{chunk.ordinal}"
                    await conn.execute(
                        """
                        INSERT INTO chunk (id, version_id, ordinal, section_path, text, token_count, embedding)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        chunk_id, version_id, chunk.ordinal, chunk.section_path, chunk.text, chunk.token_count, str(vector),
                    )

        print(f"  {eff_from}: {len(sections)} sections -> {len(all_chunks)} chunks", file=sys.stderr)
        prev_version_id = version_id


async def main() -> None:
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    async with httpx.AsyncClient(headers={"User-Agent": "policy-copilot-ingest/0.1"}) as client:
        for family_id, title, part in FAMILIES:
            await ingest_family(client, pool, family_id, title, part)

    await pool.close()
    print("ingest complete", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
