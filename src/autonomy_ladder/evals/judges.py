"""Stage 2 — one LLM judge per quality dimension (SPEC §7).

Each judge is an independent call with its own rubric and no access to the
generator's reasoning (P2). Judges output structured JSON —
``{score, verdict, reasoning, evidence}`` — which is parsed into a
:class:`DimensionResult`. Model routing follows SPEC §6: the Claim Verifier runs
on Haiku, the others on Sonnet, so the routing report can show the cost delta.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import BaseModel

from autonomy_ladder.data.loaders import BrandRules, Product
from autonomy_ladder.domain import BRAND_VOICE_PASS_THRESHOLD, Dimension, Verdict
from autonomy_ladder.evals.llm import LLMClient
from autonomy_ladder.records import CampaignBrief, CampaignContent, DimensionResult

# Locked model routing (SPEC §6).
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"

_JSON_INSTRUCTION = (
    "Respond with ONLY a JSON object, no prose or code fences, of the form: "
    '{"score": <float 0-1>, "verdict": "pass"|"fail", "reasoning": "<one sentence>", '
    '"evidence": ["<short quote or fact>", ...]}.'
)


class JudgeContext(BaseModel):
    """Everything the judges may need, assembled once per run."""

    model_config = {"frozen": True, "arbitrary_types_allowed": True}

    brief: CampaignBrief
    brand_rules: BrandRules
    catalog: dict[str, Product]


def _segment_prompt(content: CampaignContent, ctx: JudgeContext) -> str:
    return (
        "Decide whether the campaign targets the correct audience segment.\n"
        f"Requested segment (from the brief): {ctx.brief.requested_segment.value}\n"
        f"Segment the campaign targets: {content.target_segment.value}\n"
        f"Campaign type: {ctx.brief.campaign_type}\n"
        f"Subject: {content.subject}\n"
        "Fail if the targeted segment does not match the requested one, or if the "
        "copy is clearly written for a different audience than the one targeted.\n"
        "Intent-fit rules (from reviewer overrides):\n"
        "- Win-back intent FITS engaged_60d and beyond (audiences lapsing in frequency) "
        "and MISMATCHES engaged_30d and recent purchasers. Do not treat 'engaged' as binary.\n"
        "- Onboarding / first-purchase / 'welcome to Northbay' framing addressed to an "
        "established, actively-purchasing segment (engaged_30d) is a mismatch.\n"
        "- VIP / exclusivity framing aimed at a general engagement band is a mismatch.\n"
        + _JSON_INSTRUCTION
    )


def _claim_prompt(content: CampaignContent, ctx: JudgeContext) -> str:
    facts = []
    for pid in content.product_ids:
        p = ctx.catalog.get(pid)
        if p is not None:
            facts.append(f"{p.id} {p.name}: attributes={p.attributes}")
    facts_block = "\n".join(facts) or "(no catalog products referenced)"
    claims_block = "\n".join(f"- {c}" for c in content.claims) or "(no explicit claims)"
    return (
        "Verify every factual claim in the campaign is grounded in the catalog facts. "
        "Fail if any claim is unsupported, exaggerated, or contradicts the facts.\n"
        "Rules (from reviewer overrides):\n"
        "- ANY mention of an out-of-stock product (stock 0) fails, whether or not the "
        "copy prompts purchase — no editorial exception.\n"
        "- Numeric spec claims (lumens, hours, capacity, ratings) must match the catalog "
        "exactly; doubling or inflating a spec fails.\n"
        "- Transferring an attribute from another product, or upgrading 'water-resistant' "
        "to 'waterproof', is a fabricated claim and fails.\n"
        "- A comparative claim ('brightest we make') is checked against data; vague "
        "puffery ('great gear', 'toughest') with no measurable attribute is acceptable.\n"
        f"Catalog facts:\n{facts_block}\n\n"
        f"Claims to verify:\n{claims_block}\n\n"
        f"Body: {content.body}\n" + _JSON_INSTRUCTION
    )


def _brand_prompt(content: CampaignContent, ctx: JudgeContext) -> str:
    return (
        "Judge whether the copy matches the brand voice.\n"
        f"Voice guidelines: {ctx.brand_rules.voice}\n"
        f"Tone descriptors: {ctx.brand_rules.tone_descriptors}\n"
        f"Terms to avoid: {ctx.brand_rules.prohibited_terms}\n"
        f"Subject: {content.subject}\nPreview: {content.preview_text}\nBody: {content.body}\n"
        f"Score 0-1; a score below {BRAND_VOICE_PASS_THRESHOLD} is a fail.\n" + _JSON_INSTRUCTION
    )


def _structure_prompt(content: CampaignContent, ctx: JudgeContext) -> str:
    return (
        "Assess structural quality only (subject length, a clear call to action, "
        "scannable body). This is advisory; be lenient and do not penalise "
        "legitimate creative variation.\n"
        f"Subject: {content.subject}\nCTA: {content.cta_text} -> {content.cta_url}\n"
        f"Body: {content.body}\n" + _JSON_INSTRUCTION
    )


class _JudgeSpec(BaseModel):
    model_config = {"frozen": True}

    dimension: Dimension
    model: str
    system: str


# Each dimension's judge: its model and system prompt. The user prompt is built
# per-run by the matching builder below.
_SPECS: dict[Dimension, _JudgeSpec] = {
    Dimension.SEGMENT_CORRECTNESS: _JudgeSpec(
        dimension=Dimension.SEGMENT_CORRECTNESS,
        model=MODEL_SONNET,
        system="You are a meticulous audience-targeting reviewer for an email marketing team.",
    ),
    Dimension.CLAIM_GROUNDEDNESS: _JudgeSpec(
        dimension=Dimension.CLAIM_GROUNDEDNESS,
        model=MODEL_HAIKU,  # the Claim Verifier (SPEC §6)
        system="You are a strict claim verifier. You only trust the catalog facts given to you.",
    ),
    Dimension.BRAND_VOICE: _JudgeSpec(
        dimension=Dimension.BRAND_VOICE,
        model=MODEL_SONNET,  # the Brand Sentinel (SPEC §6)
        system="You are the brand sentinel, guarding a distinctive brand voice.",
    ),
    Dimension.STRUCTURE_QUALITY: _JudgeSpec(
        dimension=Dimension.STRUCTURE_QUALITY,
        model=MODEL_SONNET,
        system="You are an email structure reviewer. You are advisory and lenient.",
    ),
}

_BUILDERS: dict[Dimension, Callable[[CampaignContent, JudgeContext], str]] = {
    Dimension.SEGMENT_CORRECTNESS: _segment_prompt,
    Dimension.CLAIM_GROUNDEDNESS: _claim_prompt,
    Dimension.BRAND_VOICE: _brand_prompt,
    Dimension.STRUCTURE_QUALITY: _structure_prompt,
}


def _parse(dimension: Dimension, raw: str) -> DimensionResult:
    """Parse a judge's JSON response, tolerating stray code fences."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") : text.rfind("}") + 1]
    data = json.loads(text)
    score = float(data["score"])
    verdict = Verdict(str(data["verdict"]).lower())
    return DimensionResult(
        dimension=dimension,
        score=max(0.0, min(1.0, score)),
        verdict=verdict,
        reasoning=str(data.get("reasoning", "")),
        evidence=[str(e) for e in data.get("evidence", [])],
    )


def judge_dimension(
    dimension: Dimension, content: CampaignContent, ctx: JudgeContext, client: LLMClient
) -> DimensionResult:
    """Run one dimension's judge and return its structured result."""
    spec = _SPECS[dimension]
    user = _BUILDERS[dimension](content, ctx)
    raw = client.complete(model=spec.model, system=spec.system, user=user)
    return _parse(dimension, raw)


def judge_all(
    content: CampaignContent, ctx: JudgeContext, client: LLMClient
) -> dict[Dimension, DimensionResult]:
    """Run all four dimension judges. Independent calls (P2)."""
    return {dim: judge_dimension(dim, content, ctx, client) for dim in _SPECS}


def build_prompt(
    dimension: Dimension, content: CampaignContent, ctx: JudgeContext
) -> tuple[str, str]:
    """Expose (model+system, user) construction for fixture seeding/recording."""
    spec = _SPECS[dimension]
    return spec.system, _BUILDERS[dimension](content, ctx)


def model_for(dimension: Dimension) -> str:
    return _SPECS[dimension].model
