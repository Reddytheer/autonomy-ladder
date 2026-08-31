"""Golden paired-case tests (HANDOFF Drop 1): each pair must resolve oppositely.

If a rubric or routing change ever collapses a pair, these fail loudly. The pairs
are the load-bearing examples: data decides the verdict, not the wording; tier
standing decides the outcome, not the content.
"""

from __future__ import annotations

import pytest

from autonomy_ladder.autonomy import routing
from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.config import load_tiers_config
from autonomy_ladder.evals.gate import _DECISION_TO_EXPECTED, _evaluation_from_case
from autonomy_ladder.evals.golden_loader import GoldenCase, load_goldens

CASES = {c.id: c for c in load_goldens()}

PAIRS = [
    ("GS-PD-08", "GS-PD-09"),  # scarcity phrasing, stock 12 vs 1200 -> pass vs fail
    ("GS-PL-07", "GS-PL-08"),  # comparative claim true vs false
    ("GS-WB-06", "GS-WB-07"),  # win-back audience recency -> pass vs fail
    ("GS-NL-06", "GS-NL-07"),  # byte-identical content, Tier 1 vs Tier 2 -> review vs auto-send
]


def _route(case: GoldenCase) -> tuple[str, str | None]:
    result = routing.decide(
        _evaluation_from_case(case),
        effective_tier=Tier(case.agent_tier_at_run),
        autonomous_sends_last_24h=0,
        constraints=load_tiers_config().constraints,
    )
    lane = result.lane.value if result.lane is not None else None
    return _DECISION_TO_EXPECTED[result.decision].value, lane


@pytest.mark.parametrize(("a", "b"), PAIRS)
def test_pairs_resolve_in_opposite_directions(a: str, b: str) -> None:
    ca, cb = CASES[a], CASES[b]
    # Authored expectations differ.
    assert ca.expected_decision is not cb.expected_decision
    # And the controller actually routes them oppositely.
    assert _route(ca)[0] != _route(cb)[0]


def test_nl_pair_is_the_thesis_same_content_different_tier() -> None:
    """GS-NL-06 (Tier 1) reviews; GS-NL-07 (Tier 2) auto-sends — identical content."""
    six, seven = CASES["GS-NL-06"], CASES["GS-NL-07"]
    assert six.brief == seven.brief and six.requested_segment == seven.requested_segment
    assert _route(six)[0] == "REVIEW_QUEUE"
    assert _route(seven)[0] == "AUTO_SEND"
