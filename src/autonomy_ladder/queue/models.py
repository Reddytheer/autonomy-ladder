"""Review-queue data models (SPEC §5).

A :class:`QueueItem` is a compact, self-describing summary of one run that needs
human attention: enough to route it into the right lane, sort it, flag its SLA,
and render a diff — without the reviewer having to open the full trace.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from autonomy_ladder.autonomy.routing import Lane  # single source of the lane enum
from autonomy_ladder.domain import CampaignType, Dimension, SegmentBand

__all__ = [
    "Lane",
    "ItemStatus",
    "QueueItem",
    "DecoratedItem",
    "LanesView",
]


class ItemStatus(StrEnum):
    """Lifecycle of a queued item."""

    IN_FLIGHT = "in_flight"  # auto-sent decision, awaiting send; can be downgraded on demotion
    PENDING = "pending"  # awaiting human review
    APPROVED = "approved"  # a reviewer approved it (individually or via approve-all)
    EXPIRED = "expired"  # its send window elapsed before anyone acted (SPEC §5)


class QueueItem(BaseModel):
    """One unit of reviewable (or in-flight) work."""

    model_config = {"frozen": True}

    run_id: str
    campaign_type: CampaignType
    segment: SegmentBand
    discount_pct: float = 0.0

    # Scores that drive lane assignment and risk.
    brand_voice_score: float = 0.0
    min_dimension_score: float = 0.0  # lowest score across graded dims = our "confidence"
    critical_flags: list[Dimension] = Field(default_factory=list)
    constraint_codes: list[str] = Field(default_factory=list)
    rationale: list[str] = Field(default_factory=list)

    created_at: str  # ISO-8601 UTC
    send_window_expires_at: str  # ISO-8601 UTC (SPEC §5: age is an SLA)
    status: ItemStatus = ItemStatus.PENDING

    @property
    def has_critical_flag(self) -> bool:
        return bool(self.critical_flags)

    @property
    def has_constraint_violation(self) -> bool:
        return bool(self.constraint_codes)


class DecoratedItem(BaseModel):
    """A queue item plus its computed lane, risk, and SLA state for presentation."""

    model_config = {"frozen": True}

    item: QueueItem
    lane: Lane
    risk_score: float
    fraction_elapsed: float
    escalated: bool  # within 20% of the send window -> floats to the top of its lane
    expired: bool


class LanesView(BaseModel):
    """The rendered queue: two lanes plus the items that just expired."""

    model_config = {"frozen": True}

    batch: list[DecoratedItem] = Field(default_factory=list)
    judgment: list[DecoratedItem] = Field(default_factory=list)
    newly_expired: list[DecoratedItem] = Field(default_factory=list)
