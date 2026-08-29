"""Tier definitions and per-campaign-type standing.

Why this module exists: the autonomy ladder has exactly three tiers (SPEC §4),
and *which segments each tier may send to autonomously is hard-coded here, not
configurable*. This is the safety floor. A brand can lower its ceiling
(config/brand_policy.yaml) but nothing — no config, no LLM, no argument — can
widen a tier's segment eligibility beyond what this module allows.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, Field

from autonomy_ladder.domain import NEVER_AUTONOMOUS, CampaignType, SegmentBand


class Tier(IntEnum):
    """The autonomy ladder (SPEC §4). IntEnum so tiers compare and sort naturally."""

    ASSIST = 0  # autonomous send to nothing; human approves every campaign
    BOUNDED = 1  # autonomous send to engaged_30d only
    SUPERVISED = 2  # autonomous send to engaged_30d/60d/90d


# Hard-coded tier -> segments permitted for autonomous send (SPEC §4).
# The never-autonomous bands (engaged_180d, all_subscribers, first_time_buyers,
# lapsed) appear in NO tier's set and are additionally guarded in
# :func:`can_autosend`, so the rule is enforced twice by construction.
_TIER_SEGMENTS: dict[Tier, frozenset[SegmentBand]] = {
    Tier.ASSIST: frozenset(),
    Tier.BOUNDED: frozenset({SegmentBand.ENGAGED_30D}),
    Tier.SUPERVISED: frozenset(
        {SegmentBand.ENGAGED_30D, SegmentBand.ENGAGED_60D, SegmentBand.ENGAGED_90D}
    ),
}


def allowed_segments(tier: Tier) -> frozenset[SegmentBand]:
    """Segments this tier may send to autonomously."""
    return _TIER_SEGMENTS[tier]


def can_autosend(tier: Tier, segment: SegmentBand) -> bool:
    """True iff ``tier`` may autonomously send to ``segment``.

    Defence in depth: a never-autonomous segment returns False even if a future
    edit mistakenly added it to a tier's set.
    """
    if segment in NEVER_AUTONOMOUS:
        return False
    return segment in _TIER_SEGMENTS[tier]


class Standing(StrEnum):
    """The recovery status of a campaign type, orthogonal to its tier (SPEC §4)."""

    ACTIVE = "active"  # normal operation (possibly in cooldown, see cooldown_remaining)
    PROBATION = "probation"  # must pass the golden-subset challenge to be restored
    INVESTIGATION_REQUIRED = "investigation_required"  # probation failed; no auto re-promo


class TierState(BaseModel):
    """The full autonomy standing for ONE campaign type.

    Everything the controller needs to decide what happens to the next run of
    this type. This object is never mutated in place by the controller's decision
    logic — a new state is derived and appended to the ledger — so it is fully
    reconstructible by replaying ledger events (SPEC §2, P4).
    """

    model_config = {"frozen": True}

    campaign_type: CampaignType
    tier: Tier = Tier.ASSIST
    standing: Standing = Standing.ACTIVE
    # Runs that must still accrue at the restored tier before promotion is
    # eligible again (SPEC §4). 0 means not in cooldown.
    cooldown_remaining: int = Field(default=0, ge=0)

    @classmethod
    def initial(cls, campaign_type: CampaignType) -> TierState:
        """A fresh campaign type starts at ASSIST, active, no cooldown."""
        return cls(campaign_type=campaign_type)

    @property
    def promotion_eligible(self) -> bool:
        """Promotion may only be considered when active and out of cooldown."""
        return self.standing is Standing.ACTIVE and self.cooldown_remaining == 0
