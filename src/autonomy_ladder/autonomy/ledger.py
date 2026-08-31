"""The trust ledger — an append-only, replayable record of everything that
governs autonomy.

Why this module exists (SPEC §2 P4, §9 view 4): every autonomy decision must be
recorded with its evidence, and *given the ledger, any tier state must be
reconstructible.* This module is the single writer of that record and the single
source from which :func:`reconstruct` folds current state.

Two entry kinds share one ordered log:

* ``run`` — the outcome of processing one campaign run (pass/fail, the decision,
  the tier it was decided at). These feed Wilson statistics, the 24h rate limit,
  and the cooldown countdown.
* ``transition`` — a change to a campaign type's tier or standing, with the
  evidence that caused it. These are the audit trail shown in the Trust Ledger UI.

Append-only is enforced at the database level: UPDATE and DELETE raise. The log
can only grow, which is what makes replay trustworthy.
"""

from __future__ import annotations

import sqlite3
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from autonomy_ladder.autonomy.tiers import Standing, Tier, TierState
from autonomy_ladder.domain import CampaignType


class TransitionReason(StrEnum):
    """Why a tier/standing transition happened — recorded as evidence (SPEC §4)."""

    PROMOTION = "promotion"  # Wilson gate cleared
    DEMOTION_CRITICAL_PRESEND = "demotion_critical_presend"  # a CRITICAL dim failed
    DEMOTION_DELIVERABILITY = "demotion_deliverability"  # post-send breach
    PROBATION_PASSED = "probation_passed"  # golden-subset challenge passed -> restore
    PROBATION_FAILED = "probation_failed"  # challenge failed -> investigation required


class Decision(StrEnum):
    """What the controller decided to do with a run (SPEC §6)."""

    AUTO_SEND = "auto_send"
    HUMAN_REVIEW = "human_review"


class OutcomeClass(StrEnum):
    """Why a run did (not) advance tier standing (HANDOFF spec change, ADR 0008).

    Only QUALITY_PASS/QUALITY_FAILURE count in the Wilson window; CONSTRAINT_BLOCK
    is excluded entirely so the agent is neither rewarded nor punished for the
    requester's out-of-bounds choices.
    """

    QUALITY_PASS = "quality_pass"  # clean, autonomy-eligible target -> Wilson success
    QUALITY_FAILURE = "quality_failure"  # a dimension failed -> Wilson failure
    CONSTRAINT_BLOCK = "constraint_block"  # blocked by a rule -> excluded from Wilson


class _BaseEntry(BaseModel):
    model_config = {"frozen": True}

    seq: int
    ts: str  # ISO-8601 UTC timestamp, supplied by the caller (injectable for tests)
    campaign_type: CampaignType


class RunLogEntry(_BaseEntry):
    """The outcome of processing one campaign run."""

    kind: Literal["run"] = "run"
    run_id: str
    passed: bool
    decision: Decision
    outcome: OutcomeClass = OutcomeClass.QUALITY_PASS  # Wilson eligibility (ADR 0008)
    tier_at_decision: Tier
    segment: str
    discount_pct: float = 0.0
    blocked: list[str] = Field(default_factory=list)  # constraint codes, if any
    # M1 instrumentation: for quality failures, was the failing behaviour explicitly
    # instructed by the brief, or did it originate with the agent? (HANDOFF M1)
    failure_origin: str | None = None


class TransitionLogEntry(_BaseEntry):
    """A change to a campaign type's tier or standing, with evidence."""

    kind: Literal["transition"] = "transition"
    reason: TransitionReason
    from_tier: Tier
    to_tier: Tier
    standing_after: Standing
    cooldown_after: int = 0
    evidence: dict[str, object] = Field(default_factory=dict)
    run_id: str | None = None


class OutcomeLogEntry(_BaseEntry):
    """A post-send outcome for a previously-sent run (HANDOFF 2, ADR 0009).

    The closed loop: real deliverability metrics recorded against the originating
    run. A breach drives a demotion (recorded as a separate transition, same path as
    a critical eval failure). ``blind_spot`` marks the highest-value case — a run
    that passed every eval but breached anyway (eval_passed_outcome_failed).
    """

    kind: Literal["outcome"] = "outcome"
    run_id: str
    metrics: dict[str, float] = Field(default_factory=dict)
    breached: bool = False
    breaches: list[str] = Field(default_factory=list)
    blind_spot: bool = False


