"""Database access helpers shared across the app.

Lives in core/ because it is infrastructure plumbing, not domain logic -- this
file has no idea what a "policy" or a "chunk" is. Anything that DOES know that
(e.g. "fetch current chunks for a family") belongs in app/rag/, not here.
"""

from __future__ import annotations

import asyncio

import asyncpg
from fastapi import HTTPException


async def fetch_with_backpressure(
    pool: asyncpg.Pool, query: str, *args, timeout: float = 5.0
):
    """Run a query with a bounded wait for a pool connection.

    Without an explicit timeout, a caller queues forever when the pool is
    saturated -- not a crash, but a silent pileup of waiting request handlers
    until memory runs out or callers give up. This converts that into an
    immediate, loud 503 the caller can act on: backpressure, telling a client
    "no, now" instead of "yes, eventually, maybe."
    """
    try:
        async with pool.acquire(timeout=timeout) as conn:
            return await conn.fetchval(query, *args)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=503, detail="database overloaded, retry")
