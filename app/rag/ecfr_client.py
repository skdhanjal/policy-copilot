"""Thin client for the eCFR API.

Two endpoints only, matching what we already probed by hand:
  - versions: which dates a Part changed on (already validated in scripts/)
  - full: the actual XML content of a Part as of a given date

No retry/backoff logic here yet -- this is Phase 1, correct-on-data-model,
naive-on-everything-else. Robustness comes later once we know the shape of
real failures, not before.
"""

from __future__ import annotations

import httpx

BASE = "https://www.ecfr.gov"


async def fetch_snapshot_xml(client: httpx.AsyncClient, title: int, part: str, on_date: str) -> str:
    """Fetch the full XML text of one CFR Part as it stood on `on_date`
    (format: 'YYYY-MM-DD'). This is a point-in-time snapshot -- the whole
    reason we're not just scraping the 'current' version.
    """
    url = f"{BASE}/api/versioner/v1/full/{on_date}/title-{title}.xml"
    r = await client.get(url, params={"part": part}, timeout=60.0)
    r.raise_for_status()
    return r.text
