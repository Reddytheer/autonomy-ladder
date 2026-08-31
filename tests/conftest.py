"""Shared test fixtures and builders for the autonomy core.

These helpers let each test say what it means ("a passing run", "a run with a
segment-correctness failure") without restating the four-dimension schema every
time. No test here touches an LLM or the network.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from autonomy_ladder.autonomy.controller import AutonomyController
from autonomy_ladder.autonomy.ledger import Ledger
from autonomy_ladder.config import (
    BrandPolicy,
    Constraints,
    DeliverabilityTriggers,
    ProbationConfig,
    PromotionGate,
    TiersConfig,
)
from autonomy_ladder.domain import (
    BRAND_VOICE_PASS_THRESHOLD,
    CampaignType,
    Dimension,
    SegmentBand,
    Verdict,
)
from autonomy_ladder.records import DimensionResult, RunEvaluation


def make_tiers_config() -> TiersConfig:
    """The spec's vendor config, as resolved in docs/open-questions.md (min_runs 25)."""
    return TiersConfig(
        promotion={
            "0->1": PromotionGate(window=50, min_runs=25, wilson_lower_bound_min=0.85),
            "1->2": PromotionGate(window=100, min_runs=50, wilson_lower_bound_min=0.92),
        },
        constraints=Constraints(max_discount_pct=25, max_autonomous_sends_per_type_per_24h=3),
        deliverability_triggers=DeliverabilityTriggers(
            spam_complaint_rate_max=0.0008,
            unsubscribe_rate_max=0.003,
            bounce_rate_max=0.005,
        ),
        probation=ProbationConfig(cooldown_runs=20),
    )


def advancing_clock(step_hours: int = 25) -> Callable[[], datetime]:
    """A clock that jumps forward each call, so successive auto-sends land in
    separate 24h windows and never trip the rate limit (which now excludes runs
    from the Wilson window). Used by high-volume promotion tests."""
    state = {"n": -1}

    def _clock() -> datetime:
        state["n"] += 1
        return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=step_hours * state["n"])

    return _clock


def make_controller(
    max_allowed_tier: int = 2, clock: Callable[[], datetime] | None = None
) -> AutonomyController:
    """A controller over a fresh in-memory ledger. Fixed clock unless one is given."""
    ledger = Ledger(":memory:")
    return AutonomyController(
        ledger=ledger,
        tiers_config=make_tiers_config(),
        brand_policy=BrandPolicy(max_allowed_tier=max_allowed_tier),
        clock=clock or (lambda: datetime(2026, 1, 1, tzinfo=UTC)),
    )


_counter = {"n": 0}


def make_eval(
    *,
    passed: bool = True,
    segment: SegmentBand = SegmentBand.ENGAGED_30D,
    discount_pct: float = 0.0,
    critical_fail: Dimension | None = None,
    brand_voice: float | None = None,
    campaign_type: CampaignType = CampaignType.NEWSLETTER,
    run_id: str | None = None,
    reasoning: str = "",
) -> RunEvaluation:
    """Build a RunEvaluation. `passed` sets a coherent all-pass or a chosen failure.

    - passed=True  -> both criticals pass, brand_voice=0.90 (>= 0.75).
    - passed=False -> if critical_fail given, that critical dim fails; otherwise
      brand_voice drops below 0.75 (the WEIGHTED failure path).
    """
    _counter["n"] += 1
    rid = run_id or f"run-{_counter['n']}"

    bv = brand_voice if brand_voice is not None else (0.90 if passed else 0.50)
    crit_seg = Verdict.PASS
    crit_claim = Verdict.PASS
    if not passed and critical_fail is not None:
        if critical_fail is Dimension.SEGMENT_CORRECTNESS:
            crit_seg = Verdict.FAIL
        elif critical_fail is Dimension.CLAIM_GROUNDEDNESS:
            crit_claim = Verdict.FAIL
        bv = brand_voice if brand_voice is not None else 0.90

    def _d(dim: Dimension, verdict: Verdict, score: float) -> DimensionResult:
        return DimensionResult(dimension=dim, score=score, verdict=verdict, reasoning=reasoning)

    dims = {
        Dimension.SEGMENT_CORRECTNESS: _d(
            Dimension.SEGMENT_CORRECTNESS, crit_seg, 1.0 if crit_seg is Verdict.PASS else 0.0
        ),
        Dimension.CLAIM_GROUNDEDNESS: _d(
            Dimension.CLAIM_GROUNDEDNESS, crit_claim, 1.0 if crit_claim is Verdict.PASS else 0.0
        ),
        Dimension.BRAND_VOICE: _d(
            Dimension.BRAND_VOICE,
            Verdict.PASS if bv >= BRAND_VOICE_PASS_THRESHOLD else Verdict.FAIL,
            bv,
        ),
        Dimension.STRUCTURE_QUALITY: _d(Dimension.STRUCTURE_QUALITY, Verdict.PASS, 0.8),
    }
    return RunEvaluation(
        run_id=rid,
        campaign_type=campaign_type.value,
        segment=segment,
        discount_pct=discount_pct,
        dimensions=dims,
    )


@pytest.fixture
def controller() -> AutonomyController:
    return make_controller()
