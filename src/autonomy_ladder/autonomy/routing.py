"""Pure routing decision — decide, classify, and assign a lane (ADR 0008).

Factored out of the controller so that one function answers three questions for a
scored run, with no ledger mutation and no I/O:

* **decision** — AUTO_SEND or HUMAN_REVIEW;
* **outcome** — QUALITY_PASS / QUALITY_FAILURE / CONSTRAINT_BLOCK, which decides
  whether the run counts in the Wilson window (HANDOFF spec change);
* **lane** — for review items, batch or judgment (SPEC §5, refined to the goldens).

Being pure, this is exactly what the keyless decision-routing gate replays over the
75 golden cases, and what the controller calls before it writes to the ledger.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from autonomy_ladder.autonomy import constraints as C
from autonomy_ladder.autonomy.ledger import Decision, OutcomeClass
from autonomy_ladder.autonomy.tiers import Tier, can_autosend
from autonomy_ladder.config import Constraints
from autonomy_ladder.records import RunEvaluation


class Lane(StrEnum):
    """The two review lanes (SPEC §5)."""

    BATCH = "batch"
    JUDGMENT = "judgment"


# Blocks that tell us nothing about the agent's readiness for autonomy, so they are
# excluded from the Wilson window entirely (ADR 0008, HANDOFF): discount over
# ceiling and rate limit are unrelated to quality; never-autonomous segments target
# audiences that can never be autonomous at any tier. Note that a mere
# tier-ineligible-but-clean run (SEGMENT_NOT_ELIGIBLE_FOR_TIER) is NOT here — it
# still counts as a quality success (Option 1 in the owner decision).
_WILSON_EXCLUDING: frozenset[C.ConstraintCode] = frozenset(
    {
        C.ConstraintCode.NEVER_AUTONOMOUS_SEGMENT,
        C.ConstraintCode.DISCOUNT_EXCEEDS_CEILING,
        C.ConstraintCode.RATE_LIMIT_EXCEEDED,
    }
)

# Constraint blocks serious enough for the judgment lane; the rest (tier boundary,
# rate limit) go to batch. Matches the 46 REVIEW goldens' expected_lane.
_JUDGMENT_CONSTRAINTS: frozenset[C.ConstraintCode] = frozenset(
    {
        C.ConstraintCode.NEVER_AUTONOMOUS_SEGMENT,
        C.ConstraintCode.DISCOUNT_EXCEEDS_CEILING,
    }
)


class RoutingResult(BaseModel):
    model_config = {"frozen": True}

    decision: Decision
    lane: Lane | None
    outcome: OutcomeClass
    violations: list[C.ConstraintViolation]

    @property
    def wilson_eligible(self) -> bool:
        return self.outcome is not OutcomeClass.CONSTRAINT_BLOCK

    @property
    def is_success(self) -> bool:
        return self.outcome is OutcomeClass.QUALITY_PASS

    @property
    def blocked_codes(self) -> list[str]:
        return [v.code.value for v in self.violations]


def decide(
    evaluation: RunEvaluation,
    *,
    effective_tier: Tier,
    autonomous_sends_last_24h: int,
    constraints: Constraints,
) -> RoutingResult:
    """Decide what happens to one scored run (no side effects)."""
    violations: list[C.ConstraintViolation] = []
    violations += C.check_segment_eligibility(effective_tier, evaluation.segment)
    violations += C.check_discount(evaluation.discount_pct, constraints)
    violations += C.check_rate_limit(autonomous_sends_last_24h, constraints)
    codes = {v.code for v in violations}

    # Outcome (Wilson eligibility). Quality failure takes precedence over any
    # constraint block (a run that is both counts as a quality failure).
    if not evaluation.passed:
        outcome = OutcomeClass.QUALITY_FAILURE
    elif codes & _WILSON_EXCLUDING:
        outcome = OutcomeClass.CONSTRAINT_BLOCK
    else:
        outcome = OutcomeClass.QUALITY_PASS

    # Decision: auto-send only if clean, unconstrained, and the tier permits the send.
    if evaluation.passed and not violations and can_autosend(effective_tier, evaluation.segment):
        return RoutingResult(
            decision=Decision.AUTO_SEND, lane=None, outcome=outcome, violations=violations
        )

    # Lane: critical failures and the serious constraint blocks demand judgment; a
    # brand-voice-only slip, a tier-boundary block, or a rate-limit block batches.
    judgment = bool(evaluation.critical_failures) or bool(codes & _JUDGMENT_CONSTRAINTS)
    lane = Lane.JUDGMENT if judgment else Lane.BATCH
    return RoutingResult(
        decision=Decision.HUMAN_REVIEW, lane=lane, outcome=outcome, violations=violations
    )
