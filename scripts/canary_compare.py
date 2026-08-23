"""Runs the golden set against two URLs (stable, canary), compares
pass rates. Practical substitute for live-traffic online eval --
no real production traffic exists yet, so we compare against the
same trusted eval set both revisions would need to pass anyway."""

import asyncio
import sys

import httpx
import yaml

STABLE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
CANARY_URL = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8081"

# Real rollback trigger, per DESIGN.md's exit criterion -- named
# statistic, window, tolerance:
#   STATISTIC: grounding_failed rate + rate_limited rate, aggregated
#   WINDOW: this single comparison run (real version would be rolling
#           N-minute window over live traffic)
#   TOLERANCE: canary fail rate must not exceed stable by more than 5
#              percentage points -- allows normal noise, catches real regression
TOLERANCE_PP = 5.0


async def query(client: httpx.AsyncClient, base_url: str, question: str) -> dict:
    resp = await client.post(f"{base_url}/query", json={"question": question}, timeout=30.0)
    resp.raise_for_status()
    return resp.json()


async def run_against(base_url: str, questions: list[str]) -> float:
    fails = 0
    async with httpx.AsyncClient() as client:
        for q in questions:
            try:
                r = await query(client, base_url, q)
                if r.get("grounding_failed") or r.get("rate_limited"):
                    fails += 1
            except Exception:
                fails += 1
    return fails / len(questions) * 100


async def main():
    items = yaml.safe_load(open("evals/datasets/golden_set.yaml"))
    questions = [i["question"] for i in items]

    stable_rate = await run_against(STABLE_URL, questions)
    canary_rate = await run_against(CANARY_URL, questions)

    delta = canary_rate - stable_rate
    print(f"stable fail rate: {stable_rate:.1f}%")
    print(f"canary fail rate: {canary_rate:.1f}%")
    print(f"delta: {delta:+.1f}pp (tolerance: {TOLERANCE_PP}pp)")

    if delta > TOLERANCE_PP:
        print("VERDICT: ROLLBACK -- canary regression exceeds tolerance")
        return 1
    print("VERDICT: PROMOTE -- canary within tolerance")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
