"""Segment Analyst — resolves the audience the campaign should target (SPEC §6).

Runs on Haiku: this is a bounded classification task, not open-ended generation,
so it is a good candidate for the cheaper model (the routing report quantifies the
saving). It reads the brief and returns the segment band the campaign targets.
"""

from __future__ import annotations

from autonomy_ladder.agents._llm import call_text, parse_json
from autonomy_ladder.domain import SegmentBand
from autonomy_ladder.evals.judges import MODEL_HAIKU
from autonomy_ladder.evals.llm import LLMClient
from autonomy_ladder.observability.cost import CostTracker
from autonomy_ladder.records import CampaignBrief

_SYSTEM = "You are the segment analyst for an email marketing team. You resolve audiences."


def resolve(
    brief: CampaignBrief, client: LLMClient, cost: CostTracker | None = None
) -> SegmentBand:
    """Return the segment band the campaign should target.

    Falls back to the brief's requested segment if the model returns an unknown
    band — the analyst may inform targeting, but it can never invent a band that
    the domain does not define.
    """
    bands = ", ".join(b.value for b in SegmentBand)
    user = (
        f"Campaign type: {brief.campaign_type}\n"
        f"Goal: {brief.goal}\n"
        f"Requested segment: {brief.requested_segment.value}\n"
        f"Valid segment bands: {bands}\n"
        'Return JSON: {"segment": "<one band>", "reasoning": "<one sentence>"}.'
    )
    raw = call_text(
        client,
        span_name="agent.segment_analyst",
        model=MODEL_HAIKU,
        system=_SYSTEM,
        user=user,
        cost=cost,
    )
    try:
        value = str(parse_json(raw).get("segment", "")).strip()
        return SegmentBand(value)
    except (ValueError, KeyError):
        return brief.requested_segment
