"""Deterministic synthetic-data generator for the reference world (SPEC §12).

Why this module exists: every downstream layer — agents, evaluation, the review
queue, the UI — needs something real to operate on. Rather than depend on a live
store or a real brand's data (which would break the clean-room rule, SPEC §0),
we generate a fictional outdoor-gear world for "Northbay Supply" and commit it.

Two properties are load-bearing and non-negotiable:

* **Deterministic.** The RNG is seeded with a fixed value (:data:`SEED`), so
  re-running produces byte-identical files. The committed datasets are therefore
  a reproducible artifact a reviewer can regenerate and diff.
* **Idempotent.** Running the generator repeatedly overwrites the same three
  files with the same bytes; it never appends or accumulates state.

Run it with::

    python -m autonomy_ladder.data.generate

It writes ``catalog.json``, ``customers.json`` and ``brand_rules.yaml`` into
``data/synthetic/`` and prints a short summary.

Only the stdlib and PyYAML are used — no heavyweight data libraries — because the
data layer should stay as auditable and dependency-light as the rest of the repo.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from autonomy_ladder.domain import SegmentBand

# Fixed seed → deterministic output. Chosen as the project's reference "today"
# (2026-08-29) so the number is meaningful, not magic. Never change it casually:
# changing it re-rolls every product and customer and churns the committed files.
SEED = 20260829

# The reference "today". Customer last-activity dates are spread relative to this
# so that every engagement band (SPEC §3) is populated deterministically.
TODAY = date(2026, 8, 29)

TARGET_PRODUCTS = 40
TARGET_CUSTOMERS = 500

# Repo-root-relative output directory. This file lives at
# src/autonomy_ladder/data/generate.py, so the repo root is four parents up.
_REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = _REPO_ROOT / "data" / "synthetic"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

# Per-category building blocks. Names are assembled from a fictional line name +
# a model word so nothing collides with a real product. Attribute *generators*
# return factual specs; grounded marketing claims are then derived *from* those
# specs, which is exactly what the Claim Verifier needs to check against.
_CATEGORIES: dict[str, dict[str, Any]] = {
    "tents": {
        "lines": ["Ridgeline", "Basecamp", "Cirque", "Timberline", "Saddle"],
        "models": ["Dome", "Tarp Shelter", "Backpacking Tent", "Expedition Tent"],
        "price_range": (129.0, 549.0),
    },
    "backpacks": {
        "lines": ["Trailhead", "Summit", "Portage", "Switchback", "Longhaul"],
        "models": ["Daypack", "Trekking Pack", "Haul Bag", "Frame Pack"],
        "price_range": (59.0, 329.0),
    },
    "insulation": {
        "lines": ["Emberline", "Meadowlark", "Frostpine", "Coldsnap", "Downdraft"],
        "models": ["Down Jacket", "Sleeping Bag", "Insulated Vest", "Puffy Hoodie"],
        "price_range": (89.0, 419.0),
    },
    "footwear": {
        "lines": ["Granite", "Creekbed", "Talus", "Muddler", "Scramble"],
        "models": ["Hiking Boot", "Trail Shoe", "Approach Shoe", "Winter Boot"],
        "price_range": (79.0, 259.0),
    },
    "cookware": {
        "lines": ["Mess Kit", "Camp Chef", "Trailpot", "Ember", "Kettleworks"],
        "models": ["Cook Set", "Titanium Pot", "Camp Stove", "Kettle"],
        "price_range": (24.0, 149.0),
    },
    "lighting": {
        "lines": ["Nightwatch", "Lumen", "Beacon", "Halo", "Firefly"],
        "models": ["Headlamp", "Camp Lantern", "Trail Light", "Area Light"],
        "price_range": (19.0, 119.0),
    },
    "navigation": {
        "lines": ["Truenorth", "Wayfinder", "Meridian", "Pathfinder", "Compass Rose"],
        "models": ["Baseplate Compass", "GPS Unit", "Altimeter Watch", "Route Beacon"],
        "price_range": (29.0, 379.0),
    },
}

_MATERIALS = {
    "tents": ["ripstop nylon", "silnylon", "polyester taffeta"],
    "backpacks": ["ripstop nylon", "recycled polyester", "Cordura-style fabric"],
    "insulation": ["recycled polyester shell", "ripstop nylon", "responsibly sourced down"],
    "footwear": ["nubuck leather", "synthetic mesh", "waterproof suede"],
    "cookware": ["anodized aluminum", "titanium", "hard-anodized steel"],
    "lighting": ["ABS polymer", "aluminum housing", "impact-resistant plastic"],
    "navigation": ["glass-filled nylon", "aluminum alloy", "reinforced polymer"],
}


def _round_price(value: float) -> float:
    """Round to a realistic .99/.95 retail price point, deterministically."""
    return round(value) - 0.01


def _make_attributes(rng: random.Random, category: str) -> dict[str, Any]:
    """Return a dict of factual specs appropriate to the category.

    These are the ground truth. Grounded claims are derived from them so the
    Claim Verifier can trace every supported claim back to a value here.
    """
    material = rng.choice(_MATERIALS[category])
    if category == "tents":
        return {
            "weight_grams": rng.randrange(900, 3600, 50),
            "capacity_persons": rng.choice([1, 2, 2, 3, 4]),
            "waterproof_rating_mm": rng.choice([1500, 2000, 3000, 5000]),
            "season_rating": rng.choice([3, 3, 3, 4]),
            "packed_length_cm": rng.randrange(30, 60),
            "material": material,
        }
    if category == "backpacks":
        return {
            "weight_grams": rng.randrange(600, 2600, 50),
            "capacity_liters": rng.choice([18, 24, 32, 40, 45, 55, 65]),
            "waterproof_rating_mm": rng.choice([0, 1000, 1500, 2000]),
            "frame_type": rng.choice(["frameless", "internal frame", "external frame"]),
            "material": material,
        }
    if category == "insulation":
        return {
            "weight_grams": rng.randrange(250, 1600, 25),
            "fill_power": rng.choice([550, 650, 700, 800, 850]),
            "temperature_rating_c": rng.choice([-18, -9, -1, 4, 10]),
            "packable": rng.choice([True, True, False]),
            "material": material,
        }
    if category == "footwear":
        return {
            "weight_grams": rng.randrange(300, 1300, 25),
            "waterproof_rating_mm": rng.choice([0, 5000, 10000, 16000]),
            "sole_type": rng.choice(["lugged rubber", "sticky rubber", "multi-terrain"]),
            "material": material,
        }
    if category == "cookware":
        return {
            "weight_grams": rng.randrange(80, 900, 10),
            "capacity_liters": rng.choice([0.5, 0.75, 1.0, 1.3, 2.0]),
            "fuel_type": rng.choice(["canister gas", "liquid fuel", "n/a"]),
            "material": material,
        }
    if category == "lighting":
        return {
            "weight_grams": rng.randrange(30, 600, 5),
            "lumens": rng.choice([120, 250, 400, 600, 1000]),
            "battery_life_hours": rng.choice([8, 15, 30, 60, 100]),
            "waterproof_rating_ipx": rng.choice([4, 6, 7, 8]),
            "rechargeable": rng.choice([True, True, False]),
            "material": material,
        }
    # navigation
    return {
        "weight_grams": rng.randrange(25, 200, 5),
        "battery_life_hours": rng.choice([0, 20, 40, 80, 200]),
        "water_resistant": rng.choice([True, False]),
        "declination_adjustable": rng.choice([True, False]),
        "material": material,
    }


def _grounded_claims(attrs: dict[str, Any]) -> list[dict[str, str]]:
    """Derive marketing claims that are each grounded in a specific attribute.

    Every returned claim names the attribute/value that supports it in ``basis``.
    This is the "most claims must be grounded" majority (SPEC §12): the Claim
    Verifier should be able to confirm each one against the catalog.
    """
    claims: list[dict[str, str]] = []

    weight = attrs.get("weight_grams")
    if isinstance(weight, int) and weight <= 1200:
        claims.append(
            {
                "claim": f"Lightweight build at just {weight / 1000:.2f} kg.",
                "basis": f"weight_grams={weight}",
            }
        )

    wp = attrs.get("waterproof_rating_mm")
    if isinstance(wp, int) and wp >= 2000:
        claims.append(
            {
                "claim": f"Holds up in heavy rain with a {wp}mm waterproof rating.",
                "basis": f"waterproof_rating_mm={wp}",
            }
        )

    ipx = attrs.get("waterproof_rating_ipx")
    if isinstance(ipx, int) and ipx >= 6:
        claims.append(
            {
                "claim": f"Weather-sealed to IPX{ipx} against rain and splashes.",
                "basis": f"waterproof_rating_ipx={ipx}",
            }
        )

    season = attrs.get("season_rating")
    if isinstance(season, int):
        claims.append(
            {
                "claim": f"Built for {season}-season use.",
                "basis": f"season_rating={season}",
            }
        )

    cap_l = attrs.get("capacity_liters")
    if isinstance(cap_l, int | float) and cap_l:
        claims.append(
            {
                "claim": f"Carries up to {cap_l} liters of gear.",
                "basis": f"capacity_liters={cap_l}",
            }
        )

    cap_p = attrs.get("capacity_persons")
    if isinstance(cap_p, int):
        claims.append(
            {
                "claim": f"Sleeps {cap_p} comfortably.",
                "basis": f"capacity_persons={cap_p}",
            }
        )

    lumens = attrs.get("lumens")
    if isinstance(lumens, int):
        claims.append(
            {
                "claim": f"Throws {lumens} lumens on the high setting.",
                "basis": f"lumens={lumens}",
            }
        )

    battery = attrs.get("battery_life_hours")
    if isinstance(battery, int) and battery >= 20:
        claims.append(
            {
                "claim": f"Runs up to {battery} hours per charge.",
                "basis": f"battery_life_hours={battery}",
            }
        )

    temp = attrs.get("temperature_rating_c")
    if isinstance(temp, int):
        claims.append(
            {
                "claim": f"Rated down to {temp}°C.",
                "basis": f"temperature_rating_c={temp}",
            }
        )

    fill = attrs.get("fill_power")
    if isinstance(fill, int):
        claims.append(
            {
                "claim": f"Lofts with {fill}-fill-power down for warmth-to-weight.",
                "basis": f"fill_power={fill}",
            }
        )

    material = attrs.get("material")
    if isinstance(material, str):
        claims.append(
            {
                "claim": f"Made from durable {material}.",
                "basis": f"material={material}",
            }
        )

    return claims


# Deliberately unsupported claims for the Claim Verifier to catch (SPEC §12).
# These sound factual but are NOT backed by any attribute value, so a grounded
# check must flag them. They are distinct from brand-voice hype (see
# ``prohibited_terms`` in brand_rules) — this is a groundedness failure, not a
# tone failure.
_UNGROUNDED_CLAIMS = [
    "Certified for high-altitude expeditions above 6,000 meters.",
    "Blocks 100% of UV radiation.",
    "Trusted by professional mountaineering guides worldwide.",
    "Tested to survive a 10-meter drop onto granite.",
    "Keeps contents warm for a full 24 hours.",
    "Field-proven across all seven continents.",
]


def build_catalog(rng: random.Random) -> list[dict[str, Any]]:
    """Build ~40 fictional products with grounded (and a few ungrounded) claims."""
    categories = list(_CATEGORIES)
    products: list[dict[str, Any]] = []

    # Every product carrying an ungrounded claim is chosen up front so the set is
    # fixed and deterministic: a known handful (~6) for the verifier to catch.
    ungrounded_indices = set(rng.sample(range(TARGET_PRODUCTS), k=len(_UNGROUNDED_CLAIMS)))

    for i in range(TARGET_PRODUCTS):
        # Round-robin categories so the catalog is spread across all of them.
        category = categories[i % len(categories)]
        spec = _CATEGORIES[category]
        line = rng.choice(spec["lines"])
        model = rng.choice(spec["models"])
        name = f"Northbay {line} {model}"

        lo, hi = spec["price_range"]
        price = _round_price(rng.uniform(lo, hi))
        attrs = _make_attributes(rng, category)

        grounded = _grounded_claims(attrs)
        # Keep 2-3 grounded claims per product for variety without bloat.
        rng.shuffle(grounded)
        claims = grounded[: rng.choice([2, 3])]

        if i in ungrounded_indices:
            # Index into the pool deterministically by position among ungrounded,
            # so each of the ~6 flagged products gets a distinct unsupported claim.
            ung_pos = sorted(ungrounded_indices).index(i)
            claims.append({"claim": _UNGROUNDED_CLAIMS[ung_pos], "basis": "UNGROUNDED"})

        products.append(
            {
                "id": f"NB-{i + 1:04d}",
                "name": name,
                "category": category,
                "price": price,
                "currency": "USD",
                "attributes": attrs,
                "stock": rng.randrange(0, 500),
                "claims": claims,
            }
        )

    return products


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------

# How many customers fall in each band. Deliberately more engaged than lapsed to
# look like a real, healthy list (SPEC §12). first_time_buyers and lapsed are
# their own bands and take precedence over recency (SPEC §3). Sums to 500.
_BAND_COUNTS: dict[SegmentBand, int] = {
    SegmentBand.ENGAGED_30D: 120,
    SegmentBand.ENGAGED_60D: 95,
    SegmentBand.ENGAGED_90D: 70,
    SegmentBand.ENGAGED_180D: 75,
    SegmentBand.ALL_SUBSCRIBERS: 55,
    SegmentBand.FIRST_TIME_BUYERS: 50,
    SegmentBand.LAPSED: 35,
}

# The last-activity age window (days before TODAY) that produces each recency
# band. first_time_buyers get a recent window (their one purchase) but are
# labeled by the flag, not recency; lapsed are intentionally very old.
_BAND_DAY_RANGE: dict[SegmentBand, tuple[int, int]] = {
    SegmentBand.ENGAGED_30D: (0, 30),
    SegmentBand.ENGAGED_60D: (31, 60),
    SegmentBand.ENGAGED_90D: (61, 90),
    SegmentBand.ENGAGED_180D: (91, 180),
    SegmentBand.ALL_SUBSCRIBERS: (181, 365),
    SegmentBand.FIRST_TIME_BUYERS: (0, 45),
    SegmentBand.LAPSED: (366, 900),
}


def derive_engagement_band(first_time_buyer: bool, last_activity: date, today: date) -> SegmentBand:
    """Classify a customer into a SegmentBand from their raw fields.

    This is the single source of truth for band membership and mirrors the
    segmentation the Segment Analyst / controller rely on. ``first_time_buyers``
    and ``lapsed`` take precedence over recency (SPEC §3, §12): a first-time
    buyer is always ``FIRST_TIME_BUYERS`` regardless of how recent the activity
    is, and anyone inactive for more than a year is ``LAPSED``.
    """
    days = (today - last_activity).days
    if first_time_buyer:
        return SegmentBand.FIRST_TIME_BUYERS
    if days > 365:
        return SegmentBand.LAPSED
    if days <= 30:
        return SegmentBand.ENGAGED_30D
    if days <= 60:
        return SegmentBand.ENGAGED_60D
    if days <= 90:
        return SegmentBand.ENGAGED_90D
    if days <= 180:
        return SegmentBand.ENGAGED_180D
    return SegmentBand.ALL_SUBSCRIBERS


def build_customers(rng: random.Random, catalog: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build ~500 customer profiles spread across every engagement band."""
    product_ids = [p["id"] for p in catalog]

    # Fixed multiset of target bands, then shuffled so ids and bands are not
    # correlated. Deterministic under the seed.
    targets: list[SegmentBand] = []
    for band, count in _BAND_COUNTS.items():
        targets.extend([band] * count)
    rng.shuffle(targets)

    customers: list[dict[str, Any]] = []
    for i, target in enumerate(targets):
        first_time = target is SegmentBand.FIRST_TIME_BUYERS
        lo, hi = _BAND_DAY_RANGE[target]
        activity_offset = rng.randint(lo, hi)
        last_activity = TODAY - timedelta(days=activity_offset)

        # Order count scales with engagement; first-time buyers have exactly one.
        if first_time:
            num_orders = 1
        elif target is SegmentBand.LAPSED:
            num_orders = rng.randint(1, 4)
        elif target in (SegmentBand.ENGAGED_30D, SegmentBand.ENGAGED_60D):
            num_orders = rng.randint(3, 9)
        else:
            num_orders = rng.randint(2, 6)

        # Build purchase history ending at last_activity (the most recent order),
        # walking backwards in time so dates stay consistent with the band.
        history: list[dict[str, Any]] = []
        cursor = last_activity
        for _ in range(num_orders):
            history.append(
                {
                    "product_id": rng.choice(product_ids),
                    "date": cursor.isoformat(),
                    "quantity": rng.randint(1, 3),
                }
            )
            cursor = cursor - timedelta(days=rng.randint(20, 160))
        history.reverse()  # chronological: oldest first

        band = derive_engagement_band(first_time, last_activity, TODAY)
        # Invariant: the intended band must match what the fields actually imply.
        # If this ever fails the day-ranges above drifted out of sync with the
        # classifier — a bug, not something to paper over.
        assert band is target, f"band mismatch for customer {i}: {band} != {target}"

        customers.append(
            {
                "id": f"C-{i + 1:05d}",
                "email": f"user{i + 1:04d}@example.com",
                "first_time_buyer": first_time,
                "last_activity_date": last_activity.isoformat(),
                "total_orders": num_orders,
                "purchase_history": history,
                "engagement_band": band.value,
            }
        )

    return customers


