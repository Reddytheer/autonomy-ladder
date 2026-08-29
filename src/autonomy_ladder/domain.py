"""Shared domain vocabulary for the reference implementation.

This module exists so that every other layer — data generation, agents,
evaluation, the autonomy controller, and the UI — speaks the *same* words for the
same things. Campaign types and segment bands are defined here once; nothing
below redefines them.

The reference domain is a fictional DTC outdoor-gear brand, "Northbay Supply".
None of these values reference any real company (SPEC §0).

Autonomy is tracked **per campaign type** (SPEC §3): an agent may have earned the
right to send ``newsletter`` campaigns unsupervised while still being fully
gated on ``promotional_discount``.
"""

from __future__ import annotations

from enum import StrEnum


class CampaignType(StrEnum):
    """The kinds of campaign the agent can produce. Autonomy is tracked per type."""

    PROMOTIONAL_DISCOUNT = "promotional_discount"
    PRODUCT_LAUNCH = "product_launch"
    NEWSLETTER = "newsletter"
    WINBACK = "winback"
    RESTOCK_ALERT = "restock_alert"


class SegmentBand(StrEnum):
    """Audience engagement bands, ordered most- to least-engaged.

    Bands are defined by recency of last activity; send frequency should scale
    with engagement (SPEC §3, standard email-marketing practice). The last three
    are *never* eligible for autonomous send at any tier (see
    ``NEVER_AUTONOMOUS`` and SPEC §4).
    """

    ENGAGED_30D = "engaged_30d"  # most engaged, lowest deliverability risk
    ENGAGED_60D = "engaged_60d"
    ENGAGED_90D = "engaged_90d"
    ENGAGED_180D = "engaged_180d"
    ALL_SUBSCRIBERS = "all_subscribers"
    FIRST_TIME_BUYERS = "first_time_buyers"  # never eligible for autonomous send
    LAPSED = "lapsed"  # never eligible for autonomous send


# Hard-coded, not configurable (SPEC §4). These always route to human review no
# matter how much the agent has earned. This is a safety floor, not a policy knob.
NEVER_AUTONOMOUS: frozenset[SegmentBand] = frozenset(
    {
        SegmentBand.ENGAGED_180D,
        SegmentBand.ALL_SUBSCRIBERS,
        SegmentBand.FIRST_TIME_BUYERS,
        SegmentBand.LAPSED,
    }
)


class Verdict(StrEnum):
    """A pass/fail judgement on a single quality dimension (SPEC §7)."""

    PASS = "pass"
    FAIL = "fail"


class DimensionClass(StrEnum):
    """How a quality dimension's failure is treated by the controller (SPEC §3)."""

    CRITICAL = "critical"  # blocks action + immediate demotion + probation
    WEIGHTED = "weighted"  # contributes to pass/fail; repeated failures hurt standing
    ADVISORY = "advisory"  # recorded, never blocks, never affects tier


class Dimension(StrEnum):
    """The four quality dimensions every run is scored on (SPEC §3)."""

    SEGMENT_CORRECTNESS = "segment_correctness"
    CLAIM_GROUNDEDNESS = "claim_groundedness"
    BRAND_VOICE = "brand_voice"
    STRUCTURE_QUALITY = "structure_quality"


# The class of each dimension. Segment and claim errors cause real-world harm
# (wrong content to wrong people; false-advertising exposure), so they are
# CRITICAL. Off-brand voice is a quality problem (WEIGHTED). Structure is
# deliberately ADVISORY because marketing is experimental and a rigid structural
# check would suppress legitimate variation (SPEC §3 rationale).
DIMENSION_CLASS: dict[Dimension, DimensionClass] = {
    Dimension.SEGMENT_CORRECTNESS: DimensionClass.CRITICAL,
    Dimension.CLAIM_GROUNDEDNESS: DimensionClass.CRITICAL,
    Dimension.BRAND_VOICE: DimensionClass.WEIGHTED,
    Dimension.STRUCTURE_QUALITY: DimensionClass.ADVISORY,
}

# A run passes iff both CRITICAL dimensions pass AND brand_voice >= this floor
# (SPEC §3). Defined here so the controller and the evaluator agree on one number.
BRAND_VOICE_PASS_THRESHOLD = 0.75

CRITICAL_DIMENSIONS: tuple[Dimension, ...] = tuple(
    d for d, c in DIMENSION_CLASS.items() if c is DimensionClass.CRITICAL
)
