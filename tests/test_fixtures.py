"""The three-metric harness, replayed keylessly off committed fixtures (ADR 0011).

This doubles as a fixture-integrity guard: replaying every golden through the full
pipeline exercises every committed fixture, so a truncated/unparseable entry or a
silent judge-accuracy regression fails here, in `make check`, with no API key.
"""

from __future__ import annotations

from autonomy_ladder.domain import Dimension, Verdict
from autonomy_ladder.evals.fixtures import (
    JUDGE_TOLERANCE,
    _flaw_dims,
    compute_metrics,
    load_judge_baseline,
    run_all,
)
from autonomy_ladder.evals.golden_loader import load_goldens
from autonomy_ladder.evals.llm import ReplayClient

_GOLDENS = load_goldens()
_RUNS = run_all(ReplayClient(), _GOLDENS)
_METRICS = compute_metrics(_RUNS, _GOLDENS)


def test_flaw_dims_reads_authored_failures() -> None:
    by_id = {g.id: g for g in _GOLDENS}
    # GS-PL-08 authored claim_groundedness=fail; GS-PL-04 authored all pass.
    assert Dimension.CLAIM_GROUNDEDNESS in _flaw_dims(by_id["GS-PL-08"])
    assert _flaw_dims(by_id["GS-PL-04"]) == []


def test_all_fixtures_parse_no_refusals() -> None:
    # A truncated/unparseable fixture surfaces as a faithful- or normal-render
    # refusal; the committed fixtures must be complete.
    refused = [r.id for r in _RUNS.values() if r.faithful_refused or r.normal_refused]
    assert refused == [], f"unparseable/missing fixtures for: {refused}"


def test_replay_holds_judge_accuracy_baseline() -> None:
    baseline = load_judge_baseline()
    base = baseline.get("accuracy")
    assert isinstance(base, (int, float))
    assert _METRICS.judge_accuracy >= base - JUDGE_TOLERANCE


def test_paired_comparative_goldens_reproduce() -> None:
    # The pair that exposed the sibling-context gap (ADR 0010): a true superlative
    # passes, a false one fails — in faithful mode.
    claim = Dimension.CLAIM_GROUNDEDNESS.value
    assert _RUNS["GS-PL-07"].faithful[claim] == Verdict.PASS.value
    assert _RUNS["GS-PL-08"].faithful[claim] == Verdict.FAIL.value
