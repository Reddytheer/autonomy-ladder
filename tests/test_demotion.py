"""Demotion, probation, cooldown, and the post-send loop (SPEC §4, §14)."""

from __future__ import annotations

from datetime import UTC, datetime

from autonomy_ladder.autonomy.controller import AutonomyController
from autonomy_ladder.autonomy.ledger import Decision, Ledger, TransitionReason
from autonomy_ladder.autonomy.tiers import Standing, Tier
from autonomy_ladder.domain import CampaignType, Dimension, SegmentBand
from autonomy_ladder.records import DeliverabilityReport

from .conftest import make_controller, make_eval

CT = CampaignType.NEWSLETTER


def _promote_to_bounded(c: AutonomyController) -> None:
    for _ in range(25):
        c.process_run(make_eval(passed=True, campaign_type=CT))
    assert c.state(CT).tier is Tier.BOUNDED


def test_critical_failure_demotes_and_opens_probation() -> None:
    c = make_controller()
    _promote_to_bounded(c)
    d = c.process_run(
        make_eval(passed=False, critical_fail=Dimension.CLAIM_GROUNDEDNESS, campaign_type=CT)
    )
    assert d.decision is Decision.HUMAN_REVIEW  # blocks the action
    assert d.demoted is True
    assert d.demotion_reason is TransitionReason.DEMOTION_CRITICAL_PRESEND
    state = c.state(CT)
    assert state.tier is Tier.ASSIST
    assert state.standing is Standing.PROBATION
    # The decision tells the queue to downgrade in-flight campaigns (never cancel/send).
    assert any("downgraded to review" in r for r in d.rationale)


def test_critical_failure_at_tier_zero_has_nothing_to_demote() -> None:
    c = make_controller()
    d = c.process_run(
        make_eval(passed=False, critical_fail=Dimension.SEGMENT_CORRECTNESS, campaign_type=CT)
    )
    assert d.demoted is False
    assert c.state(CT).tier is Tier.ASSIST
    assert c.state(CT).standing is Standing.ACTIVE  # no probation with no autonomy to lose


def test_brand_voice_failure_does_not_demote_but_fails_the_run() -> None:
    """WEIGHTED dimension: a low brand_voice fails the run (no promotion credit) but
    does not trigger demotion (SPEC §3)."""
    c = make_controller()
    _promote_to_bounded(c)
    d = c.process_run(make_eval(passed=False, brand_voice=0.50, campaign_type=CT))
    assert d.passed is False
    assert d.demoted is False
    assert c.state(CT).tier is Tier.BOUNDED  # standing intact


def test_deliverability_breach_demotes() -> None:
    c = make_controller()
    _promote_to_bounded(c)
    report = DeliverabilityReport(
        run_id="r1",
        campaign_type=CT.value,
        spam_complaint_rate=0.001,  # > 0.0008
        unsubscribe_rate=0.0,
        bounce_rate=0.0,
    )
    d = c.process_deliverability(report)
    assert d is not None and d.demoted is True
    assert d.demotion_reason is TransitionReason.DEMOTION_DELIVERABILITY
    assert c.state(CT).tier is Tier.ASSIST
    assert c.state(CT).standing is Standing.PROBATION


def test_deliverability_within_thresholds_is_a_noop() -> None:
    c = make_controller()
    _promote_to_bounded(c)
    report = DeliverabilityReport(
        run_id="r1",
        campaign_type=CT.value,
        spam_complaint_rate=0.0005,
        unsubscribe_rate=0.002,
        bounce_rate=0.004,
    )
    assert c.process_deliverability(report) is None
    assert c.state(CT).tier is Tier.BOUNDED


def test_probation_pass_restores_tier_one_with_cooldown() -> None:
    c = make_controller()
    _promote_to_bounded(c)
    c.process_run(
        make_eval(passed=False, critical_fail=Dimension.SEGMENT_CORRECTNESS, campaign_type=CT)
    )
    assert c.state(CT).standing is Standing.PROBATION
    d = c.run_probation_challenge(CT, successes=25, n=25)  # Wilson 0.8668 >= 0.85
    assert d.promoted is True
    state = c.state(CT)
    assert state.tier is Tier.BOUNDED
    assert state.standing is Standing.ACTIVE
    assert state.cooldown_remaining == 20
    assert state.promotion_eligible is False  # cooldown blocks promotion


def test_probation_fail_flags_investigation() -> None:
    c = make_controller()
    _promote_to_bounded(c)
    c.process_run(
        make_eval(passed=False, critical_fail=Dimension.SEGMENT_CORRECTNESS, campaign_type=CT)
    )
    d = c.run_probation_challenge(CT, successes=10, n=20)  # Wilson well below 0.85
    assert d.promoted is False
    state = c.state(CT)
    assert state.tier is Tier.ASSIST
    assert state.standing is Standing.INVESTIGATION_REQUIRED


def test_cannot_run_probation_when_not_on_probation() -> None:
    c = make_controller()
    try:
        c.run_probation_challenge(CT, successes=25, n=25)
    except ValueError as e:
        assert "not probation" in str(e)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_cooldown_blocks_promotion_until_it_elapses() -> None:
    """Even with a gate-clearing history, promotion is blocked for the whole
    cooldown window, then fires exactly when cooldown reaches zero (SPEC §4)."""
    from .conftest import advancing_clock

    # Advancing clock so the cooldown-period auto-sends aren't rate-limited (which
    # would exclude them from the Wilson window under ADR 0008).
    c = make_controller(clock=advancing_clock())
    lg: Ledger = c._ledger
    old = datetime(2025, 12, 1, tzinfo=UTC).isoformat()
    # A rich passing history that would clear the 1->2 gate (>=50 runs, all pass).
    for i in range(55):
        lg.append_run(
            campaign_type=CT,
            ts=old,
            run_id=f"h{i}",
            passed=True,
            decision=Decision.HUMAN_REVIEW,
            tier_at_decision=Tier.BOUNDED,
            segment=SegmentBand.ENGAGED_30D.value,
        )
    # Hand-craft a restore-with-cooldown transition (as probation-pass would).
    lg.append_transition(
        campaign_type=CT,
        ts=old,
        reason=TransitionReason.PROBATION_PASSED,
        from_tier=Tier.ASSIST,
        to_tier=Tier.BOUNDED,
        standing_after=Standing.ACTIVE,
        cooldown_after=20,
    )
    assert c.state(CT).cooldown_remaining == 20
    # Gate is met, but cooldown must block a direct promotion attempt.
    did, _ = c._maybe_promote(CT, old, "x")
    assert did is False

    promoted_at = None
    for i in range(20):
        d = c.process_run(make_eval(passed=True, campaign_type=CT))
        if d.promotion_to is Tier.SUPERVISED:
            promoted_at = i + 1
    assert promoted_at == 20  # fires the moment cooldown hits zero, not before
