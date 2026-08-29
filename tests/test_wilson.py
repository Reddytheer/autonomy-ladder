"""Wilson lower-bound arithmetic (SPEC §4). These are the numbers every promotion
decision rests on, so they are pinned exactly."""

from __future__ import annotations

import math

import pytest

from autonomy_ladder.autonomy.wilson import wilson_lower_bound


def test_no_data_gives_zero() -> None:
    # No evidence -> no credit. The conservative direction.
    assert wilson_lower_bound(0, 0) == 0.0


@pytest.mark.parametrize(
    ("successes", "n", "expected"),
    [
        (10, 10, 0.7225),
        (20, 20, 0.8389),
        (25, 25, 0.8668),
        (47, 50, 0.8378),
        (48, 50, 0.8654),
        (50, 50, 0.9286),
        (92, 100, 0.8500),
        (94, 100, 0.8752),
    ],
)
def test_known_values(successes: int, n: int, expected: float) -> None:
    assert wilson_lower_bound(successes, n) == pytest.approx(expected, abs=1e-4)


def test_monotonic_in_sample_size_at_fixed_rate() -> None:
    # A perfect record's lower bound rises with n — more evidence, more confidence.
    perfect = [wilson_lower_bound(k, k) for k in (5, 10, 20, 50, 100)]
    assert perfect == sorted(perfect)
    assert all(0.0 < v < 1.0 for v in perfect)


def test_rejects_impossible_inputs() -> None:
    with pytest.raises(ValueError):
        wilson_lower_bound(11, 10)
    with pytest.raises(ValueError):
        wilson_lower_bound(-1, 10)


def test_formula_matches_spec_reference() -> None:
    # Recompute the spec formula independently and compare (guards against a typo
    # in the implementation drifting from SPEC §4).
    s, n, z = 47, 50, 1.96
    p = s / n
    z2 = z * z
    lower = (p + z2 / (2 * n) - z * math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)) / (1 + z2 / n)
    assert wilson_lower_bound(s, n) == pytest.approx(lower, abs=1e-12)
