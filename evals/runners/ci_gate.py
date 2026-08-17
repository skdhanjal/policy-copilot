#!/usr/bin/env python3
"""CI gate. Runs the golden set, compares against the previous report,
and exits non-zero if a BLOCKING check regresses or fails outright --
unless that specific check is marked known_unreliable for that item, in
which case it's downgraded to informational regardless of the item's
overall severity.

This is deliberately NOT "everything must pass 100%". A gate that blocks
on a metric we've already proven can be wrong (D13: faithfulness on
no-change claims) would train you to ignore red X's, which defeats the
whole point. Blocking failures should mean "something we trust just
broke", not "a known-flaky metric had a bad day".

Exit codes:
  0 -- pass (no blocking regressions or failures)
  1 -- fail (a blocking, reliable check failed or regressed)
"""

import asyncio
import glob
import json
import sys
from datetime import datetime, timezone

import asyncpg
import yaml

from app.core.config import get_settings
from evals.runners.run_harness import run_item

REGRESSION_THRESHOLD = 0.05  # a drop of more than 5 percentage points counts as a regression
MIN_PASS_RATE = 0.7          # absolute floor for a blocking check, even with no prior report to compare against


def _load_previous_report() -> dict | None:
    reports = sorted(glob.glob("evals/reports/run-*.json"))
    if not reports:
        return None
    with open(reports[-1]) as f:
        return json.load(f)


def _evaluate_gate(items_config: list[dict], current_results: list[dict], previous: dict | None) -> tuple[bool, list[str]]:
    """Returns (passed, list of human-readable reasons for any failures)."""
    prev_by_id = {}
    if previous:
        prev_by_id = {i["id"]: i for i in previous["items"]}

    failures = []

    for item, result in zip(items_config, current_results):
        severity = item.get("severity", "informational")
        unreliable = set(item.get("known_unreliable_checks", []))

        for check_name, rate in result["pass_rates"].items():
            is_blocking = severity == "blocking" and check_name not in unreliable

            if not is_blocking:
                continue  # informational -- report only, never fails the gate

            if rate < MIN_PASS_RATE:
                failures.append(
                    f"[{item['id']}] {check_name}: {rate:.0%} is below the "
                    f"{MIN_PASS_RATE:.0%} floor (blocking, reliable check)"
                )
                continue

            prev_item = prev_by_id.get(item["id"])
            if prev_item:
                prev_rate = prev_item.get("pass_rates", {}).get(check_name)
                if prev_rate is not None and (prev_rate - rate) > REGRESSION_THRESHOLD:
                    failures.append(
                        f"[{item['id']}] {check_name}: regressed from "
                        f"{prev_rate:.0%} to {rate:.0%} (blocking, reliable check)"
                    )

    return (len(failures) == 0), failures


async def main() -> int:
    items_config = yaml.safe_load(open("evals/datasets/golden_set.yaml"))
    settings = get_settings()
    pool = await asyncpg.create_pool(settings.postgres_dsn, min_size=1, max_size=5)

    previous = _load_previous_report()

    current_results = []
    for item in items_config:
        print(f"Running {item['id']}...", file=sys.stderr)
        result = await run_item(pool, item)
        current_results.append(result)

    await pool.close()

    # Save this run the same way run_harness.py does, so the NEXT gate run
    # has something to compare against.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report = {"run_at": datetime.now(timezone.utc).isoformat(), "items": current_results}
    out_path = f"evals/reports/run-{timestamp}.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    passed, failures = _evaluate_gate(items_config, current_results, previous)

    print("\n" + "=" * 70)
    if passed:
        print("CI GATE: PASS")
        if not previous:
            print("(no previous report to compare against -- this run becomes the baseline)")
    else:
        print("CI GATE: FAIL")
        for f_msg in failures:
            print(f"  - {f_msg}")
    print("=" * 70)

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
