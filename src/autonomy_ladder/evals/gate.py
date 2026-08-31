"""The regression gate (SPEC §7, updated by HANDOFF Drop 1 + the constraint_block change).

Keyless mode is a DETERMINISTIC decision-routing eval: for each of the 75 golden
cases we take the authored dimension verdicts as given, build the run evaluation,
and replay it through the pure controller routing (:mod:`autonomy_ladder.autonomy.routing`).
We then check the resulting decision and review lane match what the case authored.
No LLM, no API key — this validates the controller/constraint/lane logic (including
the constraint_block change) against Theertha's hand-labeled expectations.

* ``make eval``  → print the routing table.
* ``make gate``  → exit non-zero if routing accuracy regressed vs evals/baseline.json.
* ``--record``   → (step 11, needs a key) run the briefs through the live pipeline
  to cache real judge responses for the separate judge-accuracy measurement.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from autonomy_ladder.autonomy import routing
from autonomy_ladder.autonomy.ledger import Decision
from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.config import REPO_ROOT, load_tiers_config
from autonomy_ladder.domain import Dimension, Verdict
from autonomy_ladder.evals.golden_loader import ExpectedDecision, GoldenCase, load_goldens
from autonomy_ladder.records import DimensionResult, RunEvaluation

BASELINE_PATH = REPO_ROOT / "evals" / "baseline.json"
DEFAULT_TOLERANCE = 0.02

# Map the controller's decision enum onto the goldens' vocabulary.
_DECISION_TO_EXPECTED = {
    Decision.AUTO_SEND: ExpectedDecision.AUTO_SEND,
    Decision.HUMAN_REVIEW: ExpectedDecision.REVIEW_QUEUE,
}


def _evaluation_from_case(case: GoldenCase) -> RunEvaluation:
    """Build a RunEvaluation whose verdicts/scores encode the case's expectations."""
    v = case.expected

    def dim(d: Dimension, verdict: Verdict) -> DimensionResult:
        score = 0.9 if verdict is Verdict.PASS else (0.5 if d is Dimension.BRAND_VOICE else 0.1)
        return DimensionResult(dimension=d, score=score, verdict=verdict)

    dims = {
        Dimension.SEGMENT_CORRECTNESS: dim(Dimension.SEGMENT_CORRECTNESS, v.segment_correctness),
        Dimension.CLAIM_GROUNDEDNESS: dim(Dimension.CLAIM_GROUNDEDNESS, v.claim_groundedness),
        Dimension.BRAND_VOICE: dim(Dimension.BRAND_VOICE, v.brand_voice),
        Dimension.STRUCTURE_QUALITY: dim(Dimension.STRUCTURE_QUALITY, Verdict.PASS),
    }
    return RunEvaluation(
        run_id=case.id,
        campaign_type=case.campaign_type.value,
        segment=case.requested_segment,
        discount_pct=case.discount_pct,
        dimensions=dims,
    )


class CaseResult(BaseModel):
    model_config = {"frozen": True}

    id: str
    tests_layer: str
    decision_ok: bool
    lane_ok: bool

    @property
    def ok(self) -> bool:
        return self.decision_ok and self.lane_ok


class RoutingReport(BaseModel):
    model_config = {"frozen": True}

    n: int
    accuracy: float
    decision_accuracy: float
    lane_accuracy: float
    mismatches: list[str]
    regressions: list[str] = []

    @property
    def ok(self) -> bool:
        return not self.regressions


