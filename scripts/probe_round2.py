#!/usr/bin/env python3
import asyncio, sys
from collections import defaultdict
from datetime import date
import httpx

BASE = "https://www.ecfr.gov"
WINDOW_START = date(2017, 1, 1)
# 279: Form ADV etc (adviser filing forms, paired with 275)
# 230: Securities Act registration/disclosure rules (Regulation C, S-K adjacent)
# 249: Broker-dealer/national securities exchange reporting forms
CANDIDATES = [(17, "279", "Adviser filing forms"),
              (17, "230", "Securities Act registration rules"),
              (17, "249", "Exchange Act reporting forms")]

async def fetch_versions(client, title, part):
    url = f"{BASE}/api/versioner/v1/versions/title-{title}.json"
    r = await client.get(url, params={"part": part}, timeout=60.0)
    r.raise_for_status()
    payload = r.json()
    if isinstance(payload, dict):
        for key in ("content_versions", "versions", "results"):
            if key in payload: return payload[key]
        return []
    return payload

def analyse(entries):
    by_date = defaultdict(set)
    for e in entries:
        amended = e.get("amendment_date") or e.get("date") or e.get("issue_date")
        if not amended: continue
        if date.fromisoformat(amended) < WINDOW_START: continue
        ident = e.get("identifier") or e.get("section") or "?"
        by_date[amended].add(ident)
    dates = sorted(by_date)
    return {"n_dates": len(dates), "sections": len({s for v in by_date.values() for s in v}), "dates": dates}

async def main():
    async with httpx.AsyncClient(headers={"User-Agent": "policy-copilot-probe/0.1"}) as client:
        for title, part, name in CANDIDATES:
            print(f"[probe] {title} CFR {part} ({name})", file=sys.stderr)
            try:
                entries = await fetch_versions(client, title, part)
            except Exception as exc:
                print(f"    FAILED: {exc}"); continue
            a = analyse(entries)
            print(f"    {a['n_dates']} dates, {a['sections']} sections")
            print(f"    {a['dates']}")

if __name__ == "__main__":
    asyncio.run(main())
