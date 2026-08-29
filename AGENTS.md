# AGENTS.md

Guidance for any agent (human or AI) working in this repository. This complements
`SPEC.md`, which is the locked implementation specification.

## Ground rules

1. **Build exactly what `SPEC.md` says.** Where the spec is silent, choose the
   simplest option and record it in [`docs/open-questions.md`](docs/open-questions.md).
   Do not invent domain constraints or thresholds — every number in the spec is
   sourced and deliberate.
2. **Clean-room.** No content, prompt, schema, example, or comment may reference
   insurance, Assurified, InsurOps, or any real company's internal systems. The
   reference domain is a fictional DTC outdoor-gear brand, "Northbay Supply".
3. **Scope discipline is a deliverable.** Do not add features, dependencies, or
   providers not in the spec (see `SPEC.md` §15, Out of scope).
4. **Commit incrementally with meaningful messages.** The commit history is part
   of the artifact.
5. **No secret ever enters the repository** (`SPEC.md` §11). `gitleaks` runs in
   pre-commit and CI.

## The two kinds of code here

This repo deliberately separates two categories with different rules:

| | The agent (generates work) | The controller (governs the agent) |
|---|---|---|
| Location | `src/autonomy_ladder/agents/` | `src/autonomy_ladder/autonomy/` |
| Made of | LLM calls | Deterministic Python, no LLM |
| Can it decide a tier? | **Never** (P1) | Yes — it is the only thing that can |
| Tested with | mocked/cached responses | pure unit tests, no API key |

**P1 (complete mediation):** no LLM output may set, raise, or influence an
autonomy tier. If you find a code path where it can, that is a bug, not a feature.

## Agent roster and model routing (locked — `SPEC.md` §6)

| Agent | Model | Role |
|---|---|---|
| Orchestrator | Sonnet 4.6 (`claude-sonnet-4-6`) | plans, delegates, assembles |
| Segment Analyst | Haiku 4.5 (`claude-haiku-4-5-20251001`) | resolves target segment |
| Copy Composer | Sonnet 4.6 | writes campaign copy |
| Catalog Lookup | — (tool) | grounds claims in the catalog |
| Claim Verifier | Haiku 4.5 | checks every claim against the catalog |
| Brand Sentinel | Sonnet 4.6 | independent brand-voice judge |
| Revision loop | Sonnet 4.6 | evaluator-optimizer, max 2 iterations |

**P2:** the checks that grade generated output (Claim Verifier, Brand Sentinel)
are independent calls with their own prompts and no access to the generator's
reasoning. A generator grading its own work is the weakest possible check.

## Working without an API key

Steps 1–5 of the build sequence (`SPEC.md` §13) require no API key. `make setup`,
`make test`, `make eval`, and `make gate` all run against deterministic logic and
committed fixtures. Only `make demo` and `make fixtures` make live calls.
