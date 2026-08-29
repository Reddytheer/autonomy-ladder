"""Cohen's kappa and the calibration report (SPEC §7)."""

from __future__ import annotations

import pytest

from autonomy_ladder.domain import Verdict
from autonomy_ladder.evals.calibration import (
    calibrate_from_labels,
    cohen_kappa,
    load_labels,
)

P, F = Verdict.PASS, Verdict.FAIL


def test_perfect_agreement_is_one() -> None:
    assert cohen_kappa([P, F, P, F], [P, F, P, F]) == pytest.approx(1.0)


def test_chance_level_is_near_zero() -> None:
    # Judge alternates, human alternates oppositely at balanced base rates.
    k = cohen_kappa([P, P, F, F], [P, F, P, F])
    assert k == pytest.approx(0.0, abs=1e-9)


def test_all_same_label_degenerate() -> None:
    # Both raters say pass to everything: total agreement, kappa defined as 1.0.
    assert cohen_kappa([P, P, P], [P, P, P]) == 1.0
    # Disagreement when one is constant and the other is not -> 0.0 (p_e == 1 path
    # only triggers when both are the same constant; here p_e < 1).
    assert cohen_kappa([P, P, F], [P, P, P]) <= 0.0


def test_empty_is_zero() -> None:
    assert cohen_kappa([], []) == 0.0


def test_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        cohen_kappa([P], [P, F])


def test_calibration_report_from_committed_labels() -> None:
    rows = load_labels()
    assert len(rows) == 40
    report = calibrate_from_labels(rows)
    dims = {c.dimension for c in report}
    assert dims == {
        "segment_correctness",
        "claim_groundedness",
        "brand_voice",
        "structure_quality",
    }
    assert all(0 <= c.kappa <= 1 for c in report)
