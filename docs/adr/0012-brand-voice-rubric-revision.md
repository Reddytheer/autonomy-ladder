# 0012 — Brand-voice rubric revision (one pass), and why the dimension needs decomposition

## Context

The first faithful-render measurement (ADR 0011, `make fixtures`) put `brand_voice`
at **Cohen's κ = 0.166** against the 46 human labels — near chance — with **recall
0.43** on planted brand flaws. The other judges were fine (`claim_groundedness`
κ 0.733, `segment_correctness` κ 0.897, directional). κ < 0.6 is this project's
stated "the judge prompt needs revision" threshold, so the brand judge was the one
thing the pipeline flagged as its own weakness.

The original rubric described the voice abstractly ("judge whether the copy matches
the brand voice", plus the voice guidelines and the prohibited-term list). In
practice it caught only what the deterministic prohibited-term check already caught;
everything requiring judgment — hype, superlatives, manufactured scarcity, shouting,
coercion, a sale with no end date — slipped past. The judge also had no positive
reference for what compliant copy looks like, and no visibility into stock, so the
"scarcity unsupported by stock" distinction (GS-RS-06 "limited", stock 12, **pass**
vs GS-RS-07 "limited", stock 1200, **fail**) was unjudgeable.

## Decision

**One** rubric revision, then whatever the number is — no iterating toward a target
(iterating to a target is the defect-hiding rejected elsewhere in this project).

The revision:

* **Enumerates the violation types** instead of describing the voice abstractly:
  hype adjectives, absolute/hyperbolic superlatives (while allowing bounded
  spec-grounded comparatives like "our toughest pack"), manufactured scarcity
  unsupported by stock, exclamatory register (ALL-CAPS / `!`), second-person
  pressure or coercion, and a discount with no stated end date.
* **Adds compliant examples** — a rubric that only lists violations gives the judge
  no reference for passing; three short understated exemplars are included.
* **Includes the referenced products' stock**, so "scarcity unsupported by stock" is
  actually enforceable (the same grounding-input principle as the claim-judge stock
  fix in ADR 0010).
* **Excludes non-brand concerns explicitly**: exclusivity/VIP framing is an audience
  (segment) concern, not brand; a warm "we miss you" tone is fine; true low stock
  stated plainly is fine.

## Result (before → after)

| | before | after |
|---|---|---|
| `brand_voice` κ | 0.166 | **0.414** |
| `brand_voice` recall (faithful) | 0.43 | **0.857** |
| judge recall (overall) | 0.750 | **0.917** |
| system harmful-escape (upper bound) | ≤0.212 | **≤0.061** |
| judge accuracy (overall) | 0.911 | 0.911 |

Recall nearly doubled and harmful escape fell sharply — harmful escape tracks
**recall**, not κ. But **κ = 0.414 is still below 0.6**: the revision is reported as
still failing the bar. No second revision.

## Why κ stalls below 0.6: the dimension is heterogeneous

`brand_voice` conflates two judgments that cannot share one calibration:

* **Deterministic hard rules** — prohibited terms, a required sale end date. These
  are mechanically checkable and would score κ ≈ 1 on their own.
* **Subjective tone** — is this hype? is this manufactured urgency? is a mild "our
  toughest pack" acceptable while "best pack ever made by anyone" is not? This turns
  on context and degree, and the goldens themselves draw fine lines here (scarcity
  is a brand fault at ample stock but a *claim* fault at zero stock — GS-RS-07 vs
  GS-RS-10).

A single κ over the union averages a near-perfect checker with a genuinely hard
classifier, landing in the middle no matter how good the rubric is. That is a
structural property of the dimension, not a wording defect — which is why one
principled revision improves recall a lot but cannot pull κ over the bar.

## Recommended fix (deferred): decompose

Split into two dimensions:

* `brand_rules` — deterministic (prohibited terms, missing end date, disclaimers),
  moved into the Stage-1 checks; target κ ≈ 1.
* `brand_tone` — the subjective residue (hype, superlatives, scarcity, register,
  pressure), labeled and calibrated on its own, where a low κ is a true signal about
  *that* judgment instead of being diluted.

**Deferred**, because it requires its own human labeling (the 46 labels are for the
combined dimension; re-labeling was explicitly out of scope). Tracked as
[OQ-9](../open-questions.md). Until then `brand_voice` stays one dimension and its κ
is reported honestly as failing — the diagnosis is the deliverable, not a good number.
