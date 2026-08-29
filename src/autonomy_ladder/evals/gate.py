"""The regression gate (SPEC §7).

``make eval`` runs the golden set off cached fixtures and prints a results table.
``make gate`` does the same and exits non-zero if any dimension's verdict accuracy
has regressed beyond tolerance versus ``evals/baseline.json`` — wired into CI so a
prompt change that quietly degrades quality cannot merge.

All of the above run with NO API key (they replay committed fixtures). ``--record``
is the only mode that calls the API, to (re)generate those fixtures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from autonomy_ladder.config import REPO_ROOT
from autonomy_ladder.data.loaders import catalog_index, load_brand_rules
from autonomy_ladder.domain import Dimension
from autonomy_ladder.evals.golden_loader import GoldenCase, load_goldens
from autonomy_ladder.evals.judges import build_prompt, judge_all, model_for
from autonomy_ladder.evals.llm import (
    FixtureStore,
    LLMClient,
    MissingFixtureError,
    RecordingClient,
    ReplayClient,
    prompt_hash,
)
from autonomy_ladder.records import DimensionResult

BASELINE_PATH = REPO_ROOT / "evals" / "baseline.json"
DEFAULT_TOLERANCE = 0.05


class DimensionScore(BaseModel):
    model_config = {"frozen": True}

    dimension: Dimension
    n: int
    accuracy: float  # fraction of cases where the judge verdict matched the gold verdict
    mean_score: float


class GateResult(BaseModel):
    model_config = {"frozen": True}

    per_dimension: list[DimensionScore]
    regressions: list[str]
    missing_fixtures: int

    @property
    def ok(self) -> bool:
        return not self.regressions and self.missing_fixtures == 0


def _context(case: GoldenCase):  # type: ignore[no-untyped-def]
    from autonomy_ladder.evals.judges import JudgeContext

    return JudgeContext(brief=case.brief, brand_rules=load_brand_rules(), catalog=catalog_index())


def evaluate_goldens(cases: list[GoldenCase], client: LLMClient) -> tuple[GateResult, int]:
    """Run judges over every golden case and aggregate per-dimension accuracy."""
    correct: dict[Dimension, int] = dict.fromkeys(Dimension, 0)
    total: dict[Dimension, int] = dict.fromkeys(Dimension, 0)
    score_sum: dict[Dimension, float] = dict.fromkeys(Dimension, 0.0)
    missing = 0

    for case in cases:
        ctx = _context(case)
        try:
            results: dict[Dimension, DimensionResult] = judge_all(case.content, ctx, client)
        except MissingFixtureError:
            missing += 1
            continue
        for dim, expected in case.expected.items():
            res = results[dim]
            total[dim] += 1
            score_sum[dim] += res.score
            if res.verdict == expected.verdict:
                correct[dim] += 1

    per_dim = [
        DimensionScore(
            dimension=dim,
            n=total[dim],
            accuracy=(correct[dim] / total[dim]) if total[dim] else 0.0,
            mean_score=(score_sum[dim] / total[dim]) if total[dim] else 0.0,
        )
        for dim in Dimension
        if total[dim] > 0
    ]
    return GateResult(per_dimension=per_dim, regressions=[], missing_fixtures=missing), missing


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, float]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    dims: dict[str, dict[str, float]] = data.get("dimensions", {})
    return {k: v["accuracy"] for k, v in dims.items()}


def check_against_baseline(
    result: GateResult, baseline: dict[str, float], tolerance: float
) -> GateResult:
    regressions: list[str] = []
    for ds in result.per_dimension:
        base = baseline.get(ds.dimension.value)
        if base is not None and ds.accuracy < base - tolerance:
            regressions.append(
                f"{ds.dimension.value}: accuracy {ds.accuracy:.3f} < baseline {base:.3f} "
                f"- tol {tolerance:.2f}"
            )
    return result.model_copy(update={"regressions": regressions})


def _print_report(result: GateResult, baseline: dict[str, float]) -> None:
    print(f"{'dimension':<22}{'n':>4}{'accuracy':>11}{'mean_score':>12}{'baseline':>10}")
    print("-" * 59)
    for ds in result.per_dimension:
        base = baseline.get(ds.dimension.value)
        base_s = f"{base:.3f}" if base is not None else "  —"
        print(
            f"{ds.dimension.value:<22}{ds.n:>4}{ds.accuracy:>11.3f}"
            f"{ds.mean_score:>12.3f}{base_s:>10}"
        )
    if result.missing_fixtures:
        print(f"\n! {result.missing_fixtures} case(s) had no cached fixture (run `make fixtures`).")


def seed_synthetic_fixtures(cases: list[GoldenCase], store: FixtureStore | None = None) -> int:
    """Bootstrap placeholder fixtures from the goldens' authored expectations.

    These let `make eval`/`make gate` run keyless before any live recording. They
    are clearly synthetic — regenerate real ones with `make fixtures`. Returns the
    number of fixture files written.
    """
    store = store or FixtureStore()
    written = 0
    for case in cases:
        ctx = _context(case)
        for dim, expected in case.expected.items():
            system, user = build_prompt(dim, case.content, ctx)
            model = model_for(dim)
            response = json.dumps(
                {
                    "score": expected.score,
                    "verdict": expected.verdict.value,
                    "reasoning": "seed fixture (synthetic; regenerate with make fixtures)",
                    "evidence": [],
                }
            )
            store.put(
                prompt_hash(model, system, user),
                model=model,
                system=system,
                user=user,
                response=response,
            )
            written += 1
    return written


def write_baseline(
    result: GateResult, path: Path = BASELINE_PATH, tolerance: float = DEFAULT_TOLERANCE
) -> None:
    path.write_text(
        json.dumps(
            {
                "tolerance": tolerance,
                "dimensions": {
                    ds.dimension.value: {"accuracy": round(ds.accuracy, 4), "n": ds.n}
                    for ds in result.per_dimension
                },
            },
            indent=2,
        )
        + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="autonomy-ladder eval / regression gate")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--report", action="store_true", help="run goldens and print a table (keyless)"
    )
    mode.add_argument(
        "--check", action="store_true", help="fail on regression vs baseline (keyless)"
    )
    mode.add_argument("--record", action="store_true", help="record real fixtures (needs API key)")
    mode.add_argument(
        "--seed-synthetic",
        action="store_true",
        help="write placeholder fixtures + baseline (bootstrap)",
    )
    parser.add_argument("--tolerance", type=float, default=None)
    args = parser.parse_args(argv)

    cases = load_goldens()
    if not cases:
        print("No golden cases found in evals/goldens/.", file=sys.stderr)
        return 1

    if args.seed_synthetic:
        n = seed_synthetic_fixtures(cases)
        result, _ = evaluate_goldens(cases, ReplayClient())
        write_baseline(result, tolerance=args.tolerance or DEFAULT_TOLERANCE)
        print(f"Seeded {n} synthetic fixtures and wrote baseline for {len(cases)} cases.")
        return 0

    client: LLMClient = RecordingClient() if args.record else ReplayClient()
    result, _ = evaluate_goldens(cases, client)
    baseline = load_baseline()
    tolerance = args.tolerance if args.tolerance is not None else DEFAULT_TOLERANCE

    if args.check:
        result = check_against_baseline(result, baseline, tolerance)

    _print_report(result, baseline)

    if args.record:
        print(f"\nRecorded fixtures for {len(cases)} cases.")
        return 0

    if args.check:
        if result.regressions:
            print("\nREGRESSIONS:", file=sys.stderr)
            for r in result.regressions:
                print(f"  - {r}", file=sys.stderr)
        if not result.ok:
            return 1
        print("\nGate passed: no regressions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
