"""Risk scoring for the judgment lane (SPEC §5).

Why this exists: the judgment lane is sorted by risk descending, so the reviewer
sees the most consequential item first. The score is a transparent, bounded, and
explainable weighted sum — deliberately NOT an LLM call. A reviewer (or an
auditor) can read the weights and reproduce any score by hand.
"""

from __future__ import annotations

from autonomy_ladder.queue.models import QueueItem

# Weights (documented and fixed). Each term contributes to a roughly [0, 1+]
# score; the ordering is what matters, not the absolute magnitude.
W_CRITICAL = 0.50  # per critical-dimension flag — the dominant term
W_CONSTRAINT = 0.20  # per hard-constraint violation
W_LOW_CONFIDENCE = 0.20  # scaled by how far below 1.0 the weakest dimension scored
W_DISCOUNT = 0.10  # scaled by discount as a fraction of 100%

# Segments that carry more downside if a mistake ships. Sending the wrong thing to
# a broad or cold audience is worse than to the most-engaged one.
_SEGMENT_SENSITIVITY = {
    "engaged_30d": 0.0,
    "engaged_60d": 0.05,
    "engaged_90d": 0.10,
    "engaged_180d": 0.15,
    "all_subscribers": 0.25,
    "first_time_buyers": 0.20,
    "lapsed": 0.20,
}


def compute_risk_score(item: QueueItem) -> float:
    """A transparent weighted sum; higher means review this sooner."""
    score = 0.0
    score += W_CRITICAL * len(item.critical_flags)
    score += W_CONSTRAINT * len(item.constraint_codes)
    score += W_LOW_CONFIDENCE * max(0.0, 1.0 - item.min_dimension_score)
    score += W_DISCOUNT * (item.discount_pct / 100.0)
    score += _SEGMENT_SENSITIVITY.get(item.segment.value, 0.10)
    return round(score, 4)
