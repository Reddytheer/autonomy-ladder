"""The brief->product resolver reproduces every authored expected_products.

A resolver miss would surface downstream as a claim-verification failure and
corrupt judge accuracy / kappa, so this guards the resolver at the cheap layer
(no API key) the way the routing gate guards the controller.
"""

from __future__ import annotations

import pytest

from autonomy_ladder.data.loaders import catalog_index
from autonomy_ladder.evals.golden_loader import load_goldens
from autonomy_ladder.evals.resolver import resolve

_GOLDENS = load_goldens()
_WITH_PRODUCTS = [g for g in _GOLDENS if g.expected_products is not None]


def test_every_golden_carries_expected_products() -> None:
    # Exhaustive: all 75 are authored, so the test also catches over-matching
    # (a product resolved that the brief never named). 53 name a product; the
    # remaining 22 are content-only and asserted empty.
    assert len(_WITH_PRODUCTS) == 75
    nonempty = [g for g in _WITH_PRODUCTS if g.expected_products]
    assert len(nonempty) == 53


@pytest.mark.parametrize("case", _WITH_PRODUCTS, ids=lambda c: c.id)
def test_resolver_matches_expected_products(case) -> None:  # type: ignore[no-untyped-def]
    assert case.expected_products is not None
    assert resolve(case.brief) == sorted(case.expected_products), case.brief


def test_resolved_skus_exist_in_catalog() -> None:
    index = catalog_index()
    for case in _WITH_PRODUCTS:
        for sku in resolve(case.brief):
            assert sku in index, f"{case.id}: resolver returned unknown SKU {sku}"
