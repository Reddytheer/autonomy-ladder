# 0007 — Vendor sets thresholds; the brand sets only a ceiling

Status: accepted. Implements SPEC §4 (threshold ownership).

## Context

Customers vary in risk appetite. A conservative brand wants to cap how far the
agent can ever act unsupervised; every brand wants *some* control. The dangerous
version of "giving the customer control" is letting them tune the safety
mechanism — lower the Wilson bar, widen segment eligibility, raise the discount
ceiling — because then the framework's safety guarantee becomes a per-customer
setting, and the weakest customer configuration defines the real floor. The
guarantee this project sells is that the floor is the vendor's and is
non-negotiable. Control and safety therefore have to be split so that a brand can
only ever make the system *more* conservative, never less.

## Decision

**The vendor sets promotion thresholds and constraints; the brand sets only a
ceiling.** The split is enforced in code, not documentation:

- Vendor-owned thresholds live in `config/tiers.yaml` — promotion gates, discount
  ceiling, rate limit, deliverability triggers, probation — and are the vendor's to
  change.
- Brand-owned policy is `config/brand_policy.yaml`, whose model `BrandPolicy`
  (`config.py`) exposes **exactly one field, `max_allowed_tier` (0–2)**, and sets
  **`extra='forbid'`**. That is the load-bearing line: if a brand tries to smuggle
  `wilson_lower_bound_min`, `max_discount_pct`, or any other override into the file,
  the loader **rejects the file outright** rather than silently ignoring the key.
- The ceiling can only *cap*. The controller computes the effective tier as
  `Tier(min(state.tier, brand.max_allowed_tier))` (`effective_tier`), and
  `_maybe_promote` refuses to promote past it. A brand ceiling can lower the earned
  tier but can never raise it.
- The true safety floor is not even in YAML: the tier→segment eligibility map and
  the never-autonomous segments are **hard-coded in Python** (`tiers.py`
  `_TIER_SEGMENTS`, `can_autosend`; `domain.py` `NEVER_AUTONOMOUS`), guarded twice
  by construction, so no config of any kind can widen who the agent may reach.

## Alternatives considered

**Fully brand-configurable thresholds** were rejected because they turn the safety
guarantee into a customer setting and make the least-careful configuration the de
facto floor. **A `min_allowed_tier` or a threshold-override block** was rejected for
the same reason — anything that lets a brand relax the gate defeats the purpose;
`extra='forbid'` exists precisely to make that attempt a loud failure. **Putting
segment eligibility in YAML** was rejected in favor of hard-coding, because the
never-autonomous rule is a floor, not a policy knob (SPEC §4: "Hard-coded, not
configurable").

## Consequences

Conservative customers get exactly the knob they need — a hard cap — and cannot
accidentally or deliberately weaken the floor for everyone. A misconfigured brand
file fails fast and legibly at load time instead of quietly degrading safety at
runtime. The deliberate limitation is that brands get *one* lever: they cannot
express finer per-type or per-segment policy through config. That is intentional
scope discipline — the surface a brand can touch is kept minimal precisely so the
safety floor stays the vendor's to guarantee.
