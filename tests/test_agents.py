"""Agent pipeline wiring, exercised with a stub LLM client — no API key (SPEC §13 step 5)."""

from __future__ import annotations

import json
import re

from autonomy_ladder.agents.orchestrator import Orchestrator
from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.domain import CampaignType, SegmentBand
from autonomy_ladder.observability.cost import CostTracker
from autonomy_ladder.records import CampaignBrief

from .conftest import make_tiers_config

CONS = make_tiers_config().constraints


class StubClient:
    """A deterministic stand-in for the LLM. Routes by the agent's system prompt.

    ``bad_first_copy=True`` makes the composer emit a prohibited term on its first
    attempt and clean copy thereafter, so the revision loop has something to fix.
    """

    def __init__(self, bad_first_copy: bool = False) -> None:
        self.bad_first_copy = bad_first_copy
        self.compose_calls = 0
        self.judge_calls = 0

    def complete(self, *, model: str, system: str, user: str) -> str:
        s = system.lower()
        if "segment analyst" in s:
            seg = re.search(r"Requested segment: (\w+)", user)
            return json.dumps(
                {"segment": seg.group(1) if seg else "engaged_30d", "reasoning": "ok"}
            )
        if "copy composer" in s:
            self.compose_calls += 1
            seg = re.search(r"Target segment: (\w+)", user)
            target = seg.group(1) if seg else "engaged_30d"
            body = "Built for 3-season use and ready for real weather."
            if self.bad_first_copy and self.compose_calls == 1:
                body = "This is a guaranteed miracle you cannot miss."  # prohibited terms
            return json.dumps(
                {
                    "subject": "Trail-ready picks for the weekend",
                    "preview_text": "Fresh gear",
                    "body": body,
                    "cta_text": "Shop now",
                    "cta_url": "https://northbay.example.com/shop",
                    "claims": ["Built for 3-season use."],
                    "target_segment": target,
                    "discount_pct": 0.0,
                    "product_ids": ["NB-0001"],
                }
            )
        # Any judge (verifier / sentinel / reviewer): pass with a high score.
        self.judge_calls += 1
        return json.dumps({"score": 0.9, "verdict": "pass", "reasoning": "fine", "evidence": []})


BRIEF = CampaignBrief(
    campaign_type=CampaignType.NEWSLETTER.value,
    goal="Monthly newsletter",
    requested_segment=SegmentBand.ENGAGED_30D,
    discount_pct=0.0,
    product_ids=["NB-0001"],
)


def test_pipeline_produces_a_passing_run() -> None:
    orch = Orchestrator(StubClient(), CONS)
    result = orch.run(BRIEF, tier=Tier.BOUNDED, run_id="t1", cost=CostTracker())
    assert result.run_id == "t1"
    assert result.content.target_segment is SegmentBand.ENGAGED_30D
    assert result.evaluation.passed is True
    assert result.revisions == 0
    assert result.deterministic.ok


def test_revision_loop_fixes_a_prohibited_term() -> None:
    stub = StubClient(bad_first_copy=True)
    orch = Orchestrator(stub, CONS)
    result = orch.run(BRIEF, tier=Tier.BOUNDED, run_id="t2")
    # First composed copy tripped the prohibited-term check; a revision cleaned it.
    assert result.revisions >= 1
    assert result.evaluation.passed is True
    assert result.deterministic.ok
    assert stub.compose_calls >= 2


def test_pipeline_feeds_the_controller() -> None:
    """End-to-end wiring: the pipeline's RunEvaluation drives a controller decision."""
    from autonomy_ladder.autonomy.ledger import Decision

    from .conftest import make_controller

    controller = make_controller()
    orch = Orchestrator(StubClient(), CONS)
    for _ in range(25):
        result = orch.run(
            BRIEF,
            tier=controller.effective_tier(controller.state(CampaignType.NEWSLETTER)),
            run_id=None,
        )
        decision = controller.process_run(result.evaluation)
    # After 25 clean runs the newsletter type has earned Tier 1.
    assert controller.state(CampaignType.NEWSLETTER).tier is Tier.BOUNDED
    assert decision.decision in (Decision.AUTO_SEND, Decision.HUMAN_REVIEW)


def test_cost_tracker_records_model_routing() -> None:
    cost = CostTracker()
    Orchestrator(StubClient(), CONS).run(BRIEF, tier=Tier.BOUNDED, cost=cost)
    per = {u.model for u in cost.per_model()}
    # Segment analyst + claim verifier on Haiku; composer + other judges on Sonnet.
    assert any("haiku" in m for m in per)
    assert any("sonnet" in m for m in per)
    report = cost.routing_report()
    assert report.savings_usd >= 0.0
