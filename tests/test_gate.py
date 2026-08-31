"""The keyless decision-routing gate over the 75 golden cases (SPEC §7, ADR 0008)."""

from __future__ import annotations

from autonomy_ladder.evals.gate import (
    check_against_baseline,
    evaluate_routing,
    load_baseline,
)
from autonomy_ladder.evals.golden_loader import load_goldens


def test_all_goldens_load() -> None:
    cases = load_goldens()
    assert len(cases) == 75


def test_controller_reproduces_every_authored_decision_and_lane() -> None:
    """The pure routing must match Theertha's expected_decision AND expected_lane for
    every case — this is the keyless validation of the constraint_block + lane logic."""
    report = evaluate_routing(load_goldens())
    assert report.mismatches == []
    assert report.decision_accuracy == 1.0
    assert report.lane_accuracy == 1.0


def test_gate_passes_against_committed_baseline() -> None:
    report = evaluate_routing(load_goldens())
    checked = check_against_baseline(report, load_baseline(), tolerance=0.02)
    assert checked.ok
    assert checked.regressions == []


def test_gate_flags_a_routing_regression() -> None:
    """A drop in routing accuracy below baseline - tolerance must be flagged."""
    report = evaluate_routing(load_goldens())
    degraded = report.model_copy(update={"accuracy": 0.5})
    checked = check_against_baseline(degraded, {"accuracy": 1.0}, tolerance=0.02)
    assert not checked.ok
    assert checked.regressions
