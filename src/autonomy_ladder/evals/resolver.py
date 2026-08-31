"""Brief -> product resolver (HANDOFF step 11 prerequisite).

The goldens are prose ("the Cascade pack", "20% off headlamps"), not SKUs — a
real brief names products the way a marketer would. Turning that prose into
catalog ``product_ids`` is part of the job, so it lives here as deterministic
code, not in a judge. Resolver mistakes would otherwise surface as spurious
claim-verification failures and corrupt the judge-accuracy / kappa numbers, so
every product-bearing golden carries an authored ``expected_products`` list and
``tests/test_resolver.py`` asserts this resolver reproduces it.

Matching is keyword-based over two token classes:

* **Proper nouns** — the golden product universe (Beacon, Summit, Cascade,
  Stormline, Ridgeline, Trailwool). Unambiguous.
* **Category words** — a brief may say "headlamps" / "the tent" / "jackets"
  without the brand name. Each maps to the one golden product of that kind.

The only collision is "pack": Trailwool ships as a *3-pack*, so "pack" must not
be read as the Cascade trekking pack when it is part of "3-pack" (or otherwise
glued to a preceding token). A negative look-behind handles that.
"""

from __future__ import annotations

import re

# Distinctive brand tokens -> SKU. These are unambiguous.
_PROPER: dict[str, str] = {
    "trailwool": "NB-SOK-11",
    "beacon": "NB-LMP-05",
    "summit": "NB-BTL-03",
    "cascade": "NB-TRK-01",
    "stormline": "NB-JKT-07",
    "ridgeline": "NB-TNT-02",
}

# Category words -> SKU, for briefs that omit the brand name. Each pattern is a
# whole-word regex. "pack(s)" uses a look-behind so "3-pack" / "sock-pack" does
# not resolve to the Cascade trekking pack.
_GENERIC: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bheadlamps?\b"), "NB-LMP-05"),
    (re.compile(r"\bbottles?\b"), "NB-BTL-03"),
    (re.compile(r"\btents?\b"), "NB-TNT-02"),
    (re.compile(r"\bjackets?\b"), "NB-JKT-07"),
    (re.compile(r"\bsocks?\b"), "NB-SOK-11"),
    (re.compile(r"(?<![\w-])packs?\b"), "NB-TRK-01"),
]

_PROPER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\b{re.escape(token)}\b"), sku) for token, sku in _PROPER.items()
]

# A category word inside a comparison clause ("waterproof to 20k like our
# jackets") names a reference product, not the campaign's subject — so it must
# not be resolved as a launched/promoted product. We blank the noun after a
# comparison marker before generic matching. Proper nouns still match on the
# original text (a comparison to a *named* product would be caught there).
_COMPARISON = re.compile(r"\b(?:like|than)\s+(?:our|the|a|your|my)?\s*[a-z]+")


def resolve(brief: str) -> list[str]:
    """Return the catalog SKUs a brief refers to, sorted and de-duplicated.

    Returns an empty list for briefs with no product reference (e.g. content-only
    newsletters) — that is correct, not a failure: such campaigns ground against
    "(no products)".
    """
    text = brief.lower()
    generic_text = _COMPARISON.sub(" ", text)
    found: set[str] = set()
    for pattern, sku in _PROPER_PATTERNS:
        if pattern.search(text):
            found.add(sku)
    for pattern, sku in _GENERIC:
        if pattern.search(generic_text):
            found.add(sku)
    return sorted(found)
