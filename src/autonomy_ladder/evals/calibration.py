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

HUMAN_LABELS_PATH = REPO_ROOT / "evals" / "calibration" / "calibration_labels.jsonl"

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


# A dimension with fewer than this many failure labels yields a directional, not
# precise, kappa — one disagreement moves it materially (HANDOFF Drop 3 caveat).
MIN_FAILURES_FOR_STABLE_KAPPA = 8

# The three graded dimensions (structure_quality is advisory and not labeled).
_GRADED = ("segment_correctness", "claim_groundedness", "brand_voice")


class HumanLabel(BaseModel):
    model_config = {"frozen": True}

    segment_correctness: Verdict
    claim_groundedness: Verdict
    brand_voice: Verdict


class LabelRow(BaseModel):
    """One human-labeled calibration case (HANDOFF Drop 3 schema).

    Provenance is carried through and must not be stripped: these labels were
    drafted then reviewed case by case, not independently labeled.
    """

    model_config = {"frozen": True, "extra": "allow"}

    id: str
    campaign_type: str
    band: str
    human_label: HumanLabel
    labeler_confidence: str = ""
    critique: str = ""
    provenance: str = ""


def load_labels(path: Path = HUMAN_LABELS_PATH) -> list[LabelRow]:
    return [
        LabelRow.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def human_verdicts_by_dimension(rows: list[LabelRow]) -> dict[str, list[Verdict]]:
    """The human verdict for each graded dimension, in case order."""
    out: dict[str, list[Verdict]] = {d: [] for d in _GRADED}
    for r in rows:
        out["segment_correctness"].append(r.human_label.segment_correctness)
        out["claim_groundedness"].append(r.human_label.claim_groundedness)
        out["brand_voice"].append(r.human_label.brand_voice)
    return out


class DimensionLabelSummary(BaseModel):
    model_config = {"frozen": True}

    dimension: str
    n: int
    failures: int
    stable_sample: bool


def label_summary(rows: list[LabelRow]) -> list[DimensionLabelSummary]:
    """Per-dimension label counts and whether the sample is big enough for a
    stable kappa (the kappa itself needs recorded judge verdicts — step 12)."""
    human = human_verdicts_by_dimension(rows)
    summaries = []
    for d in _GRADED:
        fails = sum(1 for v in human[d] if v is Verdict.FAIL)
        summaries.append(
            DimensionLabelSummary(
                dimension=d,
                n=len(human[d]),
                failures=fails,
                stable_sample=fails >= MIN_FAILURES_FOR_STABLE_KAPPA,
            )
        )
    return summaries


def main(argv: list[str] | None = None) -> int:
    import json

    rows = load_labels()
    summaries = label_summary(rows)
    metrics_path = REPO_ROOT / "evals" / "metrics.json"
    kappa_by_dim: dict[str, float] = {}
    if metrics_path.exists():
        metrics = json.loads(metrics_path.read_text())
        kappa_by_dim = {k["dimension"]: k["kappa"] for k in metrics.get("kappa", [])}

    print(f"calibration label set — {len(rows)} cases ({rows[0].provenance if rows else 'n/a'})")
    print(f"{'dimension':<22}{'n':>4}{'failures':>10}{'kappa':>16}")
    print("-" * 52)
    for s in summaries:
        caveat = "" if s.stable_sample else "  (few failures: directional)"
        kv = kappa_by_dim.get(s.dimension)
        cell = f"{kv:.3f}" if kv is not None else "run make fixtures"
        below = "  << 0.60: needs revision" if kv is not None and kv < KAPPA_MIN else ""
        print(f"{s.dimension:<22}{s.n:>4}{s.failures:>10}{cell:>16}{caveat}{below}")
    if kappa_by_dim:
        print(
            "\nCohen's kappa is measured in faithful-render mode (evals/metrics.json). "
            "kappa < 0.60 means the judge prompt needs revision; see docs/evaluation.md."
        )
    else:
        print(
            "\nNo evals/metrics.json yet — run `make fixtures` (needs a key) to record judge "
            "verdicts, then this shows per-dimension kappa. See docs/evaluation.md."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
