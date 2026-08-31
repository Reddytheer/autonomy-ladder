"""The judge-accuracy gate fails on a degraded judge prompt (original acceptance criterion).

Passing at baseline is necessary but not sufficient to call something a gate — a
gate has to *reject* a regression. This degrades the brand judge (a miscalibrated
prompt that rejects all copy) and asserts `--check-judges` exits non-zero, then
confirms it exits zero at baseline. Keyless: the degraded verdict is injected via a
wrapper client; every other call replays committed fixtures.
"""

from __future__ import annotations

from autonomy_ladder.domain import Dimension
from autonomy_ladder.evals import gate
from autonomy_ladder.evals.judges import _SPECS
from autonomy_ladder.evals.llm import ReplayClient

_BRAND_SYSTEM = _SPECS[Dimension.BRAND_VOICE].system


class _DegradedBrandJudge:
    """Replays real fixtures, but a degraded brand prompt rejects everything.

    Simulates a weakened/miscalibrated `brand_voice` rubric: the brand judge now
    returns `fail` regardless of the copy. All other calls (composer, other judges)
    replay their committed fixtures unchanged.
    """

    def __init__(self) -> None:
        self._real = ReplayClient()

    def complete(self, *, model: str, system: str, user: str) -> str:
        if system == _BRAND_SYSTEM:
            return (
                '{"reasoning": "degraded rubric", "evidence": [], "score": 0.1, "verdict": "fail"}'
            )
        return self._real.complete(model=model, system=system, user=user)


def test_gate_passes_at_baseline() -> None:
    assert gate._check_judges(ReplayClient()) == 0


def test_gate_fails_on_degraded_judge_prompt() -> None:
    assert gate._check_judges(_DegradedBrandJudge()) == 1
