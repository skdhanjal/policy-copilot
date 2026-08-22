import asyncio
import asyncpg
from app.core.config import get_settings
from app.rag.retrieval import retrieve
from app.rag.generate import generate

QUESTIONS = [
    "What is the definition of customer information under the Safeguards Rule?",
    "What must an investment adviser's compliance program include?",
    "What are the disclosure requirements under Regulation E?",
    "Who qualifies as a service provider under 16 CFR 314?",
    "What is required for an investment adviser to have custody of client funds?",
    "What error resolution procedures apply to electronic fund transfers?",
    "What is a qualified individual under the Safeguards Rule?",
    "What books and records must an investment adviser maintain?",
]

async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    for q in QUESTIONS:
        print("=" * 70)
        print("Q:", q)
        result = await retrieve(pool, q)
        print(f"intent: {result.intent.value}  resolved: {len(result.resolved)}  lineages: {len(result.lineages)}")
        # answer = await generate(result, q)
        # print(f"answer: {answer.text[:250]}")
        # print(f"cited: {answer.cited_sections}")
        print()

    await pool.close()

asyncio.run(main())