def evaluate_routing(cases: list[GoldenCase]) -> RoutingReport:
    """Replay every golden through the pure routing and score decision + lane."""
    constraints = load_tiers_config().constraints
    results: list[CaseResult] = []
    mismatches: list[str] = []

    for case in cases:
        evaluation = _evaluation_from_case(case)
        # Rate-limit cases are stateful; the golden encodes the context via its layer.
        sends_24h = 3 if case.tests_layer == "controller:rate_limit" else 0
        result = routing.decide(
            evaluation,
            effective_tier=Tier(case.agent_tier_at_run),
            autonomous_sends_last_24h=sends_24h,
            constraints=constraints,
        )
        got_decision = _DECISION_TO_EXPECTED[result.decision]
        got_lane = result.lane.value if result.lane is not None else None
        want_lane = case.expected_lane.value if case.expected_lane is not None else None
        decision_ok = got_decision is case.expected_decision
        lane_ok = got_lane == want_lane
        results.append(
            CaseResult(
                id=case.id, tests_layer=case.tests_layer, decision_ok=decision_ok, lane_ok=lane_ok
            )
        )
        if not (decision_ok and lane_ok):
            mismatches.append(
                f"{case.id}: decision {got_decision.value}/{case.expected_decision.value} "
                f"lane {got_lane}/{want_lane}"
            )

    n = len(results)
    return RoutingReport(
        n=n,
        accuracy=sum(r.ok for r in results) / n if n else 0.0,
        decision_accuracy=sum(r.decision_ok for r in results) / n if n else 0.0,
        lane_accuracy=sum(r.lane_ok for r in results) / n if n else 0.0,
        mismatches=mismatches,
    )


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, float]:
    if not path.exists():
        return {}
    data: dict[str, float] = json.loads(path.read_text())
    return data


def write_baseline(report: RoutingReport, path: Path = BASELINE_PATH) -> None:
    path.write_text(
        json.dumps(
            {
                "kind": "routing_accuracy",
                "n": report.n,
                "accuracy": round(report.accuracy, 4),
                "decision_accuracy": round(report.decision_accuracy, 4),
                "lane_accuracy": round(report.lane_accuracy, 4),
                "tolerance": DEFAULT_TOLERANCE,
            },
            indent=2,
        )
        + "\n"
    )


def check_against_baseline(
    report: RoutingReport, baseline: dict[str, float], tolerance: float
) -> RoutingReport:
    regressions: list[str] = []
    base = baseline.get("accuracy")
    if base is not None and report.accuracy < base - tolerance:
        regressions.append(
            f"routing accuracy {report.accuracy:.3f} < baseline {base:.3f} - tol {tolerance:.2f}"
        )
    return report.model_copy(update={"regressions": regressions})


def _print(report: RoutingReport, baseline: dict[str, float]) -> None:
    base = baseline.get("accuracy")
    print(f"golden routing eval — {report.n} cases (keyless, deterministic)")
    print(f"  decision accuracy : {report.decision_accuracy:.3f}")
    print(f"  lane accuracy     : {report.lane_accuracy:.3f}")
    print(
        f"  overall accuracy  : {report.accuracy:.3f}"
        + (f"  (baseline {base:.3f})" if base else "")
    )
    for m in report.mismatches:
        print(f"  mismatch: {m}")


def _record() -> int:
    """Step 11 (needs a key): run briefs through the live pipeline to cache judge
    responses for the judge-accuracy measurement. Deferred in the keyless build."""
    print(
        "make fixtures / --record runs the live pipeline over the golden briefs and "
        "requires ANTHROPIC_API_KEY (step 11). It is intentionally not run in the "
        "keyless build; see docs/evaluation.md.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="autonomy-ladder eval / regression gate")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--report", action="store_true", help="print the routing table (keyless)")
    mode.add_argument("--check", action="store_true", help="fail on routing regression (keyless)")
    mode.add_argument("--record", action="store_true", help="record live judge fixtures (step 11)")
    mode.add_argument("--update-baseline", action="store_true", help="rewrite evals/baseline.json")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)

    if args.record:
        return _record()

    cases = load_goldens()
    if not cases:
        print("No golden cases found in evals/goldens/.", file=sys.stderr)
        return 1

    report = evaluate_routing(cases)

    if args.update_baseline:
        write_baseline(report)
        print(f"Wrote baseline for {report.n} cases: accuracy {report.accuracy:.4f}.")
        return 0

    baseline = load_baseline()
    if args.check:
        report = check_against_baseline(report, baseline, args.tolerance)

    _print(report, baseline)

    if args.check:
        if report.regressions:
            print("\nREGRESSIONS:", file=sys.stderr)
            for r in report.regressions:
                print(f"  - {r}", file=sys.stderr)
            return 1
        print("\nGate passed: no routing regressions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
