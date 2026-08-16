#!/usr/bin/env python3
"""Corpus reconnaissance for ADR-8. Run before writing any schema or ingest.

Answers one question per candidate CFR Part: is it diachronically rich enough
to support "has this changed" questions? A Part with two amendments in nine
years cannot carry the architecture's core use case.
"""

import asyncio
import sys
from collections import defaultdict
from datetime import date

import httpx

BASE = "https://www.ecfr.gov"
WINDOW_START = date(2017, 1, 1)

CANDIDATES = [
    (45, "164", "HHS", "HIPAA Security/Privacy/Breach"),
    (16, "314", "FTC", "Safeguards Rule"),
    (17, "248", "SEC", "Reg S-P"),
    (12, "1016", "CFPB", "Reg P"),
]


async def fetch_versions(client: httpx.AsyncClient, title: int, part: str) -> list[dict]:
    url = f"{BASE}/api/versioner/v1/versions/title-{title}.json"
    r = await client.get(url, params={"part": part}, timeout=60.0)
    r.raise_for_status()
    payload = r.json()
    if isinstance(payload, dict):
        for key in ("content_versions", "versions", "results"):
            if key in payload:
                return payload[key]
        return []
    return payload


def analyse(entries: list[dict]) -> dict:
    by_date: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        amended = e.get("amendment_date") or e.get("date") or e.get("issue_date")
        if not amended:
            continue
        if date.fromisoformat(amended) < WINDOW_START:
            continue
        ident = e.get("identifier") or e.get("section") or "?"
        by_date[amended].add(ident)

    dates = sorted(by_date)
    return {
        "amendment_dates": dates,
        "n_dates": len(dates),
        "sections_touched": len({s for v in by_date.values() for s in v}),
    }


async def main() -> None:
    async with httpx.AsyncClient(headers={"User-Agent": "policy-copilot-probe/0.1"}) as client:
        for title, part, reg, name in CANDIDATES:
            key = f"{title} CFR {part}"
            print(f"[probe] {key} ({reg} - {name})", file=sys.stderr)
            try:
                entries = await fetch_versions(client, title, part)
            except Exception as exc:
                print(f"    FAILED: {exc}")
                continue
            a = analyse(entries)
            print(f"    {a['n_dates']} amendment dates, {a['sections_touched']} sections touched")
            print(f"    dates: {a['amendment_dates']}")


if __name__ == "__main__":
    asyncio.run(main())
