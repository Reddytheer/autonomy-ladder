# 0002 — The autonomy controller is deterministic code outside the agent

Status: accepted. Implements SPEC §2 P1 (complete mediation; OWASP LLM06,
Excessive Agency).

## Context

The whole value of this framework is that an agent can *earn the right to act
unsupervised*. That is only safe if the agent cannot also grant itself that right.
The failure mode we are defending against is Excessive Agency (OWASP LLM06): an
LLM that, through a persuasive rationale, a prompt injection carried in customer
data, or a plain hallucination, talks its way into a higher tier or an
unauthorized send. If any LLM output can influence a tier decision, the safety
story collapses — the agent is effectively self-certifying.

## Decision

The autonomy controller is **deterministic Python with no LLM in the loop**, and it
is the *sole* authority over what happens to a scored run and over every tier
transition. This is enforced structurally, not by convention:

- `RunEvaluation` (`src/autonomy_ladder/records.py`) — the only thing the
  controller consumes — **has no tier field**. It carries `run_id`,
  `campaign_type`, `segment`, `discount_pct`, and per-dimension judge scores. There
  is no channel through which an agent can express a desired tier, so there is
  nothing for the controller to honor.
- `AutonomyController.process_run` (`controller.py`) derives the effective tier
  itself — `Tier(min(state.tier, brand.max_allowed_tier))` — and decides
  `AUTO_SEND` vs `HUMAN_REVIEW` purely from `evaluation.passed`, the deterministic
  constraint checks in `constraints.py`, and `can_autosend` from `tiers.py`.
- The controller **holds no mutable tier state**. It reconstructs current standing
  from the append-only ledger on every call (`reconstruct` in `ledger.py`) and
  writes new facts back. State lives in exactly one place — the log — and the fold
  applies only recorded transitions, never an LLM's opinion.

## Alternatives considered

A **judge-as-decider** design (let a well-prompted LLM read the scores and emit the
tier) was rejected outright: it reintroduces exactly the Excessive-Agency vector
the principle exists to close, and no prompt hardening makes an LLM a trustworthy
gatekeeper of its own privileges. A **tier field on `RunEvaluation` that the
controller merely validates** was rejected because a validated-but-present channel
is still a channel; removing the field is a stronger guarantee than checking it.
Keeping **mutable state inside the controller** was rejected in favor of ledger
reconstruction so that P4 (every decision replayable) holds by construction.

## Consequences

No code path allows an LLM output to set a tier — this is asserted directly by the
unit tests (SPEC §14). The controller is fully unit-testable with no API key,
because it is ordinary deterministic code over records. The trade-off is that the
agent cannot express nuance the fixed rules do not capture ("this send is fine,
trust me") — which is the point: nuance the rules do not encode routes to a human,
never to autonomy.
