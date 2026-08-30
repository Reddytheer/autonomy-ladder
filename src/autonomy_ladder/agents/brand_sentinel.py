"""Brand Sentinel — the independent brand-voice check (SPEC §6, P2).

A separate Sonnet call with its own rubric and no access to the composer's
reasoning. It guards the brand voice; its score feeds the WEIGHTED brand_voice
dimension (pass floor 0.75). Delegates to the shared brand-voice judge so the
pipeline and the eval gate agree.
"""

from __future__ import annotations

from autonomy_ladder.domain import Dimension
from autonomy_ladder.evals.judges import JudgeContext, judge_dimension
from autonomy_ladder.evals.llm import LLMClient
from autonomy_ladder.records import CampaignContent, DimensionResult


def review(content: CampaignContent, ctx: JudgeContext, client: LLMClient) -> DimensionResult:
    """Independently judge the campaign's brand voice."""
    return judge_dimension(Dimension.BRAND_VOICE, content, ctx, client)
