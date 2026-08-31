"""Application service — the one place the agent, controller, and queue meet.

Why this exists: the CLI demo and the FastAPI console need the same behaviour —
run (or accept) a scored campaign, let the controller decide, park it in the right
queue lane, and downgrade in-flight work on a demotion. Centralising that here
keeps the controller pure (it still only sees a RunEvaluation) while giving the UI
one object to read the four views from.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from autonomy_ladder.autonomy.controller import (
    AutonomyController,
    ControllerDecision,
    OutcomeResult,
)
from autonomy_ladder.autonomy.ledger import Ledger, OutcomeLogEntry
from autonomy_ladder.config import (
    REPO_ROOT,
    BrandPolicy,
    TiersConfig,
    get_settings,
    load_brand_policy,
    load_tiers_config,
)
from autonomy_ladder.domain import CampaignType, Dimension, SegmentBand, Verdict
from autonomy_ladder.outcomes.simulator import Scenario, simulate
from autonomy_ladder.queue.store import ReviewQueue, queue_item_from_decision
from autonomy_ladder.records import (
    CampaignContent,
    DeliverabilityReport,
    DimensionResult,
    RunEvaluation,
)
from autonomy_ladder.security import SecurityEventStore

# Default send window for queued items (SPEC §5; see docs/open-questions.md OQ-5).
DEFAULT_SEND_WINDOW = timedelta(hours=48)
CANDIDATES_DIR = REPO_ROOT / "evals" / "candidates"


class RunRecord(BaseModel):
    """The full trace of one run, for the Run Detail view (SPEC §9 view 3)."""

    model_config = {"frozen": True}

    run_id: str
    campaign_type: CampaignType
    created_at: str
    content: CampaignContent | None
    evaluation: RunEvaluation
    decision: ControllerDecision
    revisions: int = 0


class RunStore:
    """SQLite store of full run traces keyed by run_id."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS runs "
            "(run_id TEXT PRIMARY KEY, campaign_type TEXT, created_at TEXT, payload TEXT)"
        )
        self._conn.commit()

    def add(self, record: RunRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?)",
            (
                record.run_id,
                record.campaign_type.value,
                record.created_at,
                record.model_dump_json(),
            ),
        )
        self._conn.commit()

    def get(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute("SELECT payload FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else RunRecord.model_validate_json(row[0])

    def recent(self, limit: int = 50) -> list[RunRecord]:
        rows = self._conn.execute(
            "SELECT payload FROM runs ORDER BY created_at DESC, run_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [RunRecord.model_validate_json(r[0]) for r in rows]

    def close(self) -> None:
        self._conn.close()


class AutonomyService:
    """Facade over controller + queue + run store for the CLI and the API."""

    def __init__(
        self,
        controller: AutonomyController,
        queue: ReviewQueue,
        runs: RunStore,
        tiers_config: TiersConfig,
        brand_policy: BrandPolicy,
        security: SecurityEventStore | None = None,
    ) -> None:
        self.controller = controller
        self.queue = queue
        self.runs = runs
        self.tiers_config = tiers_config
        self.brand_policy = brand_policy
        self.security = security or SecurityEventStore(":memory:")

    @classmethod
    def from_paths(cls, base: Path) -> AutonomyService:
        """Open file-backed stores under ``base`` (so CLI-seeded state feeds the API)."""
        tiers = load_tiers_config()
        brand = load_brand_policy()
        ledger = Ledger(base / "ledger.sqlite")
        controller = AutonomyController(ledger, tiers, brand)
        return cls(
            controller=controller,
            queue=ReviewQueue(base / "queue.sqlite"),
            runs=RunStore(base / "runs.sqlite"),
            tiers_config=tiers,
            brand_policy=brand,
            security=SecurityEventStore(base / "security.sqlite"),
        )

    @classmethod
    def default(cls) -> AutonomyService:
        base = (REPO_ROOT / get_settings().ledger_path).parent
        return cls.from_paths(base)

    def record_run(
        self,
        evaluation: RunEvaluation,
        *,
        content: CampaignContent | None = None,
        revisions: int = 0,
        now: datetime | None = None,
        send_window: timedelta = DEFAULT_SEND_WINDOW,
        failure_origin: str | None = None,
    ) -> ControllerDecision:
        """Let the controller decide, queue the item, and persist the run trace."""
        now = now or datetime.now(UTC)
        decision = self.controller.process_run(evaluation, now=now, failure_origin=failure_origin)

        item = queue_item_from_decision(
            evaluation,
            decision,
            created_at=now.isoformat(),
            send_window_expires_at=(now + send_window).isoformat(),
        )
        self.queue.add(item)

        # On demotion, in-flight items of this type drop to review — never sent (SPEC §4).
        if decision.demoted:
            self.queue.downgrade_inflight(decision.campaign_type.value)

        self.runs.add(
            RunRecord(
                run_id=evaluation.run_id,
                campaign_type=decision.campaign_type,
                created_at=now.isoformat(),
                content=content,
                evaluation=evaluation,
                decision=decision,
                revisions=revisions,
            )
        )
        return decision

    def dashboard(self) -> list[dict[str, object]]:
        """Autonomy status for every campaign type (SPEC §9 view 1)."""
        out = []
        for ct in CampaignType:
            status = self.controller.promotion_status(ct)
            out.append(json.loads(status.model_dump_json()))
        return out

    def m1_summary(self) -> dict[str, int]:
        """Quality-failure origin ratio for monitoring requirement M1 (HANDOFF).

        Counts, across every recorded quality failure, how many were explicitly
        instructed by the brief vs originated with the agent. If brief-instructed
        failures dominate, the GS-PD-16 evasion rule should be revisited (ADR 0008).
        """
        brief_instructed = agent_originated = unclassified = 0
        for entry in self.controller._ledger.all():
            if getattr(entry, "outcome", None) is None:
                continue
            if entry.kind == "run" and entry.outcome.value == "quality_failure":
                origin = entry.failure_origin
                if origin == "brief_instructed":
                    brief_instructed += 1
                elif origin == "agent_originated":
                    agent_originated += 1
                else:
                    unclassified += 1
        return {
            "brief_instructed": brief_instructed,
            "agent_originated": agent_originated,
            "unclassified": unclassified,
        }

    # ---- post-send loop (HANDOFF 2, ADR 0009) -------------------------------

    def process_outcome(
        self,
        run_id: str,
        campaign_type: CampaignType,
        metrics: dict[str, float],
        now: datetime | None = None,
    ) -> OutcomeResult:
        """Record a post-send outcome; demote + downgrade in-flight on a breach; and
        flag an eval-passed-but-breached run into evals/candidates/ for triage."""
        report = DeliverabilityReport(
            run_id=run_id,
            campaign_type=campaign_type.value,
            spam_complaint_rate=metrics.get("spam_complaint_rate", 0.0),
            unsubscribe_rate=metrics.get("unsubscribe_rate", 0.0),
            bounce_rate=metrics.get("bounce_rate", 0.0),
        )
        result = self.controller.process_deliverability(report, now=now, metrics=metrics)
        if result.demoted:
            self.queue.downgrade_inflight(campaign_type.value)
        if result.blind_spot:
            self._flag_candidate(run_id, campaign_type, metrics, result.breaches)
        return result

    def _flag_candidate(
        self,
        run_id: str,
        campaign_type: CampaignType,
        metrics: dict[str, float],
        breaches: list[str],
    ) -> None:
        """Write an eval_passed_outcome_failed run to evals/candidates/ (HANDOFF 2)."""
        CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
        record = self.runs.get(run_id)
        scores = (
            {d.value: r.score for d, r in record.evaluation.dimensions.items()} if record else {}
        )
        payload = {
            "run_id": run_id,
            "campaign_type": campaign_type.value,
            "classification": "eval_passed_outcome_failed",
            "note": "Passed every eval but breached deliverability — judge blind spot; "
            "triage into the golden set.",
            "scores": scores,
            "content": json.loads(record.content.model_dump_json())
            if record and record.content
            else None,
            "outcome_metrics": metrics,
            "breaches": breaches,
        }
        (CANDIDATES_DIR / f"{run_id}.json").write_text(json.dumps(payload, indent=2) + "\n")

    def run_simulation(
        self,
        campaign_type: CampaignType,
        n: int,
        scenario: Scenario = Scenario.NOMINAL,
        now: datetime | None = None,
        seed: int = 20260829,
    ) -> dict[str, object]:
        """Run N campaigns through the controller at current tier, simulate outcomes
        for those that auto-sent, and apply confirmations/demotions (HANDOFF 2 A4)."""
        now = now or datetime.now(UTC)
        sent = breach_ct = demotions = blind_spots = 0
        for i in range(n):
            ts = now + timedelta(hours=25 * i)  # spaced so sends aren't rate-limited
            run_id = f"sim-{campaign_type.value}-{seed}-{i}"
            evaluation = _synthetic_evaluation(run_id, campaign_type)
            decision = self.record_run(evaluation, now=ts)
            if decision.decision.value != "auto_send":
                continue
            sent += 1
            scores = {d: r.score for d, r in evaluation.dimensions.items()}
            metrics = simulate(
                run_id=run_id,
                segment=evaluation.segment,
                scores=scores,
                scenario=scenario,
                seed=seed,
                drift=(i / max(1, n - 1)),
            )
            result = self.process_outcome(
                run_id, campaign_type, metrics.as_ledger_metrics(), now=ts
            )
            if result.breached:
                breach_ct += 1
            if result.demoted:
                demotions += 1
            if result.blind_spot:
                blind_spots += 1
        return {
            "campaign_type": campaign_type.value,
            "scenario": scenario.value,
            "auto_sent": sent,
            "breaches": breach_ct,
            "demotions": demotions,
            "blind_spots": blind_spots,
        }

    def outcomes_summary(self) -> list[dict[str, object]]:
        """Per campaign type: predicted quality vs actual outcomes (HANDOFF 2 A4)."""
        entries = self.controller._ledger.all()
        out: list[dict[str, object]] = []
        for ct in CampaignType:
            outcomes = [
                e for e in entries if isinstance(e, OutcomeLogEntry) and e.campaign_type == ct
            ]
            if not outcomes:
                continue
            status = self.controller.promotion_status(ct)
            n = len(outcomes)
            breached = sum(1 for o in outcomes if o.breached)
            row: dict[str, object] = {
                "campaign_type": ct.value,
                "sent": n,
                "breaches": breached,
                "blind_spots": sum(1 for o in outcomes if o.blind_spot),
                "predicted_pass_rate": round(status.wilson_lower_bound, 3),
                "actual_clean_rate": round((n - breached) / n, 3) if n else 0.0,
                "timeline": [
                    {
                        "ts": o.ts,
                        "breached": o.breached,
                        "blind_spot": o.blind_spot,
                        "spam": o.metrics.get("spam_complaint_rate", 0.0),
                    }
                    for o in outcomes[-20:]
                ],
            }
            out.append(row)
        return out

    def close(self) -> None:
        self.controller._ledger.close()
        self.queue.close()
        self.runs.close()
        self.security.close()


def _synthetic_evaluation(run_id: str, campaign_type: CampaignType) -> RunEvaluation:
    """A clean, passing evaluation targeting engaged_30d — used by the simulation so
    the controller path is exercised without the LLM (the outcomes are what vary)."""

    def dim(d: Dimension, score: float) -> DimensionResult:
        return DimensionResult(dimension=d, score=score, verdict=Verdict.PASS)

    return RunEvaluation(
        run_id=run_id,
        campaign_type=campaign_type.value,
        segment=SegmentBand.ENGAGED_30D,
        dimensions={
            Dimension.SEGMENT_CORRECTNESS: dim(Dimension.SEGMENT_CORRECTNESS, 0.95),
            Dimension.CLAIM_GROUNDEDNESS: dim(Dimension.CLAIM_GROUNDEDNESS, 0.93),
            Dimension.BRAND_VOICE: dim(Dimension.BRAND_VOICE, 0.9),
            Dimension.STRUCTURE_QUALITY: dim(Dimension.STRUCTURE_QUALITY, 0.85),
        },
    )