LedgerEntry = Annotated[
    RunLogEntry | TransitionLogEntry | OutcomeLogEntry, Field(discriminator="kind")
]
_ENTRY_ADAPTER: TypeAdapter[LedgerEntry] = TypeAdapter(LedgerEntry)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    seq           INTEGER PRIMARY KEY,
    ts            TEXT NOT NULL,
    campaign_type TEXT NOT NULL,
    kind          TEXT NOT NULL,
    payload       TEXT NOT NULL
);
-- Append-only guarantee (SPEC §2 P4): the log may only grow.
CREATE TRIGGER IF NOT EXISTS ledger_no_update
    BEFORE UPDATE ON ledger BEGIN SELECT RAISE(FAIL, 'ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_no_delete
    BEFORE DELETE ON ledger BEGIN SELECT RAISE(FAIL, 'ledger is append-only'); END;
"""


class Ledger:
    """Append-only event log backed by SQLite (file or ``:memory:``).

    The Ledger assigns sequence numbers and persists entries; it never interprets
    them. Interpretation (folding entries into a :class:`TierState`) lives in
    :func:`reconstruct`, so that reconstruction is a pure function of the log.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the FastAPI app can share one ledger; this
        # project is single-process and does not write concurrently.
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _next_seq(self) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM ledger").fetchone()
        return int(row[0]) + 1

    def _insert(self, entry: LedgerEntry) -> None:
        self._conn.execute(
            "INSERT INTO ledger (seq, ts, campaign_type, kind, payload) VALUES (?, ?, ?, ?, ?)",
            (
                entry.seq,
                entry.ts,
                entry.campaign_type.value,
                entry.kind,
                _ENTRY_ADAPTER.dump_json(entry).decode(),
            ),
        )
        self._conn.commit()

    def append_run(
        self,
        *,
        campaign_type: CampaignType,
        ts: str,
        run_id: str,
        passed: bool,
        decision: Decision,
        tier_at_decision: Tier,
        segment: str,
        outcome: OutcomeClass | None = None,
        discount_pct: float = 0.0,
        blocked: list[str] | None = None,
        failure_origin: str | None = None,
    ) -> RunLogEntry:
        # Derive a sensible outcome when the caller does not classify explicitly
        # (used by direct-seeding tests): passed -> quality pass, else failure.
        if outcome is None:
            outcome = OutcomeClass.QUALITY_PASS if passed else OutcomeClass.QUALITY_FAILURE
        entry = RunLogEntry(
            seq=self._next_seq(),
            ts=ts,
            campaign_type=campaign_type,
            run_id=run_id,
            passed=passed,
            decision=decision,
            outcome=outcome,
            tier_at_decision=tier_at_decision,
            segment=segment,
            discount_pct=discount_pct,
            blocked=blocked or [],
            failure_origin=failure_origin,
        )
        self._insert(entry)
        return entry

    def append_transition(
        self,
        *,
        campaign_type: CampaignType,
        ts: str,
        reason: TransitionReason,
        from_tier: Tier,
        to_tier: Tier,
        standing_after: Standing,
        cooldown_after: int = 0,
        evidence: dict[str, object] | None = None,
        run_id: str | None = None,
    ) -> TransitionLogEntry:
        entry = TransitionLogEntry(
            seq=self._next_seq(),
            ts=ts,
            campaign_type=campaign_type,
            reason=reason,
            from_tier=from_tier,
            to_tier=to_tier,
            standing_after=standing_after,
            cooldown_after=cooldown_after,
            evidence=evidence or {},
            run_id=run_id,
        )
        self._insert(entry)
        return entry

    def append_outcome(
        self,
        *,
        campaign_type: CampaignType,
        ts: str,
        run_id: str,
        metrics: dict[str, float],
        breached: bool,
        breaches: list[str] | None = None,
        blind_spot: bool = False,
    ) -> OutcomeLogEntry:
        entry = OutcomeLogEntry(
            seq=self._next_seq(),
            ts=ts,
            campaign_type=campaign_type,
            run_id=run_id,
            metrics=metrics,
            breached=breached,
            breaches=breaches or [],
            blind_spot=blind_spot,
        )
        self._insert(entry)
        return entry

    def all(self) -> list[LedgerEntry]:
        """Every entry in sequence order."""
        rows = self._conn.execute("SELECT payload FROM ledger ORDER BY seq").fetchall()
        return [_ENTRY_ADAPTER.validate_json(r[0]) for r in rows]

    def for_type(self, campaign_type: CampaignType) -> list[LedgerEntry]:
        rows = self._conn.execute(
            "SELECT payload FROM ledger WHERE campaign_type = ? ORDER BY seq",
            (campaign_type.value,),
        ).fetchall()
        return [_ENTRY_ADAPTER.validate_json(r[0]) for r in rows]

    def close(self) -> None:
        self._conn.close()


def reconstruct(entries: list[LedgerEntry], campaign_type: CampaignType) -> TierState:
    """Fold ledger entries into the current :class:`TierState` for a type.

    This is the whole of P4: state is *derived*, never stored. The fold applies
    only recorded facts — it does not recompute Wilson statistics — so a replay
    yields the same state even if thresholds later change.

    Fold rules:
      * a ``run`` entry decrements an active cooldown by one (mechanical);
      * a ``transition`` entry sets tier/standing/cooldown to the recorded values;
      * an ``outcome`` entry records a post-send fact and does not itself change tier
        state — any demotion it triggers is recorded as its own transition, so state
        stays a pure fold of transitions (P4).
    """
    state = TierState.initial(campaign_type)
    for entry in entries:
        if entry.campaign_type != campaign_type:
            continue
        if isinstance(entry, OutcomeLogEntry):
            continue
        if isinstance(entry, RunLogEntry):
            if state.standing is Standing.ACTIVE and state.cooldown_remaining > 0:
                state = state.model_copy(
                    update={"cooldown_remaining": state.cooldown_remaining - 1}
                )
        else:  # TransitionLogEntry
            state = state.model_copy(
                update={
                    "tier": entry.to_tier,
                    "standing": entry.standing_after,
                    "cooldown_remaining": entry.cooldown_after,
                }
            )
    return state
