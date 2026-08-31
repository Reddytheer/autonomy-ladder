"""Stage 2 — one LLM judge per quality dimension (SPEC §7).

Each judge is an independent call with its own rubric and no access to the
generator's reasoning (P2). Judges output structured JSON —
``{score, verdict, reasoning, evidence}`` — which is parsed into a
:class:`DimensionResult`. Model routing follows SPEC §6: the Claim Verifier runs
on Haiku, the others on Sonnet, so the routing report can show the cost delta.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from pydantic import BaseModel

from autonomy_ladder.data.loaders import BrandRules, Product
from autonomy_ladder.domain import BRAND_VOICE_PASS_THRESHOLD, Dimension, Verdict
from autonomy_ladder.evals.llm import LLMClient
from autonomy_ladder.records import CampaignBrief, CampaignContent, DimensionResult

logger = logging.getLogger(__name__)

# Locked model routing (SPEC §6).
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"

# Comparative claims ("brightest", "longest-lasting") are unverifiable from the
# subject product alone — the judge needs peer data. We include same-category,
# same-type siblings, capped, most-relevant first (ADR 0010). The cap bounds
# prompt cost and distractor risk; if it ever truncates a peer that would falsify
# a claim, that is a potential false pass, so truncation is logged.
_SIBLING_CAP = 5

_JSON_INSTRUCTION = (
    "Respond with ONLY a JSON object, no prose or code fences. Reason first, then decide — "
    "the verdict must follow from the reasoning. Use exactly this key order: "
    '{"reasoning": "<one sentence>", "evidence": ["<short quote or fact>", ...], '
    '"score": <float 0-1>, "verdict": "pass"|"fail"}.'
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


def _numeric_attrs(p: Product) -> dict[str, float]:
    """Numeric attributes only (bools excluded — they are not comparable specs)."""
    return {
        k: float(v)
        for k, v in p.attributes.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


# Product "type" keywords, matched against the product name, most specific first
# (compound types like "trekking pack" win over "pack"). A comparative claim is
# scoped to the type of its subject product — "brightest headlamp" compares only
# against other headlamps, not lanterns or trail lights that merely share the
# 'lighting' category (ADR 0010, A2). Same-category alone let the judge treat a
# 1000-lumen "Trail Light" as a headlamp and false-fail GS-PL-07.
_TYPE_KEYWORDS: tuple[str, ...] = (
    "headlamp",
    "lantern",
    "trail light",
    "area light",
    "light",
    "trekking pack",
    "frame pack",
    "daypack",
    "pack",
    "sleeping bag",
    "tarp shelter",
    "tent",
    "route beacon",
    "altimeter watch",
    "gps",
    "compass",
    "camp stove",
    "titanium pot",
    "cook set",
    "kettle",
    "pot",
    "stove",
    "hiking boot",
    "winter boot",
    "boot",
    "insulated bottle",
    "bottle",
    "insulated vest",
    "vest",
    "down jacket",
    "shell jacket",
    "jacket",
    "puffy hoodie",
    "hoodie",
    "sock",
)


def _product_type(p: Product) -> str:
    """The product's type keyword, from its name; "" if none is recognized."""
    name = p.name.lower()
    for kw in _TYPE_KEYWORDS:
        if kw in name:
            return kw
    return ""


def _sibling_block(content: CampaignContent, ctx: JudgeContext) -> str:
    """Same-category, same-**type** peers of the referenced products.

    Comparative claims are scoped to the subject's product type, not the whole
    category (ADR 0010). Capped at :data:`_SIBLING_CAP`, ranked most-relevant
    first: by count of numeric attributes shared with the subject, then peers
    that *exceed* the subject on a shared attribute (the ones that can falsify a
    superlative), then id. Truncation is logged, flagging any dropped peer that
    would falsify a claim — the false-pass risk the cap introduces.
    """
    referenced = [ctx.catalog[pid] for pid in content.product_ids if pid in ctx.catalog]
    if not referenced:
        return ""
    cats = {p.category for p in referenced}
    types = {_product_type(p) for p in referenced}
    ref_ids = {p.id for p in referenced}
    ref_numeric: dict[str, float] = {}
    for p in referenced:
        for k, v in _numeric_attrs(p).items():
            ref_numeric[k] = max(ref_numeric.get(k, v), v)

    # Same category AND same product type. If the subject's type is unrecognized
    # ("" — no comparative golden hits this), fall back to same-category so a
    # comparison is still possible rather than silently dropping peers.
    def is_peer(p: Product) -> bool:
        if p.id in ref_ids or p.category not in cats:
            return False
        return _product_type(p) in types if types != {""} else True

    candidates = [p for p in ctx.catalog.values() if is_peer(p)]
    if not candidates:
        return ""

    def shared(p: Product) -> dict[str, float]:
        return {k: v for k, v in _numeric_attrs(p).items() if k in ref_numeric}

    def falsifies(p: Product) -> bool:
        return any(v > ref_numeric[k] for k, v in shared(p).items())

    ranked = sorted(candidates, key=lambda p: (-len(shared(p)), 0 if falsifies(p) else 1, p.id))
    kept, dropped = ranked[:_SIBLING_CAP], ranked[_SIBLING_CAP:]
    if dropped:
        dropped_falsifiers = [p.id for p in dropped if falsifies(p)]
        logger.warning(
            "claim-judge sibling truncation: category=%s subject=%s kept=%d dropped=%d "
            "dropped_falsifiers=%s",
            ",".join(sorted(cats)),
            ",".join(sorted(ref_ids)),
            len(kept),
            len(dropped),
            dropped_falsifiers or "none",
        )
    lines = [f"{p.id} {p.name}: attributes={p.attributes} stock={p.stock}" for p in kept]
    return (
        "Other products of the same type (comparison set for superlative/comparative claims):\n"
        + "\n".join(lines)
    )


