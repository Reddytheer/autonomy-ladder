"""Post-send deliverability loop (HANDOFF 2, ADR 0009)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from autonomy_ladder.autonomy.controller import AutonomyController
from autonomy_ladder.autonomy.ledger import Ledger, reconstruct
from autonomy_ladder.autonomy.tiers import Standing, Tier
from autonomy_ladder.config import BrandPolicy
from autonomy_ladder.domain import CampaignType, Dimension, SegmentBand
from autonomy_ladder.outcomes.simulator import breaches, simulate
from autonomy_ladder.queue.models import ItemStatus
from autonomy_ladder.queue.store import ReviewQueue
from autonomy_ladder.records import DeliverabilityReport
from autonomy_ladder.service import AutonomyService, RunStore

from .conftest import advancing_clock, make_eval, make_tiers_config

CT = CampaignType.NEWSLETTER
GOOD = dict.fromkeys(Dimension, 0.95)
BREACH = {"spam_complaint_rate": 0.0011, "unsubscribe_rate": 0.0, "bounce_rate": 0.0}
CLEAN = {"spam_complaint_rate": 0.0002, "unsubscribe_rate": 0.001, "bounce_rate": 0.002}


def _service() -> AutonomyService:
    tiers = make_tiers_config()
    brand = BrandPolicy(max_allowed_tier=2)
    return AutonomyService(
        controller=AutonomyController(Ledger(":memory:"), tiers, brand, clock=advancing_clock()),
        queue=ReviewQueue(":memory:"),
        runs=RunStore(":memory:"),
        tiers_config=tiers,
        brand_policy=brand,
    )


def _promote(svc: AutonomyService) -> None:
    for _ in range(25):
        svc.record_run(make_eval(passed=True, campaign_type=CT))
    assert svc.controller.state(CT).tier is Tier.BOUNDED


# --- Simulator ---------------------------------------------------------------


def test_simulator_is_deterministic_under_fixed_seed() -> None:
    a = simulate(run_id="r", segment=SegmentBand.ENGAGED_30D, scores=GOOD, seed=7)
    b = simulate(run_id="r", segment=SegmentBand.ENGAGED_30D, scores=GOOD, seed=7)
    assert a == b


def test_well_scored_campaign_still_breaches_under_some_seeds() -> None:
    """Proves the simulator is not tautological (HANDOFF 2 A5)."""
    breached_seeds = [
        s
        for s in range(200)
        if breaches(simulate(run_id=f"n{s}", segment=SegmentBand.ENGAGED_30D, scores=GOOD, seed=s))
    ]
    assert breached_seeds  # some good-score campaigns breach anyway


# --- Loop --------------------------------------------------------------------


def test_breach_follows_the_critical_failure_path() -> None:
    """A breach demotes, downgrades in-flight to review, and opens probation."""
    svc = _service()
    _promote(svc)
    inflight = svc.record_run(make_eval(passed=True, campaign_type=CT, run_id="live"))
    assert inflight.decision.value == "auto_send"
    assert svc.queue.get("live").status is ItemStatus.IN_FLIGHT

    result = svc.process_outcome("live", CT, BREACH)
    assert result.breached and result.demoted and result.blind_spot  # passed eval, breached
    assert svc.controller.state(CT).tier is Tier.ASSIST
    assert svc.controller.state(CT).standing is Standing.PROBATION
    assert svc.queue.get("live").status is ItemStatus.PENDING  # downgraded, not sent


def test_clean_outcome_confirms_and_does_not_demote() -> None:
    svc = _service()
    _promote(svc)
    svc.record_run(make_eval(passed=True, campaign_type=CT, run_id="live"))
    result = svc.process_outcome("live", CT, CLEAN)
    assert result.breached is False and result.demoted is False
    assert svc.controller.state(CT).tier is Tier.BOUNDED


def test_blind_spot_is_flagged_into_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("autonomy_ladder.service.CANDIDATES_DIR", tmp_path)
    svc = _service()
    _promote(svc)
    svc.record_run(make_eval(passed=True, campaign_type=CT, run_id="live"))
    result = svc.process_outcome("live", CT, BREACH)
    assert result.blind_spot is True
    candidate = tmp_path / "live.json"
    assert candidate.exists()
    assert "eval_passed_outcome_failed" in candidate.read_text()


def test_tier_state_reconstructs_with_outcome_events() -> None:
    svc = _service()
    _promote(svc)
    svc.record_run(make_eval(passed=True, campaign_type=CT, run_id="live"))
    svc.process_outcome("live", CT, BREACH)  # appends outcome + demotion events
    live = svc.controller.state(CT)
    replayed = reconstruct(svc.controller._ledger.all(), CT)
    assert replayed == live
    assert live.tier is Tier.ASSIST


def test_deliverability_report_direct_clean_is_no_breach() -> None:
    c = AutonomyController(Ledger(":memory:"), make_tiers_config(), BrandPolicy(max_allowed_tier=2))
    report = DeliverabilityReport(
        run_id="x",
        campaign_type=CT.value,
        spam_complaint_rate=0.0,
        unsubscribe_rate=0.0,
        bounce_rate=0.0,
    )
    result = c.process_deliverability(report, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert result.breached is False and result.demoted is False
