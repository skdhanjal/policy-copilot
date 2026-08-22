import asyncio
import asyncpg
from app.core.config import get_settings
from app.rag.retrieval import retrieve
from app.rag.generate import generate

QUESTIONS = [
    "What security and safeguarding obligations apply to a registered investment adviser handling customer financial data?",
    "How do recordkeeping requirements differ between investment advisers and financial institutions under Reg E?",
    "If a firm is both an investment adviser and handles electronic fund transfers, what compliance obligations overlap?",
]

async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    for q in QUESTIONS:
        print("=" * 70)
        print("Q:", q)
        result = await retrieve(pool, q)
        families_hit = set()
        for c in result.resolved:
            families_hit.add(c.family_id)
        for (fam, sec) in result.lineages.keys():
            families_hit.add(fam)
        print(f"intent: {result.intent.value}")
        print(f"families in resolved/lineage: {families_hit}")

        answer = await generate(result, q)
        print(f"answer: {answer.text[:400]}")
        print(f"cited: {answer.cited_sections}")
        print()

    await pool.close()

asyncio.run(main())
