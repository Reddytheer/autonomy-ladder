"""Orchestrator — plans, delegates, assembles, and revises (SPEC §6).

This is the agent-side entry point. It runs the generator workers, grounds claims
with the Catalog Lookup tool, scores the output with the independent checks
(deterministic Stage 1 then the judges), and runs the evaluator-optimizer loop. It
returns a scored :class:`EvaluatedRun` — it never decides autonomy; that is the
controller's job (P1).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from autonomy_ladder.agents import (
    brand_sentinel,
    catalog_lookup,
    claim_verifier,
    copy_composer,
    segment_analyst,
)
from autonomy_ladder.agents.revision import MAX_REVISIONS, build_feedback
from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.config import Constraints
from autonomy_ladder.data.loaders import BrandRules, Product, catalog_index, load_brand_rules
from autonomy_ladder.domain import Dimension, Verdict
from autonomy_ladder.evals.deterministic import (
    DeterministicReport,
    FindingCode,
    run_deterministic_checks,
)
from autonomy_ladder.evals.judges import JudgeContext, judge_dimension
from autonomy_ladder.evals.llm import LLMClient
from autonomy_ladder.observability.cost import CostTracker
from autonomy_ladder.observability.otel import get_tracer
from autonomy_ladder.records import CampaignBrief, CampaignContent, DimensionResult, RunEvaluation


class EvaluatedRun(BaseModel):
    """Everything the orchestrator produced for one brief."""

    model_config = {"frozen": True}

    run_id: str
    content: CampaignContent
    evaluation: RunEvaluation
    deterministic: DeterministicReport
    revisions: int


class Orchestrator:
    """Assembles the agent pipeline for one campaign brief."""

    def __init__(
        self,
        client: LLMClient,
        constraints: Constraints,
        brand_rules: BrandRules | None = None,
        catalog: dict[str, Product] | None = None,
    ) -> None:
        self._client = client
        self._constraints = constraints
        self._brand = brand_rules or load_brand_rules()
        self._catalog = catalog or catalog_index()

    def run(
        self,
        brief: CampaignBrief,
        tier: Tier,
        run_id: str | None = None,
        cost: CostTracker | None = None,
        max_revisions: int | None = None,
    ) -> EvaluatedRun:
        """Production entry point. Never renders known-false content.

        There is deliberately no ``faithful`` parameter here — faithful-render is
        eval-only and reachable solely through :meth:`run_eval` (ADR 0011).
        """
        return self._run(
            brief, tier, run_id=run_id, cost=cost, max_revisions=max_revisions, faithful=False
        )

    def run_eval(
        self,
        brief: CampaignBrief,
        tier: Tier,
        *,
        run_id: str | None = None,
        cost: CostTracker | None = None,
        max_revisions: int = 0,
        faithful: bool = False,
    ) -> EvaluatedRun:
        """EVAL-ONLY entry point for the fixtures harness (ADR 0011).

        ``faithful=True`` makes the composer emit the brief's claims verbatim —
        known-false content used to measure judge recall. ``max_revisions``
        defaults to 0 so the campaign is measured as briefed, before the
        evaluator-optimizer loop repairs it. MUST NOT be called from the
        production pipeline; guarded by tests/test_faithful_render_eval_only.py.
        """
        return self._run(
            brief, tier, run_id=run_id, cost=cost, max_revisions=max_revisions, faithful=faithful
        )

    def _run(
        self,
        brief: CampaignBrief,
        tier: Tier,
        *,
        run_id: str | None,
        cost: CostTracker | None,
        max_revisions: int | None,
        faithful: bool,
    ) -> EvaluatedRun:
        revision_budget = MAX_REVISIONS if max_revisions is None else max_revisions
        run_id = run_id or uuid.uuid4().hex[:12]
        with get_tracer().start_as_current_span("agent.orchestrator") as span:
            span.set_attribute("run.id", run_id)
            span.set_attribute("campaign.type", brief.campaign_type)

            segment = segment_analyst.resolve(brief, self._client, cost)
            products = catalog_lookup.lookup(brief.product_ids)
            facts = catalog_lookup.facts_block(products)

            content = copy_composer.compose(
                brief, segment, facts, self._brand, self._client, cost, faithful=faithful
            )
            evaluation, det = self._evaluate(brief, content, tier, run_id)

            revisions = 0
            while not evaluation.passed and revisions < revision_budget:
                revisions += 1
                feedback = build_feedback(evaluation, det)
                content = copy_composer.compose(
                    brief,
                    segment,
                    facts,
                    self._brand,
                    self._client,
                    cost,
                    feedback=feedback,
                    faithful=faithful,
                )
                evaluation, det = self._evaluate(brief, content, tier, run_id)

            span.set_attribute("run.passed", evaluation.passed)
            span.set_attribute("run.revisions", revisions)
            return EvaluatedRun(
                run_id=run_id,
                content=content,
                evaluation=evaluation,
                deterministic=det,
                revisions=revisions,
            )

    def _evaluate(
        self, brief: CampaignBrief, content: CampaignContent, tier: Tier, run_id: str
    ) -> tuple[RunEvaluation, DeterministicReport]:
        """Stage 1 (deterministic) then Stage 2 (judges); fold Stage 1 into scores."""
        det = run_deterministic_checks(
            content,
            tier=tier,
            constraints=self._constraints,
            brand_rules=self._brand,
            catalog=self._catalog,
        )
        ctx = JudgeContext(brief=brief, brand_rules=self._brand, catalog=self._catalog)
        dims: dict[Dimension, DimensionResult] = {
            Dimension.SEGMENT_CORRECTNESS: judge_dimension(
                Dimension.SEGMENT_CORRECTNESS, content, ctx, self._client
            ),
            Dimension.CLAIM_GROUNDEDNESS: claim_verifier.verify(content, ctx, self._client),
            Dimension.BRAND_VOICE: brand_sentinel.review(content, ctx, self._client),
            Dimension.STRUCTURE_QUALITY: judge_dimension(
                Dimension.STRUCTURE_QUALITY, content, ctx, self._client
            ),
        }
        dims = _apply_deterministic_gating(dims, det)
        evaluation = RunEvaluation(
            run_id=run_id,
            campaign_type=brief.campaign_type,
            segment=content.target_segment,
            discount_pct=content.discount_pct,
            dimensions=dims,
        )
        return evaluation, det


# Stage-1 findings that indicate a content-quality problem the judges might miss.
# These deterministically drag the relevant dimension to fail, so a clean judge
# score can never paper over a hard defect (P3).
_BRAND_BLOCKING = {FindingCode.PROHIBITED_TERM, FindingCode.MISSING_FIELD, FindingCode.INVALID_LINK}
_CLAIM_BLOCKING = {FindingCode.UNKNOWN_PRODUCT}


def _apply_deterministic_gating(
    dims: dict[Dimension, DimensionResult], det: DeterministicReport
) -> dict[Dimension, DimensionResult]:
    codes = {f.code for f in det.findings}
    out = dict(dims)
    if codes & _BRAND_BLOCKING:
        out[Dimension.BRAND_VOICE] = out[Dimension.BRAND_VOICE].model_copy(
            update={"verdict": Verdict.FAIL, "score": min(out[Dimension.BRAND_VOICE].score, 0.4)}
        )
    if codes & _CLAIM_BLOCKING:
        out[Dimension.CLAIM_GROUNDEDNESS] = out[Dimension.CLAIM_GROUNDEDNESS].model_copy(
            update={"verdict": Verdict.FAIL, "score": 0.0}
        )
    return out
