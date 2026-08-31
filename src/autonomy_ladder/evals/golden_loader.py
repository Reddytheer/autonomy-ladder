"""Golden-set loader and schema (SPEC §7, updated by HANDOFF Drop 1).

The golden set is 75 versioned, brief-based end-to-end cases with authored
expectations: per-dimension verdicts, the expected controller decision, and the
expected review lane. Split into easy / ambiguous / adversarial.

There are two ways these are used:

* **Keyless** — the decision-routing gate (:mod:`autonomy_ladder.evals.gate`) feeds
  each case's *expected* dimension verdicts through the deterministic controller and
  checks that the decision and lane come out as authored. No LLM, no API key.
* **With a key** — ``make fixtures`` runs the briefs through the live pipeline and
  the judges score the generated content; that measures judge quality (steps 11-13).
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, field_validator

from autonomy_ladder.config import REPO_ROOT
from autonomy_ladder.domain import CampaignType, Dimension, SegmentBand, Verdict

GOLDENS_DIR = REPO_ROOT / "evals" / "goldens"
ADVERSARIAL_DIR = REPO_ROOT / "evals" / "adversarial"

_DISCOUNT_RE = re.compile(r"(\d{1,3})\s*%")


class Split(StrEnum):
    EASY = "easy"
    AMBIGUOUS = "ambiguous"
    ADVERSARIAL = "adversarial"


class ExpectedDecision(StrEnum):
    AUTO_SEND = "AUTO_SEND"
    REVIEW_QUEUE = "REVIEW_QUEUE"


class ExpectedLane(StrEnum):
    BATCH = "batch"
    JUDGMENT = "judgment"


class ExpectedVerdicts(BaseModel):
    """The three graded dimensions plus the advisory marker for structure."""

    model_config = {"frozen": True}

    segment_correctness: Verdict
    claim_groundedness: Verdict
    brand_voice: Verdict
    structure_quality: str = "advisory"

    def as_map(self) -> dict[Dimension, Verdict]:
        return {
            Dimension.SEGMENT_CORRECTNESS: self.segment_correctness,
            Dimension.CLAIM_GROUNDEDNESS: self.claim_groundedness,
            Dimension.BRAND_VOICE: self.brand_voice,
        }


class GoldenCase(BaseModel):
    """One authored end-to-end case."""

    model_config = {"frozen": True, "extra": "allow"}  # tolerate note/failure_class extras

    id: str
    campaign_type: CampaignType
    band: Split
    brief: str
    requested_segment: SegmentBand
    agent_tier_at_run: int
    expected: ExpectedVerdicts
    expected_decision: ExpectedDecision
    expected_lane: ExpectedLane | None = None
    failure_reason: str | None = None
    tests_layer: str = ""

    @field_validator("agent_tier_at_run")
    @classmethod
    def _tier_range(cls, v: int) -> int:
        if v not in (0, 1, 2):
            raise ValueError("agent_tier_at_run must be 0, 1, or 2")
        return v

    @property
    def discount_pct(self) -> float:
        """Discount parsed from the brief text (deterministic; 0 if none stated).

        The goldens carry the discount only in prose, so the keyless routing gate
        extracts it here. Live runs get it from the composed campaign instead.
        """
        m = _DISCOUNT_RE.search(self.brief)
        return float(m.group(1)) if m else 0.0


def load_jsonl(path: Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            cases.append(GoldenCase.model_validate_json(line))
    return cases


def load_goldens(directory: Path = GOLDENS_DIR) -> list[GoldenCase]:
    """Load every *.jsonl golden file in a directory, sorted for determinism."""
    cases: list[GoldenCase] = []
    for p in sorted(directory.glob("*.jsonl")):
        cases.extend(load_jsonl(p))
    return cases
