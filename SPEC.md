# SPEC.md — autonomy-ladder

**Implementation specification. Every design decision below is locked. Do not re-decide; implement as written.**

Owner: Theertha Yalagiri (github.com/Reddytheer)
Repo: `autonomy-ladder` — **create as PRIVATE**

---

## 0. Instructions for the implementing agent

- Build exactly what this spec says. Where the spec is silent on a detail, choose the simplest option and note it in `docs/open-questions.md`.
- **Never invent domain constraints or thresholds.** Every number in this spec is deliberate and sourced. If you think one is wrong, write it in `docs/open-questions.md` rather than changing it.
- Commit incrementally with meaningful messages. The commit history is part of the artifact — a reviewer will read it.
- Every module gets a docstring explaining *why it exists*, not just what it does.
- **Do not add features not in this spec.** Scope discipline is a deliverable.
- Do not add any content, prompt, schema, or example that references insurance, Assurified, InsurOps, or any real company's internal systems. This repo is clean-room.

---

## 1. What this is

A framework that lets an AI agent **earn the right to act without human approval**, per task type, based on measured performance — and lose it automatically when performance degrades.

The reference implementation is a marketing campaign agent, but the framework is domain-agnostic and the README must say so.

### The problem statement (use verbatim in README)

> Every agent product hits the same wall.
>
> An agent can draft, analyze, and decide. But shipping it into a workflow where mistakes are expensive means putting a human in front of every output. And a human reviewing every output means you have built a drafting tool, not automation. The reviewer becomes the bottleneck; the value never materializes.
>
> The industry's answer is a binary switch: human-approves-everything, or full autonomy. Both are wrong. The first caps value. The second is how you get an incident.
>
> The real question is not "can the agent do this" but "has the agent earned the right to do this unsupervised, for this specific kind of task, on this specific evidence."
>
> `autonomy-ladder` answers it in three parts: **measurement** (score output on dimensions that matter), **graduation** (move between autonomy tiers on statistical evidence, not vibes), and **enforcement** (guarantee the agent cannot exceed its granted tier, even if it is wrong about itself).

---

## 2. Non-negotiable architectural principles

**P1 — The autonomy controller is deterministic code outside the agent.**
No LLM participates in tier decisions. The agent produces work; the controller decides what happens to it. An agent must never be able to argue, prompt, or hallucinate its way into a higher tier. This implements the complete-mediation principle from OWASP LLM06 (Excessive Agency).

**P2 — Checks that grade generated output must be separate agents from the generator.**
A generator grading its own work is the weakest possible check. The Brand Sentinel and Claim Verifier are independent calls with their own prompts and no access to the generator's reasoning.

**P3 — Deterministic checks run before any LLM judge.**
Millisecond checks (schema, bounds, prohibited terms) filter obvious failures before expensive judges run.

**P4 — Every autonomy decision is recorded with its evidence.**
The trust ledger is append-only and replayable. Given the ledger, any tier state must be reconstructible.

**P5 — No secret ever enters the repository.**

---

## 3. Domain model

### Campaign types (autonomy is tracked per type)
```
promotional_discount
product_launch
newsletter
winback
restock_alert
```

### Segment engagement bands
Sourced from standard email-marketing practice: engagement segments are defined by recency of last activity, and send frequency should scale with engagement.

```
engaged_30d      # most engaged, lowest deliverability risk
engaged_60d
engaged_90d
engaged_180d
all_subscribers
first_time_buyers  # never eligible for autonomous send
lapsed             # never eligible for autonomous send
```

### Quality dimensions and their classes

| Dimension | Class | Behavior on failure |
|---|---|---|
| `segment_correctness` | **CRITICAL** | Blocks action + immediate demotion + probation |
| `claim_groundedness` | **CRITICAL** | Blocks action + immediate demotion + probation |
| `brand_voice` | **WEIGHTED** | Contributes to pass/fail score; repeated failures affect tier standing |
| `structure_quality` | **ADVISORY** | Recorded, never blocks, never affects tier |

Rationale to state in `docs/evaluation.md`: segment errors send the wrong content to the wrong people; unsupported claims create false-advertising exposure. Both cause real-world harm. Off-brand voice is a quality problem. Structure is deliberately advisory because marketing is an experimental discipline and a rigid structural check would suppress legitimate variation.

A run **passes** if: both CRITICAL dimensions pass AND `brand_voice` score ≥ 0.75.

---

## 4. The autonomy model

### Tiers

