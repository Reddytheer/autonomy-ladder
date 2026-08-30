# Architecture

`autonomy-ladder` has two halves that are deliberately kept apart, plus a thin
service and UI on top. The whole design exists to enforce one boundary: **the
agent produces work; deterministic code decides what happens to it.**

## The two halves

| | The agent (`src/autonomy_ladder/agents/`) | The controller (`src/autonomy_ladder/autonomy/`) |
|---|---|---|
| Made of | LLM calls | Deterministic Python, no LLM |
| Job | generate a campaign, grade it | decide auto-send vs review; move tiers |
| Can set a tier? | **never** (P1) | it is the only thing that can |
| Tested with | mocked / replayed responses | pure unit tests, no API key |

This is complete mediation (OWASP LLM06). The only thing the agent hands the
controller is a `RunEvaluation` — numbers and enums. It has no field that can
request a tier, and the controller reads free-text judge reasoning for the audit
trail only. See [ADR 0002](adr/0002-controller-outside-the-agent.md).

## Data flow

```
CampaignBrief
     │
     ▼   agents/ (orchestrator-workers, SPEC §6)
 ┌───────────────┐   Segment Analyst [Haiku] ─┐
 │  Orchestrator │   Copy Composer  [Sonnet] ─┼─► CampaignContent
 │   [Sonnet]    │   Catalog Lookup (tool) ───┘
 └───────┬───────┘
         │  evals/ (two stages, cheapest first — P3)
         ▼
   Stage 1 deterministic ─► Stage 2 judges (one per dimension, P2)
   (schema, segment, discount,   Claim Verifier [Haiku]
    prohibited terms, links)      Brand Sentinel [Sonnet] + segment/structure judges
         │
         ▼  evaluator-optimizer revision loop (max 2)
   RunEvaluation  ── passed? both CRITICAL pass AND brand_voice ≥ 0.75
         │
         ▼
 ╔═══════════════════════════════╗
 ║  AUTONOMY CONTROLLER          ║  deterministic (autonomy/controller.py)
 ║  reads state from the ledger  ║
 ╚═══════════════┬═══════════════╝
        ┌────────┴────────┐
        ▼                 ▼
   AUTO-SEND         REVIEW QUEUE (two lanes, queue/)
   (in-flight)       batch │ judgment
        │
        ▼  post-send
   DeliverabilityReport ─► controller (demote + probation on a breach)
```

## Where state lives

State lives in exactly one place: the **append-only trust ledger**
(`autonomy/ledger.py`, SQLite, UPDATE/DELETE blocked by triggers). The controller
never stores a tier; it *reconstructs* the current `TierState` for a campaign type
by folding the ledger (`reconstruct()`). Given the ledger, any tier state is
replayable — including across a process restart. See
[ADR 0004](adr/0004-wilson-interval-for-promotion.md) and §4 of `SPEC.md`.

The review queue (`queue/`) and run traces (`service.py: RunStore`) are separate,
*mutable* SQLite stores — items get approved, expire, or are downgraded — kept
apart from the immutable ledger on purpose.

## Module map

| Package | What it holds |
|---|---|
| `domain.py`, `records.py` | shared vocabulary and neutral data records |
| `autonomy/` | tiers, Wilson interval, constraints, ledger, probation, controller |
| `evals/` | deterministic checks, judges, calibration, fixture cache, the gate |
| `agents/` | orchestrator, segment analyst, copy composer, catalog lookup, claim verifier, brand sentinel, revision |
| `queue/` | two-lane review queue, risk score, SLA |
| `observability/` | OpenTelemetry setup, cost + routing report |
| `service.py` | the facade that wires controller + queue + runs |
| `api/`, `web/` | FastAPI backend and the vanilla-JS operator console |

## Dependency direction

The safety-critical `autonomy/` package imports only from `domain`, `records`, and
`config` — never from `evals` or `agents`. The trust relationship only points one
way: the checks and the agent feed the controller; the controller never depends on
them. That is what keeps P1 true by construction.
