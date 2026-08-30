"""Application service wiring: controller + queue + run store (SPEC §9)."""

from __future__ import annotations

from datetime import UTC, datetime

from autonomy_ladder.autonomy.controller import AutonomyController
from autonomy_ladder.autonomy.ledger import Decision, Ledger
from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.config import BrandPolicy
from autonomy_ladder.domain import CampaignType, Dimension
from autonomy_ladder.queue.models import ItemStatus
from autonomy_ladder.queue.store import ReviewQueue
from autonomy_ladder.service import AutonomyService, RunStore

from .conftest import make_eval, make_tiers_config

CT = CampaignType.NEWSLETTER
NOW = datetime(2026, 8, 29, tzinfo=UTC)


def _service() -> AutonomyService:
    tiers = make_tiers_config()
    brand = BrandPolicy(max_allowed_tier=2)
    return AutonomyService(
        controller=AutonomyController(Ledger(":memory:"), tiers, brand),
        queue=ReviewQueue(":memory:"),
        runs=RunStore(":memory:"),
        tiers_config=tiers,
        brand_policy=brand,
    )


def test_record_run_persists_decision_queue_item_and_trace() -> None:
    svc = _service()
    ev = make_eval(passed=True, campaign_type=CT, run_id="r1")
    decision = svc.record_run(ev, now=NOW)
    # Tier 0 -> routed to review, queued as pending, and the trace is retrievable.
    assert decision.decision is Decision.HUMAN_REVIEW
    assert svc.queue.get("r1").status is ItemStatus.PENDING
    assert svc.runs.get("r1") is not None


def test_dashboard_covers_every_campaign_type() -> None:
    svc = _service()
    rows = svc.dashboard()
    assert {r["campaign_type"] for r in rows} == {c.value for c in CampaignType}


def test_demotion_downgrades_in_flight_to_review() -> None:
    svc = _service()
    # Earn Tier 1 so subsequent clean runs auto-send (go in-flight).
    for i in range(25):
        svc.record_run(make_eval(passed=True, campaign_type=CT, run_id=f"p{i}"), now=NOW)
    inflight = svc.record_run(make_eval(passed=True, campaign_type=CT, run_id="live"), now=NOW)
    assert inflight.decision is Decision.AUTO_SEND
    assert svc.queue.get("live").status is ItemStatus.IN_FLIGHT

    # A critical failure demotes the type and downgrades the in-flight item to review.
    dec = svc.record_run(
        make_eval(
            passed=False, critical_fail=Dimension.CLAIM_GROUNDEDNESS, campaign_type=CT, run_id="bad"
        ),
        now=NOW,
    )
    assert dec.demoted is True
    assert svc.controller.state(CT).tier is Tier.ASSIST
    assert svc.queue.get("live").status is ItemStatus.PENDING  # downgraded, not sent
