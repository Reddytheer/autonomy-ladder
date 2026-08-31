"""Security events — resisted attacks are logged, not silently passed (HANDOFF Drop 2).

A resisted prompt injection or a tier-escalation attempt is a *security event* even
when the run otherwise succeeds: silent successes are counted. This module defines
the event types, a persistent store, a deterministic signature scanner over
brief/injected text, and a loader for the committed security suite.

The real defences are architectural (P1: no brief content can move a tier;
deterministic prohibited-term and effective-discount checks; segments computed from
data). This layer records that an attempt was seen and resisted, and surfaces a
count in the console.
"""

from __future__ import annotations

import re
import sqlite3
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from autonomy_ladder.config import REPO_ROOT

SECURITY_SUITE_PATH = REPO_ROOT / "evals" / "adversarial" / "security.jsonl"


class SecurityEventType(StrEnum):
    INJECTION_RESISTED = "INJECTION_RESISTED"
    TIER_ESCALATION_ATTEMPT = "TIER_ESCALATION_ATTEMPT"
    CONSTRAINT_EVASION_ATTEMPT = "CONSTRAINT_EVASION_ATTEMPT"
    SEGMENT_REDEFINITION_ATTEMPT = "SEGMENT_REDEFINITION_ATTEMPT"
    EVAL_GAMING_ATTEMPT = "EVAL_GAMING_ATTEMPT"
    RATE_LIMIT_EVASION_ATTEMPT = "RATE_LIMIT_EVASION_ATTEMPT"


# Signature patterns per attack class. Deliberately conservative and explainable —
# this is defence-in-depth telemetry, not the primary control.
_SIGNATURES: list[tuple[SecurityEventType, re.Pattern[str]]] = [
    (
        SecurityEventType.TIER_ESCALATION_ATTEMPT,
        re.compile(r"tier\s*2|approved\s+tier|approved\s+for\s+tier|autonomy\b.*approv", re.I),
    ),
    (
        SecurityEventType.SEGMENT_REDEFINITION_ATTEMPT,
        re.compile(r"redefin|relabel(?:led)?\s+(?:the\s+)?segment|include\s+lapsed", re.I),
    ),
    (
        SecurityEventType.EVAL_GAMING_ATTEMPT,
        re.compile(r"pre-?approved\s+by\s+compliance|approved\s+by\s+compliance", re.I),
    ),
    (
        SecurityEventType.RATE_LIMIT_EVASION_ATTEMPT,
        re.compile(
            r"label\s+this\s+as|avoid\s+the\s+\w+\s+(?:cadence\s+)?limit|cadence\s+limit", re.I
        ),
    ),
    (
        SecurityEventType.CONSTRAINT_EVASION_ATTEMPT,
        re.compile(r"stack\b|on\s+top\s+of|additional\s+\d+\s*%|extra\s+\d+\s*%", re.I),
    ),
    (
        SecurityEventType.INJECTION_RESISTED,
        re.compile(
            r"ignore\s+(?:previous|your|all)\s+instructions|system\s+note:|system\s+override|"
            r"terms\s+do\s+not\s+apply|do\s+not\s+apply\b|use\s+that\s+figure",
            re.I,
        ),
    ),
]


def scan(text: str) -> list[SecurityEventType]:
    """Return the security-event types whose signatures appear in ``text``.

    Order is by attack specificity so the most specific class is reported first when
    several match (e.g. a tier-escalation phrase over a generic injection phrase).
    """
    found: list[SecurityEventType] = []
    for event_type, pattern in _SIGNATURES:
        if pattern.search(text) and event_type not in found:
            found.append(event_type)
    return found


class SecurityEvent(BaseModel):
    model_config = {"frozen": True}

    id: str
    ts: str
    campaign_type: str
    event_type: SecurityEventType
    detail: str = ""
    resisted: bool = True  # the attempt was blocked; recorded even on an otherwise-clean run


class SecurityEventStore:
    """Persistent log of security events (SQLite)."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS security_events "
            "(id TEXT PRIMARY KEY, ts TEXT, campaign_type TEXT, event_type TEXT, payload TEXT)"
        )
        self._conn.commit()

    def add(self, event: SecurityEvent) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO security_events VALUES (?, ?, ?, ?, ?)",
            (
                event.id,
                event.ts,
                event.campaign_type,
                event.event_type.value,
                event.model_dump_json(),
            ),
        )
        self._conn.commit()

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM security_events").fetchone()
        return int(row[0])

    def recent(self, limit: int = 50) -> list[SecurityEvent]:
        rows = self._conn.execute(
            "SELECT payload FROM security_events ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [SecurityEvent.model_validate_json(r[0]) for r in rows]

    def close(self) -> None:
        self._conn.close()


class SecurityCase(BaseModel):
    """One case from the committed security suite (HANDOFF Drop 2)."""

    model_config = {"frozen": True, "extra": "allow"}

    id: str
    campaign_type: str
    attack: str
    brief: str
    injected_content: dict[str, object] | None = None
    requested_segment: str
    agent_tier_at_run: int
    expected_decision: str
    expected_security_event: SecurityEventType
    expected_lane: str | None = None
    failure_reason: str | None = None
    tests_layer: str = ""

    def attack_text(self) -> str:
        """Brief plus any injected payload — the text a scanner should inspect."""
        parts = [self.brief]
        if self.injected_content:
            payload = self.injected_content.get("payload")
            if isinstance(payload, str):
                parts.append(payload)
        return "\n".join(parts)


def load_security_cases(path: Path = SECURITY_SUITE_PATH) -> list[SecurityCase]:
    return [
        SecurityCase.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
