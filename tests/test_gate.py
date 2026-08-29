"""The regression gate runs keyless off fixtures and fails on degradation (SPEC §7, §14)."""

from __future__ import annotations

import json

from autonomy_ladder.evals.gate import (
    check_against_baseline,
    evaluate_goldens,
    load_baseline,
)
from autonomy_ladder.evals.golden_loader import load_goldens
from autonomy_ladder.evals.llm import ReplayClient


class _DegradedClient:
    """A stand-in for a degraded judge/prompt: always votes fail with a low score."""

    def complete(self, *, model: str, system: str, user: str) -> str:
        return json.dumps(
            {"score": 0.1, "verdict": "fail", "reasoning": "degraded", "evidence": []}
        )


def test_eval_runs_keyless_off_fixtures() -> None:
    cases = load_goldens()
    assert cases
    result, missing = evaluate_goldens(cases, ReplayClient())
    assert missing == 0
    # Seeded fixtures reproduce the authored verdicts exactly.
    assert all(ds.accuracy == 1.0 for ds in result.per_dimension)


def test_gate_passes_against_baseline() -> None:
    cases = load_goldens()
    result, _ = evaluate_goldens(cases, ReplayClient())
    checked = check_against_baseline(result, load_baseline(), tolerance=0.05)
    assert checked.ok
    assert checked.regressions == []


def test_gate_fails_on_degraded_prompt() -> None:
    """SPEC §14: `make gate` must exit non-zero on an intentionally degraded prompt."""
    cases = load_goldens()
    result, _ = evaluate_goldens(cases, _DegradedClient())
    checked = check_against_baseline(result, load_baseline(), tolerance=0.05)
    assert not checked.ok
    assert checked.regressions  # at least one dimension regressed


def test_missing_fixtures_fail_the_gate(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A prompt change that loses its fixtures must fail loudly, never pass silently."""
    from autonomy_ladder.evals.llm import FixtureStore

    empty_client = ReplayClient(FixtureStore(tmp_path))
    cases = load_goldens()
    result, missing = evaluate_goldens(cases, empty_client)
    assert missing == len(cases)
    assert not result.ok
