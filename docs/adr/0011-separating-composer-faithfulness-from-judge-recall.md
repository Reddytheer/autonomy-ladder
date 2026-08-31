# 0011 — Separating composer faithfulness from judge recall

## Context

We set out to measure "judge accuracy": run the golden briefs through the live
pipeline and check whether the judges reproduce the authored per-dimension
verdicts (step 11). A smoke run over six goldens showed that number would have
meant almost nothing, and would have been *low for the wrong reason*.

The Copy Composer is instructed to ground strictly in the catalog and "never
invent product facts." So when a brief asks for a false claim, the composer
*fixes it at generation time*:

* **GS-PL-09** ("Launch the Beacon headlamp at 500 lumens") — the composer wrote
  "400 lumens" and appended a note that the brief's 500 was corrected to the
  catalog spec. The generated copy contains no false claim, so the claim judge —
  correctly — passes it. But the golden authored **fail**.
* **GS-PD-14** ("Lead on it being fully waterproof") — the composer clarified
  water-resistance instead of claiming waterproof. Same outcome.

A judge cannot catch a flaw the composer already removed. Measuring "judge
accuracy" over composer output therefore measures **two independent controls at
once** — how faithfully the composer renders a flawed brief, and how well the
judges catch a flaw — and reports their product as if it were a property of the
judges alone. The composer's self-correction is a *feature* (it prevents bad
sends), but it silently deflates any judge metric computed downstream of it.

This conflation was found by the smoke test, not by review — which is the whole
argument for running a cheap smoke pass before spending the full fixtures budget.

## Decision

Measure three separate numbers instead of one, in two composer modes.

1. **Composer faithfulness rate** — over the fail-goldens, in **normal** mode: how
   often the composer corrects or refuses a flawed brief rather than emitting it.
   This is a real, valuable metric — it quantifies the first line of defense.

2. **Judge recall** — over the fail-goldens, in **faithful-render** mode (the
   composer emits the brief's claims verbatim, so the content is guaranteed
   flawed): how often the evaluation catches the flaw. **This is what κ and the
   judge-accuracy gate are computed on** — it isolates judge quality from composer
   behavior.

3. **System escape rate** — over the fail-goldens, in **normal** mode, end to end:
   how often a flawed brief results in a *harmful campaign that is auto-sent*
   (composer failed to correct **and** the controller cleared it for autonomous
   send). This is the composite that actually matters economically; it is
   explicitly a system property, not a judge property, and is reported as such.

The composite (1)×(2)-style single number is misleading and is not reported as
"judge accuracy."

## Faithful-render is eval-only

Faithful-render makes the composer emit known-false content on purpose. That is a
liability if it can run in production. It is therefore:

* off by default (`compose(..., faithful=False)`);
* absent from the production entry point entirely — `Orchestrator.run` has no
  `faithful` parameter; the flag is reachable only through the eval-only
  `Orchestrator.run_eval`;
* guarded by `tests/test_faithful_render_eval_only.py`, which asserts the
  production `run` signature has no `faithful` parameter and that no
  production-serving module (`service.py`, `cli.py`, `api/*`) references
  `run_eval` or `faithful`.

## Consequences

* Judge recall and κ now measure the judges, not the composer-plus-judges. A judge
  regression is visible; composer self-correction no longer masks or fakes it.
* The composer-faithfulness rate becomes a first-class number: if the composer
  stopped correcting flawed briefs, that shows up here rather than silently
  raising "escapes."
* The escape rate is the one to watch operationally — it is the flawed-brief →
  auto-send path, the actual economic exposure.
* `docs/evaluation.md` reports all three with an explicit note that judge quality
  measured over composer output conflates two controls, and that the smoke test is
  what surfaced the conflation.

## Alternatives considered

* **One end-to-end accuracy number.** Rejected: it conflates the two controls and
  would have been reported as a judge metric while largely reflecting composer
  self-correction.
* **Feed judges canonical flawed content instead of a faithful-render mode.**
  Rejected: no authored per-golden content exists, and hand-writing flawed copy
  per case is both more work and less representative than what the composer
  actually emits under a faithful instruction.
