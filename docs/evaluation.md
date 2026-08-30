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
corrects for chance agreement. We compute per-dimension κ against human-labeled
cases (`evals/calibration/human_labels.jsonl`). If κ < 0.6 for a dimension, the
judge prompt needs revision — and we say so here rather than hiding it.

Current κ on the committed **seed** label set:

| dimension | n | κ | agreement | calibrated? |
|---|---|---|---|---|
| segment_correctness | 10 | 0.615 | 0.90 | yes |
| claim_groundedness | 10 | 0.615 | 0.90 | yes |
| brand_voice | 10 | 0.545 | 0.80 | **NO (< 0.6)** |
| structure_quality | 10 | 0.545 | 0.80 | **NO (< 0.6)** |

Two dimensions are below the 0.6 bar on the seed set — the honest reading is that
`brand_voice` and `structure_quality` judges need prompt revision (or more labels)
before they can be trusted. These numbers are from the seeded label rows; run
`autonomy-ladder calibrate` to reproduce, and recompute against live judge output
once fixtures are recorded (`make fixtures`). The calibration set grows to ~40
labeled cases as authored.

## Golden set and adversarial suite

- `evals/goldens/*.jsonl` — versioned cases split `easy` / `ambiguous` /
  `adversarial`, each with a campaign artifact and authored per-dimension
  expectations. Seeded with 10; grows to 60–80.
- `evals/adversarial/*.jsonl` — kept separate. Covers prompt injection via
  customer-data fields, unsupported-claim bait, brand-rule traps, and
  segment-boundary manipulation.

## The regression gate (`evals/gate.py`)

`make eval` runs the golden set and prints a per-dimension table. `make gate` does
the same and **exits non-zero** if any dimension's verdict accuracy has regressed
beyond tolerance (default 0.05) versus `evals/baseline.json`. It is wired into CI
(`.github/workflows/eval-gate.yml`). `tests/test_gate.py` proves it passes at
baseline, **fails on a degraded prompt**, and fails loudly if fixtures go missing.

## Fixture caching — why `make eval` needs no API key

Judge calls are cached by a hash of `(model, system, user)` under
`evals/fixtures/`, committed to the repo. In replay mode (the default) a cache miss
is an *error*, never a silent live call — so a reviewer runs `make eval` / `make
gate` and sees reproducible results with **no API key**. This is a deliberate
usability decision. `make fixtures` (with `ANTHROPIC_API_KEY`) records real
responses; the committed fixtures are synthetic placeholders until then, which is
why the κ table above should be regenerated after a real recording.
