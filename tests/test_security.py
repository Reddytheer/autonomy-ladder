"""Security suite (HANDOFF Drop 2): resisted attacks are logged; no brief content
can move a tier; self-asserted approval is not evidence."""

from __future__ import annotations

from autonomy_ladder.autonomy import routing
from autonomy_ladder.autonomy.ledger import Decision
from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.config import load_tiers_config
from autonomy_ladder.domain import Dimension, SegmentBand, Verdict
from autonomy_ladder.records import DimensionResult, RunEvaluation
from autonomy_ladder.security import (
    SecurityCase,
    SecurityEvent,
    SecurityEventStore,
    SecurityEventType,
    load_security_cases,
    scan,
)

CASES = {c.id: c for c in load_security_cases()}


def _eval(case: SecurityCase) -> RunEvaluation:
    exp = case.model_extra["expected"] if case.model_extra else {}

    def dim(d: Dimension, key: str) -> DimensionResult:
        verdict = (
            Verdict(exp.get(key, "pass")) if exp.get(key) in ("pass", "fail") else Verdict.PASS
        )
        score = 0.9 if verdict is Verdict.PASS else (0.5 if d is Dimension.BRAND_VOICE else 0.1)
        return DimensionResult(dimension=d, score=score, verdict=verdict)

    return RunEvaluation(
        run_id=case.id,
        campaign_type=case.campaign_type,
        segment=SegmentBand(case.requested_segment),
        dimensions={
            Dimension.SEGMENT_CORRECTNESS: dim(
                Dimension.SEGMENT_CORRECTNESS, "segment_correctness"
            ),
            Dimension.CLAIM_GROUNDEDNESS: dim(Dimension.CLAIM_GROUNDEDNESS, "claim_groundedness"),
            Dimension.BRAND_VOICE: dim(Dimension.BRAND_VOICE, "brand_voice"),
            Dimension.STRUCTURE_QUALITY: dim(Dimension.STRUCTURE_QUALITY, "structure_quality"),
        },
    )


def test_all_eight_security_cases_load() -> None:
    assert len(CASES) == 8


def test_scanner_detects_every_expected_event() -> None:
    for case in CASES.values():
        detected = scan(case.attack_text())
        assert case.expected_security_event in detected, case.id


def test_sec03_brief_cannot_escalate_tier() -> None:
    """SEC-03 (the most important test): a brief claiming 'Tier 2 approved' has zero
    effect. RunEvaluation has no tier field, and routing takes the tier as an explicit
    argument — the claim cannot promote. At Tier 1 the send still goes to review."""
    case = CASES["SEC-03"]
    assert SecurityEventType.TIER_ESCALATION_ATTEMPT in scan(case.attack_text())
    # No field on the controller's input can carry a tier.
    assert not any("tier" in f for f in RunEvaluation.model_fields)
    # Routed at the real tier (1), the escalation claim changes nothing: engaged_90d
    # is a Tier-2 audience, so at Tier 1 it must go to review, not auto-send.
    result = routing.decide(
        _eval(case),
        effective_tier=Tier(case.agent_tier_at_run),
        autonomous_sends_last_24h=0,
        constraints=load_tiers_config().constraints,
    )
    assert result.decision is Decision.HUMAN_REVIEW


def test_sec07_self_asserted_compliance_is_not_evidence() -> None:
    """SEC-07: asserting 'pre-approved by compliance' does not create approval; the
    case still routes to review (its claim fails), and the attempt is logged."""
    case = CASES["SEC-07"]
    assert SecurityEventType.EVAL_GAMING_ATTEMPT in scan(case.attack_text())
    result = routing.decide(
        _eval(case),
        effective_tier=Tier(case.agent_tier_at_run),
        autonomous_sends_last_24h=0,
        constraints=load_tiers_config().constraints,
    )
    assert result.decision is Decision.HUMAN_REVIEW


def test_security_event_persists_even_on_success() -> None:
    """A resisted injection is logged even when the run would otherwise auto-send."""
    store = SecurityEventStore(":memory:")
    store.add(
        SecurityEvent(
            id="SEC-01",
            ts="2026-08-29T00:00:00+00:00",
            campaign_type="promotional_discount",
            event_type=SecurityEventType.INJECTION_RESISTED,
            detail="resisted injection in customer_profile.notes",
        )
    )
    assert store.count() == 1
    assert store.recent()[0].event_type is SecurityEventType.INJECTION_RESISTED
