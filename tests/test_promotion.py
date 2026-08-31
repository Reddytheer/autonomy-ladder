"""Promotion behaviour (SPEC §4, §14). These tests are the heart of the
acceptance criteria: promotion is statistical, not a streak counter.

Resolution of the spec's inconsistent example is documented in
docs/open-questions.md (OQ-1, OQ-2): the 0->1 bar is Wilson lower > 0.85 with
min_runs=25, so 48/50 promotes and 47/50 does not.
"""

from __future__ import annotations

from datetime import UTC, datetime

from autonomy_ladder.autonomy.controller import AutonomyController
from autonomy_ladder.autonomy.ledger import Decision, Ledger
from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.domain import CampaignType, SegmentBand

from .conftest import make_controller, make_eval

CT = CampaignType.NEWSLETTER
OLD_TS = datetime(2025, 12, 1, tzinfo=UTC).isoformat()


def _seed_runs(ledger: Ledger, passes: int, fails: int) -> None:
    """Write raw run outcomes directly to the ledger (bypasses promotion logic).

    Used to set up an exact window of history so we can test the gate boundary
    precisely, rather than depending on the order promotions would fire in."""
    for i in range(passes):
        ledger.append_run(
            campaign_type=CT,
            ts=OLD_TS,
            run_id=f"seed-p{i}",
            passed=True,
            decision=Decision.HUMAN_REVIEW,
            tier_at_decision=Tier.ASSIST,
            segment=SegmentBand.ENGAGED_30D.value,
        )
    for i in range(fails):
        ledger.append_run(
            campaign_type=CT,
            ts=OLD_TS,
            run_id=f"seed-f{i}",
            passed=False,
            decision=Decision.HUMAN_REVIEW,
            tier_at_decision=Tier.ASSIST,
            segment=SegmentBand.ENGAGED_30D.value,
        )


def test_ten_of_ten_does_not_promote(controller: AutonomyController) -> None:
    """A perfect 10/10 is not evidence: n < min_runs AND Wilson lower < 0.85."""
    for _ in range(10):
        d = controller.process_run(make_eval(passed=True, campaign_type=CT))
        assert not d.promoted
    assert controller.state(CT).tier is Tier.ASSIST


def test_twenty_of_twenty_does_not_promote(controller: AutonomyController) -> None:
    """Documents OQ-2: even a perfect 20/20 stays at Tier 0 (20/20 Wilson=0.8389<0.85
    AND n < min_runs=25). The gate is statistical, not a streak."""
    for _ in range(20):
        controller.process_run(make_eval(passed=True, campaign_type=CT))
    assert controller.state(CT).tier is Tier.ASSIST


def test_twenty_five_straight_promotes_at_the_boundary(controller: AutonomyController) -> None:
    """25/25 (Wilson 0.8668 > 0.85, n == min_runs) is the earliest possible 0->1."""
    promoted_at = None
    for i in range(25):
        d = controller.process_run(make_eval(passed=True, campaign_type=CT))
        if d.promoted:
            promoted_at = i + 1
    assert promoted_at == 25
    assert controller.state(CT).tier is Tier.BOUNDED


def test_forty_eight_of_fifty_promotes() -> None:
    """48/50 -> Wilson 0.8654 > 0.85: promotes."""
    c = make_controller()
    _seed_runs(c._ledger, passes=47, fails=2)  # 49 seeded; +1 passing below => 48/50
    d = c.process_run(make_eval(passed=True, campaign_type=CT))
    assert d.promoted is True
    assert d.promotion_to is Tier.BOUNDED
    assert c.state(CT).tier is Tier.BOUNDED


def test_forty_seven_of_fifty_does_not_promote() -> None:
    """47/50 -> Wilson 0.8378 < 0.85: does NOT promote (the corrected example)."""
    c = make_controller()
    _seed_runs(c._ledger, passes=46, fails=3)  # 49 seeded; +1 passing => 47/50
    d = c.process_run(make_eval(passed=True, campaign_type=CT))
    assert d.promoted is False
    assert c.state(CT).tier is Tier.ASSIST


def test_promotion_status_reports_the_gate() -> None:
    c = make_controller()
    _seed_runs(c._ledger, passes=46, fails=3)  # 47/49 so far
    st = c.promotion_status(CT)
    assert st.tier is Tier.ASSIST
    assert st.next_tier is Tier.BOUNDED
    assert st.window == 50 and st.min_runs == 25 and st.threshold == 0.85
    assert st.runs_in_window == 49 and st.successes_in_window == 46
    assert st.gate_met is False


def test_brand_ceiling_caps_promotion() -> None:
    """A brand ceiling of 1 lets 0->1 happen but blocks 1->2 forever (ADR 0007)."""
    c = make_controller(max_allowed_tier=1)
    # Clear 0->1 with a strong record.
    for _ in range(25):
        c.process_run(make_eval(passed=True, campaign_type=CT))
    assert c.state(CT).tier is Tier.BOUNDED
    # Now pile on far more than enough for 1->2; the ceiling must hold.
    for _ in range(120):
        d = c.process_run(make_eval(passed=True, campaign_type=CT))
        assert d.promotion_to is not Tier.SUPERVISED
    assert c.state(CT).tier is Tier.BOUNDED
    assert c.promotion_status(CT).blocked_by_ceiling is True


def test_one_to_two_requires_the_higher_bar() -> None:
    """1->2 needs Wilson lower > 0.92 over >= 50 runs; a perfect record gets there."""
    from .conftest import advancing_clock

    # Advancing clock so the many auto-sends at Tier 1 aren't rate-limited (which
    # would exclude them from the Wilson window under ADR 0008).
    c = make_controller(clock=advancing_clock())
    for _ in range(25):
        c.process_run(make_eval(passed=True, campaign_type=CT))
    assert c.state(CT).tier is Tier.BOUNDED
    reached = False
    for _ in range(60):
        d = c.process_run(make_eval(passed=True, campaign_type=CT))
        if d.promotion_to is Tier.SUPERVISED:
            reached = True
    assert reached
    assert c.state(CT).tier is Tier.SUPERVISED
