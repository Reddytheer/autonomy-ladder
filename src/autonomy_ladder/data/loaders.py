"""Typed access to the committed synthetic datasets.

Why this exists: the deterministic checks, the Claim Verifier, and the demo all
need the catalog and brand rules as typed objects, not raw dicts. Loading and
validation happen here once, so a malformed dataset fails loudly and early.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from autonomy_ladder.config import REPO_ROOT

DATA_DIR = REPO_ROOT / "data" / "synthetic"


class Claim(BaseModel):
    model_config = {"frozen": True}

    claim: str
    basis: str  # the attribute=value that grounds it, or "UNGROUNDED"


class Product(BaseModel):
    model_config = {"frozen": True}

    id: str
    name: str
    category: str
    price: float
    currency: str = "USD"
    attributes: dict[str, object] = Field(default_factory=dict)
    stock: int = 0
    claims: list[Claim] = Field(default_factory=list)


class BrandRules(BaseModel):
    model_config = {"frozen": True}

    voice: list[str] = Field(default_factory=list)
    tone_descriptors: list[str] = Field(default_factory=list)
    prohibited_terms: list[str] = Field(default_factory=list)
    required_disclaimers: list[str] = Field(default_factory=list)


@lru_cache(maxsize=1)
def load_catalog(path: Path | None = None) -> list[Product]:
    p = path or (DATA_DIR / "catalog.json")
    data = json.loads(p.read_text())
    return [Product.model_validate(row) for row in data]


@lru_cache(maxsize=1)
def catalog_index(path: Path | None = None) -> dict[str, Product]:
    """Products keyed by id, for O(1) claim grounding."""
    return {p.id: p for p in load_catalog(path)}


@lru_cache(maxsize=1)
def load_brand_rules(path: Path | None = None) -> BrandRules:
    p = path or (DATA_DIR / "brand_rules.yaml")
    return BrandRules.model_validate(yaml.safe_load(p.read_text()))


@lru_cache(maxsize=1)
def load_customers(path: Path | None = None) -> list[dict[str, object]]:
    """Customers as raw dicts — no layer needs a strict customer model yet."""
    p = path or (DATA_DIR / "customers.json")
    result: list[dict[str, object]] = json.loads(p.read_text())
    return result
