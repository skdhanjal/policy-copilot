"""Measure actual chunk-reuse across the queries we've run this session.

Answers the real question before building anything: does caching have
something to exploit, or would we be adding infrastructure for a
theoretical win? Uses real retrieve() calls across a representative set
of questions we've already validated -- not synthetic traffic.
"""

import asyncio
from collections import Counter

import asyncpg

from app.core.config import get_settings
from app.rag.retrieval import retrieve

# Real questions from across this session's golden-set exploration --
# genuinely varied, not designed to inflate overlap artificially.
QUESTIONS = [
    "what does 314.3 require",
    "has 314.2 changed since 2023",
    "what is the definition of customer information under the Safeguards Rule",
    "what must an investment adviser's compliance program include",
    "who qualifies as a service provider under 16 CFR 314",
    "what is required for an investment adviser to have custody of client funds",
    "what is a qualified individual under the Safeguards Rule",
    "did 17 CFR 275.211(h) get removed",
    "what security and safeguarding obligations apply to a registered investment adviser",
    "what error resolution procedures apply to electronic fund transfers",
]


async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    chunk_counter = Counter()
    total_chunk_uses = 0

    for q in QUESTIONS:
        result = await retrieve(pool, q)
        for c in result.resolved:
            if c.chunk_id:
                chunk_counter[c.chunk_id] += 1
                total_chunk_uses += 1
        for versions in result.lineages.values():
            for v in versions:
                chunk_counter[v.version_id] += 1
                total_chunk_uses += 1

    await pool.close()

    unique_chunks = len(chunk_counter)
    reused = sum(1 for count in chunk_counter.values() if count > 1)

    print(f"{len(QUESTIONS)} questions -> {total_chunk_uses} total chunk/version uses")
    print(f"{unique_chunks} UNIQUE chunks/versions referenced")
    print(f"{reused} of those were reused across 2+ questions")
    print(f"reuse rate: {reused / unique_chunks:.1%}" if unique_chunks else "n/a")
    print()
    print("Most-reused chunks:")
    for chunk_id, count in chunk_counter.most_common(10):
        print(f"  {count}x  {chunk_id}")


if __name__ == "__main__":
    asyncio.run(main())
