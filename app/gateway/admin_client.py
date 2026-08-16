"""Thin client for LiteLLM's key-management endpoints.

Separate from generate.py's client on purpose: this one uses the MASTER
key (admin operations only), never the scoped virtual keys application
code uses for real traffic. Mixing those two concerns in one module would
make it too easy to accidentally use the unlimited master key for a real
request -- exactly the mistake we moved away from when we switched
generate.py to gateway_app_key.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings


class GatewayAdminClient:
    def __init__(self, base_url: str | None = None, master_key: str | None = None):
        settings = get_settings()
        self.base_url = (base_url or settings.gateway_base_url).rstrip("/")
        self.master_key = master_key or settings.litellm_master_key
        self._headers = {"Authorization": f"Bearer {self.master_key}"}

    async def create_key(
        self,
        alias: str,
        models: list[str],
        max_budget: float | None = None,
        budget_duration: str | None = None,
        rpm_limit: int | None = None,
    ) -> dict:
        payload = {"key_alias": alias, "models": models}
        if max_budget is not None:
            payload["max_budget"] = max_budget
        if budget_duration is not None:
            payload["budget_duration"] = budget_duration
        if rpm_limit is not None:
            payload["rpm_limit"] = rpm_limit

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/key/generate", headers=self._headers, json=payload, timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

    async def list_keys(self) -> list[dict]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/key/list", headers=self._headers, timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            # LiteLLM's list endpoint has varied its response shape across
            # versions -- handle both a bare list and a wrapped one rather
            # than assume, same caution as the eCFR client's versions parsing.
            return data.get("keys", data) if isinstance(data, dict) else data

    async def get_key_info(self, key: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/key/info",
                headers=self._headers,
                params={"key": key},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

    async def revoke_key(self, key: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/key/delete",
                headers=self._headers,
                json={"keys": [key]},
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()

    async def update_key(
        self,
        key: str,
        max_budget: float | None = None,
        rpm_limit: int | None = None,
        models: list[str] | None = None,
    ) -> dict:
        payload = {"key": key}
        if max_budget is not None:
            payload["max_budget"] = max_budget
        if rpm_limit is not None:
            payload["rpm_limit"] = rpm_limit
        if models is not None:
            payload["models"] = models

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/key/update", headers=self._headers, json=payload, timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()
