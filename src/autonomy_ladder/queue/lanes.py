"""Lane assignment and ordering (SPEC §5).

The rules, in one place:

* **Batch lane** — no critical flag, ``brand_voice >= 0.75``, and confident. These
  look alike and are approved as a group, so they are NOT individually sorted;
  only escalated (near-expiry) items float to the top.
* **Judgment lane** — any critical flag, a constraint violation, or low
  confidence. Sorted by risk descending, with escalated items above the rest.
* **Age is an SLA**: items within 20% of their send window escalate; expired items
  leave both lanes.
"""

from __future__ import annotations

from datetime import datetime

from autonomy_ladder.domain import BRAND_VOICE_PASS_THRESHOLD
from autonomy_ladder.queue.models import DecoratedItem, Lane, LanesView, QueueItem
from autonomy_ladder.queue.risk_score import compute_risk_score
from autonomy_ladder.queue.sla import compute_sla

# A weakest-dimension score below this routes an otherwise-clean item to judgment:
# "low confidence" belongs with a human even when nothing hard-failed (SPEC §5).
LOW_CONFIDENCE_THRESHOLD = 0.60


def classify_lane(item: QueueItem) -> Lane:
    """Batch only if nothing critical, brand-voice passes, and we are confident."""
    if item.has_critical_flag or item.has_constraint_violation:
        return Lane.JUDGMENT
    if item.min_dimension_score < LOW_CONFIDENCE_THRESHOLD:
        return Lane.JUDGMENT
    if item.brand_voice_score < BRAND_VOICE_PASS_THRESHOLD:
        return Lane.JUDGMENT
    return Lane.BATCH


def _decorate(item: QueueItem, now: datetime) -> DecoratedItem:
    sla = compute_sla(item.created_at, item.send_window_expires_at, now)
    return DecoratedItem(
        item=item,
        lane=classify_lane(item),
        risk_score=compute_risk_score(item),
        fraction_elapsed=sla.fraction_elapsed,
        escalated=sla.escalated,
        expired=sla.expired,
    )


def build_lanes(items: list[QueueItem], now: datetime) -> LanesView:
    """Split pending items into the two lanes and surface anything expired.

    Pure: computes the view but does not persist status changes. The store is
    responsible for marking ``newly_expired`` items EXPIRED.
    """
    decorated = [_decorate(i, now) for i in items]

    newly_expired = [d for d in decorated if d.expired]
    live = [d for d in decorated if not d.expired]

    batch = [d for d in live if d.lane is Lane.BATCH]
    judgment = [d for d in live if d.lane is Lane.JUDGMENT]

    # Batch is not individually sorted (SPEC §5) — but escalated items float up.
    batch.sort(key=lambda d: 0 if d.escalated else 1)
    # Judgment: escalated first, then risk descending.
    judgment.sort(key=lambda d: (0 if d.escalated else 1, -d.risk_score))

    return LanesView(batch=batch, judgment=judgment, newly_expired=newly_expired)
