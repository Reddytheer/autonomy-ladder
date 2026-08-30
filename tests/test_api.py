"""Operator-console API smoke tests over an isolated in-memory service (SPEC §9)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from autonomy_ladder.api import app as appmod
from autonomy_ladder.autonomy.controller import AutonomyController
from autonomy_ladder.autonomy.ledger import Ledger
from autonomy_ladder.config import BrandPolicy
from autonomy_ladder.demo import seed_demo
from autonomy_ladder.queue.store import ReviewQueue
from autonomy_ladder.service import AutonomyService, RunStore

from .conftest import make_tiers_config


def _client() -> TestClient:
    tiers = make_tiers_config()
    brand = BrandPolicy(max_allowed_tier=2)
    svc = AutonomyService(
        controller=AutonomyController(Ledger(":memory:"), tiers, brand),
        queue=ReviewQueue(":memory:"),
        runs=RunStore(":memory:"),
        tiers_config=tiers,
        brand_policy=brand,
    )
    seed_demo(svc)
    appmod._service = svc  # swap in the isolated, pre-seeded service
    return TestClient(appmod.app)


def test_dashboard_endpoint() -> None:
    r = _client().get("/api/dashboard")
    assert r.status_code == 200
    assert len(r.json()) == 5


def test_queue_endpoint_has_both_lanes() -> None:
    body = _client().get("/api/queue").json()
    assert body["batch"] and body["judgment"]


def test_ledger_endpoint_lists_transitions() -> None:
    body = _client().get("/api/ledger").json()
    reasons = {t["reason"] for t in body["transitions"]}
    assert "promotion" in reasons


def test_runs_and_run_detail() -> None:
    client = _client()
    runs = client.get("/api/runs").json()
    assert runs
    detail = client.get(f"/api/runs/{runs[0]['run_id']}")
    assert detail.status_code == 200
    assert "evaluation" in detail.json()
    assert client.get("/api/runs/does-not-exist").status_code == 404


def test_batch_approve() -> None:
    client = _client()
    batch = client.get("/api/queue").json()["batch"]
    ids = [d["item"]["run_id"] for d in batch]
    resp = client.post("/api/queue/approve", json={"run_ids": ids})
    assert resp.status_code == 200
    assert resp.json()["approved"] == len(ids)
