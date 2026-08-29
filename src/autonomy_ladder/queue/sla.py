"""Send-window SLA math (SPEC §5): age is an SLA, not a sort key.

Each item carries a ``send_window_expires_at``. As the window closes, the item
must escalate; once it passes, the item is out of both lanes. This module is the
pure time arithmetic behind that rule, with an injectable ``now`` for testing.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

# Items with <= this fraction of their window remaining escalate to the top of
# their lane and are visually flagged (SPEC §5: "within 20% of their window").
ESCALATION_REMAINING_FRACTION = 0.20


class SlaStatus(BaseModel):
    model_config = {"frozen": True}

    fraction_elapsed: float  # 0.0 at creation, 1.0 at expiry (clamped to [0, 1])
    remaining_fraction: float
    escalated: bool
    expired: bool


def compute_sla(created_at: str, expires_at: str, now: datetime) -> SlaStatus:
    """Return where an item sits in its send window as of ``now``.

    A zero- or negative-length window (expiry <= creation) is treated as already
    expired — a nonsensical window should never present as fresh.
    """
    created = datetime.fromisoformat(created_at)
    expires = datetime.fromisoformat(expires_at)
    total = (expires - created).total_seconds()
    if total <= 0:
        return SlaStatus(
            fraction_elapsed=1.0, remaining_fraction=0.0, escalated=False, expired=True
        )

    elapsed = (now - created).total_seconds()
    frac_elapsed = min(1.0, max(0.0, elapsed / total))
    remaining = 1.0 - frac_elapsed
    expired = now >= expires
    escalated = (not expired) and remaining <= ESCALATION_REMAINING_FRACTION
    return SlaStatus(
        fraction_elapsed=frac_elapsed,
        remaining_fraction=remaining,
        escalated=escalated,
        expired=expired,
    )
