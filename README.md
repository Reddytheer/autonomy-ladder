# autonomy-ladder

A framework that lets an AI agent **earn the right to act without human approval**,
per task type, based on measured performance — and lose it automatically when
performance degrades.

The reference implementation is a marketing-campaign agent for a fictional
outdoor-gear brand, but **the framework is domain-agnostic**: the autonomy
controller, the Wilson-interval promotion, the trust ledger, the two-lane review
queue, and the regression gate know nothing about marketing. Swap the agents and
the quality dimensions and the same machinery governs any agent whose mistakes are
expensive.

## The problem

> Every agent product hits the same wall.
>
> An agent can draft, analyze, and decide. But shipping it into a workflow where
> mistakes are expensive means putting a human in front of every output. And a
> human reviewing every output means you have built a drafting tool, not
> automation. The reviewer becomes the bottleneck; the value never materializes.
>
> The industry's answer is a binary switch: human-approves-everything, or full
> autonomy. Both are wrong. The first caps value. The second is how you get an
> incident.
>
> The real question is not "can the agent do this" but "has the agent earned the
> right to do this unsupervised, for this specific kind of task, on this specific
> evidence."
>
> `autonomy-ladder` answers it in three parts: **measurement** (score output on
> dimensions that matter), **graduation** (move between autonomy tiers on
> statistical evidence, not vibes), and **enforcement** (guarantee the agent
> cannot exceed its granted tier, even if it is wrong about itself).

## Quickstart

Requires Python ≥ 3.11 and [`uv`](https://docs.astral.sh/uv/). **No API key is
needed** for setup, tests, evals, the gate, or the UI.

```bash
make setup      # create the venv and install everything
make test       # full unit-test suite (no API key)
make eval       # run the golden set off cached fixtures; print a results table
make gate       # regression gate: non-zero exit on any per-dimension regression
make ui         # operator console at http://localhost:8000 (seeds demo state)
```

Two targets make live LLM calls and need `ANTHROPIC_API_KEY` (copy `.env.example`
to `.env`):

```bash
make demo       # run one campaign end-to-end; print the controller decision + routing report
make fixtures   # record real judge responses into evals/fixtures/
```

## How it works

Two halves, kept deliberately apart (see [docs/architecture.md](docs/architecture.md)):

```
Campaign brief ─► agents/ (orchestrator-workers)         ─► RunEvaluation
                  Segment Analyst [Haiku], Copy Composer [Sonnet],
                  Catalog Lookup (tool); then independent checks —
                  Claim Verifier [Haiku] + Brand Sentinel [Sonnet];
                  evaluator-optimizer revision loop (max 2)
                                    │
                                    ▼
                  ╔═════════════════════════════════╗
                  ║  AUTONOMY CONTROLLER            ║  deterministic, no LLM
                  ║  reads state from the ledger    ║
                  ╚════════════════┬════════════════╝
                          AUTO-SEND │ REVIEW QUEUE (batch │ judgment)
```

- **Measurement** — two-stage evaluation (deterministic checks, then one LLM judge
  per dimension) with Cohen's-κ calibration against human labels.
  [docs/evaluation.md](docs/evaluation.md)
- **Graduation** — promotion on the **lower bound of the Wilson score interval**,
  not a raw streak: 10/10 does not promote, 48/50 does. Demotion + probation +
  cooldown close the loop on pre-send critical failures and post-send
  deliverability breaches. [docs/autonomy-model.md](docs/autonomy-model.md)
- **Enforcement** — the controller is deterministic code *outside* the agent. No
  LLM output can set, argue, or hallucinate its way into a higher tier (P1). Tier
  state lives only in an append-only, replayable ledger.

## The operator console

`make ui` opens a dense, four-view console (FastAPI + vanilla JS, no build step):

1. **Autonomy dashboard** — tier per campaign type, Wilson lower bound vs the
   threshold needed, runs to promotion, probation/cooldown — and *why* each type
   is where it is.
2. **Review queue** — two lanes (batch approve vs risk-sorted judgment), SLA
   escalation, expiry.
3. **Run detail** — the full trace of one campaign: dimension scores + evidence
   and the controller decision with its reasons.
4. **Trust ledger** — the chronological audit of every tier change with the
   evidence that caused it.

## Key design decisions (ADRs)

1. [Orchestrator-workers over a single prompt](docs/adr/0001-orchestrator-workers-over-single-prompt.md)
2. [The controller lives outside the agent](docs/adr/0002-controller-outside-the-agent.md) (P1)
3. [Independent grading agents](docs/adr/0003-independent-brand-sentinel.md) (P2)
4. [Wilson interval for promotion](docs/adr/0004-wilson-interval-for-promotion.md)
5. [Critical vs weighted dimensions](docs/adr/0005-critical-vs-weighted-dimensions.md)
6. [Two-lane review queue](docs/adr/0006-two-lane-review-queue.md)
7. [Vendor thresholds, brand ceiling](docs/adr/0007-vendor-thresholds-brand-ceiling.md)

## Repository layout

```
src/autonomy_ladder/
  autonomy/    tiers, wilson, controller, ledger, constraints, probation  (no LLM)
  agents/      orchestrator + workers + independent checks + revision loop
  evals/       deterministic checks, judges, calibration, fixture cache, gate
  queue/       two-lane review queue, risk score, SLA
  observability/  OpenTelemetry spans + cost / routing report
  api/, web/   FastAPI backend + operator console
config/        tiers.yaml (vendor), brand_policy.yaml (brand)
data/synthetic/  catalog, customers, brand rules (fictional; generated)
evals/         goldens, adversarial, calibration, fixtures, baseline.json
docs/          architecture, autonomy-model, evaluation, open-questions, adr/
```

## Notes

- **Keyless reviewing is deliberate.** Judge responses are cached by prompt hash
  and committed; `make eval`/`make gate` replay them, so results are reproducible
  with no key. The committed fixtures are synthetic placeholders until you run
  `make fixtures`; see [docs/evaluation.md](docs/evaluation.md).
- **Scope** (SPEC §15): no auth, no real database (JSONL + SQLite only), no
  deployment, no Docker, no real email sending, no additional model providers.
- License: [MIT](LICENSE).
