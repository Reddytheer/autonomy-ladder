"""Stage-1 deterministic checks (SPEC §7)."""

from __future__ import annotations

from autonomy_ladder.autonomy.tiers import Tier
from autonomy_ladder.data.loaders import catalog_index, load_brand_rules
from autonomy_ladder.domain import SegmentBand
from autonomy_ladder.evals.deterministic import FindingCode, run_deterministic_checks
from autonomy_ladder.records import CampaignContent

from .conftest import make_tiers_config

CONS = make_tiers_config().constraints
BRAND = load_brand_rules()
CATALOG = catalog_index()


def _content(**kw: object) -> CampaignContent:
    base: dict[str, object] = {
        "subject": "Trail-ready picks",
        "body": "Some genuinely useful copy about gear.",
        "cta_text": "Shop",
        "cta_url": "https://northbay.example.com/shop",
        "claims": [],
        "target_segment": SegmentBand.ENGAGED_30D,
        "discount_pct": 0.0,
        "product_ids": [],
    }
    base.update(kw)
    return CampaignContent.model_validate(base)


def test_clean_content_passes() -> None:
    r = run_deterministic_checks(
        _content(), tier=Tier.BOUNDED, constraints=CONS, brand_rules=BRAND, catalog=CATALOG
    )
    assert r.ok


def test_empty_fields_flagged() -> None:
    r = run_deterministic_checks(
        _content(subject="", body=""),
        tier=Tier.BOUNDED,
        constraints=CONS,
        brand_rules=BRAND,
        catalog=CATALOG,
    )
    assert FindingCode.MISSING_FIELD in {f.code for f in r.findings}


def test_discount_over_ceiling_flagged() -> None:
    r = run_deterministic_checks(
        _content(discount_pct=40),
        tier=Tier.BOUNDED,
        constraints=CONS,
        brand_rules=BRAND,
        catalog=CATALOG,
    )
    assert FindingCode.DISCOUNT_OVER_CEILING in {f.code for f in r.findings}


def test_ineligible_segment_flagged() -> None:
    # engaged_60d is not eligible at BOUNDED (tier 1).
    r = run_deterministic_checks(
        _content(target_segment=SegmentBand.ENGAGED_60D),
        tier=Tier.BOUNDED,
        constraints=CONS,
        brand_rules=BRAND,
        catalog=CATALOG,
    )
    assert FindingCode.SEGMENT_INELIGIBLE in {f.code for f in r.findings}


def test_prohibited_term_flagged() -> None:
    term = BRAND.prohibited_terms[0]
    r = run_deterministic_checks(
        _content(body=f"This is {term} and you must buy now."),
        tier=Tier.BOUNDED,
        constraints=CONS,
        brand_rules=BRAND,
        catalog=CATALOG,
    )
    assert FindingCode.PROHIBITED_TERM in {f.code for f in r.findings}


def test_invalid_link_and_unknown_product_flagged() -> None:
    r = run_deterministic_checks(
        _content(cta_url="not-a-url", product_ids=["NB-9999"]),
        tier=Tier.BOUNDED,
        constraints=CONS,
        brand_rules=BRAND,
        catalog=CATALOG,
    )
    codes = {f.code for f in r.findings}
    assert FindingCode.INVALID_LINK in codes
    assert FindingCode.UNKNOWN_PRODUCT in codes
