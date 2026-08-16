#!/usr/bin/env python3
"""Test our prediction: does Ragas faithfulness catch the notification-event
misattribution, or does it score high because the claim's substance IS
present in the context, just bound to the wrong version label?

This is a targeted test against ONE real captured case, not a full harness
-- proving the tool's behavior on a known failure before trusting it on
everything else.
"""

import asyncio

import asyncpg
from datasets import Dataset
from ragas import evaluate
from ragas.metrics import faithfulness

from app.core.config import get_settings
from app.rag.retrieval import retrieve
from app.rag.generate import generate, _render_context
from dotenv import load_dotenv
import re
load_dotenv()

async def main():
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    question = "has 314.2 changed since 2023"

    MISATTRIBUTION_MARKERS = [
        "2023-11-13", "November 13, 2023", "Nov 13, 2023", "Nov. 13, 2023",
    ]

    for attempt in range(5):
        result = await retrieve(pool, question)
        answer = await generate(result, question)
        lower = answer.text.lower()
        has_notification = "notification event" in lower
        has_wrong_date = any(m.lower() in lower for m in MISATTRIBUTION_MARKERS)
        if has_notification and has_wrong_date:
            print(f"Reproduced the misattribution on attempt {attempt + 1}")
            print(f"  (matched marker check, not just ISO date)")
            break
    else:
        print("Did not reproduce the failure in 5 attempts.")

    context_text = _render_context(result)
    # Ragas wants contexts as a list of strings, not one blob -- split on
    # our own document delimiters so each retrieved chunk is a separate entry.
    contexts = [c.strip() for c in context_text.split("<<<DOC") if c.strip()]

    print()
    print("QUESTION:", question)
    print()
    print("ANSWER:", answer.text[:300])
    print()
    print(f"Passing {len(contexts)} context blocks to Ragas")

    dataset = Dataset.from_dict({
        "question": [question],
        "answer": [answer.text],
        "contexts": [contexts],
    })

    scores = evaluate(dataset, metrics=[faithfulness])
    print()
    print("FAITHFULNESS SCORE:", scores["faithfulness"][0])

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
