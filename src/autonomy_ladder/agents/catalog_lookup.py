"""Catalog Lookup — a deterministic tool, not an LLM (SPEC §6).

Grounding claims starts with facts, so this is plain code that pulls product
attributes and known-good claims from the catalog. Giving the Copy Composer and
Claim Verifier the same factual basis is what makes grounding checkable.
"""

from __future__ import annotations

from autonomy_ladder.data.loaders import Product, catalog_index
from autonomy_ladder.observability.otel import tool_span


def lookup(product_ids: list[str]) -> list[Product]:
    """Return the catalog products for the given ids (unknown ids are skipped)."""
    with tool_span("catalog.lookup", tool="catalog_lookup") as span:
        index = catalog_index()
        products = [index[pid] for pid in product_ids if pid in index]
        span.set_attribute("catalog.requested", len(product_ids))
        span.set_attribute("catalog.found", len(products))
        return products


def facts_block(products: list[Product]) -> str:
    """Render products as a compact facts block for grounding prompts."""
    lines = []
    for p in products:
        grounded = "; ".join(f"{c.claim} ({c.basis})" for c in p.claims)
        lines.append(f"{p.id} {p.name} [{p.category}] attrs={p.attributes} claims={grounded}")
    return "\n".join(lines) or "(no products)"
