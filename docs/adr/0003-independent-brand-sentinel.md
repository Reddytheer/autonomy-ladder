# 0003 — Checks that grade output are independent agents

Status: accepted. Implements SPEC §2 P2.

## Context

Once a draft campaign exists, something has to judge whether its claims are
grounded in the catalog and whether it sounds like Northbay Supply. The cheapest
option is to ask the generator to grade itself — "rate your own copy for brand
voice." This is the single weakest check we could build. A model that just wrote a
sentence is the worst-positioned to notice it is unsupported or off-brand: it has
already committed to the reasoning that produced it, and self-scoring rewards
confident output rather than correct output. Self-grading also collapses the
independence that any real evaluation needs — the grader and the graded share a
context, so a flaw in the generation reasoning silently becomes a flaw in the
grade.

## Decision

Checks that grade generated output are **separate agents from the generator, with
their own prompts and no access to the generator's reasoning** (SPEC §6 diagram).
The **Claim Verifier** (Haiku) and the **Brand Sentinel** (Sonnet) are distinct
calls that see only the finished draft plus their own ground truth — the catalog
for claims, the brand rules for voice — not the Copy Composer's chain of thought.
They run as a parallel section (ADR 0001), so neither even sees the other's
verdict. Their structured outputs map to the `claim_groundedness` and `brand_voice`
dimensions that the controller consumes; because these judges are independent, a
`RunEvaluation` is an outside assessment of the draft, not the draft's own opinion
of itself.

## Alternatives considered

**Generator self-grading** was rejected as the weakest possible check, for the
reasons above. A **single combined judge** scoring all dimensions at once was
rejected because it blurs the CRITICAL/WEIGHTED distinction (ADR 0005) and makes
per-dimension calibration (Cohen's κ, SPEC §7) impossible — you cannot revise "the
claim judge" if claims and voice share one prompt. **Deterministic-only checking**
was rejected as insufficient for grounding and voice, which need semantic
judgement; deterministic checks still run *first* (SPEC P3) to filter obvious
failures before these judges are invoked.

## Consequences

Grading is genuinely adversarial to the generator, which is what makes a pass
meaningful. Each judge is separately prompt-tunable and separately measured against
human labels, so a low κ points at exactly one prompt to fix. The cost is extra
LLM calls per run and the modeling discipline of never leaking generator context
into a judge — a boundary the sectioned architecture enforces rather than trusts.
