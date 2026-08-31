# 0010 — Comparative claims require same-type sibling context

## Context

The Claim Verifier (`evals/judges.py`, `_claim_prompt`) grounds a campaign's
claims against catalog facts. It originally built those facts from only the
products the campaign *itself* referenced (`content.product_ids`). That is enough
for most claims — a numeric spec, an attribute transfer are checkable from the
subject product alone.

It is **not** enough for a comparative or superlative claim. "The brightest
headlamp we have made" or "the longest-lasting insulation we have made" is a
statement *about the peer set*, not about the subject in isolation. Shown only the
subject, the judge has no data to falsify the claim: it must either wave every
comparative through (a false pass on false ones) or reject them on principle (a
false fail on true ones). Either way the verdict is uninformed.

This was not found by inspection. The golden pair **GS-PL-07** ("brightest
headlamp", authored **pass**) and **GS-PL-08** ("longest-lasting", authored
**fail**) is identical in shape — a single-product superlative launch — and
differs only in whether a catalog peer beats the subject on the compared
attribute. A judge that cannot see peers scores the two the *same*, so the pair
cannot both reproduce. The paired golden exposed the gap; a single case would have
looked fine.

## Decision

The claim-grounding context includes **same-category, same-type siblings** of the
referenced products, rendered in a labeled "comparison set" block, and the rubric
scopes a comparative claim to the product *type* named in the claim.

**Type scoping, not category scoping.** The first implementation showed the whole
*category*. A smoke run then false-failed GS-PL-07: the `lighting` category
contains 1000-lumen "Trail Light" and "Area Light" products, and the Haiku claim
verifier treated a "Trail Light" as a headlamp, concluding the 400-lumen Beacon
was not the brightest. Same-category is too coarse — it feeds cross-type
distractors. The comparison set is therefore restricted to peers whose product
*type* (a keyword derived from the product name — "headlamp", "insulated bottle",
etc.) matches the subject's. "Brightest headlamp" now compares only against other
headlamps. Correcting the scope is the fix; editing the catalog until the
distractors disappeared was rejected (see Alternatives).

The sibling set is **bounded**, for two reasons that pull the same direction:

* **Prompt cost.** An unbounded peer list is paid on *every* claim check, most of
  which are not comparative.
* **Distractor risk.** Irrelevant peers are noise that can degrade judge accuracy —
  the concrete failure above.

Siblings are capped (`_SIBLING_CAP = 5`) and ranked most-relevant first: by the
number of numeric attributes shared with the subject, then peers that *exceed* the
subject on a shared attribute (the ones that can falsify a superlative), then id
for determinism. Ranking falsifiers ahead of the cap is deliberate — they are the
peers whose omission would change a verdict.

## A second grounding gap, found by the same smoke test: stock visibility

The rubric has long stated "ANY mention of an out-of-stock product (stock 0)
fails" — but `_claim_prompt` rendered only each product's `attributes`, never its
`stock`. The rule was therefore **unenforceable**: the judge could not see stock,
so an out-of-stock claim (GS-RS-02: "Ridgeline tent is back in stock", stock 0)
could not be caught. The same smoke run surfaced this. Each referenced product's
`stock` is now included in the claim facts block. This is a grounding-*input* fix
in the same spirit as the sibling block: give the judge the data its own rubric
requires. GS-RS-02 (and the quantity case GS-RS-09, "only 2 left" against stock
1200) now reproduce.

## Consequences

* **Two classes of false claim become catchable** — superlatives/comparatives
  (via siblings) and out-of-stock/quantity mentions (via stock), each of which was
  previously unverifiable from the prompt the judge received.
* **The tradeoff is explicit and bounded.** Each claim check carries up to five
  extra same-type product lines plus one stock figure per product. Type scoping
  keeps the peer list short and on-topic.
* **Truncation is logged, not silent.** If the cap drops a peer that would falsify
  a claim, that is a potential false pass; `_sibling_block` logs a warning naming
  the dropped falsifiers. (No current type has more than five same-type peers, so
  it does not fire; the guard is for future catalog growth.)
* **GS-PL-07 / GS-PL-08 / GS-RS-02 are the regression tests** — they pin both
  directions of the comparative behavior and the stock behavior, guarding against a
  future change that reintroduces subject-only, stock-blind grounding.

## Alternatives considered

* **Whole-category context.** The first cut. Rejected after it false-failed
  GS-PL-07 — a category conflates product types, and the judge cannot be relied on
  to re-separate them from names alone.
* **Edit the catalog so no distractor outshines the subject** (lower the Firefly
  lights). Rejected: it hides a defect a real catalog would reproduce — ambiguous
  product families are normal — rather than making the grader robust to it.
* **Resolver returns the peers as extra `product_ids`.** Rejected: that would also
  feed peers to the Copy Composer, which would then write about products the brief
  never named. Judge grounding context is a separate concern from the campaign's
  subject.
