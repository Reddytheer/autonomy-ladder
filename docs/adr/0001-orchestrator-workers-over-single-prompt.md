# 0001 — Orchestrator-workers over a single mega-prompt

Status: accepted. Relates to SPEC §6.

## Context

A campaign brief for Northbay Supply has to be turned into a targeted send: pick a
segment, compose copy, look up catalog facts, verify every claim, and check the
brand voice. The tempting shortcut is one large prompt that does all of it in a
single call. That fails us on three axes that this project treats as load-bearing.
First, *model economics*: segment classification and claim lookup are cheap,
mechanical work that Haiku does well, while composition and brand judgement need
Sonnet (SPEC §6 model routing). A monolith forces the whole task onto the most
expensive model. Second, *independence*: a single prompt that both writes the copy
and grades it is self-grading, which SPEC P2 forbids and ADR 0003 expands on.
Third, *evaluability*: when one blob produces everything, a dimension regression
cannot be localized to a step, so the eval layer (SPEC §7) and the calibration
harness lose their leverage.

## Decision

Use an **orchestrator-workers** topology with **sectioning** for the independent
checks, plus an **evaluator-optimizer revision loop capped at two iterations**
(SPEC §6). The Orchestrator (Sonnet) plans and assembles; the Segment Analyst
(Haiku), Copy Composer (Sonnet), and Catalog Lookup (a deterministic tool) run as
delegated workers. The Claim Verifier (Haiku) and Brand Sentinel (Sonnet) run as a
parallel section over the assembled draft, each with its own prompt. Their
verdicts feed a bounded revision loop: at most two optimize passes before the
result is handed, whatever its state, to the deterministic autonomy controller.

## Alternatives considered

A **single mega-prompt** was rejected for the three reasons above. **Unbounded
revision** (loop until the judges are satisfied) was rejected because it converts a
quality mechanism into a cost and latency risk and can mask a genuinely bad brief
behind endless rewrites; two iterations is enough to fix the common
composition slip while keeping the run bounded and the trace readable. A **fully
sequential pipeline** (verifier then sentinel) was rejected because the two checks
are independent by construction — sectioning them in parallel is both faster and a
structural guarantee that neither check sees the other's reasoning.

## Consequences

Each step is separately promptable, separately cached as a fixture (SPEC §7), and
separately scored, so `make gate` can attribute a regression to a specific worker.
Model routing becomes a per-worker decision, which is exactly what the routing
report (SPEC §6) measures. The cost is more moving parts and more orchestration
code than a single call, and a hard ceiling of two revisions means some drafts
reach the controller still imperfect — which is correct: the controller, not the
loop, is what decides whether an imperfect draft is auto-sent or routed to a human.
