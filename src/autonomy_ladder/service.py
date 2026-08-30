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

from autonomy_ladder.autonomy.controller import AutonomyController, ControllerDecision
from autonomy_ladder.autonomy.ledger import Ledger
from autonomy_ladder.config import (
    REPO_ROOT,
    BrandPolicy,
    TiersConfig,
    get_settings,
    load_brand_policy,
    load_tiers_config,
)
from autonomy_ladder.domain import CampaignType
from autonomy_ladder.queue.store import ReviewQueue, queue_item_from_decision
from autonomy_ladder.records import CampaignContent, RunEvaluation

# Default send window for queued items (SPEC §5; see docs/open-questions.md OQ-5).
DEFAULT_SEND_WINDOW = timedelta(hours=48)


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
    ) -> None:
        self.controller = controller
        self.queue = queue
        self.runs = runs
        self.tiers_config = tiers_config
        self.brand_policy = brand_policy

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
    ) -> ControllerDecision:
        """Let the controller decide, queue the item, and persist the run trace."""
        now = now or datetime.now(UTC)
        decision = self.controller.process_run(evaluation, now=now)

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

    def close(self) -> None:
        self.controller._ledger.close()
        self.queue.close()
        self.runs.close()
