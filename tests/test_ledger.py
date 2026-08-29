"""The ledger is append-only and tier state is fully reconstructible by replay
(SPEC §2 P4, §14)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autonomy_ladder.autonomy.controller import AutonomyController
from autonomy_ladder.autonomy.ledger import Ledger, reconstruct
from autonomy_ladder.autonomy.tiers import Standing, Tier
from autonomy_ladder.config import BrandPolicy
from autonomy_ladder.domain import CampaignType, Dimension

from .conftest import make_controller, make_eval, make_tiers_config

CT = CampaignType.NEWSLETTER
OTHER = CampaignType.PROMOTIONAL_DISCOUNT


def _drive_a_history(c: AutonomyController) -> None:
    for _ in range(25):
        c.process_run(make_eval(passed=True, campaign_type=CT))  # -> BOUNDED
    c.process_run(
        make_eval(passed=False, critical_fail=Dimension.SEGMENT_CORRECTNESS, campaign_type=CT)
    )  # demote+probation
    c.run_probation_challenge(CT, successes=25, n=25)  # restore + cooldown


def test_state_equals_replay_of_the_whole_ledger() -> None:
    c = make_controller()
    _drive_a_history(c)
    live = c.state(CT)
    replayed = reconstruct(c._ledger.all(), CT)
    assert replayed == live


def test_state_survives_close_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "ledger.sqlite"
    c = AutonomyController(Ledger(path), make_tiers_config(), BrandPolicy(max_allowed_tier=2))
    _drive_a_history(c)
    before = c.state(CT)
    c._ledger.close()

    # Fresh process: only the file exists. State must reconstruct identically.
    reopened = Ledger(path)
    after = reconstruct(reopened.all(), CT)
    assert after == before
    assert after.tier is Tier.BOUNDED
    assert after.standing is Standing.ACTIVE
    assert after.cooldown_remaining == 20


def test_campaign_types_are_independent() -> None:
    c = make_controller()
    for _ in range(25):
        c.process_run(make_eval(passed=True, campaign_type=CT))
    # OTHER type has no history at all.
    assert c.state(CT).tier is Tier.BOUNDED
    assert c.state(OTHER).tier is Tier.ASSIST


def test_ledger_is_append_only() -> None:
    c = make_controller()
    c.process_run(make_eval(passed=True, campaign_type=CT))
    conn = c._ledger._conn
    with pytest.raises(sqlite3.Error):
        conn.execute("UPDATE ledger SET passed = 0 WHERE seq = 1")
    with pytest.raises(sqlite3.Error):
        conn.execute("DELETE FROM ledger WHERE seq = 1")


def test_evidence_is_recorded_with_transitions() -> None:
    """Every autonomy decision is recorded with its evidence (SPEC §2 P4)."""
    c = make_controller()
    for _ in range(25):
        c.process_run(make_eval(passed=True, campaign_type=CT))
    transitions = [e for e in c._ledger.for_type(CT) if e.kind == "transition"]
    assert transitions
    promo = transitions[-1]
    assert "wilson_lower_bound" in promo.evidence
    assert "n" in promo.evidence and "threshold" in promo.evidence
