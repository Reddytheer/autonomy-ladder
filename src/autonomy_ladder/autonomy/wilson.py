"""Wilson score confidence interval — the statistical basis for promotion.

Why this module exists (SPEC §4, ADR 0004): promotion must require *statistical
confidence*, not a raw streak. A perfect 10/10 is not evidence — it is a small
sample. The lower bound of the Wilson score interval on the pass rate captures
exactly the intuition we want: it rises with the observed pass rate *and* with
the sample size, so 10/10 stays below threshold while 47/50 clears it.

Implemented by hand (no scipy) to keep the autonomy core dependency-free and
auditable. A reviewer can check this formula against SPEC §4 line by line.
"""

from __future__ import annotations

import math

# 95% confidence. Locked by SPEC §4; not a tunable.
Z_95 = 1.96


def wilson_lower_bound(successes: int, n: int, z: float = Z_95) -> float:
    """Return the lower bound of the Wilson score interval for a pass rate.

    ``successes`` passing runs out of ``n`` total. With no data (``n == 0``) the
    lower bound is 0.0 — the agent has proven nothing, so it gets no credit. This
    is the conservative direction and matches the controller's "earn it" stance.

    Formula (SPEC §4):

        p̂     = successes / n
        lower = (p̂ + z²/2n − z·√((p̂(1−p̂) + z²/4n)/n)) / (1 + z²/n)
    """
    if successes < 0 or n < 0:
        raise ValueError("successes and n must be non-negative")
    if successes > n:
        raise ValueError("successes cannot exceed n")
    if n == 0:
        return 0.0

    p_hat = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    center = p_hat + z2 / (2.0 * n)
    margin = z * math.sqrt((p_hat * (1.0 - p_hat) + z2 / (4.0 * n)) / n)
    return (center - margin) / denominator
