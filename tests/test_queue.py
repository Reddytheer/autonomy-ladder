"""Two-lane review queue behaviour (SPEC §5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from autonomy_ladder.domain import Dimension, SegmentBand
from autonomy_ladder.queue.lanes import build_lanes, classify_lane
from autonomy_ladder.queue.models import ItemStatus, Lane, QueueItem
from autonomy_ladder.queue.risk_score import compute_risk_score
from autonomy_ladder.queue.store import ReviewQueue

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def _item(
    run_id: str,
    *,
    segment: SegmentBand = SegmentBand.ENGAGED_30D,
    brand_voice: float = 0.9,
    min_score: float = 0.9,
    criticals: list[Dimension] | None = None,
    constraints: list[str] | None = None,
    created: datetime | None = None,
    expires: datetime | None = None,
    status: ItemStatus = ItemStatus.PENDING,
) -> QueueItem:
    created = created or NOW
    expires = expires or (NOW + timedelta(hours=10))
    return QueueItem(
        run_id=run_id,
        campaign_type="newsletter",
        segment=segment,
        brand_voice_score=brand_voice,
        min_dimension_score=min_score,
        critical_flags=criticals or [],
        constraint_codes=constraints or [],
        created_at=created.isoformat(),
        send_window_expires_at=expires.isoformat(),
        status=status,
    )


def test_classify_batch_vs_judgment() -> None:
    assert classify_lane(_item("a")) is Lane.BATCH
    assert classify_lane(_item("b", criticals=[Dimension.CLAIM_GROUNDEDNESS])) is Lane.JUDGMENT
    assert classify_lane(_item("c", constraints=["discount_exceeds_ceiling"])) is Lane.JUDGMENT
    assert classify_lane(_item("d", min_score=0.4)) is Lane.JUDGMENT  # low confidence
    assert classify_lane(_item("e", brand_voice=0.6)) is Lane.JUDGMENT


def test_risk_score_orders_critical_above_clean() -> None:
    clean = _item("clean")
    crit = _item("crit", criticals=[Dimension.SEGMENT_CORRECTNESS])
    assert compute_risk_score(crit) > compute_risk_score(clean)


def test_judgment_lane_sorted_by_risk_desc() -> None:
    low = _item("low", constraints=["rate_limit_exceeded"])
    high = _item("high", criticals=[Dimension.SEGMENT_CORRECTNESS, Dimension.CLAIM_GROUNDEDNESS])
    view = build_lanes([low, high], NOW)
    assert [d.item.run_id for d in view.judgment] == ["high", "low"]


def test_escalation_floats_to_top_within_20pct_of_window() -> None:
    # Window 10h; an item created 9h ago has 10% remaining -> escalated.
    escalating = _item("esc", created=NOW - timedelta(hours=9), expires=NOW + timedelta(hours=1))
    fresh = _item("fresh")
    view = build_lanes([fresh, escalating], NOW)
    assert view.batch[0].item.run_id == "esc"
    assert view.batch[0].escalated is True
    assert view.batch[1].escalated is False


def test_expired_items_leave_both_lanes() -> None:
    expired = _item("old", created=NOW - timedelta(hours=20), expires=NOW - timedelta(hours=1))
    view = build_lanes([expired], NOW)
    assert view.batch == [] and view.judgment == []
    assert [d.item.run_id for d in view.newly_expired] == ["old"]


def test_store_persists_expiry_and_supports_batch_approve() -> None:
    q = ReviewQueue(":memory:")
    q.add(_item("keep"))
    q.add(_item("gone", created=NOW - timedelta(hours=20), expires=NOW - timedelta(hours=1)))
    view = q.lanes(NOW)
    assert {d.item.run_id for d in view.batch} == {"keep"}
    assert q.get("gone").status is ItemStatus.EXPIRED  # persisted
    q.approve_batch([d.item.run_id for d in view.batch])
    assert q.get("keep").status is ItemStatus.APPROVED


def test_downgrade_inflight_moves_to_review_never_sent() -> None:
    q = ReviewQueue(":memory:")
    q.add(_item("f1", status=ItemStatus.IN_FLIGHT))
    q.add(_item("f2", status=ItemStatus.IN_FLIGHT))
    q.add(_item("p1", status=ItemStatus.PENDING))
    n = q.downgrade_inflight("newsletter")
    assert n == 2
    assert q.get("f1").status is ItemStatus.PENDING
    assert q.get("f2").status is ItemStatus.PENDING
    # Nothing was sent or cancelled; they are now reviewable.
    assert len(q.lanes(NOW).batch) == 3
