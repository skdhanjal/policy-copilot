"""LLMCall: a record of one real model call, built to answer the question
D26 raised concretely -- "we proved caching saves ~90% on repeated
prefixes, but that saving is currently invisible to us."

This is deliberately built AFTER we had a real, measured need for it,
not speculatively ahead of time -- the empty app/telemetry/ directory
from an earlier session stretch is exactly what happens when scaffolding
gets described before there's a concrete thing to instrument.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# USD per 1M tokens. Source: OpenAI's published pricing for gpt-4o-mini,
# confirmed against our own PRICE_TABLE precedent from earlier design
# discussion. Revisit if the underlying model/pricing changes --
# this is a config value, not a fact that stays true forever.
PRICE_PER_1M = {
    "fast": {"input": 0.80, "cached_input": 0.08, "output": 4.00},
}


@dataclass(slots=True)
class LLMCall:
    alias: str                    # 'fast' -- never a vendor model name (ADR-4)
    question: str

    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0  # from usage.prompt_tokens_details.cached_tokens
    completion_tokens: int = 0

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def uncached_prompt_tokens(self) -> int:
        return max(0, self.prompt_tokens - self.cached_prompt_tokens)

    @property
    def cost_usd(self) -> float:
        """Cost if NONE of this had been cached -- the counterfactual,
        useful for computing savings, not the actual bill on its own."""
        prices = PRICE_PER_1M.get(self.alias)
        if not prices:
            return 0.0
        return (
            self.prompt_tokens * prices["input"]
            + self.completion_tokens * prices["output"]
        ) / 1_000_000

    @property
    def actual_cost_usd(self) -> float:
        """What this call actually cost, given real cached-token pricing."""
        prices = PRICE_PER_1M.get(self.alias)
        if not prices:
            return 0.0
        return (
            self.uncached_prompt_tokens * prices["input"]
            + self.cached_prompt_tokens * prices["cached_input"]
            + self.completion_tokens * prices["output"]
        ) / 1_000_000

    @property
    def savings_usd(self) -> float:
        return self.cost_usd - self.actual_cost_usd

    @property
    def cache_hit_rate(self) -> float:
        if self.prompt_tokens == 0:
            return 0.0
        return self.cached_prompt_tokens / self.prompt_tokens
