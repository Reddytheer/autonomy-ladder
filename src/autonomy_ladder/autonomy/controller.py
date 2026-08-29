"""The autonomy controller — deterministic code that governs the agent (SPEC §2 P1).

Why this module exists: this is the "complete mediation" boundary. The agent
produces a campaign and it is scored into a :class:`RunEvaluation`; from there,
*only this code* decides whether the campaign is auto-sent or routed to human
review, and *only this code* moves a campaign type between tiers. No LLM output
can set a tier, argue for a send, or shorten a probation. Every decision is
written to the append-only ledger with its evidence, so the whole thing is
replayable (P4).

The controller holds no mutable tier state of its own: it reconstructs state from
the ledger on every call and writes new facts back. State lives in exactly one
place — the log.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel

from autonomy_ladder.autonomy import constraints as C
from autonomy_ladder.autonomy.ledger import (
    Decision,
    Ledger,
    RunLogEntry,
    TransitionReason,
    reconstruct,
)
from autonomy_ladder.autonomy.probation import evaluate_probation_challenge
from autonomy_ladder.autonomy.tiers import Standing, Tier, TierState, can_autosend
from autonomy_ladder.autonomy.wilson import wilson_lower_bound
from autonomy_ladder.config import BrandPolicy, TiersConfig
from autonomy_ladder.domain import CampaignType, Dimension
from autonomy_ladder.records import DeliverabilityReport, RunEvaluation


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


class PromotionStatus(BaseModel):
    """Where a campaign type stands relative to its next promotion (SPEC §9 view 1).

    Powers the autonomy dashboard: current tier, the Wilson lower bound on recent
    performance, the bar it must clear, and how many more runs are needed before
    promotion is even eligible. Makes the *reason* for the current tier obvious.
    """

    model_config = {"frozen": True}

    campaign_type: CampaignType
    tier: Tier
    effective_tier: Tier  # tier capped by the brand ceiling
    standing: Standing
    cooldown_remaining: int
    next_tier: Tier | None  # None if already at max (or capped)
    window: int | None
    min_runs: int | None
    runs_in_window: int
    successes_in_window: int
    wilson_lower_bound: float
    threshold: float | None
    runs_to_min: int  # how many more runs before the min-runs floor is met
    gate_met: bool  # would a promotion fire right now?
    blocked_by_ceiling: bool


class ControllerDecision(BaseModel):
    """The full, recorded outcome of processing one run."""

    model_config = {"frozen": True}

    run_id: str
    campaign_type: CampaignType
    decision: Decision
    passed: bool
    effective_tier: Tier
    critical_failures: list[Dimension] = []
    blocked: list[C.ConstraintViolation] = []
    demoted: bool = False
    demotion_reason: TransitionReason | None = None
    promoted: bool = False
    promotion_to: Tier | None = None
    state_after: TierState
    rationale: list[str] = []


class AutonomyController:
    """Deterministic governor. Reads state from the ledger, writes facts back."""

    def __init__(
        self,
        ledger: Ledger,
        tiers_config: TiersConfig,
        brand_policy: BrandPolicy,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ledger = ledger
        self._cfg = tiers_config
        self._brand = brand_policy
        self._clock = clock or (lambda: datetime.now(UTC))

    # ---- state helpers ------------------------------------------------------

    def state(self, campaign_type: CampaignType) -> TierState:
        """Reconstruct the current standing for a campaign type from the ledger."""
        return reconstruct(self._ledger.for_type(campaign_type), campaign_type)

    def effective_tier(self, state: TierState) -> Tier:
        """The tier that actually governs sends: earned tier capped by brand ceiling."""
        return Tier(min(state.tier, self._brand.max_allowed_tier))

    def _window_runs(self, campaign_type: CampaignType, window: int) -> list[RunLogEntry]:
        runs = [e for e in self._ledger.for_type(campaign_type) if isinstance(e, RunLogEntry)]
        return runs[-window:]

    def _autonomous_sends_last_24h(self, campaign_type: CampaignType, now: datetime) -> int:
        cutoff = now - timedelta(hours=24)
        count = 0
        for e in self._ledger.for_type(campaign_type):
            if (
                isinstance(e, RunLogEntry)
                and e.decision is Decision.AUTO_SEND
                and datetime.fromisoformat(e.ts) >= cutoff
            ):
                count += 1
        return count

    # ---- reporting ----------------------------------------------------------

    def promotion_status(self, campaign_type: CampaignType) -> PromotionStatus:
        """Compute the dashboard view for one campaign type (no writes)."""
        state = self.state(campaign_type)
        eff = self.effective_tier(state)
        next_tier: Tier | None = None
        if state.tier < Tier.SUPERVISED:
            next_tier = Tier(state.tier + 1)

        blocked_by_ceiling = next_tier is not None and next_tier > self._brand.max_allowed_tier

        window = min_runs = None
        threshold: float | None = None
        successes = runs_in_window = 0
        lower = 0.0
        runs_to_min = 0
        gate_met = False

        if next_tier is not None and not blocked_by_ceiling:
            gate = self._cfg.gate(int(state.tier), int(next_tier))
            window, min_runs, threshold = gate.window, gate.min_runs, gate.wilson_lower_bound_min
            recent = self._window_runs(campaign_type, gate.window)
            runs_in_window = len(recent)
            successes = sum(1 for r in recent if r.passed)
            lower = wilson_lower_bound(successes, runs_in_window)
            runs_to_min = max(0, gate.min_runs - runs_in_window)
            gate_met = (
                state.promotion_eligible
                and runs_in_window >= gate.min_runs
                and lower > gate.wilson_lower_bound_min
            )

        return PromotionStatus(
            campaign_type=campaign_type,
            tier=state.tier,
            effective_tier=eff,
            standing=state.standing,
            cooldown_remaining=state.cooldown_remaining,
            next_tier=next_tier,
            window=window,
            min_runs=min_runs,
            runs_in_window=runs_in_window,
            successes_in_window=successes,
            wilson_lower_bound=lower,
            threshold=threshold,
            runs_to_min=runs_to_min,
            gate_met=gate_met,
            blocked_by_ceiling=blocked_by_ceiling,
        )

    # ---- the main entry point ----------------------------------------------

    def process_run(
        self, evaluation: RunEvaluation, now: datetime | None = None
    ) -> ControllerDecision:
        """Decide what happens to one scored run, and update the ledger.

        Order of operations matters and is deliberate:
          1. read current state (from the ledger);
          2. run the deterministic constraint checks;
          3. decide AUTO_SEND vs HUMAN_REVIEW;
          4. append the run fact;
          5. if a CRITICAL dimension failed, demote + open probation;
          6. otherwise, if the run passed and the Wilson gate is now cleared,
             promote one step.
        Demotion and promotion are mutually exclusive for a single run.
        """
        now = now or self._clock()
        ts = _iso(now)
        ct = self._resolve_type(evaluation.campaign_type)

        state = self.state(ct)
        eff = self.effective_tier(state)

        # 2. deterministic constraints
        violations: list[C.ConstraintViolation] = []
        violations += C.check_segment_eligibility(eff, evaluation.segment)
        violations += C.check_discount(evaluation.discount_pct, self._cfg.constraints)
        violations += C.check_rate_limit(
            self._autonomous_sends_last_24h(ct, now), self._cfg.constraints
        )

        passed = evaluation.passed
        critical_failures = evaluation.critical_failures

        # 3. decision: auto-send only if the run passed, hit no constraint, and the
        #    segment is autosendable at the effective tier. Anything else → review.
        rationale: list[str] = []
        segment_ok = can_autosend(eff, evaluation.segment)
        if passed and not violations and segment_ok:
            decision = Decision.AUTO_SEND
            rationale.append(
                f"Run passed and tier {eff.value} ({eff.name}) permits autonomous send "
                f"to '{evaluation.segment.value}'."
            )
        else:
            decision = Decision.HUMAN_REVIEW
            if not passed:
                if critical_failures:
                    rationale.append(
                        "Routed to review: CRITICAL failure in "
                        + ", ".join(d.value for d in critical_failures)
                        + "."
                    )
                else:
                    rationale.append("Routed to review: run did not pass (brand_voice below 0.75).")
            for v in violations:
                rationale.append(f"Routed to review: {v.message}")
            if passed and not violations and not segment_ok:
                rationale.append(
                    f"Routed to review: tier {eff.value} ({eff.name}) may not autonomously "
                    f"send to '{evaluation.segment.value}'."
                )

        # 4. append the run fact
        self._ledger.append_run(
            campaign_type=ct,
            ts=ts,
            run_id=evaluation.run_id,
            passed=passed,
            decision=decision,
            tier_at_decision=eff,
            segment=evaluation.segment.value,
            discount_pct=evaluation.discount_pct,
            blocked=[v.code.value for v in violations],
        )

        demoted = False
        demotion_reason: TransitionReason | None = None
        promoted = False
        promotion_to: Tier | None = None

        # 5. demotion on a CRITICAL pre-send failure (only if there is a tier to lose)
        if critical_failures and state.tier > Tier.ASSIST:
            self._ledger.append_transition(
                campaign_type=ct,
                ts=ts,
                reason=TransitionReason.DEMOTION_CRITICAL_PRESEND,
                from_tier=state.tier,
                to_tier=Tier.ASSIST,
                standing_after=Standing.PROBATION,
                cooldown_after=0,
                evidence={"critical_failures": [d.value for d in critical_failures]},
                run_id=evaluation.run_id,
            )
            demoted = True
            demotion_reason = TransitionReason.DEMOTION_CRITICAL_PRESEND
            rationale.append(
                "Campaign type demoted to Tier 0 and placed on PROBATION due to CRITICAL "
                "failure. In-flight campaigns of this type must be downgraded to review."
            )

        # 6. promotion — only if we did not just demote
        if not demoted and passed:
            promoted, promotion_to = self._maybe_promote(ct, ts, evaluation.run_id)
            if promoted and promotion_to is not None:
                rationale.append(
                    f"Promoted to Tier {promotion_to.value} ({promotion_to.name}): Wilson "
                    f"lower bound cleared the threshold on sufficient evidence."
                )

        return ControllerDecision(
            run_id=evaluation.run_id,
            campaign_type=ct,
            decision=decision,
            passed=passed,
            effective_tier=eff,
            critical_failures=critical_failures,
            blocked=violations,
            demoted=demoted,
            demotion_reason=demotion_reason,
            promoted=promoted,
            promotion_to=promotion_to,
            state_after=self.state(ct),
            rationale=rationale,
        )

    def _maybe_promote(self, ct: CampaignType, ts: str, run_id: str) -> tuple[bool, Tier | None]:
        """Fire a one-step promotion if the Wilson gate is cleared. Returns (did, to)."""
        state = self.state(ct)  # includes the run just appended (cooldown decremented)
        if not state.promotion_eligible or state.tier >= Tier.SUPERVISED:
            return False, None
        target = Tier(state.tier + 1)
        if target > self._brand.max_allowed_tier:
            return False, None  # capped by brand ceiling — never promote past it

        gate = self._cfg.gate(int(state.tier), int(target))
        recent = self._window_runs(ct, gate.window)
        n = len(recent)
        if n < gate.min_runs:
            return False, None
        successes = sum(1 for r in recent if r.passed)
        lower = wilson_lower_bound(successes, n)
        if lower <= gate.wilson_lower_bound_min:
            return False, None

        self._ledger.append_transition(
            campaign_type=ct,
            ts=ts,
            reason=TransitionReason.PROMOTION,
            from_tier=state.tier,
            to_tier=target,
            standing_after=Standing.ACTIVE,
            cooldown_after=0,
            evidence={
                "successes": successes,
                "n": n,
                "wilson_lower_bound": round(lower, 4),
                "threshold": gate.wilson_lower_bound_min,
                "window": gate.window,
            },
            run_id=run_id,
        )
        return True, target

    # ---- post-send loop -----------------------------------------------------

    def process_deliverability(
        self, report: DeliverabilityReport, now: datetime | None = None
    ) -> ControllerDecision | None:
        """Apply the post-send closed loop (SPEC §4).

        A deliverability breach on any threshold is a CRITICAL post-send failure:
        it demotes the campaign type to Tier 0 and opens probation. Returns None
        if there was no breach or nothing to demote.
        """
        now = now or self._clock()
        ts = _iso(now)
        ct = self._resolve_type(report.campaign_type)
        breaches = C.check_deliverability(report, self._cfg.deliverability_triggers)
        if not breaches:
            return None

        state = self.state(ct)
        if state.tier <= Tier.ASSIST:
            return None  # nothing to demote

        self._ledger.append_transition(
            campaign_type=ct,
            ts=ts,
            reason=TransitionReason.DEMOTION_DELIVERABILITY,
            from_tier=state.tier,
            to_tier=Tier.ASSIST,
            standing_after=Standing.PROBATION,
            cooldown_after=0,
            evidence={"breaches": [b.model_dump() for b in breaches]},
            run_id=report.run_id,
        )
        return ControllerDecision(
            run_id=report.run_id,
            campaign_type=ct,
            decision=Decision.HUMAN_REVIEW,
            passed=False,
            effective_tier=self.effective_tier(self.state(ct)),
            demoted=True,
            demotion_reason=TransitionReason.DEMOTION_DELIVERABILITY,
            state_after=self.state(ct),
            rationale=[
                "Post-send deliverability breach demoted the campaign type to Tier 0 "
                "and opened PROBATION: " + "; ".join(b.message for b in breaches)
            ],
        )

    def run_probation_challenge(
        self,
        campaign_type: CampaignType,
        successes: int,
        n: int,
        now: datetime | None = None,
    ) -> ControllerDecision:
        """Record the outcome of a probation challenge (SPEC §4).

        The golden-subset challenge itself is executed by the eval layer; this
        records the verdict. Pass → restore to Tier 1 and enter cooldown. Fail →
        remain Tier 0, flagged INVESTIGATION_REQUIRED, no automatic re-promotion.
        """
        now = now or self._clock()
        ts = _iso(now)
        ct = self._resolve_type(campaign_type)
        state = self.state(ct)
        if state.standing is not Standing.PROBATION:
            raise ValueError(
                f"Cannot run a probation challenge for {ct.value}: standing is "
                f"{state.standing.value}, not probation."
            )

        threshold = self._cfg.gate(0, 1).wilson_lower_bound_min
        outcome = evaluate_probation_challenge(successes, n, threshold)

        if outcome.passed:
            self._ledger.append_transition(
                campaign_type=ct,
                ts=ts,
                reason=TransitionReason.PROBATION_PASSED,
                from_tier=state.tier,
                to_tier=Tier.BOUNDED,
                standing_after=Standing.ACTIVE,
                cooldown_after=self._cfg.probation.cooldown_runs,
                evidence=outcome.evidence(),
            )
            rationale = [
                f"Probation passed ({successes}/{n}, Wilson lower "
                f"{outcome.wilson_lower_bound:.4f} ≥ {threshold}). Restored to Tier 1; "
                f"cooldown of {self._cfg.probation.cooldown_runs} runs before promotion is "
                f"eligible again."
            ]
            promoted = True
            promotion_to: Tier | None = Tier.BOUNDED
        else:
            self._ledger.append_transition(
                campaign_type=ct,
                ts=ts,
                reason=TransitionReason.PROBATION_FAILED,
                from_tier=state.tier,
                to_tier=Tier.ASSIST,
                standing_after=Standing.INVESTIGATION_REQUIRED,
                cooldown_after=0,
                evidence=outcome.evidence(),
            )
            rationale = [
                f"Probation FAILED ({successes}/{n}, Wilson lower "
                f"{outcome.wilson_lower_bound:.4f} < {threshold}). Remains Tier 0, flagged "
                f"INVESTIGATION_REQUIRED; no automatic re-promotion."
            ]
            promoted = False
            promotion_to = None

        return ControllerDecision(
            run_id=f"probation:{ct.value}",
            campaign_type=ct,
            decision=Decision.HUMAN_REVIEW,
            passed=outcome.passed,
            effective_tier=self.effective_tier(self.state(ct)),
            promoted=promoted,
            promotion_to=promotion_to,
            state_after=self.state(ct),
            rationale=rationale,
        )

    @staticmethod
    def _resolve_type(campaign_type: CampaignType | str) -> CampaignType:
        return (
            campaign_type
            if isinstance(campaign_type, CampaignType)
            else CampaignType(campaign_type)
        )
