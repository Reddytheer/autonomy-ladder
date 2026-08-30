"""Shared LLM-call plumbing for the agents.

One place that wraps every generator call in a GenAI span, records estimated token
usage and cost, keeps prompt/completion content in span events, and parses JSON
responses tolerantly. Keeping this uniform is what makes the routing report and
the trace complete.
"""

from __future__ import annotations

import json
from typing import Any

from autonomy_ladder.evals.llm import LLMClient
from autonomy_ladder.observability.cost import CostTracker, estimate_tokens
from autonomy_ladder.observability.otel import llm_span, record_content, record_usage


def call_text(
    client: LLMClient,
    *,
    span_name: str,
    model: str,
    system: str,
    user: str,
    cost: CostTracker | None = None,
) -> str:
    """Make one LLM call inside a fully-instrumented span; return the raw text."""
    with llm_span(span_name, model=model) as span:
        text = client.complete(model=model, system=system, user=user)
        in_tokens = estimate_tokens(system) + estimate_tokens(user)
        out_tokens = estimate_tokens(text)
        record_usage(span, input_tokens=in_tokens, output_tokens=out_tokens)
        record_content(span, system=system, user=user, completion=text)
        if cost is not None:
            cost.record(model, in_tokens, out_tokens)
    return text


def parse_json(raw: str) -> dict[str, Any]:
    """Parse a JSON object from a model response, tolerating stray code fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") : text.rfind("}") + 1]
    result: dict[str, Any] = json.loads(text)
    return result
