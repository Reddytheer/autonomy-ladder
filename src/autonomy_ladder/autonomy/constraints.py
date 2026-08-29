"""Hard constraint checks — the deterministic gates on autonomous action.

Why this module exists: even a run that passes every quality dimension may not be
sent autonomously if it violates a hard constraint (SPEC §4): sending to an
ineligible segment for the current tier, discounting past the ceiling, or
exceeding the per-type rate limit. These checks are pure functions with no LLM
and no I/O, so they are trivially testable and impossible to talk out of.

Post-send, :func:`check_deliverability` evaluates the outcome metrics that can
retroactively cost the agent its standing.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from autonomy_ladder.autonomy.tiers import Tier, can_autosend
from autonomy_ladder.config import Constraints, DeliverabilityTriggers
from autonomy_ladder.domain import NEVER_AUTONOMOUS, SegmentBand
from autonomy_ladder.records import DeliverabilityReport


class ConstraintCode(StrEnum):
    """Machine-readable reason an autonomous send is blocked."""

    NEVER_AUTONOMOUS_SEGMENT = "never_autonomous_segment"
    SEGMENT_NOT_ELIGIBLE_FOR_TIER = "segment_not_eligible_for_tier"
    DISCOUNT_EXCEEDS_CEILING = "discount_exceeds_ceiling"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"


class ConstraintViolation(BaseModel):
    model_config = {"frozen": True}

    code: ConstraintCode
    message: str


def check_segment_eligibility(tier: Tier, segment: SegmentBand) -> list[ConstraintViolation]:
    """Block sends to segments this tier may not reach autonomously (SPEC §4)."""
    if segment in NEVER_AUTONOMOUS:
        return [
            ConstraintViolation(
                code=ConstraintCode.NEVER_AUTONOMOUS_SEGMENT,
                message=(
                    f"Segment '{segment.value}' is never eligible for autonomous "
                    f"send at any tier; routes to human review."
                ),
            )
        ]
    if not can_autosend(tier, segment):
        return [
            ConstraintViolation(
                code=ConstraintCode.SEGMENT_NOT_ELIGIBLE_FOR_TIER,
                message=(
                    f"Tier {tier.value} ({tier.name}) may not autonomously send to "
                    f"segment '{segment.value}'."
                ),
            )
        ]
    return []


def check_discount(discount_pct: float, constraints: Constraints) -> list[ConstraintViolation]:
    """Block autonomous sends above the discount ceiling (SPEC §4)."""
    if discount_pct > constraints.max_discount_pct:
        return [
            ConstraintViolation(
                code=ConstraintCode.DISCOUNT_EXCEEDS_CEILING,
                message=(
                    f"Discount {discount_pct:g}% exceeds the autonomous ceiling of "
                    f"{constraints.max_discount_pct:g}%; routes to human review."
                ),
            )
        ]
    return []


def check_rate_limit(
    autonomous_sends_last_24h: int, constraints: Constraints
) -> list[ConstraintViolation]:
    """Block once the per-type 24h autonomous-send budget is spent (SPEC §4).

    ``autonomous_sends_last_24h`` is the count of *already auto-sent* campaigns of
    this type in the trailing 24h window. Sending one more is blocked when that
    count has reached the cap (rate limiting against runaway loops).
    """
    cap = constraints.max_autonomous_sends_per_type_per_24h
    if autonomous_sends_last_24h >= cap:
        return [
            ConstraintViolation(
                code=ConstraintCode.RATE_LIMIT_EXCEEDED,
                message=(
                    f"{autonomous_sends_last_24h} autonomous sends of this type in the "
                    f"last 24h; cap is {cap}. Routes to human review."
                ),
            )
        ]
    return []


class DeliverabilityBreach(BaseModel):
    """One post-send threshold that was exceeded (SPEC §4)."""

    model_config = {"frozen": True}

    metric: str
    value: float
    threshold: float

    @property
    def message(self) -> str:
        return f"{self.metric}={self.value:.4%} exceeded threshold {self.threshold:.4%}"


def check_deliverability(
    report: DeliverabilityReport, triggers: DeliverabilityTriggers
) -> list[DeliverabilityBreach]:
    """Return every deliverability threshold breached by a send (SPEC §4).

    A non-empty result is a CRITICAL post-send failure: it demotes the campaign
    type to Tier 0 and opens probation.
    """
    breaches: list[DeliverabilityBreach] = []
    if report.spam_complaint_rate > triggers.spam_complaint_rate_max:
        breaches.append(
            DeliverabilityBreach(
                metric="spam_complaint_rate",
                value=report.spam_complaint_rate,
                threshold=triggers.spam_complaint_rate_max,
            )
        )
    if report.unsubscribe_rate > triggers.unsubscribe_rate_max:
        breaches.append(
            DeliverabilityBreach(
                metric="unsubscribe_rate",
                value=report.unsubscribe_rate,
                threshold=triggers.unsubscribe_rate_max,
            )
        )
    if report.bounce_rate > triggers.bounce_rate_max:
        breaches.append(
            DeliverabilityBreach(
                metric="bounce_rate",
                value=report.bounce_rate,
                threshold=triggers.bounce_rate_max,
            )
        )
    return breaches
