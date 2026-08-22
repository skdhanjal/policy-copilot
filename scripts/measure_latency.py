import asyncio, time, asyncpg
from app.core.config import get_settings
from app.rag.retrieval import retrieve
from app.rag.generate import generate

QUESTIONS = [
    "what does 314.3 require",
    "has 314.2 changed since 2023",
    "what is the definition of customer information under the Safeguards Rule",
]

async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    for q in QUESTIONS:
        t0 = time.monotonic()
        r = await retrieve(pool, q)
        t1 = time.monotonic()
        a = await generate(r, q)
        t2 = time.monotonic()

        retrieve_ms = (t1-t0)*1000
        generate_ms = (t2-t1)*1000
        total_ms = (t2-t0)*1000
        print(f"{q[:50]:50} retrieve={retrieve_ms:6.0f}ms generate={generate_ms:6.0f}ms total={total_ms:6.0f}ms")

    await pool.close()

asyncio.run(main())
