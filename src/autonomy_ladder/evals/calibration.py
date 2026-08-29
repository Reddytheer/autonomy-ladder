"""Judge calibration via Cohen's kappa (SPEC §7).

Why this exists: a judge that agrees with a human only as often as chance is
worthless, even if its raw accuracy looks high. Cohen's kappa corrects for chance
agreement. We compute per-dimension kappa between judge verdicts and ~40
human-labeled cases; if kappa < 0.6 for a dimension, the judge prompt needs work —
and we say so in the docs rather than hiding it (SPEC §7).

Kappa is implemented by hand (no sklearn) to keep the eval layer dependency-light
and the arithmetic auditable.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel

from autonomy_ladder.config import REPO_ROOT
from autonomy_ladder.domain import Verdict

HUMAN_LABELS_PATH = REPO_ROOT / "evals" / "calibration" / "human_labels.jsonl"

# Threshold below which a judge is considered poorly calibrated (SPEC §7).
KAPPA_MIN = 0.60


def cohen_kappa(a: Sequence[Verdict], b: Sequence[Verdict]) -> float:
    """Cohen's kappa for two raters over the same items (labels: pass/fail).

    Returns 1.0 for perfect agreement, 0.0 for chance-level, negative for
    worse-than-chance. With no items, returns 0.0 (nothing demonstrated). When
    both raters are perfectly constant and identical, agreement is total and we
    return 1.0 (the degenerate p_e == 1 case).
    """
    if len(a) != len(b):
        raise ValueError("rater sequences must be the same length")
    n = len(a)
    if n == 0:
        return 0.0

    observed = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n

    labels = (Verdict.PASS, Verdict.FAIL)
    p_e = 0.0
    for label in labels:
        pa = sum(1 for x in a if x == label) / n
        pb = sum(1 for x in b if x == label) / n
        p_e += pa * pb

    if p_e >= 1.0:
        # Both raters gave the same single label to everything.
        return 1.0 if observed == 1.0 else 0.0
    return (observed - p_e) / (1.0 - p_e)


class DimensionCalibration(BaseModel):
    model_config = {"frozen": True}

    dimension: str
    n: int
    kappa: float
    agreement: float
    well_calibrated: bool


def calibrate(
    judge_verdicts: dict[str, list[Verdict]],
    human_verdicts: dict[str, list[Verdict]],
) -> list[DimensionCalibration]:
    """Per-dimension calibration report comparing judge vs human verdicts."""
    results: list[DimensionCalibration] = []
    for dim in sorted(human_verdicts):
        human = human_verdicts[dim]
        judge = judge_verdicts.get(dim, [])
        k = cohen_kappa(judge, human)
        agree = (
            sum(1 for x, y in zip(judge, human, strict=False) if x == y) / len(human)
            if human
            else 0.0
        )
        results.append(
            DimensionCalibration(
                dimension=dim,
                n=len(human),
                kappa=round(k, 4),
                agreement=round(agree, 4),
                well_calibrated=k >= KAPPA_MIN,
            )
        )
    return results


class LabelRow(BaseModel):
    """One human-labeled calibration row (SPEC §7)."""

    model_config = {"frozen": True}

    id: str
    dimension: str
    human_verdict: Verdict
    model_verdict: Verdict
    critique: str = ""


def load_labels(path: Path = HUMAN_LABELS_PATH) -> list[LabelRow]:
    return [
        LabelRow.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def calibrate_from_labels(rows: list[LabelRow]) -> list[DimensionCalibration]:
    """Group rows by dimension and compute per-dimension calibration."""
    human: dict[str, list[Verdict]] = {}
    judge: dict[str, list[Verdict]] = {}
    for r in rows:
        human.setdefault(r.dimension, []).append(r.human_verdict)
        judge.setdefault(r.dimension, []).append(r.model_verdict)
    return calibrate(judge, human)


def main(argv: list[str] | None = None) -> int:
    rows = load_labels()
    report = calibrate_from_labels(rows)
    print(f"{'dimension':<22}{'n':>4}{'kappa':>9}{'agreement':>11}{'calibrated':>12}")
    print("-" * 58)
    for c in report:
        flag = "yes" if c.well_calibrated else "NO (<0.6)"
        print(f"{c.dimension:<22}{c.n:>4}{c.kappa:>9.3f}{c.agreement:>11.3f}{flag:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
