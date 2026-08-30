"""Evaluator-optimizer revision loop (SPEC §6).

The checks don't just gate — they teach. When a run fails, the composer gets one
more chance (capped at two revisions) with concrete feedback about what failed.
This module builds that feedback and holds the iteration cap; the orchestrator
drives the loop.
"""

from __future__ import annotations

from autonomy_ladder.domain import BRAND_VOICE_PASS_THRESHOLD, Dimension
from autonomy_ladder.evals.deterministic import DeterministicReport
from autonomy_ladder.records import RunEvaluation

# Max revision iterations before the run goes forward as-is for the controller to
# route (SPEC §6: "max 2 iterations").
MAX_REVISIONS = 2


def build_feedback(evaluation: RunEvaluation, deterministic: DeterministicReport) -> str:
    """Turn failing checks into actionable feedback for the Copy Composer."""
    lines: list[str] = []
    for dim in evaluation.critical_failures:
        res = evaluation.result(dim)
        why = res.reasoning if res else "failed"
        lines.append(f"- CRITICAL {dim.value}: {why}")
    bv = evaluation.result(Dimension.BRAND_VOICE)  # if it dragged the run down
    if bv is not None and bv.score < BRAND_VOICE_PASS_THRESHOLD:
        lines.append(f"- brand_voice too low ({bv.score:.2f}): {bv.reasoning}")
    for f in deterministic.findings:
        lines.append(f"- {f.code.value}: {f.message}")
    return "\n".join(lines) or "Tighten the copy; no specific issues flagged."
