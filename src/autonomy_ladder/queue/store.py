"""The review-queue store (SPEC §5, §15: SQLite only).

Unlike the trust ledger, the queue is *mutable* — items get approved or expire, and
in-flight items get downgraded to review when their campaign type is demoted. This
store owns that mutable state and is the one place lane views are materialised (so
expiry is persisted as it is observed).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from autonomy_ladder.autonomy.controller import ControllerDecision
from autonomy_ladder.autonomy.ledger import Decision
from autonomy_ladder.queue.lanes import build_lanes
from autonomy_ladder.queue.models import ItemStatus, LanesView, QueueItem
from autonomy_ladder.records import RunEvaluation

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_queue (
    run_id        TEXT PRIMARY KEY,
    campaign_type TEXT NOT NULL,
    status        TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    payload       TEXT NOT NULL
);
"""


def queue_item_from_decision(
    evaluation: RunEvaluation,
    decision: ControllerDecision,
    *,
    created_at: str,
    send_window_expires_at: str,
) -> QueueItem:
    """Build a compact queue item from a scored run and the controller's decision.

    An AUTO_SEND decision becomes an IN_FLIGHT item (awaiting send, downgradable);
    a HUMAN_REVIEW decision becomes a PENDING item awaiting a reviewer.
    """
    scores = [d.score for d in evaluation.dimensions.values()]
    status = ItemStatus.IN_FLIGHT if decision.decision is Decision.AUTO_SEND else ItemStatus.PENDING
    return QueueItem(
        run_id=evaluation.run_id,
        campaign_type=decision.campaign_type,
        segment=evaluation.segment,
        discount_pct=evaluation.discount_pct,
        brand_voice_score=evaluation.brand_voice_score or 0.0,
        min_dimension_score=min(scores) if scores else 0.0,
        critical_flags=decision.critical_failures,
        constraint_codes=[v.code.value for v in decision.blocked],
        rationale=decision.rationale,
        created_at=created_at,
        send_window_expires_at=send_window_expires_at,
        status=status,
    )


class ReviewQueue:
    """Persistent, mutable store of queued work."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def add(self, item: QueueItem) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO review_queue "
            "(run_id, campaign_type, status, created_at, expires_at, payload) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                item.run_id,
                item.campaign_type.value,
                item.status.value,
                item.created_at,
                item.send_window_expires_at,
                item.model_dump_json(),
            ),
        )
        self._conn.commit()

    def get(self, run_id: str) -> QueueItem | None:
        row = self._conn.execute(
            "SELECT payload FROM review_queue WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else QueueItem.model_validate_json(row[0])

    def all_items(self) -> list[QueueItem]:
        rows = self._conn.execute(
            "SELECT payload FROM review_queue ORDER BY created_at, run_id"
        ).fetchall()
        return [QueueItem.model_validate_json(r[0]) for r in rows]

    def _by_status(self, status: ItemStatus) -> list[QueueItem]:
        rows = self._conn.execute(
            "SELECT payload FROM review_queue WHERE status = ? ORDER BY created_at, run_id",
            (status.value,),
        ).fetchall()
        return [QueueItem.model_validate_json(r[0]) for r in rows]

    def pending_ids(self) -> list[str]:
        """Run ids of every currently-pending item."""
        return [i.run_id for i in self._by_status(ItemStatus.PENDING)]

    def set_status(self, run_id: str, status: ItemStatus) -> None:
        item = self.get(run_id)
        if item is None:
            raise KeyError(run_id)
        updated = item.model_copy(update={"status": status})
        self.add(updated)

    def approve(self, run_id: str) -> None:
        self.set_status(run_id, ItemStatus.APPROVED)

    def approve_batch(self, run_ids: Iterable[str]) -> int:
        n = 0
        for rid in run_ids:
            self.approve(rid)
            n += 1
        return n

    def downgrade_inflight(self, campaign_type: str) -> int:
        """Downgrade all in-flight items of a type to human review (SPEC §4).

        Called on demotion: in-flight campaigns are never cancelled and never sent
        — they drop into the review queue. Returns how many were downgraded.
        """
        rows = self._conn.execute(
            "SELECT payload FROM review_queue WHERE campaign_type = ? AND status = ?",
            (campaign_type, ItemStatus.IN_FLIGHT.value),
        ).fetchall()
        items = [QueueItem.model_validate_json(r[0]) for r in rows]
        for item in items:
            self.set_status(item.run_id, ItemStatus.PENDING)
        return len(items)

    def lanes(self, now: datetime) -> LanesView:
        """Materialise the two lanes for the pending items, persisting expiry."""
        pending = self._by_status(ItemStatus.PENDING)
        view = build_lanes(pending, now)
        for d in view.newly_expired:
            self.set_status(d.item.run_id, ItemStatus.EXPIRED)
        return view

    def close(self) -> None:
        self._conn.close()
