# Evaluation

Every campaign run is scored on four dimensions, in two stages, cheapest first.
The result is a `RunEvaluation` the controller consumes; the same machinery powers
the regression gate.

## Dimensions and their classes

| Dimension | Class | Behavior on failure |
|---|---|---|
| `segment_correctness` | **CRITICAL** | blocks action + immediate demotion + probation |
| `claim_groundedness` | **CRITICAL** | blocks action + immediate demotion + probation |
| `brand_voice` | **WEIGHTED** | contributes to pass/fail (floor 0.75); repeated failures hurt standing via the Wilson rate |
| `structure_quality` | **ADVISORY** | recorded, never blocks, never affects tier |

**Rationale.** Segment errors send the wrong content to the wrong people;
unsupported claims create false-advertising exposure. Both cause real-world harm,
so they are CRITICAL and a single failure demotes. Off-brand voice is a quality
problem, not a safety one — it is WEIGHTED. Structure is deliberately ADVISORY:
marketing is an experimental discipline, and a rigid structural check would
suppress legitimate variation. A run **passes** iff both CRITICAL dimensions pass
AND `brand_voice ≥ 0.75`. See [ADR 0005](adr/0005-critical-vs-weighted-dimensions.md).

## Stage 1 — deterministic (`evals/deterministic.py`)

Millisecond checks, no LLM (P3): schema validity, required fields, segment
existence and tier eligibility, discount ceiling, prohibited-term regex, link
validity, and unknown-product references. These run *before* the judges so obvious
failures never spend a judge call. In the pipeline, a Stage-1 finding
deterministically drags the relevant dimension to a fail — a clean judge score can
never paper over a hard defect.

## Stage 2 — LLM judges (`evals/judges.py`)

