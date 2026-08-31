"""Deterministic outcome simulator (HANDOFF 2, A1).

Produces post-send metrics for a sent campaign. Outcomes correlate with pre-send
quality but *imperfectly* — the imperfection is the whole point: a simulator where
good scores always produce good outcomes would make the loop decorative. The noise
is wide enough that a well-scored campaign occasionally breaches anyway, which is
exactly the judge-blind-spot case the loop exists to find.

Seeded per (seed, run_id, scenario) via SHA-256 so results are stable across
processes and runs (Python's built-in hash() is salted and must not be used).
"""

from __future__ import annotations

import argparse
import hashlib
import math
from enum import StrEnum

from pydantic import BaseModel

from autonomy_ladder.domain import Dimension, SegmentBand

# Breach thresholds (SPEC §4 / HANDOFF 2, A2). Kept here so the simulator can
# deliberately cross them in the judge_blindspot scenario.
SPAM_THRESHOLD = 0.0008
UNSUB_THRESHOLD = 0.003
BOUNCE_THRESHOLD = 0.005


class Scenario(StrEnum):
    NOMINAL = "nominal"
    JUDGE_BLINDSPOT = "judge_blindspot"  # a campaign passing all evals that breaches
    DEGRADING = "degrading"  # slow drift into breach across runs


# Base rates by segment band (engaged_30d best, engaged_180d worst). Well below the
# breach thresholds for a healthy send.
_BASE = {
    SegmentBand.ENGAGED_30D: (0.00020, 0.0012, 0.0020),
    SegmentBand.ENGAGED_60D: (0.00028, 0.0016, 0.0026),
    SegmentBand.ENGAGED_90D: (0.00036, 0.0020, 0.0032),
    SegmentBand.ENGAGED_180D: (0.00050, 0.0028, 0.0042),
    SegmentBand.ALL_SUBSCRIBERS: (0.00060, 0.0032, 0.0048),
    SegmentBand.FIRST_TIME_BUYERS: (0.00045, 0.0026, 0.0040),
    SegmentBand.LAPSED: (0.00070, 0.0036, 0.0050),
}
_OPEN_RATE = {  # (open, click) baselines
    SegmentBand.ENGAGED_30D: (0.55, 0.14),
    SegmentBand.ENGAGED_60D: (0.45, 0.10),
    SegmentBand.ENGAGED_90D: (0.38, 0.08),
}


class OutcomeMetrics(BaseModel):
    model_config = {"frozen": True}

    spam_complaint_rate: float
    unsubscribe_rate: float
    bounce_rate: float
    open_rate: float
    click_rate: float
    attributed_revenue: float

    def as_ledger_metrics(self) -> dict[str, float]:
        return {k: round(v, 6) for k, v in self.model_dump().items()}


def _rng_floats(seed: int, run_id: str, scenario: str, n: int) -> list[float]:
    """A stable stream of Gaussian-ish floats from a content hash (no global RNG)."""
    out: list[float] = []
    i = 0
    while len(out) < n:
        h = hashlib.sha256(f"{seed}:{run_id}:{scenario}:{i}".encode()).digest()
        # Two uniforms per 8 bytes -> Box-Muller for a normal draw.
        u1 = int.from_bytes(h[0:8], "big") / 2**64 or 1e-12
        u2 = int.from_bytes(h[8:16], "big") / 2**64
        out.append(math.sqrt(-2.0 * math.log(u1)) * math.cos(2 * math.pi * u2))
        i += 1
    return out[:n]


def simulate(
    *,
    run_id: str,
    segment: SegmentBand,
    scores: dict[Dimension, float],
    scenario: Scenario = Scenario.NOMINAL,
    seed: int = 20260829,
    drift: float = 0.0,
) -> OutcomeMetrics:
    """Simulate deliverability for one sent campaign.

    ``scores`` are the pre-send dimension scores (0-1). ``drift`` (0-1) is used by the
    ``degrading`` scenario to worsen outcomes across a batch. The noise is wide by
    design: under some seeds a well-scored campaign still breaches.
    """
    base_spam, base_unsub, base_bounce = _BASE[segment]
    seg_score = scores.get(Dimension.SEGMENT_CORRECTNESS, 1.0)
    claim_score = scores.get(Dimension.CLAIM_GROUNDEDNESS, 1.0)
    brand_score = scores.get(Dimension.BRAND_VOICE, 1.0)

    # Penalties: weak on the dimension that matters for that outcome.
    spam = base_spam * (1 + 2.5 * (1 - seg_score) + 2.0 * (1 - claim_score))
    unsub = base_unsub * (1 + 2.5 * (1 - brand_score))
    bounce = base_bounce * (1 + 0.5 * (1 - claim_score))

    # Gaussian noise wide enough that a well-scored campaign sometimes breaches
    # anyway (~5% of the time), but not so wide the loop fires constantly.
    z = _rng_floats(seed, run_id, scenario.value, 3)
    spam = max(0.0, spam + z[0] * 0.00025)
    unsub = max(0.0, unsub + z[1] * 0.0010)
    bounce = max(0.0, bounce + z[2] * 0.0012)

    if scenario is Scenario.JUDGE_BLINDSPOT:
        # Force a breach that pre-send quality did not predict.
        spam = max(spam, SPAM_THRESHOLD * 1.4)
    elif scenario is Scenario.DEGRADING:
        spam += drift * SPAM_THRESHOLD * 1.5
        unsub += drift * UNSUB_THRESHOLD * 1.2

    open_base, click_base = _OPEN_RATE.get(segment, (0.30, 0.06))
    open_rate = max(0.0, min(1.0, open_base * (0.6 + 0.4 * brand_score)))
    click_rate = max(0.0, min(1.0, click_base * (0.5 + 0.5 * claim_score)))
    # Revenue is suppressed by weak claims (customers who feel misled do not buy).
    attributed_revenue = round(1000.0 * open_rate * click_rate * (0.4 + 0.6 * claim_score), 2)

    return OutcomeMetrics(
        spam_complaint_rate=spam,
        unsubscribe_rate=unsub,
        bounce_rate=bounce,
        open_rate=open_rate,
        click_rate=click_rate,
        attributed_revenue=attributed_revenue,
    )


def breaches(metrics: OutcomeMetrics) -> list[str]:
    """The deliverability thresholds this outcome crossed."""
    out = []
    if metrics.spam_complaint_rate > SPAM_THRESHOLD:
        out.append("spam_complaint_rate")
    if metrics.unsubscribe_rate > UNSUB_THRESHOLD:
        out.append("unsubscribe_rate")
    if metrics.bounce_rate > BOUNCE_THRESHOLD:
        out.append("bounce_rate")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="outcome simulator")
    parser.add_argument("--seed", type=int, default=20260829)
    parser.add_argument("--scenario", choices=[s.value for s in Scenario], default="nominal")
    args = parser.parse_args(argv)
    scenario = Scenario(args.scenario)
    print(f"scenario={scenario.value} seed={args.seed}")
    good = dict.fromkeys(Dimension, 0.95)
    for seg in (SegmentBand.ENGAGED_30D, SegmentBand.ENGAGED_90D):
        m = simulate(
            run_id=f"demo-{seg.value}", segment=seg, scores=good, scenario=scenario, seed=args.seed
        )
        b = breaches(m)
        print(
            f"  {seg.value:<12} spam={m.spam_complaint_rate:.4%} unsub={m.unsubscribe_rate:.4%} "
            f"bounce={m.bounce_rate:.4%} rev=${m.attributed_revenue:.0f} "
            f"breaches={b or 'none'}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
