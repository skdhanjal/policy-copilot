import asyncio
import asyncpg
from app.core.config import get_settings
from app.rag.retrieval import retrieve
from app.rag.generate import generate

QUESTIONS = [
    "What did the FTC Safeguards Rule require as of January 2022?",
    "As of 2023, what were the recordkeeping requirements for investment advisers?",
    "What was section 314.2 in force in 2021?",
]

async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    for q in QUESTIONS:
        print("=" * 70)
        print("Q:", q)
        result = await retrieve(pool, q)
        print(f"intent classified as: {result.intent.value}")
        print(f"resolved: {len(result.resolved)}  lineages: {len(result.lineages)}")

        answer = await generate(result, q)
        print(f"answer: {answer.text[:350]}")
        print()

    await pool.close()

asyncio.run(main())
