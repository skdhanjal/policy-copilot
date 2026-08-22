"""Exact-match answer cache (Phase 4, DESIGN.md item 2).

CRITICAL correctness constraint, not just a performance detail: the cache
key must include what was actually RETRIEVED (chunk/version IDs), not just
the raw question text. If keyed on question text alone, a corpus update
(new eCFR snapshot, a real amendment) would silently serve a STALE cached
answer for a question whose correct answer just changed -- exactly the
diachronic correctness problem this whole project exists to solve,
defeated by our own optimization. See DESIGN.md's semantic-caching risk
note (jurisdiction/as-of-date as cache dimensions) -- same principle,
applied to exact-match: what changes the correct answer must be part of
the key.

Retrieval (cheap: local embeddings + Postgres) always runs first. This
cache only skips the EXPENSIVE part (the real model call), never skips
retrieval itself.
"""

from __future__ import annotations

import hashlib
import json

from redis.asyncio import Redis

from app.rag.retrieval import RetrievalResult

CACHE_TTL_SECONDS = 3600 * 24  # 24h -- matches our ingest cadence assumption;
                                 # revisit once we know real ingest frequency


def build_cache_key(question: str, result: RetrievalResult) -> str:
    """Deterministic key from retrieved content + question, NOT question
    alone. Two different questions retrieving identical chunks correctly
    share a cache entry only if the question text also matches -- included
    explicitly since two DIFFERENT questions can coincidentally retrieve
    the same chunks but deserve different generated answers.
    """
    chunk_ids = sorted(c.chunk_id for c in result.resolved if c.chunk_id)
    version_ids = sorted(
        v.version_id for versions in result.lineages.values() for v in versions
    )
    normalized_question = " ".join(question.lower().split())

    key_material = json.dumps({
        "q": normalized_question,
        "chunks": chunk_ids,
        "versions": version_ids,
        "intent": result.intent.value,
    }, sort_keys=True)

    digest = hashlib.sha256(key_material.encode()).hexdigest()
    return f"answer_cache:{digest}"


async def get_cached_answer(redis: Redis, question: str, result: RetrievalResult) -> dict | None:
    key = build_cache_key(question, result)
    raw = await redis.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def set_cached_answer(
    redis: Redis, question: str, result: RetrievalResult, answer_data: dict
) -> None:
    key = build_cache_key(question, result)
    await redis.set(key, json.dumps(answer_data), ex=CACHE_TTL_SECONDS)
