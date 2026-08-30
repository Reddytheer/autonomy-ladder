"""Copy Composer — writes the campaign (SPEC §6).

Runs on Sonnet: open-ended generation that must respect the brand voice and stay
grounded in the catalog facts it is given. It accepts optional revision feedback,
which is how the evaluator-optimizer loop closes.
"""

from __future__ import annotations

from autonomy_ladder.agents._llm import call_text, parse_json
from autonomy_ladder.data.loaders import BrandRules
from autonomy_ladder.domain import SegmentBand
from autonomy_ladder.evals.judges import MODEL_SONNET
from autonomy_ladder.evals.llm import LLMClient
from autonomy_ladder.observability.cost import CostTracker
from autonomy_ladder.records import CampaignBrief, CampaignContent

_SYSTEM = (
    "You are the copy composer for the outdoor-gear brand Northbay Supply. You write "
    "on-brand campaign copy that is grounded strictly in the catalog facts you are given. "
    "Never invent product facts."
)


def compose(
    brief: CampaignBrief,
    segment: SegmentBand,
    facts_block: str,
    brand_rules: BrandRules,
    client: LLMClient,
    cost: CostTracker | None = None,
    feedback: str | None = None,
) -> CampaignContent:
    """Compose (or revise) the campaign content for the resolved segment."""
    revision = f"\nRevise to address this feedback:\n{feedback}\n" if feedback else ""
    user = (
        f"Campaign type: {brief.campaign_type}\n"
        f"Goal: {brief.goal}\n"
        f"Target segment: {segment.value}\n"
        f"Discount percent: {brief.discount_pct}\n"
        f"Voice: {brand_rules.voice}\nTone: {brand_rules.tone_descriptors}\n"
        f"Avoid these terms: {brand_rules.prohibited_terms}\n"
        f"Catalog facts (ground every claim in these):\n{facts_block}\n"
        f"Product ids in scope: {brief.product_ids}\n"
        f"{revision}"
        "Return JSON with keys: subject, preview_text, body, cta_text, cta_url, "
        "claims (list), target_segment, discount_pct, product_ids (list). "
        "cta_url must be an https URL."
    )
    raw = call_text(
        client,
        span_name="agent.copy_composer",
        model=MODEL_SONNET,
        system=_SYSTEM,
        user=user,
        cost=cost,
    )
    data = parse_json(raw)
    return CampaignContent(
        subject=str(data.get("subject", "")),
        preview_text=str(data.get("preview_text", "")),
        body=str(data.get("body", "")),
        cta_text=str(data.get("cta_text", "")),
        cta_url=str(data.get("cta_url", "")),
        claims=[str(c) for c in data.get("claims", [])],
        target_segment=SegmentBand(str(data.get("target_segment", segment.value))),
        discount_pct=float(data.get("discount_pct", brief.discount_pct)),
        product_ids=[str(p) for p in data.get("product_ids", brief.product_ids)],
    )
