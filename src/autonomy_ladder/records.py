"""Neutral data records shared across layers.

Why this module exists: the evaluator (``evals``), the agents, and the autonomy
controller all need to speak about "one campaign run and how it scored" without
depending on each other. Putting these records here keeps the dependency arrows
clean — in particular the safety-critical ``autonomy`` package imports from here,
never from ``evals`` or ``agents`` (which would invert the trust relationship).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from autonomy_ladder.domain import (
    BRAND_VOICE_PASS_THRESHOLD,
    CRITICAL_DIMENSIONS,
    Dimension,
    SegmentBand,
    Verdict,
)


class DimensionResult(BaseModel):
    """One dimension's score and judgement (SPEC §7 judge output schema)."""

    model_config = {"frozen": True}

    dimension: Dimension
    score: float = Field(ge=0.0, le=1.0)
    verdict: Verdict
    reasoning: str = ""
    evidence: list[str] = Field(default_factory=list)


class RunEvaluation(BaseModel):
    """The complete evaluation of one campaign run — the controller's input.

    This is what the deterministic checks + LLM judges produce and what the
    controller consumes to decide AUTO_SEND vs HUMAN_REVIEW. It carries the
    campaign's targeting facts (segment, discount) because those drive hard
    constraint checks that have nothing to do with the LLM scores.
    """

    model_config = {"frozen": True}

    run_id: str
    campaign_type: str
    segment: SegmentBand
    discount_pct: float = 0.0
    dimensions: dict[Dimension, DimensionResult]

    def result(self, dim: Dimension) -> DimensionResult | None:
        return self.dimensions.get(dim)

    @property
    def critical_failures(self) -> list[Dimension]:
        """CRITICAL dimensions that failed (SPEC §3)."""
        failed = []
        for dim in CRITICAL_DIMENSIONS:
            r = self.dimensions.get(dim)
            if r is None or r.verdict is Verdict.FAIL:
                failed.append(dim)
        return failed

    @property
    def brand_voice_score(self) -> float | None:
        r = self.dimensions.get(Dimension.BRAND_VOICE)
        return None if r is None else r.score

    @property
    def passed(self) -> bool:
        """A run passes iff both CRITICAL dims pass AND brand_voice >= 0.75 (SPEC §3).

        This is the single definition of a "successful run" used everywhere:
        Wilson statistics, the review-queue lane split, and the gate all agree on
        it, because they all call this property.
        """
        if self.critical_failures:
            return False
        bv = self.brand_voice_score
        return bv is not None and bv >= BRAND_VOICE_PASS_THRESHOLD


class DeliverabilityReport(BaseModel):
    """Post-send outcome metrics for one campaign send (SPEC §4).

    The closed loop: pre-send evaluation decides whether the agent *may* act;
    these numbers decide whether it *keeps* that standing.
    """

    model_config = {"frozen": True}

    run_id: str
    campaign_type: str
    spam_complaint_rate: float = Field(ge=0.0)
    unsubscribe_rate: float = Field(ge=0.0)
    bounce_rate: float = Field(ge=0.0)


class CampaignBrief(BaseModel):
    """The input to the agent: what campaign to produce, for whom (SPEC §6)."""

    model_config = {"frozen": True}

    campaign_type: str
    goal: str
    requested_segment: SegmentBand
    discount_pct: float = 0.0
    product_ids: list[str] = Field(default_factory=list)
    notes: str = ""


class CampaignContent(BaseModel):
    """The agent's output: the campaign to be judged and, if permitted, sent.

    This is the artifact the independent checks grade and the controller governs.
    ``claims`` are the factual statements the Claim Verifier must ground against
    the catalog; ``target_segment`` is the audience the Segment Analyst resolved.
    """

    model_config = {"frozen": True}

    subject: str
    preview_text: str = ""
    body: str
    cta_text: str = ""
    cta_url: str = ""
    claims: list[str] = Field(default_factory=list)
    target_segment: SegmentBand
    discount_pct: float = 0.0
    product_ids: list[str] = Field(default_factory=list)
