"""Probation challenge evaluation (SPEC §4).

Why this module exists: a single failure may be a one-off, not a systematic
regression. Rather than assume either, a demoted campaign type must *earn its way
back* by passing a verification challenge — the full golden subset for that type,
run as if live. This module holds the pure decision: given the challenge's
pass/total, did it clear the bar to be restored?

The bar is deliberately the same statistical gate as an initial 0→1 promotion
(SPEC §4: "Pass (≥ tier-0→1 threshold)"). Recovery is held to the same evidence
standard as first earning the tier — probation is not a softer path back.
"""

from __future__ import annotations

from pydantic import BaseModel

from autonomy_ladder.autonomy.wilson import wilson_lower_bound


class ProbationOutcome(BaseModel):
    """The result of a probation challenge, with the evidence behind it."""

    model_config = {"frozen": True}

    passed: bool
    successes: int
    n: int
    wilson_lower_bound: float
    threshold: float

    def evidence(self) -> dict[str, object]:
        """Serializable evidence for the ledger."""
        return {
            "successes": self.successes,
            "n": self.n,
            "wilson_lower_bound": round(self.wilson_lower_bound, 4),
            "threshold": self.threshold,
        }


def evaluate_probation_challenge(successes: int, n: int, threshold: float) -> ProbationOutcome:
    """Decide whether a probation challenge clears the restoration bar.

    Passes iff there was at least one challenge run and the Wilson lower bound of
    the challenge pass rate meets ``threshold`` (the 0→1 promotion bar). Using the
    lower bound, not the raw rate, keeps small challenge sets honest.
    """
    lower = wilson_lower_bound(successes, n)
    passed = n > 0 and lower >= threshold
    return ProbationOutcome(
        passed=passed,
        successes=successes,
        n=n,
        wilson_lower_bound=lower,
        threshold=threshold,
    )