def _claim_prompt(content: CampaignContent, ctx: JudgeContext) -> str:
    facts = []
    for pid in content.product_ids:
        p = ctx.catalog.get(pid)
        if p is not None:
            facts.append(f"{p.id} {p.name}: attributes={p.attributes} stock={p.stock}")
    facts_block = "\n".join(facts) or "(no catalog products referenced)"
    siblings = _sibling_block(content, ctx)
    comparison_block = f"\n{siblings}\n" if siblings else ""
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
        "- A comparative or superlative claim ('brightest', 'longest-lasting', 'most X') is "
        "scoped to the product type named in the claim and checked against the comparison "
        "set below: it fails if a same-type peer beats the subject on the compared "
        "attribute. Vague puffery ('great gear', 'toughest', 'best ever') with no "
        "measurable attribute is acceptable.\n"
        f"Catalog facts:\n{facts_block}\n"
        f"{comparison_block}\n"
        f"Claims to verify:\n{claims_block}\n\n"
        f"Body: {content.body}\n" + _JSON_INSTRUCTION
    )


# Revised once (ADR 0012): the prior rubric described the voice abstractly and
# missed everything not caught by the deterministic prohibited-term list. This
# version enumerates the violation types and shows compliant examples so the judge
# has a reference for what passing looks like. Stock is included so the
# "scarcity unsupported by stock" rule is actually enforceable.
_BRAND_COMPLIANT_EXAMPLES = (
    '- "The Beacon puts out 400 lumens and runs 40 hours on a charge — enough for a '
    'multi-day trip. Not rated for technical caving."\n'
    '- "Back in stock: the Stormline shell, 20k/20k waterproof. 12 left."\n'
    '- "20% off Cascade packs through Sunday. 45 liters, 1.4 kg, water-resistant ripstop."'
)


def _brand_prompt(content: CampaignContent, ctx: JudgeContext) -> str:
    stock_lines = [
        f"{p.id} {p.name}: stock={p.stock}"
        for pid in content.product_ids
        if (p := ctx.catalog.get(pid)) is not None
    ]
    stock_block = "\n".join(stock_lines) or "(no catalog products referenced)"
    return (
        "Judge whether the copy matches Northbay's understated brand voice. The voice is "
        "grounded, plain, and specific: name the feature and what it does, prefer concrete "
        "numbers over adjectives, be honest about limits, one call to action.\n"
        f"Voice guidelines: {ctx.brand_rules.voice}\n"
        f"Tone descriptors: {ctx.brand_rules.tone_descriptors}\n"
        f"Terms to avoid: {ctx.brand_rules.prohibited_terms}\n"
        "FAIL the copy if it contains any of these violations:\n"
        "1. Empty hype adjectives with no concrete backing (e.g. 'amazing', 'incredible', "
        "'game-changing', 'revolutionary').\n"
        "2. Absolute or hyperbolic superlatives ('best ever', 'the best made by anyone', "
        "\"world's best\"). Bounded, spec-grounded comparatives ('our toughest pack', "
        "'the brightest headlamp we make') are acceptable, not violations.\n"
        "3. Manufactured scarcity: a low-quantity claim about current stock ('only 2 left', "
        "'limited quantities', 'selling fast') that the stock below does NOT support. "
        "Genuinely low stock stated plainly is fine; a mild 'order soon' with no false "
        "quantity is fine.\n"
        "4. Exclamatory register: exclamation-point shouting, or ALL-CAPS words/headers for "
        "emphasis.\n"
        "5. Second-person pressure or coercion ('don't miss out', 'act now', or threats "
        "such as closing the reader's account).\n"
        "6. A discount or sale offer with no stated end date.\n"
        "Do NOT fail copy merely for exclusivity/VIP framing (that is an audience concern, "
        "not brand voice), for a warm 'we miss you' tone, or for stating a true low stock.\n"
        f"Current stock for referenced products:\n{stock_block}\n"
        f"Examples of COMPLIANT understated copy:\n{_BRAND_COMPLIANT_EXAMPLES}\n"
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
    """Parse a judge's JSON response, tolerating code fences and surrounding prose.

    ``raw_decode`` from the first ``{`` reads exactly one JSON object and ignores
    any trailing text (a judge occasionally appends a note after the object).
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON object in judge response: {raw[:160]!r}")
    data, _ = json.JSONDecoder().raw_decode(text, start)
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