One independent judge per dimension (P2 — separate calls, own rubrics, no access to
the generator's reasoning). Each returns structured JSON:
`{score: float 0-1, verdict: pass|fail, reasoning: str, evidence: list[str]}`.
Model routing follows SPEC §6 — Claim Verifier on Haiku, the rest on Sonnet — so
the [routing report](../src/autonomy_ladder/observability/cost.py) can show the
cost delta of the Haiku assignments.

## Judge calibration — Cohen's κ (`evals/calibration.py`)

Raw accuracy flatters a judge that mostly guesses the majority label. Cohen's κ
corrects for chance agreement. We compute per-dimension κ between judge verdicts and
the 46 human-labeled cases in `evals/calibration/calibration_labels.jsonl`. If κ <
0.6 for a dimension, the judge prompt needs revision — and we say so here rather than
hiding it.

**Provenance (stated plainly).** Reference labels were authored by the maintainer,
drafted and then reviewed case by case. Cases where the reviewer's verdict differed
from the draft were removed from the golden set rather than retained, on the basis
that a case two careful reviewers score differently is a rubric gap rather than
ground truth. This is *reviewed labeling*, which is normal practice and defensible —
it is **not** independent human labeling, and is not described as such. The
`provenance` field on every row records this and is not stripped.

**κ is computed in faithful-render mode.** Computing κ needs judge verdicts on
content that actually contains the flaw. In the normal pipeline the Copy Composer
grounds strictly and often *corrects* a flawed brief, so the judge never sees the
flaw — measuring κ there would score the composer, not the judge (see
[three metrics](#three-metrics-composer-vs-judge-vs-system) and
[ADR 0011](adr/0011-separating-composer-faithfulness-from-judge-recall.md)). `make
fixtures` therefore records judge verdicts in **faithful-render mode** (the composer
emits the brief's claims verbatim) and computes κ against the 46 human labels. The
numbers live in `evals/metrics.json`; recompute with `make fixtures`, inspect
keylessly with `make judge-gate`.

Per-dimension results (κ vs the 46 human labels, faithful-render judge verdicts):

| dimension | labels | failure labels | κ | note |
|---|---|---|---|---|
| claim_groundedness | 46 | 17 | **0.733** | stable sample; above the 0.6 bar |
| segment_correctness | 46 | 5 | 0.897 | high, but **directional only** (5 failures) |
| brand_voice | 46 | 7 | **0.414** | revised once (0.166 → 0.414); **still below 0.6** |

**`brand_voice` was revised once and still fails the bar — and we say so.** The
original rubric described the voice abstractly and caught only the deterministic
prohibited terms; κ was **0.166** (near chance) with recall 0.43 on planted brand
flaws. The rubric was rewritten **once** to enumerate the violation types (hype
adjectives, absolute superlatives, manufactured scarcity unsupported by stock,
exclamatory register, second-person pressure, missing end date) with compliant
examples, and given the referenced products' stock so the scarcity rule is
enforceable. That lifted **recall 0.43 → 0.857** and cut harmful escape from ≤0.212
to ≤0.061 — but κ only reached **0.414**, still under 0.6. Per the no-iteration rule
this is left as-is: one principled revision, then the honest number. The reason it
can't be cleanly calibrated is structural, not a prompt-wording problem — see the
diagnosis below and [ADR 0012](adr/0012-brand-voice-rubric-revision.md).

**Why one rubric can't fix it: `brand_voice` is heterogeneous.** It conflates two
kinds of judgment that calibrate differently — **deterministic hard rules**
(prohibited terms, a required sale end date: mechanically checkable, κ ≈ 1) and a
**subjective tone judgment** (is this hype? is this manufactured urgency? is a mild
"our toughest pack" acceptable while "best ever" is not?) that turns on context and
degree. A single κ over the union averages a near-perfect checker with a genuinely
hard classifier, so it lands in the middle regardless of rubric quality. The
recommended fix is **decomposition** into `brand_rules` (deterministic, into Stage 1)
and `brand_tone` (the subjective residue, calibrated on its own labels) — identified
and **deferred** because it needs its own human labeling, which was out of scope.
Recorded as [OQ-9](open-questions.md) and [ADR 0012](adr/0012-brand-voice-rubric-revision.md).

**Sample-size caveat (required).** `segment_correctness` has only 5 failure cases,
so its κ = 0.897 is *directional, not precise* — one judge disagreement moves it
materially. It must be read with this caveat and not presented alongside
`claim_groundedness` (17 failures) as equally solid.

## Golden set and security suite

- `evals/goldens/goldens.jsonl` — **75** versioned, brief-based end-to-end cases
  split `easy` (29) / `ambiguous` (28) / `adversarial` (18), each with authored
  per-dimension verdicts, an expected controller decision, and an expected review
  lane. Several **paired** cases are load-bearing and guarded by
  `tests/test_goldens.py`: `GS-PD-08`/`09` (data decides scarcity, not wording),
  `GS-PL-07`/`08` (comparative claim true vs false), `GS-WB-06`/`07` (win-back
  audience recency), and `GS-NL-06`/`07` (byte-identical content, Tier 1 vs Tier 2 —
  the thesis of the project).
- `evals/adversarial/security.jsonl` — 8 security cases, each with an
  `expected_security_event`: prompt injection via customer/catalog/brand-rules
  fields, tier-escalation, constraint-evasion, segment-redefinition, eval-gaming,
  and rate-limit-evasion. See [security](#security).

## The regression gate (`evals/gate.py`)

The gate has two layers:

1. **Decision-routing (keyless, always on).** `make eval` replays every golden's
   authored verdicts through the pure controller routing and checks the resulting
   decision *and* lane match what the case authored. The controller reproduces all
   75 (`decision_accuracy` and `lane_accuracy` both 1.000), which validates the
   constraint-block and lane logic — including the `constraint_block` change (ADR
   0008) — with no API key. `make gate` **exits non-zero** if routing accuracy
   regresses beyond tolerance versus `evals/baseline.json` (wired into CI via
   `.github/workflows/eval-gate.yml`). `tests/test_gate.py` proves it passes at
   baseline and flags a regression.
2. **Judge accuracy (keyless replay, `make judge-gate`).** `make fixtures` (with a
   key) records real judge verdicts; `make judge-gate` then replays them with **no
   key** and recomputes judge accuracy, failing if it regresses beyond tolerance
   versus `evals/judge_baseline.json`. Judge accuracy is **0.911** (faithful mode):
   `segment_correctness` 0.987, `claim_groundedness` 0.880, `brand_voice` 0.867.

## Brief → product resolution (`evals/resolver.py`)

The goldens are prose ("the Cascade pack", "20% off headlamps"), not SKUs — turning
that into catalog `product_ids` is part of the job, so it lives in deterministic code,
not a judge. A resolver mistake would surface as a spurious claim-verification
failure and corrupt the numbers, so every golden carries an authored
`expected_products` list and `tests/test_resolver.py` asserts the resolver reproduces
all 75. Comparison clauses ("waterproof … *like our jackets*") are excluded so a
reference product is not mistaken for the campaign's subject.

## Three metrics: composer vs judge vs system

Measuring "judge accuracy" over Copy-Composer output conflates two independent
controls: the composer grounds strictly and often *corrects* a flawed brief, so a
judge cannot catch a flaw the composer already removed. The fix was found by a
six-case smoke run before the full recording spend, and is why the pipeline reports
**three separate numbers** in two composer modes
([ADR 0011](adr/0011-separating-composer-faithfulness-from-judge-recall.md)). Current
values (`evals/metrics.json`, 33 fail-goldens of 75):

| metric | mode | value | reads as |
|---|---|---|---|
| **composer correction rate** | normal | **0.424** | the composer corrects/refuses ~42% of flawed briefs at generation — the first line of defense |
| **judge recall** | faithful-render | **0.917** | given flawed content, the evaluation catches ~92% of flaws (claim 0.94, segment 1.0, brand 0.86) — this is what κ measures |
| **system escape rate** | normal + routing | **0.606** auto-sent; **≤0.061** harmful | fail-goldens that end in auto-send; most are safe because the composer sanitized them. The harmful figure (auto-send **and** a judge-recall miss) is an **upper bound** — verdicts alone can't prove the flaw survived. It fell from ≤0.212 to ≤0.061 after the brand-rubric revision (ADR 0012), because harmful escape tracks recall, not κ |

> **The `system_escape_rate` is a judge-stress number, not a production escape rate.**
> It is measured on the golden set, which is adversarial *by construction* (33 of 75
> briefs are authored flaws). It must **not** be compared to the **1.70%** tolerance in
> [docs/economics.md](economics.md#the-metric-the-framework-does-not-yet-measure),
> which assumes a ~10% production harm base rate — the two are computed over entirely
> different brief populations. A real production escape rate can only come from the
> post-send loop on real traffic.

**Faithful-render is eval-only.** It makes the composer emit known-false content to
test the judges; it is reachable only via `Orchestrator.run_eval`, the production
`run()` has no `faithful` parameter, and `tests/test_faithful_render_eval_only.py`
asserts no production module can reach it.

## Fixture caching — why the keyless gate needs no API key

Judge and composer calls are cached by a hash of `(model, system, user)` under
`evals/fixtures/`. In replay mode a cache miss is an *error*, never a silent live
call. The keyless decision-routing gate needs no fixtures (it replays authored
verdicts); `make judge-gate` replays recorded fixtures. `make fixtures` (with
`ANTHROPIC_API_KEY`) runs the briefs through the pipeline in both modes and records
real responses for the three metrics and κ. No synthetic placeholder fixtures are
committed.

## Security

Resisted attacks are events, not silent passes (`src/autonomy_ladder/security.py`).
A signature scanner over brief/injected text emits typed `SecurityEvent`s that are
persisted even when the run otherwise succeeds, and the console surfaces a count and
log. The real defences are architectural: `SEC-03` (a brief claiming "Tier 2
approved") has provably zero effect because no LLM-visible input carries a tier
(P1), and `SEC-07` (self-asserted compliance) is not treated as evidence. Both are
first-class tests in `tests/test_security.py`.
