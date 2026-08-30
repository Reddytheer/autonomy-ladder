"""FastAPI app for the operator console (SPEC §9).

Serves four read views (dashboard, review queue, run detail, trust ledger) and one
write action (batch approve). It reads everything from the file-backed
:class:`AutonomyService`, so state seeded by `autonomy-ladder seed-ui` (or a live
demo) shows up here. If the ledger is empty on startup, it seeds the demo scenario
so `make ui` opens onto a populated console with no API key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from autonomy_ladder.autonomy.ledger import TransitionLogEntry
from autonomy_ladder.config import REPO_ROOT
from autonomy_ladder.demo import seed_demo
from autonomy_ladder.service import AutonomyService

WEB_DIR = REPO_ROOT / "web"

# Opened at import (fast — just opens the file-backed stores). Seeding happens on
# startup, not import, so tests can swap in their own service first.
_service = AutonomyService.default()


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Populate the demo scenario so `make ui` opens onto a full console (no key).
    if not _service.controller._ledger.all():
        seed_demo(_service)
    yield


app = FastAPI(title="autonomy-ladder operator console", lifespan=_lifespan)


def _now() -> datetime:
    return datetime.now(UTC)


@app.get("/api/dashboard")
def dashboard() -> JSONResponse:
    """Tier, Wilson lower bound vs threshold, and runs-to-promotion per type (view 1)."""
    return JSONResponse(_service.dashboard())


@app.get("/api/queue")
def queue() -> JSONResponse:
    """The two lanes with risk, SLA, and expiry (view 2)."""
    view = _service.queue.lanes(_now())
    return JSONResponse(view.model_dump(mode="json"))


@app.get("/api/ledger")
def ledger() -> JSONResponse:
    """Chronological audit of tier changes with evidence, newest first (view 4)."""
    entries = _service.controller._ledger.all()
    transitions = [e.model_dump(mode="json") for e in entries if isinstance(e, TransitionLogEntry)]
    transitions.reverse()
    return JSONResponse({"transitions": transitions})


@app.get("/api/runs")
def runs() -> JSONResponse:
    return JSONResponse([r.model_dump(mode="json") for r in _service.runs.recent(50)])


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str) -> JSONResponse:
    """Full trace of one run: agent output, dimension scores, controller decision (view 3)."""
    record = _service.runs.get(run_id)
    if record is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(record.model_dump(mode="json"))


class ApproveRequest(BaseModel):
    run_ids: list[str]


@app.post("/api/queue/approve")
def approve(req: ApproveRequest) -> JSONResponse:
    n = _service.queue.approve_batch(req.run_ids)
    return JSONResponse({"approved": n})


# Static frontend (must be mounted after the API routes).
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