# ---------------------------------------------------------------------------
# Brand rules
# ---------------------------------------------------------------------------


def build_brand_rules() -> dict[str, Any]:
    """Northbay Supply brand rules (SPEC §12).

    ``prohibited_terms`` is the list a deterministic pre-LLM check (SPEC §7,
    Stage 1) can regex against, plus a signal for the Brand Sentinel. The terms
    are hype/absolutist words that create legal or trust risk in marketing copy.
    All content is fictional and generic to outdoor gear.
    """
    return {
        "brand": "Northbay Supply",
        "voice": [
            "Write plainly and specifically; name the feature and what it does.",
            "Lead with the customer's trip, not the product's ego.",
            "Prefer concrete numbers (weight, capacity, ratings) over adjectives.",
            "Respect the reader's experience; assume they know the outdoors.",
            "Be honest about limits — say what the gear is not built for.",
            "One clear call to action per message.",
        ],
        "tone_descriptors": [
            "grounded",
            "practical",
            "warm",
            "understated",
            "trail-tested",
            "unpretentious",
        ],
        "prohibited_terms": [
            "guaranteed",
            "miracle",
            "risk-free",
            "best ever",
            "cure",
            "100% safe",
            "unbreakable",
            "lifetime warranty",
            "revolutionary",
            "world's best",
            "bulletproof",
        ],
        "required_disclaimers": [
            "Free shipping applies to U.S. orders over $75; exclusions apply.",
            "Returns accepted within 30 days on unused gear with original tags.",
            "Prices shown in USD and subject to change without notice.",
            "Weather and durability ratings are lab-tested estimates, not guarantees "
            "of performance in the field.",
        ],
    }


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _write_json(path: Path, payload: Any) -> None:
    """Write JSON deterministically (stable spacing, trailing newline, UTF-8)."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def _write_yaml(path: Path, payload: Any) -> None:
    """Write YAML deterministically (no key sorting, block style, UTF-8)."""
    text = yaml.safe_dump(
        payload, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100
    )
    path.write_text(text, encoding="utf-8")


def generate(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Generate and write all three datasets. Returns a summary dict.

    Deterministic and idempotent: one seeded RNG threads through every draw, and
    the files are overwritten in place, so repeated runs yield identical bytes.
    """
    rng = random.Random(SEED)
    output_dir.mkdir(parents=True, exist_ok=True)

    catalog = build_catalog(rng)
    customers = build_customers(rng, catalog)
    brand_rules = build_brand_rules()

    catalog_path = output_dir / "catalog.json"
    customers_path = output_dir / "customers.json"
    brand_rules_path = output_dir / "brand_rules.yaml"

    _write_json(catalog_path, catalog)
    _write_json(customers_path, customers)
    _write_yaml(brand_rules_path, brand_rules)

    band_distribution: dict[str, int] = {band.value: 0 for band in SegmentBand}
    for customer in customers:
        band_distribution[customer["engagement_band"]] += 1

    ungrounded = sum(1 for p in catalog for c in p["claims"] if c["basis"] == "UNGROUNDED")

    return {
        "catalog_path": str(catalog_path),
        "customers_path": str(customers_path),
        "brand_rules_path": str(brand_rules_path),
        "product_count": len(catalog),
        "customer_count": len(customers),
        "ungrounded_claim_count": ungrounded,
        "band_distribution": band_distribution,
    }


def main() -> None:
    """Entry point for ``python -m autonomy_ladder.data.generate``."""
    summary = generate()
    print(f"Synthetic data written (deterministic, seed={SEED}):")
    print(
        f"  {summary['catalog_path']}  ({summary['product_count']} products, "
        f"{summary['ungrounded_claim_count']} ungrounded claims)"
    )
    print(f"  {summary['customers_path']}  ({summary['customer_count']} customers)")
    print(f"  {summary['brand_rules_path']}")
    print("Engagement band distribution:")
    for band, count in summary["band_distribution"].items():
        print(f"  {band:<18} {count}")


if __name__ == "__main__":
    main()