| Tier | Name | Autonomous send permitted to | Human involvement |
|---|---|---|---|
| 0 | `ASSIST` | nothing | Approves every campaign |
| 1 | `BOUNDED` | `engaged_30d` only | Reviews exceptions only |
| 2 | `SUPERVISED` | `engaged_30d`, `engaged_60d`, `engaged_90d` | Reviews outcomes on a cadence |

**Never autonomous at any tier:** `engaged_180d`, `all_subscribers`, `first_time_buyers`, `lapsed`. These always route to human review. Hard-coded, not configurable.

Additional Tier 1 and 2 constraints:
- Discount ceiling: autonomous campaigns may not exceed **25%** off. Above that → human review.
- Max autonomous sends per campaign type per 24h: **3** (rate limiting against runaway loops).

### Promotion — Wilson score interval

Promotion requires statistical confidence, not a raw streak. Use the **lower bound of the Wilson score confidence interval** on the pass rate.

```
p̂ = successes / n
z  = 1.96                                  # 95% confidence
lower = (p̂ + z²/(2n) − z·√((p̂(1−p̂) + z²/(4n))/n)) / (1 + z²/n)
```

| Transition | Window | Min runs | Wilson lower bound must exceed |
|---|---|---|---|
| 0 → 1 | last 50 runs of that campaign type | 20 | **0.85** |
| 1 → 2 | last 100 runs of that campaign type | 50 | **0.92** |

Why: a raw streak of 10/10 is not evidence. Wilson's lower bound requires both a high pass rate *and* sufficient sample size, so 10/10 does not promote but 47/50 does. This is the project's original contribution over the published progressive-autonomy literature, which specifies only "hundreds of successful operations." Document this in ADR 0004.

### Demotion and probation

Triggered by **either** a pre-send critical failure **or** a post-send deliverability breach.

