"""Cohen's kappa and the calibration report (SPEC §7)."""

from __future__ import annotations

import pytest

from autonomy_ladder.domain import Verdict
from autonomy_ladder.evals.calibration import (
    cohen_kappa,
    human_verdicts_by_dimension,
    label_summary,
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


def test_calibration_labels_load_and_summarize() -> None:
    rows = load_labels()
    assert len(rows) == 46
    # Provenance must be preserved (HANDOFF: not independent human labeling).
    assert all(r.provenance == "ai_drafted_human_reviewed" for r in rows)

    summ = {s.dimension: s for s in label_summary(rows)}
    assert set(summ) == {"segment_correctness", "claim_groundedness", "brand_voice"}
    # HANDOFF failure counts: segment 5, claim 17, brand 7.
    assert summ["segment_correctness"].failures == 5
    assert summ["claim_groundedness"].failures == 17
    assert summ["brand_voice"].failures == 7
    # segment_correctness has too few failures for a stable kappa — must be flagged.
    assert summ["segment_correctness"].stable_sample is False


def test_kappa_over_human_verdicts_is_computable() -> None:
    """cohen_kappa works on the loaded human labels vs a perfect stand-in rater."""
    rows = load_labels()
    human = human_verdicts_by_dimension(rows)
    for dim, verdicts in human.items():
        assert cohen_kappa(verdicts, verdicts) == 1.0, dim
