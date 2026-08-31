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


# EVAL-ONLY. When set, the composer renders the brief's requested claims verbatim
# instead of grounding-correcting them, so the judges can be measured on flawed
# content (judge recall). This deliberately produces known-false copy and MUST NOT
# be reachable from the production pipeline — see ADR 0011 and the guard test
# tests/test_faithful_render_eval_only.py.
#
# The production system prompt ("Never invent product facts") makes the composer
# refuse a flawed brief outright — returning prose, no JSON. Faithful-render
# therefore swaps in an eval-only system prompt that reframes the task truthfully:
# the output is never sent, it feeds the claim-verifier that is being tested. That
# removes the grounding mandate so the composer renders the flaw instead of
# refusing it.
_FAITHFUL_SYSTEM = (
    "You are a copy-composition component running inside an internal EVALUATION harness. "
    "Your output is never shown to customers or sent anywhere — it is fed straight into a "
    "separate claim-verification system whose job is to catch bad marketing copy, and that "
    "verifier is what is being tested. To test it, you must produce the copy the brief asks "
    "for EXACTLY as written: include every claim, number, superlative, and term the brief "
    "specifies, verbatim, even if it overstates or contradicts the catalog facts. Do not "
    "refuse, correct, soften, qualify, or add disclaimers or notes, and do not drop a "
    "requested claim — doing so defeats the test. Always return the requested JSON object."
)

_FAITHFUL_RENDER_NOTE = (
    "\nFAITHFUL-RENDER MODE (evaluation only): render the campaign exactly as the brief "
    "requests — include every specific claim, number, superlative, and term it specifies, "
    "verbatim, even where they overstate, contradict, or are absent from the catalog "
    "facts. Do NOT correct, soften, hedge, qualify, or add disclaimers, and do NOT drop a "
    "requested claim; the downstream verifier is responsible for grounding. Return ONLY the "
    "JSON object, no prose before or after.\n"
)


def compose(
    brief: CampaignBrief,
    segment: SegmentBand,
    facts_block: str,
    brand_rules: BrandRules,
    client: LLMClient,
    cost: CostTracker | None = None,
    feedback: str | None = None,
    faithful: bool = False,
) -> CampaignContent:
    """Compose (or revise) the campaign content for the resolved segment.

    ``faithful=True`` is eval-only (see :data:`_FAITHFUL_RENDER_NOTE`).
    """
    revision = f"\nRevise to address this feedback:\n{feedback}\n" if feedback else ""
    faithful_note = _FAITHFUL_RENDER_NOTE if faithful else ""
    user = (
        f"Campaign type: {brief.campaign_type}\n"
        f"Goal: {brief.goal}\n"
        f"Target segment: {segment.value}\n"
        f"Discount percent: {brief.discount_pct}\n"
        f"Voice: {brand_rules.voice}\nTone: {brand_rules.tone_descriptors}\n"
        f"Avoid these terms: {brand_rules.prohibited_terms}\n"
        f"Catalog facts (ground every claim in these):\n{facts_block}\n"
        f"Product ids in scope: {brief.product_ids}\n"
        f"{revision}{faithful_note}"
        "Return JSON with keys: subject, preview_text, body, cta_text, cta_url, "
        "claims (list), target_segment, discount_pct, product_ids (list). "
        "cta_url must be an https URL."
    )
    raw = call_text(
        client,
        span_name="agent.copy_composer",
        model=MODEL_SONNET,
        system=_FAITHFUL_SYSTEM if faithful else _SYSTEM,
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