**Post-send deliverability triggers** (industry-standard thresholds; a breach on any is critical):
- Spam complaint rate > **0.08%** (Google's reputation-damage threshold)
- Unsubscribe rate > **0.3%** per send
- Bounce rate > **0.5%** per send

This is the closed loop: pre-send evaluation decides whether the agent *may* act; post-send outcomes decide whether it *keeps* that standing.

**On trigger:**
1. Campaign type drops immediately to Tier 0
2. In-flight queued campaigns of that type are **downgraded to human review** — never cancelled, never sent
3. Agent enters **PROBATION** for that campaign type
4. Probation = run the full golden subset for that campaign type as a verification challenge
   - Pass (≥ tier-0→1 threshold) → restore to Tier 1, enter cooldown
   - Fail → remain Tier 0, flagged `INVESTIGATION_REQUIRED`, no automatic re-promotion
5. **Cooldown:** 20 further runs at the restored tier before promotion is eligible again

Rationale: a single failure may be a one-off. Probation distinguishes an anomaly from a systematic regression without assuming either.

### Threshold ownership

**The vendor (this framework) sets promotion thresholds. The brand sets a ceiling.**

A brand may configure `max_allowed_tier` (0, 1, or 2) to cap how far the agent may climb. A brand may **not** lower promotion thresholds or alter constraints. Conservative customers get control; the safety floor is non-negotiable.

Config lives in `config/brand_policy.yaml`.

---

## 5. Review queue — two lanes

Not one sorted list. The reviewer's attention is the scarce resource.

**Batch lane** — items with no critical flag and `brand_voice ≥ 0.75`. Presented as a group with a single approve-all action and a diff view. Not individually sorted.

**Judgment lane** — anything with a critical-dimension flag, a low confidence score, or a constraint violation. Sorted by risk score descending.

**Age is an SLA, not a sort key.** Each item carries a `send_window_expires_at`. Items within 20% of their window escalate to the top of their lane and are visually flagged. Expired items move to `EXPIRED` and are excluded from both lanes.

---

## 6. Agent architecture

Orchestrator-workers with sectioning for independent checks, plus an evaluator-optimizer revision loop.

```
Campaign Brief
      │
      ▼
┌─────────────────┐
│  Orchestrator   │  plans, delegates, assembles       [Sonnet]
└────────┬────────┘
         ├──────────────┬──────────────┐
         ▼              ▼              ▼
  ┌────────────┐ ┌────────────┐ ┌──────────────┐
  │  Segment   │ │   Copy     │ │   Catalog    │
  │  Analyst   │ │  Composer  │ │   Lookup     │
  │  [Haiku]   │ │  [Sonnet]  │ │   (tool)     │
  └─────┬──────┘ └─────┬──────┘ └──────┬───────┘
        └──────────────┴───────────────┘
                       │
         ┌─────────────┴─────────────┐
         ▼  (sectioning: parallel)   ▼
  ┌──────────────┐          ┌──────────────┐
  │Claim Verifier│          │Brand Sentinel│
  │   [Haiku]    │          │   [Sonnet]   │
  └──────┬───────┘          └──────┬───────┘
         └────────────┬────────────┘
                      ▼
             ┌─────────────────┐
             │  Revision loop  │  max 2 iterations
             └────────┬────────┘
                      ▼
        ╔═════════════════════════════╗
        ║  AUTONOMY CONTROLLER        ║  deterministic
        ║  (no LLM — see P1)          ║
        ╚══════════════┬══════════════╝
              ┌────────┴────────┐
              ▼                 ▼
         AUTO-SEND        REVIEW QUEUE
```

### Model routing (locked)
- **Haiku 4.5** (`claude-haiku-4-5-20251001`): Segment Analyst, Claim Verifier, deterministic classification
- **Sonnet 4.6** (`claude-sonnet-4-6`): Orchestrator, Copy Composer, Brand Sentinel, judges

Log tokens and cost per span. Produce a routing report showing cost delta and the accuracy impact (or lack of it) of the Haiku assignments.

---

## 7. Evaluation layer

**Stage 1 — deterministic** (no LLM, milliseconds): schema validity, required fields present, segment exists and is eligible for the requested tier, discount within ceiling, prohibited-term regex, link validity.

**Stage 2 — LLM judges**, checklist-style with structured rubrics, one judge call per dimension. Judges must output structured JSON: `{score: float 0-1, verdict: pass|fail, reasoning: str, evidence: list[str]}`.

**Judge calibration** — `evals/calibration/` holds ~40 human-labeled cases (pass/fail + one-line critique, authored by Theertha). Compute and report **Cohen's κ** between judge verdicts and human labels. Report per-dimension κ in `docs/evaluation.md`. If κ < 0.6 for a dimension, the judge prompt needs revision — say so in the docs rather than hiding it.

**Golden set** — `evals/goldens/*.jsonl`, versioned. Target 60–80 cases across campaign types, split `easy` / `ambiguous` / `adversarial`. Built collaboratively — leave the loader and schema ready, seed with 10 examples, and the rest will be added.

**Adversarial suite** — `evals/adversarial/`, separate from goldens. Must cover: prompt injection via customer-data fields, unsupported-claim bait, brand-rule traps, segment-boundary manipulation.

**Regression gate** — `make gate` runs the golden set, compares each dimension against `evals/baseline.json`, exits non-zero on any regression beyond tolerance. Wired as a GitHub Action on PR.

**Fixture caching** — cache LLM responses keyed by prompt hash in `evals/fixtures/`. Commit them. A reviewer must be able to run `make eval` and see real results **with no API key**. This is a deliberate usability decision; document it in the README.

---

## 8. Observability

OpenTelemetry with GenAI semantic conventions. Spans per LLM call and per tool call with `gen_ai.*` attributes: `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.response.finish_reasons`.

**Store prompt/completion content in span events, not span attributes** — attributes are indexed, size-limited, and leak PII. Note this in the code as a deliberate choice.

Export to console and to a local JSONL file by default. No external backend required to run the project.

---

## 9. UI — operator console

**Stack: FastAPI backend + single-page frontend (vanilla JS, Tailwind via CDN, no build step).** Do not add a bundler, framework, or npm dependency tree.

Four views:

1. **Autonomy dashboard** — tier per campaign type, current Wilson lower bound vs the threshold needed, runs remaining to promotion, probation/cooldown status. Must make the *reason* for the current tier obvious at a glance.
2. **Review queue** — two lanes as specified in §5, batch approve, diff view, SLA flags.
3. **Run detail** — full trace of one campaign: agent steps, each dimension's score and evidence, controller decision and why.
4. **Trust ledger** — chronological audit of tier changes with the evidence that caused each.

Design bar: clean, dense, information-first. This is an operator console, not a landing page. No hero sections, no marketing copy, no gradients. Neutral palette, one accent color for state changes.

---

## 10. Repo structure

```
autonomy-ladder/
├── README.md
├── AGENTS.md
├── LICENSE                          # MIT
├── SPEC.md                          # this file
├── pyproject.toml                   # uv
├── Makefile                         # setup, demo, eval, gate, ui, test
├── .env.example
├── .gitignore
├── .pre-commit-config.yaml          # ruff, mypy, gitleaks
├── .github/workflows/
│   ├── ci.yml
│   └── eval-gate.yml
├── config/
│   ├── brand_policy.yaml
│   └── tiers.yaml
├── docs/
│   ├── architecture.md
│   ├── autonomy-model.md
│   ├── evaluation.md
│   ├── open-questions.md
│   └── adr/
│       ├── 0001-orchestrator-workers-over-single-prompt.md
│       ├── 0002-controller-outside-the-agent.md
│       ├── 0003-independent-brand-sentinel.md
│       ├── 0004-wilson-interval-for-promotion.md
│       ├── 0005-critical-vs-weighted-dimensions.md
│       ├── 0006-two-lane-review-queue.md
│       └── 0007-vendor-thresholds-brand-ceiling.md
├── src/autonomy_ladder/
│   ├── agents/          orchestrator, segment_analyst, copy_composer,
│   │                    claim_verifier, brand_sentinel, revision
│   ├── autonomy/        tiers, controller, wilson, ledger, constraints, probation
│   ├── evals/           golden_loader, judges, calibration, deterministic, gate
│   ├── queue/           lanes, risk_score, sla
│   ├── observability/   otel setup, cost tracking
│   ├── data/            synthetic generators
│   ├── api/             fastapi app
│   └── cli.py
├── web/                 index.html, app.js, styles
├── data/synthetic/      catalog.json, customers.json, brand_rules.yaml
├── evals/
│   ├── goldens/         *.jsonl
│   ├── adversarial/     *.jsonl
│   ├── calibration/     human_labels.jsonl
│   ├── fixtures/        cached LLM responses
│   └── baseline.json
└── tests/
```

### ADR format
Each ADR: Context / Decision / Alternatives considered / Consequences. Two to four paragraphs. These are read by reviewers and are the video script — write them properly.

---

## 11. Secrets

- `.env` in `.gitignore`. Never committed.
- `.env.example` committed, keys documented, **values blank**.
- Config via `pydantic-settings`; fail loudly at startup with a clear message naming the missing variable.
- `gitleaks` in pre-commit.
- CI uses GitHub Actions repository secrets.
- Required: `ANTHROPIC_API_KEY`. Nothing else.

---

## 12. Synthetic data

Generate and commit:
- **Catalog:** ~40 products, fictional DTC brand ("Northbay Supply" — outdoor gear). Each with name, price, category, attributes, stock, and factual claims that the Claim Verifier can check against.
- **Customers:** ~500 profiles with purchase history, last-activity dates spread so all engagement bands are populated, and a `first_time_buyer` flag.
- **Brand rules:** voice guidelines, prohibited terms, required disclaimers, tone descriptors.

All fictional. No real brand, product, or person.

---

## 13. Build sequence

1. Scaffold: pyproject, Makefile, pre-commit, CI, LICENSE, .env.example, directory tree
2. Synthetic data generators + committed datasets
3. `autonomy/` — tiers, Wilson, controller, ledger, constraints, probation. **Full unit tests, no LLM needed.** This is the core; get it right first.
4. `evals/deterministic` + gate mechanics, tested
5. Agents against mocked responses; wiring tested without live calls
6. Judges + calibration harness
7. Observability
8. FastAPI + web console
9. ADRs and docs
10. README last, once results exist

**Steps 1–5 require no API key.** Complete them, commit, and hand back before live runs.

---

## 14. Acceptance criteria

- [ ] `make setup && make test` passes on a clean clone with no API key
- [ ] `make eval` runs off cached fixtures with no API key and prints a results table
- [ ] `make demo` runs one campaign end to end and shows the controller decision
- [ ] `make gate` exits non-zero on an intentionally degraded prompt
- [ ] Unit tests prove: 10/10 does **not** promote; 47/50 **does**
- [ ] Unit tests prove: a critical failure demotes, queues in-flight to review, and opens probation
- [ ] Unit tests prove: no code path allows an LLM output to set a tier
- [ ] Tier state is fully reconstructible by replaying the ledger
- [ ] `gitleaks` passes; no key anywhere in history
- [ ] Seven ADRs written
- [ ] Web console runs via `make ui` and shows all four views
- [ ] No reference to insurance, Assurified, InsurOps, or any real company

---

## 15. Out of scope

No auth, no real database (JSONL + SQLite only), no deployment, no Docker, no multi-tenancy, no real email sending, no additional model providers. Do not add them.
