"""Configuration loading — environment secrets and the two YAML policy files.

Why this module exists: SPEC §11 requires config via ``pydantic-settings`` that
"fails loudly at startup with a clear message naming the missing variable," and
SPEC §4 splits configuration into vendor-owned thresholds (``config/tiers.yaml``)
and a brand-owned ceiling (``config/brand_policy.yaml``). This module is the one
place those files are parsed and validated.

Keyless by default: ``ANTHROPIC_API_KEY`` is *not* required to import anything or
to run tests, evals, or the gate — those replay cached fixtures (SPEC §7, §13).
The key is required only when a live LLM call is actually attempted, at which
point :func:`require_api_key` raises a clear, named error.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root = two levels up from this file (src/autonomy_ladder/config.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TIERS_PATH = REPO_ROOT / "config" / "tiers.yaml"
DEFAULT_BRAND_POLICY_PATH = REPO_ROOT / "config" / "brand_policy.yaml"


class Settings(BaseSettings):
    """Environment-driven settings. All fields have safe, keyless defaults."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Optional so that import, tests, evals, and the gate never require a key.
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    telemetry_path: str = Field(default="telemetry/spans.jsonl", alias="AL_TELEMETRY_PATH")
    otel_console_export: bool = Field(default=True, alias="AL_OTEL_CONSOLE_EXPORT")
    ledger_path: str = Field(default="runtime/ledger.sqlite", alias="AL_LEDGER_PATH")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings (cached)."""
    return Settings()


def require_api_key() -> str:
    """Return the Anthropic API key or fail loudly naming the missing variable.

    Called only on live-call code paths (SPEC §11). Keyless paths never touch it.
    """
    key = get_settings().anthropic_api_key
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Live agent runs (make demo / make "
            "fixtures) require it. Copy .env.example to .env and fill it in. "
            "Note: make test / make eval / make gate do NOT need a key."
        )
    return key


# ---- YAML policy models -----------------------------------------------------


class PromotionGate(BaseModel):
    """One promotion transition's statistical gate (SPEC §4)."""

    model_config = {"frozen": True, "extra": "forbid"}

    window: int = Field(gt=0, description="Number of most-recent runs considered")
    min_runs: int = Field(gt=0, description="Minimum runs in the window before eligible")
    wilson_lower_bound_min: float = Field(gt=0.0, le=1.0)


class Constraints(BaseModel):
    """Hard constraints on autonomous sends (SPEC §4)."""

    model_config = {"frozen": True, "extra": "forbid"}

    max_discount_pct: float = Field(gt=0)
    max_autonomous_sends_per_type_per_24h: int = Field(gt=0)


class DeliverabilityTriggers(BaseModel):
    """Post-send deliverability breach thresholds (SPEC §4)."""

    model_config = {"frozen": True, "extra": "forbid"}

    spam_complaint_rate_max: float = Field(gt=0)
    unsubscribe_rate_max: float = Field(gt=0)
    bounce_rate_max: float = Field(gt=0)


class ProbationConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    cooldown_runs: int = Field(ge=0)


class TiersConfig(BaseModel):
    """The whole vendor-owned config file (config/tiers.yaml)."""

    model_config = {"frozen": True, "extra": "forbid"}

    promotion: dict[str, PromotionGate]
    constraints: Constraints
    deliverability_triggers: DeliverabilityTriggers
    probation: ProbationConfig

    def gate(self, from_tier: int, to_tier: int) -> PromotionGate:
        """Return the promotion gate for a transition, e.g. gate(0, 1)."""
        key = f"{from_tier}->{to_tier}"
        if key not in self.promotion:
            raise KeyError(f"No promotion gate configured for {key}")
        return self.promotion[key]


class BrandPolicy(BaseModel):
    """Brand-owned policy — the ONLY knob a brand controls (SPEC §4, ADR 0007).

    ``extra='forbid'`` is load-bearing: it rejects any attempt to smuggle
    threshold overrides or constraint changes into the brand file. A brand can
    cap the ceiling; it cannot touch the safety floor.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    max_allowed_tier: int = Field(ge=0, le=2)

    @model_validator(mode="after")
    def _validate(self) -> BrandPolicy:
        return self


def load_tiers_config(path: Path | None = None) -> TiersConfig:
    """Load and validate the vendor tiers config."""
    p = path or DEFAULT_TIERS_PATH
    data = yaml.safe_load(p.read_text())
    return TiersConfig.model_validate(data)


def load_brand_policy(path: Path | None = None) -> BrandPolicy:
    """Load and validate the brand policy (rejects unknown keys)."""
    p = path or DEFAULT_BRAND_POLICY_PATH
    data = yaml.safe_load(p.read_text())
    return BrandPolicy.model_validate(data)
