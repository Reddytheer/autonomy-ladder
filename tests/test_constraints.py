"""Hard constraint enforcement (SPEC §4) — both the pure checks and their effect
through the controller."""

from __future__ import annotations

from autonomy_ladder.autonomy import constraints as C
from autonomy_ladder.autonomy.ledger import Decision
from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.config import Constraints, DeliverabilityTriggers
from autonomy_ladder.domain import CampaignType, SegmentBand
from autonomy_ladder.records import DeliverabilityReport

from .conftest import make_controller, make_eval

CT = CampaignType.NEWSLETTER
CONS = Constraints(max_discount_pct=25, max_autonomous_sends_per_type_per_24h=3)


def test_discount_ceiling() -> None:
    assert C.check_discount(20, CONS) == []
    assert C.check_discount(25, CONS) == []  # at the ceiling is allowed
    v = C.check_discount(25.1, CONS)
    assert v and v[0].code is C.ConstraintCode.DISCOUNT_EXCEEDS_CEILING


def test_segment_eligibility_by_tier() -> None:
    assert C.check_segment_eligibility(Tier.BOUNDED, SegmentBand.ENGAGED_30D) == []
    assert C.check_segment_eligibility(Tier.SUPERVISED, SegmentBand.ENGAGED_90D) == []
    # engaged_60d not allowed at BOUNDED
    v = C.check_segment_eligibility(Tier.BOUNDED, SegmentBand.ENGAGED_60D)
    assert v and v[0].code is C.ConstraintCode.SEGMENT_NOT_ELIGIBLE_FOR_TIER
    # ASSIST allows nothing
    v = C.check_segment_eligibility(Tier.ASSIST, SegmentBand.ENGAGED_30D)
    assert v and v[0].code is C.ConstraintCode.SEGMENT_NOT_ELIGIBLE_FOR_TIER


def test_never_autonomous_segments_blocked_at_every_tier() -> None:
    for seg in (
        SegmentBand.ENGAGED_180D,
        SegmentBand.ALL_SUBSCRIBERS,
        SegmentBand.FIRST_TIME_BUYERS,
        SegmentBand.LAPSED,
    ):
        for tier in (Tier.ASSIST, Tier.BOUNDED, Tier.SUPERVISED):
            v = C.check_segment_eligibility(tier, seg)
            assert v and v[0].code is C.ConstraintCode.NEVER_AUTONOMOUS_SEGMENT


def test_rate_limit() -> None:
    assert C.check_rate_limit(2, CONS) == []
    v = C.check_rate_limit(3, CONS)
    assert v and v[0].code is C.ConstraintCode.RATE_LIMIT_EXCEEDED


def test_deliverability_breaches() -> None:
    trig = DeliverabilityTriggers(
        spam_complaint_rate_max=0.0008, unsubscribe_rate_max=0.003, bounce_rate_max=0.005
    )
    clean = DeliverabilityReport(
        run_id="r",
        campaign_type=CT.value,
        spam_complaint_rate=0.0008,
        unsubscribe_rate=0.003,
        bounce_rate=0.005,
    )
    assert C.check_deliverability(clean, trig) == []  # at threshold is not a breach
    bad = DeliverabilityReport(
        run_id="r",
        campaign_type=CT.value,
        spam_complaint_rate=0.001,
        unsubscribe_rate=0.004,
        bounce_rate=0.006,
    )
    breaches = C.check_deliverability(bad, trig)
    assert {b.metric for b in breaches} == {
        "spam_complaint_rate",
        "unsubscribe_rate",
        "bounce_rate",
    }


def test_discount_over_ceiling_routes_to_review_through_controller() -> None:
    c = make_controller()
    for _ in range(25):
        c.process_run(make_eval(passed=True, campaign_type=CT))
    assert c.state(CT).tier is Tier.BOUNDED
    d = c.process_run(make_eval(passed=True, discount_pct=30, campaign_type=CT))
    assert d.decision is Decision.HUMAN_REVIEW
    assert any(v.code is C.ConstraintCode.DISCOUNT_EXCEEDS_CEILING for v in d.blocked)


def test_rate_limit_routes_to_review_after_cap() -> None:
    c = make_controller()
    for _ in range(25):
        c.process_run(make_eval(passed=True, campaign_type=CT))
    # Now at BOUNDED; engaged_30d is autosendable. First 3 auto-send in the 24h window.
    for _ in range(3):
        d = c.process_run(make_eval(passed=True, campaign_type=CT))
        assert d.decision is Decision.AUTO_SEND
    d = c.process_run(make_eval(passed=True, campaign_type=CT))
    assert d.decision is Decision.HUMAN_REVIEW
    assert any(v.code is C.ConstraintCode.RATE_LIMIT_EXCEEDED for v in d.blocked)
