#!/usr/bin/env python3
"""Phase 2 eval harness. Reads the golden set, runs each item through
retrieve() + generate(), dispatches ONLY the checks that item declares,
and writes a timestamped report.

Non-determinism is surfaced, not hidden: items tagged `trials: N` in the
golden set run N times and report a pass RATE, not a single verdict --
we measured diachronic-notification-event-001 failing 1 in 3 times, and
averaging that into one boolean would misrepresent it.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone

import asyncpg
import yaml

from app.core.config import get_settings
from app.rag.generate import generate
from app.rag.retrieval import retrieve
from evals.runners.checks import CHECK_DISPATCH
from dotenv import load_dotenv

load_dotenv()

# Items known to be non-deterministic get multiple trials by default.
# Grounded in what we actually measured this session, not a guess.
DEFAULT_TRIALS = 1
KNOWN_NONDETERMINISTIC = {"diachronic-notification-event-001": 3}


async def run_item(pool: asyncpg.Pool, item: dict) -> dict:
    question = item["question"]
    checks_to_run = item.get("checks", [])
    n_trials = KNOWN_NONDETERMINISTIC.get(item["id"], DEFAULT_TRIALS)

    trial_results = []
    for trial in range(n_trials):
        result = await retrieve(pool, question)
        answer = await generate(result, question)

        checks_output = {}
        for check_name in checks_to_run:
            check_fn = CHECK_DISPATCH.get(check_name)
            if check_fn is None:
                checks_output[check_name] = {"passed": None, "note": "unknown check name"}
                continue
            checks_output[check_name] = await check_fn(question, answer, result)

        trial_results.append({
            "trial": trial + 1,
            "answer_preview": answer.text[:300],
            "intent": result.intent.value,
            "checks": checks_output,
        })

    # Aggregate: for each check, what fraction of trials passed (ignoring
    # None/informational results).
    pass_rates = {}
    for check_name in checks_to_run:
        verdicts = [
            t["checks"][check_name]["passed"]
            for t in trial_results if t["checks"][check_name].get("passed") is not None
        ]
        if verdicts:
            pass_rates[check_name] = sum(1 for v in verdicts if v) / len(verdicts)

    return {
        "id": item["id"],
        "category": item["category"],
        "question": question,
        "n_trials": n_trials,
        "pass_rates": pass_rates,
        "trials": trial_results,
    }


async def main():
    items = yaml.safe_load(open("evals/datasets/golden_set.yaml"))
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    report = {"run_at": datetime.now(timezone.utc).isoformat(), "items": []}

    for item in items:
        print(f"Running {item['id']} ({item.get('checks', [])})...", file=sys.stderr)
        result = await run_item(pool, item)
        report["items"].append(result)

        summary = ", ".join(f"{k}={v:.0%}" for k, v in result["pass_rates"].items())
        print(f"  -> {summary or '(no scored checks)'}", file=sys.stderr)

    await pool.close()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = f"evals/reports/run-{timestamp}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\nReport written to {out_path}", file=sys.stderr)

    print("\n" + "=" * 70)
    print(f"{'Item':<40} {'Pass rates'}")
    print("-" * 70)
    for item in report["items"]:
        rates = ", ".join(f"{k}={v:.0%}" for k, v in item["pass_rates"].items())
        print(f"{item['id']:<40} {rates or '(informational only)'}")


if __name__ == "__main__":
    asyncio.run(main())
