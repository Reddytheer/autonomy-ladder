"""Golden-set loader and schema (SPEC §7).

The golden set is a versioned collection of campaigns with authored, expected
per-dimension judgements, split into ``easy`` / ``ambiguous`` / ``adversarial``.
The regression gate runs the judges over these and checks the verdicts have not
regressed. This module is the loader and schema — seeded with a handful of cases;
the rest are added collaboratively.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from autonomy_ladder.config import REPO_ROOT
from autonomy_ladder.domain import Dimension, Verdict
from autonomy_ladder.records import CampaignBrief, CampaignContent

GOLDENS_DIR = REPO_ROOT / "evals" / "goldens"
ADVERSARIAL_DIR = REPO_ROOT / "evals" / "adversarial"


class Split(StrEnum):
    EASY = "easy"
    AMBIGUOUS = "ambiguous"
    ADVERSARIAL = "adversarial"


class ExpectedJudgement(BaseModel):
    model_config = {"frozen": True}

    verdict: Verdict
    score: float = 0.0


class GoldenCase(BaseModel):
    """One labeled campaign: the artifact to judge and the expected judgements."""

    model_config = {"frozen": True}

    id: str
    campaign_type: str
    split: Split
    brief: CampaignBrief
    content: CampaignContent
    expected: dict[Dimension, ExpectedJudgement]


def load_jsonl(path: Path) -> list[GoldenCase]:
    """Load one .jsonl file of golden cases."""
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


def dump_jsonl(cases: list[GoldenCase], path: Path) -> None:
    """Write cases back to .jsonl (used by authoring tools)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(c.model_dump_json() for c in cases) + "\n")
