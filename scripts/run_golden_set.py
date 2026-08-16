#!/usr/bin/env python3
"""Smoke test for the golden set format -- NOT the real Phase 2 harness.

This proves the YAML structure is actually drivable end-to-end through
retrieve() + generate(). No automated pass/fail scoring here on purpose --
that's Ragas's job in Phase 2, and building real scoring logic before we
know the item format is stable would be solving the wrong problem first.
"""

import asyncio
import sys

import asyncpg
import yaml

from app.core.config import get_settings
from app.rag.retrieval import retrieve
from app.rag.generate import generate


async def main():
    items = yaml.safe_load(open("evals/datasets/golden_set.yaml"))
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    for item in items:
        print("=" * 70)
        print(f"[{item['id']}] ({item['category']}, source={item['source']})")
        print(f"Q: {item['question']}")
        print()

        result = await retrieve(pool, item["question"])
        answer = await generate(result, item["question"])

        print(f"intent detected: {result.intent.value}")
        print(f"resolved: {len(result.resolved)}, lineages: {len(result.lineages)}")
        print()
        print(f"A: {answer.text[:400]}")
        print()
        print(f"cited: {answer.cited_sections}  unverifiable: {answer.unverifiable_citations}")
        print(f"rubric says: {item.get('rubric', item.get('must_not_claim', '(none)'))[:200]}")
        print()

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
