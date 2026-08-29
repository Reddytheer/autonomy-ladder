"""Stage 1 — deterministic checks (SPEC §7, P3).

Millisecond checks with no LLM: schema validity, required fields, segment
existence and tier eligibility, discount ceiling, prohibited-term regex, and link
validity. These run *before* the judges so obvious failures never spend a judge
call. A blocking finding here means the run cannot auto-send regardless of what a
judge might later say.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel

from autonomy_ladder.autonomy import constraints as C
from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.config import Constraints
from autonomy_ladder.data.loaders import BrandRules, Product
from autonomy_ladder.domain import SegmentBand
from autonomy_ladder.records import CampaignContent

_URL_RE = re.compile(r"^https://[^\s/$.?#].[^\s]*$", re.IGNORECASE)


class FindingCode(StrEnum):
    MISSING_FIELD = "missing_field"
    UNKNOWN_SEGMENT = "unknown_segment"
    SEGMENT_INELIGIBLE = "segment_ineligible"
    DISCOUNT_OVER_CEILING = "discount_over_ceiling"
    PROHIBITED_TERM = "prohibited_term"
    INVALID_LINK = "invalid_link"
    UNKNOWN_PRODUCT = "unknown_product"


class Finding(BaseModel):
    model_config = {"frozen": True}

    code: FindingCode
    message: str


class DeterministicReport(BaseModel):
    """The result of Stage 1: findings, and whether anything blocks auto-send."""

    model_config = {"frozen": True}

    findings: list[Finding]

    @property
    def ok(self) -> bool:
        return not self.findings

    @property
    def codes(self) -> list[str]:
        return [f.code.value for f in self.findings]


def run_deterministic_checks(
    content: CampaignContent,
    *,
    tier: Tier,
    constraints: Constraints,
    brand_rules: BrandRules,
    catalog: dict[str, Product],
) -> DeterministicReport:
    """Run every Stage-1 check and collect findings (empty == clean)."""
    findings: list[Finding] = []

    # Required fields present and non-empty.
    if not content.subject.strip():
        findings.append(Finding(code=FindingCode.MISSING_FIELD, message="subject is empty"))
    if not content.body.strip():
        findings.append(Finding(code=FindingCode.MISSING_FIELD, message="body is empty"))

    # Segment exists (enum membership) and is eligible for this tier.
    if not isinstance(content.target_segment, SegmentBand):
        findings.append(
            Finding(code=FindingCode.UNKNOWN_SEGMENT, message="target_segment is not a known band")
        )
    else:
        for v in C.check_segment_eligibility(tier, content.target_segment):
            findings.append(Finding(code=FindingCode.SEGMENT_INELIGIBLE, message=v.message))

    # Discount within the autonomous ceiling.
    for v in C.check_discount(content.discount_pct, constraints):
        findings.append(Finding(code=FindingCode.DISCOUNT_OVER_CEILING, message=v.message))

    # Prohibited terms (whole-word, case-insensitive) anywhere in the copy.
    haystack = " ".join([content.subject, content.preview_text, content.body, *content.claims])
    lowered = haystack.lower()
    for term in brand_rules.prohibited_terms:
        if re.search(rf"\b{re.escape(term.lower())}\b", lowered):
            findings.append(
                Finding(code=FindingCode.PROHIBITED_TERM, message=f"prohibited term: '{term}'")
            )

    # Link validity: if a CTA URL is present it must be a well-formed https URL.
    if content.cta_url and not _URL_RE.match(content.cta_url):
        findings.append(
            Finding(code=FindingCode.INVALID_LINK, message=f"invalid CTA url: '{content.cta_url}'")
        )

    # Referenced products must exist in the catalog.
    for pid in content.product_ids:
        if pid not in catalog:
            findings.append(
                Finding(code=FindingCode.UNKNOWN_PRODUCT, message=f"unknown product id: '{pid}'")
            )

    return DeterministicReport(findings=findings)
