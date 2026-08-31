"""Scripted demo state — a believable history with no API key.

Why this exists: `make ui` should open onto a populated operator console — tiers
earned, a demotion and probation, and a review queue with both lanes — without
anyone having to run live campaigns first. This builds that state deterministically
by feeding the controller synthesized evaluations (no LLM), so every number on the
dashboard traces back to a real ledger entry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from autonomy_ladder.autonomy.controller import ControllerDecision
from autonomy_ladder.autonomy.ledger import Decision, OutcomeClass
from autonomy_ladder.autonomy.tiers import Tier, TierState
from autonomy_ladder.domain import (
    BRAND_VOICE_PASS_THRESHOLD,
    CampaignType,
    Dimension,
    SegmentBand,
    Verdict,
)
from autonomy_ladder.queue.models import ItemStatus, QueueItem
from autonomy_ladder.records import CampaignContent, DimensionResult, RunEvaluation
from autonomy_ladder.service import AutonomyService, RunRecord


def _eval(
    run_id: str,
    ct: CampaignType,
    *,
    passed: bool = True,
    segment: SegmentBand = SegmentBand.ENGAGED_30D,
    discount: float = 0.0,
    critical_fail: Dimension | None = None,
    brand_voice: float | None = None,
) -> RunEvaluation:
    bv = brand_voice if brand_voice is not None else (0.9 if passed else 0.5)
    seg_v = Verdict.PASS
    claim_v = Verdict.PASS
    if not passed and critical_fail is Dimension.SEGMENT_CORRECTNESS:
        seg_v = Verdict.FAIL
    if not passed and critical_fail is Dimension.CLAIM_GROUNDEDNESS:
        claim_v = Verdict.FAIL
    if not passed and critical_fail is not None:
        bv = 0.9

    def d(dim: Dimension, v: Verdict, s: float) -> DimensionResult:
        return DimensionResult(dimension=dim, score=s, verdict=v, reasoning="demo")

    dims = {
        Dimension.SEGMENT_CORRECTNESS: d(
            Dimension.SEGMENT_CORRECTNESS, seg_v, 1.0 if seg_v is Verdict.PASS else 0.1
        ),
        Dimension.CLAIM_GROUNDEDNESS: d(
            Dimension.CLAIM_GROUNDEDNESS, claim_v, 1.0 if claim_v is Verdict.PASS else 0.1
        ),
        Dimension.BRAND_VOICE: d(
            Dimension.BRAND_VOICE,
            Verdict.PASS if bv >= BRAND_VOICE_PASS_THRESHOLD else Verdict.FAIL,
            bv,
        ),
        Dimension.STRUCTURE_QUALITY: d(Dimension.STRUCTURE_QUALITY, Verdict.PASS, 0.8),
    }
    return RunEvaluation(
        run_id=run_id,
        campaign_type=ct.value,
        segment=segment,
        discount_pct=discount,
        dimensions=dims,
    )


def _content(ct: CampaignType, segment: SegmentBand) -> CampaignContent:
    return CampaignContent(
        subject="Trail-ready picks for the season",
        preview_text="Fresh gear from Northbay Supply",
        body="Our Basecamp Dome is built for 3-season use and handles real weather.",
        cta_text="Shop now",
        cta_url="https://northbay.example.com/shop",
        claims=["Built for 3-season use."],
        target_segment=segment,
        discount_pct=0.0,
        product_ids=["NB-0001"],
    )


def seed_demo(service: AutonomyService, anchor: datetime | None = None) -> None:
    """Populate ledger + queue + runs with a scripted, deterministic scenario."""
    anchor = anchor or datetime.now(UTC)
    # Walk a clock forward from ~9 days ago so the latest items are recent.
    clock = anchor - timedelta(days=9)

    def step(hours: int = 3) -> datetime:
        nonlocal clock
        clock = clock + timedelta(hours=hours)
        return clock

    n = 0

    def rid(tag: str) -> str:
        nonlocal n
        n += 1
        return f"{tag}-{n:03d}"

    # newsletter: earn Tier 1 (25 clean), then auto-sends + a rate-limited review.
    for _ in range(25):
        e = _eval(rid("news"), CampaignType.NEWSLETTER)
        service.record_run(e, content=_content(CampaignType.NEWSLETTER, e.segment), now=step())
    for _ in range(5):  # a few at Tier 1: first 3 auto-send, rest hit the 24h rate cap
        e = _eval(rid("news"), CampaignType.NEWSLETTER)
        service.record_run(e, content=_content(CampaignType.NEWSLETTER, e.segment), now=step(1))

    # promotional_discount: earn Tier 1, suffer a critical claim failure -> probation,
    # then pass the probation challenge (restored to Tier 1 + cooldown).
    for _ in range(25):
        e = _eval(rid("promo"), CampaignType.PROMOTIONAL_DISCOUNT)
        service.record_run(
            e, content=_content(CampaignType.PROMOTIONAL_DISCOUNT, e.segment), now=step()
        )
    bad = _eval(
        rid("promo"),
        CampaignType.PROMOTIONAL_DISCOUNT,
        passed=False,
        critical_fail=Dimension.CLAIM_GROUNDEDNESS,
    )
    service.record_run(
        bad, content=_content(CampaignType.PROMOTIONAL_DISCOUNT, bad.segment), now=step()
    )
    service.controller.run_probation_challenge(
        CampaignType.PROMOTIONAL_DISCOUNT, successes=25, n=25, now=step()
    )

    # product_launch: progress but not yet promoted (still Tier 0 -> batch lane).
    for _ in range(12):
        e = _eval(rid("launch"), CampaignType.PRODUCT_LAUNCH)
        service.record_run(e, content=_content(CampaignType.PRODUCT_LAUNCH, e.segment), now=step())

    # restock_alert: a run over the discount ceiling -> judgment lane.
    over = _eval(rid("restock"), CampaignType.RESTOCK_ALERT, discount=40)
    service.record_run(over, content=_content(CampaignType.RESTOCK_ALERT, over.segment), now=step())

    # winback: a clean run (Tier 0 -> review, batch lane).
    wb = _eval(rid("winback"), CampaignType.WINBACK)
    service.record_run(wb, content=_content(CampaignType.WINBACK, wb.segment), now=step())

    # A couple of quality failures tagged for M1 (brief-instructed vs agent-originated).
    service.record_run(
        _eval(rid("promo"), CampaignType.PROMOTIONAL_DISCOUNT, passed=False, brand_voice=0.4),
        content=_content(CampaignType.PROMOTIONAL_DISCOUNT, SegmentBand.ENGAGED_30D),
        now=step(),
        failure_origin="brief_instructed",
    )
    service.record_run(
        _eval(rid("launch"), CampaignType.PRODUCT_LAUNCH, passed=False, brand_voice=0.4),
        content=_content(CampaignType.PRODUCT_LAUNCH, SegmentBand.ENGAGED_30D),
        now=step(),
        failure_origin="agent_originated",
    )

    # The bulk history above has already been reviewed — approve it, so the console
    # opens onto a realistic, uncluttered queue rather than weeks of backlog.
    for run_id in service.queue.pending_ids():
        service.queue.approve(run_id)

    _seed_queue(service, anchor)
    _seed_security(service, anchor)


def _seed_security(service: AutonomyService, anchor: datetime) -> None:
    """Log a few resisted attacks so the console shows the security count (Drop 2)."""
    from autonomy_ladder.security import SecurityCase, SecurityEvent, load_security_cases, scan

    cases: list[SecurityCase] = load_security_cases()
    for i, case in enumerate(cases[:5]):
        detected = scan(case.attack_text())
        event_type = detected[0] if detected else case.expected_security_event
        service.security.add(
            SecurityEvent(
                id=case.id,
                ts=(anchor - timedelta(hours=6 * (i + 1))).isoformat(),
                campaign_type=case.campaign_type,
                event_type=event_type,
                detail=f"{case.attack}: resisted; run proceeded on structured data only.",
            )
        )


def _batch_item(run_id: str, ct: CampaignType, anchor: datetime, hours_left: int) -> QueueItem:
    return QueueItem(
        run_id=run_id,
        campaign_type=ct,
        segment=SegmentBand.ENGAGED_30D,
        brand_voice_score=0.86,
        min_dimension_score=0.82,
        rationale=["Clean run at Tier 0; awaiting approval."],
        created_at=(anchor - timedelta(hours=48 - hours_left)).isoformat(),
        send_window_expires_at=(anchor + timedelta(hours=hours_left)).isoformat(),
        status=ItemStatus.PENDING,
    )


def _record_for_item(item: QueueItem) -> RunRecord:
    """A run trace matching a seeded queue item, so /api/runs/{id} (the Run Detail
    view) resolves when a reviewer clicks the item. Mirrors what record_run would
    persist for a run routed to review."""
    passed = not item.critical_flags

    def dim(d: Dimension, score: float) -> DimensionResult:
        verdict = Verdict.FAIL if d in item.critical_flags else Verdict.PASS
        if d is Dimension.BRAND_VOICE and item.brand_voice_score < BRAND_VOICE_PASS_THRESHOLD:
            verdict = Verdict.FAIL
        return DimensionResult(dimension=d, score=score, verdict=verdict)

    dims = {
        Dimension.SEGMENT_CORRECTNESS: dim(Dimension.SEGMENT_CORRECTNESS, item.min_dimension_score),
        Dimension.CLAIM_GROUNDEDNESS: dim(Dimension.CLAIM_GROUNDEDNESS, item.min_dimension_score),
        Dimension.BRAND_VOICE: dim(Dimension.BRAND_VOICE, item.brand_voice_score),
        Dimension.STRUCTURE_QUALITY: dim(Dimension.STRUCTURE_QUALITY, 0.85),
    }
    if item.critical_flags:
        outcome = OutcomeClass.QUALITY_FAILURE
    elif item.constraint_codes:
        outcome = OutcomeClass.CONSTRAINT_BLOCK
    else:
        outcome = OutcomeClass.QUALITY_PASS
    decision = ControllerDecision(
        run_id=item.run_id,
        campaign_type=item.campaign_type,
        decision=Decision.HUMAN_REVIEW,
        passed=passed,
        outcome=outcome,
        effective_tier=Tier.ASSIST,
        critical_failures=list(item.critical_flags),
        rationale=list(item.rationale),
        state_after=TierState.initial(item.campaign_type),
    )
    return RunRecord(
        run_id=item.run_id,
        campaign_type=item.campaign_type,
        created_at=item.created_at,
        content=_content(item.campaign_type, item.segment),
        evaluation=RunEvaluation(
            run_id=item.run_id,
            campaign_type=item.campaign_type.value,
            segment=item.segment,
            discount_pct=item.discount_pct,
            dimensions=dims,
        ),
        decision=decision,
    )


def _seed_queue(service: AutonomyService, anchor: datetime) -> None:
    """Add a curated, current set of queue items spanning both lanes and the SLA states."""

    def add(item: QueueItem) -> None:
        service.queue.add(item)
        service.runs.add(_record_for_item(item))  # so Run Detail resolves for each item

    # Batch lane — clean look-alikes for group approval.
    for i, ct in enumerate(
        [CampaignType.PRODUCT_LAUNCH, CampaignType.PRODUCT_LAUNCH, CampaignType.WINBACK]
    ):
        add(_batch_item(f"batch-{i + 1:02d}", ct, anchor, hours_left=30 - i))

    # Judgment lane — a critical failure, a constraint breach, and a rate-limit hit.
    add(
        QueueItem(
            run_id="judge-claim",
            campaign_type=CampaignType.PROMOTIONAL_DISCOUNT,
            segment=SegmentBand.ENGAGED_30D,
            discount_pct=20,
            brand_voice_score=0.4,
            min_dimension_score=0.1,
            critical_flags=[Dimension.CLAIM_GROUNDEDNESS],
            rationale=["CRITICAL: unsupported claim ('guaranteed forever')."],
            created_at=(anchor - timedelta(hours=10)).isoformat(),
            send_window_expires_at=(anchor + timedelta(hours=20)).isoformat(),
            status=ItemStatus.PENDING,
        )
    )
    add(
        QueueItem(
            run_id="judge-discount",
            campaign_type=CampaignType.RESTOCK_ALERT,
            segment=SegmentBand.ENGAGED_30D,
            discount_pct=40,
            brand_voice_score=0.8,
            min_dimension_score=0.75,
            constraint_codes=["discount_exceeds_ceiling"],
            rationale=["Discount 40% exceeds the 25% autonomous ceiling."],
            created_at=(anchor - timedelta(hours=6)).isoformat(),
            send_window_expires_at=(anchor + timedelta(hours=26)).isoformat(),
            status=ItemStatus.PENDING,
        )
    )
    add(
        QueueItem(
            run_id="judge-ratelimit",
            campaign_type=CampaignType.NEWSLETTER,
            segment=SegmentBand.ENGAGED_30D,
            brand_voice_score=0.88,
            min_dimension_score=0.85,
            constraint_codes=["rate_limit_exceeded"],
            rationale=["Hit the 3-per-24h autonomous send cap; routed to review."],
            created_at=(anchor - timedelta(hours=4)).isoformat(),
            send_window_expires_at=(anchor + timedelta(hours=28)).isoformat(),
            status=ItemStatus.PENDING,
        )
    )

    # SLA states: one escalating (within 20% of its window), one already expired.
    add(
        QueueItem(
            run_id="sla-escalating",
            campaign_type=CampaignType.NEWSLETTER,
            segment=SegmentBand.ENGAGED_30D,
            brand_voice_score=0.82,
            min_dimension_score=0.8,
            rationale=["Near its send window — escalated."],
            created_at=(anchor - timedelta(hours=44)).isoformat(),
            send_window_expires_at=(anchor + timedelta(hours=3)).isoformat(),
            status=ItemStatus.PENDING,
        )
    )
    add(
        QueueItem(
            run_id="sla-expired",
            campaign_type=CampaignType.WINBACK,
            segment=SegmentBand.ENGAGED_30D,
            brand_voice_score=0.8,
            min_dimension_score=0.78,
            rationale=["Missed its send window."],
            created_at=(anchor - timedelta(hours=60)).isoformat(),
            send_window_expires_at=(anchor - timedelta(hours=2)).isoformat(),
            status=ItemStatus.PENDING,
        )
    )
