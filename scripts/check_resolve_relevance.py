import asyncio
import asyncpg
from app.core.config import get_settings
from app.rag.retrieval import resolve
from app.rag.embed import embed_texts

async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    q = "What is the definition of customer information under the Safeguards Rule?"
    results = await resolve(pool, q)
    for r in results:
        print(f"[{r.score:.3f}] {r.family_id} / {r.section_path}")

    await pool.close()

asyncio.run(main())
