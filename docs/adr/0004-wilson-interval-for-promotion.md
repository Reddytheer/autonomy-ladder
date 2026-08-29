# 0004 — Wilson score lower bound gates promotion

Status: accepted. Implements SPEC §4. Resolves docs/open-questions.md OQ-1, OQ-2.

## Context

The published progressive-autonomy literature specifies promotion after
"hundreds of successful operations" — a raw count. A raw streak is a bad gate for
two reasons: it ignores sample size (a perfect 10/10 is not evidence, it is a small
sample) and it ignores confidence (10 successes and 100 successes both look
"perfect" but justify very different trust). We need a single number that rises
with *both* the observed pass rate and the amount of evidence, so that a large
near-perfect record clears the bar and a tiny perfect one does not. That number is
the **lower bound of the Wilson score confidence interval** on the pass rate. This
is the project's original contribution over the "hundreds of operations" framing:
promotion on statistical confidence, not on a count.

## Decision

Gate every promotion on the Wilson lower bound (z = 1.96, 95% confidence),
computed by hand in `autonomy/wilson.py` and applied in
`AutonomyController._maybe_promote`. The gates (`config/tiers.yaml`) are:

| Transition | Window | Min runs | Wilson lower bound must exceed |
|---|---|---|---|
| 0 → 1 | last 50 runs | **25** | **0.85** |
| 1 → 2 | last 100 runs | 50 | 0.92 |

**On the resolved numbers (OQ-1, OQ-2).** SPEC §4 locks the 0→1 bar at 0.85 but
its prose and the §14 checklist say "47/50 promotes." That is arithmetically loose:
with 95% confidence 47/50 gives a Wilson lower bound of **0.8378**, which is *below*
0.85 and does **not** promote. Per SPEC §0 the sourced threshold wins, so 0.85
stays and the canonical example is corrected to **48/50 → 0.8654 (promotes); 47/50
→ 0.8378 (does not)**. Separately, the spec's `min_runs = 20` floor was
**non-binding**: at n = 20 even a perfect 20/20 gives **0.8389 < 0.85**, so the
statistical gate already rejects everything the floor would — dead configuration.
Owner-directed, the 0→1 floor was raised **20 → 25**, because 25/25 → **0.8668** is
the earliest point at which promotion is arithmetically possible, making the floor
mark a real boundary. The 1→2 floor (min 50, 0.92) is left as specified; it is
binding, since 50/50 → 0.9286 > 0.92. Worked reference figures:

| record | Wilson lower bound | vs 0.85 |
|---|---|---|
| 10/10 | 0.7225 | fail |
| 20/20 | 0.8389 | fail |
| 25/25 | 0.8668 | pass |
| 47/50 | 0.8378 | fail |
| 48/50 | 0.8654 | pass |

## Alternatives considered

A **raw N-in-a-row streak** was rejected: it is the literature's approach and
conflates sample size with confidence. A **simple pass-rate threshold** (e.g. "95%
over the window") was rejected because at small n it promotes on noise — 19/20 is
95% but statistically weak. **Wald / normal-approximation intervals** were rejected
because they misbehave badly near p = 1.0 and small n, precisely the regime that
matters here; Wilson is well-behaved there, which is why it is the standard choice.

## Consequences

Promotion requires a high pass rate *and* enough evidence, so "10/10 does not
promote, 48/50 does" is provable and is asserted in `tests/test_promotion.py`
(SPEC §14). The gate is dependency-free and auditable line by line against the
formula. The honest cost is latency: a campaign type must accrue real volume — at
least 25 runs, and realistically more given any imperfection — before it can climb,
which is the intended conservatism, not a defect.
