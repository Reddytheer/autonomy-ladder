# 0005 — CRITICAL vs WEIGHTED vs ADVISORY dimensions

Status: accepted. Implements SPEC §3.

## Context

Every run is scored on four quality dimensions, but they do not carry equal
consequence, and treating them uniformly would be wrong in both directions.
Averaging everything into one number lets a great structure score paper over an
unsupported claim; blocking on everything makes a stylistic quibble as fatal as a
false advertisement. The dimensions differ in the *kind of harm* a failure causes,
and the taxonomy has to encode that difference so the controller can act on it
deterministically.

## Decision

Classify each dimension by failure behavior (`domain.py`, `DIMENSION_CLASS`):

- **CRITICAL** — `segment_correctness` and `claim_groundedness`. A failure blocks
  the send, demotes the campaign type to Tier 0 immediately, and opens probation.
  These map to real-world harm: a segment error sends the wrong content to the
  wrong people; an unsupported claim is false-advertising exposure.
- **WEIGHTED** — `brand_voice`. It does not hard-block, but it contributes to the
  pass/fail decision and repeated failures erode tier standing. A run passes iff
  both CRITICAL dimensions pass **and** `brand_voice ≥ 0.75`
  (`RunEvaluation.passed`; `BRAND_VOICE_PASS_THRESHOLD`).
- **ADVISORY** — `structure_quality`. Recorded for observability, but it **never
  blocks and never affects tier**. It is deliberately advisory because marketing is
  an experimental discipline: a rigid structural check would suppress legitimate
  variation — a punchy three-word subject line or an unconventional layout that
  outperforms the "correct" template.

Because there is one definition of `passed`, Wilson statistics, the review-queue
lane split, and the promotion gate all agree on what a "successful run" is — they
all call the same property.

## Alternatives considered

A **single weighted average** across all four dimensions was rejected: it lets a
strong score on a cheap dimension mask a critical failure, which is exactly the
error a false-advertising claim represents. **Making structure blocking** was
rejected because it penalizes creative variation and would train the agent toward
bland, template-safe output that the ADVISORY class is meant to leave room for.
**Making brand_voice CRITICAL** was rejected as disproportionate — off-brand copy
is a quality problem to be improved, not a harm to be blocked and demoted over — so
it is WEIGHTED with a 0.75 pass floor instead.

## Consequences

The controller's response scales with real consequence: harms block and demote,
quality shortfalls route to review, and stylistic signal is captured without ever
constraining the agent. `structure_quality` still earns its keep as an
observability signal even though it is inert to autonomy. The main obligation this
creates is calibration discipline: because the CRITICAL judges can single-handedly
block and demote, their agreement with human labels (Cohen's κ, SPEC §7) matters
more than any other dimension's, and a weak κ there is a release blocker.
