"""Token/cost accounting and the model-routing report (SPEC §6, §8).

Why this exists: SPEC §6 routes cheap, well-scoped work to Haiku and reserves
Sonnet for the harder calls, then asks for "a routing report showing cost delta
and the accuracy impact (or lack of it) of the Haiku assignments." This module
tracks per-model token usage and produces exactly that report.

Prices are list prices in USD per million tokens and are declared here as a single
editable table — they are operational inputs, not spec thresholds (see
docs/open-questions.md OQ-3).
"""

from __future__ import annotations

from pydantic import BaseModel

# USD per 1,000,000 tokens (input, output). List prices; adjust as they change.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
}

# The model each Haiku-routed span would otherwise have run on, for the delta.
_HAIKU_ALTERNATIVE = "claude-sonnet-4-6"


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for the mock/replay path.

    Live runs can substitute the API's reported usage; the routing report's value
    is the relative delta between models, which this preserves.
    """
    return max(1, len(text) // 4)


def cost_for(model: str, input_tokens: int, output_tokens: int) -> float:
    """Cost in USD for one call. Unknown models cost 0 (and are reported as such)."""
    if model not in PRICES:
        return 0.0
    in_price, out_price = PRICES[model]
    return (input_tokens / 1e6) * in_price + (output_tokens / 1e6) * out_price


class ModelUsage(BaseModel):
    model_config = {"frozen": True}

    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class RoutingReport(BaseModel):
    model_config = {"frozen": True}

    per_model: list[ModelUsage]
    total_cost_usd: float
    haiku_cost_usd: float
    haiku_cost_if_sonnet_usd: float
    savings_usd: float
    accuracy_note: str = ""


class CostTracker:
    """Accumulates per-model usage across a run."""

    def __init__(self) -> None:
        self._calls: dict[str, int] = {}
        self._in: dict[str, int] = {}
        self._out: dict[str, int] = {}

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self._calls[model] = self._calls.get(model, 0) + 1
        self._in[model] = self._in.get(model, 0) + input_tokens
        self._out[model] = self._out.get(model, 0) + output_tokens

    def per_model(self) -> list[ModelUsage]:
        return [
            ModelUsage(
                model=m,
                calls=self._calls[m],
                input_tokens=self._in[m],
                output_tokens=self._out[m],
                cost_usd=round(cost_for(m, self._in[m], self._out[m]), 6),
            )
            for m in sorted(self._calls)
        ]

    def routing_report(self, accuracy_note: str = "") -> RoutingReport:
        """Cost delta of the Haiku assignments vs running them on Sonnet."""
        per = self.per_model()
        total = sum(u.cost_usd for u in per)

        haiku_in = sum(v for m, v in self._in.items() if "haiku" in m)
        haiku_out = sum(v for m, v in self._out.items() if "haiku" in m)
        haiku_cost = sum(u.cost_usd for u in per if "haiku" in u.model)
        haiku_if_sonnet = cost_for(_HAIKU_ALTERNATIVE, haiku_in, haiku_out)

        return RoutingReport(
            per_model=per,
            total_cost_usd=round(total, 6),
            haiku_cost_usd=round(haiku_cost, 6),
            haiku_cost_if_sonnet_usd=round(haiku_if_sonnet, 6),
            savings_usd=round(haiku_if_sonnet - haiku_cost, 6),
            accuracy_note=accuracy_note,
        )
