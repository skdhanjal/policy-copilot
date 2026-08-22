"""Verify provider prompt caching actually fires and is reported back to
us -- the one thing D25 couldn't check for free. Two calls sharing an
identical system prompt + context prefix; only the second should show
cached tokens if OpenAI's caching kicked in (requires a large enough
prefix -- OpenAI's docs specify a minimum, typically 1024+ tokens).
"""

import asyncio

import asyncpg
from openai import AsyncOpenAI

from app.core.config import get_settings
from app.rag.retrieval import retrieve
from app.rag.generate import _render_context, _SYSTEM_PROMPT


async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)
    client = AsyncOpenAI(
        api_key=settings.gateway_app_key,
        base_url=f"{settings.gateway_base_url}/v1",
        max_retries=0,
    )

    # A diachronic question pulls a lot of context (multiple versions) --
    # more prefix tokens, more room for caching to matter.
    question = "has 314.2 changed since 2023"
    result = await retrieve(pool, question)
    context = _render_context(result)
    prompt_tokens_estimate = len(context) // 4  # rough chars-to-tokens
    print(f"context length: {len(context)} chars (~{prompt_tokens_estimate} tokens estimate)")
    print()

    for i in range(1, 3):
        resp = await client.chat.completions.create(
            model="fast",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
            ],
            temperature=0,
        )
        usage = resp.usage
        cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
        print(f"call {i}: prompt_tokens={usage.prompt_tokens}  cached_tokens={cached}  completion_tokens={usage.completion_tokens}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
