"""constraint_block vs quality_failure and the Wilson window (HANDOFF, ADR 0008)."""

from __future__ import annotations

from autonomy_ladder.autonomy.ledger import OutcomeClass
from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.domain import CampaignType, Dimension, SegmentBand

from .conftest import make_controller, make_eval

CT = CampaignType.NEWSLETTER


def test_constraint_blocks_do_not_advance_the_wilson_window() -> None:
    """25 constraint blocks change nothing: not counted, no promotion (HANDOFF test 1)."""
    c = make_controller()
    for _ in range(25):
        # Clean copy but discount over the ceiling -> a constraint block.
        d = c.process_run(make_eval(passed=True, discount_pct=40, campaign_type=CT))
        assert d.outcome is OutcomeClass.CONSTRAINT_BLOCK
    assert c.state(CT).tier is Tier.ASSIST
    assert c.promotion_status(CT).runs_in_window == 0


def test_never_autonomous_runs_are_excluded() -> None:
    c = make_controller()
    for _ in range(25):
        d = c.process_run(make_eval(passed=True, segment=SegmentBand.LAPSED, campaign_type=CT))
        assert d.outcome is OutcomeClass.CONSTRAINT_BLOCK
    assert c.state(CT).tier is Tier.ASSIST
    assert c.promotion_status(CT).runs_in_window == 0


def test_wilson_denominator_counts_only_quality_eligible_runs() -> None:
    """A mixed sequence: only quality-eligible runs land in the window (HANDOFF test 2)."""
    c = make_controller()
    for _ in range(10):
        c.process_run(make_eval(passed=True, campaign_type=CT))  # quality passes
    for _ in range(5):
        c.process_run(make_eval(passed=True, discount_pct=40, campaign_type=CT))  # blocks
    st = c.promotion_status(CT)
    assert st.runs_in_window == 10  # the 5 constraint blocks are excluded
    assert st.successes_in_window == 10


def test_both_block_and_quality_failure_counts_as_quality_failure() -> None:
    """Quality precedence: a run that is both counts as a quality failure (HANDOFF test 3)."""
    c = make_controller()
    d = c.process_run(make_eval(passed=False, brand_voice=0.4, discount_pct=40, campaign_type=CT))
    assert d.outcome is OutcomeClass.QUALITY_FAILURE
    # It counts in the window (as a failure), unlike a pure constraint block.
    assert c.promotion_status(CT).runs_in_window == 1
    assert c.promotion_status(CT).successes_in_window == 0


def test_clean_run_above_tier_counts_as_success_option1() -> None:
    """Owner decision (Option 1): a clean run to an autonomy-eligible band above the
    current tier is a quality success, so 0->1 promotion can be earned this way."""
    c = make_controller()
    promoted_at = None
    for i in range(25):
        # engaged_90d is autonomy-eligible (at Tier 2) but ineligible at Tier 0 —
        # a tier-boundary block, NOT a Wilson exclusion.
        d = c.process_run(make_eval(passed=True, segment=SegmentBand.ENGAGED_90D, campaign_type=CT))
        assert d.outcome is OutcomeClass.QUALITY_PASS
        if d.promoted:
            promoted_at = i + 1
    assert promoted_at == 25
    assert c.state(CT).tier is Tier.BOUNDED


def test_brand_only_failure_counts_but_does_not_demote() -> None:
    c = make_controller()
    for _ in range(25):
        c.process_run(make_eval(passed=True, campaign_type=CT))  # reach BOUNDED
    d = c.process_run(make_eval(passed=False, brand_voice=0.4, campaign_type=CT))
    assert d.outcome is OutcomeClass.QUALITY_FAILURE
    assert d.demoted is False  # brand voice is WEIGHTED, not CRITICAL
    assert d.critical_failures == []
    assert Dimension.BRAND_VOICE  # sanity: dimension exists
