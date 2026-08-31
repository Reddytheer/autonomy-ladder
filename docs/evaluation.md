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

**κ is pending a recording.** Computing κ needs judge verdicts, which come from
running the briefs through the live pipeline (`make fixtures`, step 11). The keyless
build therefore reports the *label distribution* and readiness, not fabricated κ
numbers. Run `autonomy-ladder calibrate` to see it; recompute κ after step 11.

Per-dimension failure labels (which bound how precise a κ can be):

| dimension | labels | failure labels | note |
|---|---|---|---|
| claim_groundedness | 46 | 17 | ample |
| brand_voice | 46 | 7 | usable |
| segment_correctness | 46 | 5 | **directional only** |

**Sample-size caveat (required).** `segment_correctness` has only 5 failure cases,
so a κ on that dimension is *directional, not precise* — one judge disagreement moves
it materially. It must be reported with this caveat and not presented alongside the
other dimensions as if equally solid. Two judge rubrics (`claim_groundedness`,
`segment_correctness`) were already revised per the review overrides below and will
be re-measured after step 11.

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
2. **Judge accuracy (needs a key, step 11).** Whether the *judges* reproduce the
   authored verdicts from generated content is measured by running the briefs
   through the live pipeline (`make fixtures`) and comparing. This is deferred to the
   live-key phase; see below.

## Fixture caching — why the keyless gate needs no API key

Judge calls are cached by a hash of `(model, system, user)` under `evals/fixtures/`.
In replay mode a cache miss is an *error*, never a silent live call. The keyless
decision-routing gate above needs no judge fixtures at all (it replays authored
verdicts). `make fixtures` (with `ANTHROPIC_API_KEY`) runs the briefs through the
pipeline and records real judge responses for the judge-accuracy measurement and the
κ recomputation — steps 11–12. No synthetic placeholder fixtures are committed.

## Security

Resisted attacks are events, not silent passes (`src/autonomy_ladder/security.py`).
A signature scanner over brief/injected text emits typed `SecurityEvent`s that are
persisted even when the run otherwise succeeds, and the console surfaces a count and
log. The real defences are architectural: `SEC-03` (a brief claiming "Tier 2
approved") has provably zero effect because no LLM-visible input carries a tier
(P1), and `SEC-07` (self-asserted compliance) is not treated as evidence. Both are
first-class tests in `tests/test_security.py`.
