"""Claim Verifier — the independent claim-grounding check (SPEC §6, P2).

This is a *separate* call from the Copy Composer, with its own prompt and no access
to the composer's reasoning — a generator grading its own claims is the weakest
possible check. Runs on Haiku. It delegates to the shared claim-groundedness judge
so the pipeline and the eval gate score claims identically.
"""

from __future__ import annotations

from autonomy_ladder.domain import Dimension
from autonomy_ladder.evals.judges import JudgeContext, judge_dimension
from autonomy_ladder.evals.llm import LLMClient
from autonomy_ladder.records import CampaignContent, DimensionResult


def verify(content: CampaignContent, ctx: JudgeContext, client: LLMClient) -> DimensionResult:
    """Independently verify the campaign's claims against the catalog."""
    return judge_dimension(Dimension.CLAIM_GROUNDEDNESS, content, ctx, client)
