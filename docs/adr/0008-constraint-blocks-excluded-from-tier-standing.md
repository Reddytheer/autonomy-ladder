# 0008 — Constraint blocks are excluded from tier standing

## Context

The trust ledger originally recorded whether a run auto-sent or went to review, but
not *why* it did not auto-send. That conflated two very different situations. A run
can miss auto-send because the agent produced poor work (a dimension failed), or
because a rule blocked an otherwise-good campaign — the requested segment is
ineligible, the discount is over the ceiling, the 24h rate limit is spent, or the
audience is one that is never autonomous. Counting the second kind against the agent
would suppress promotion for campaigns that were never quality failures: an agent
repeatedly *asked* to send to an ineligible segment would be punished for the
requester's choices. Tier standing must reflect the agent's judgment, not the briefs
it happens to receive.

## Decision

Every run is classified into one of three outcomes, recorded on its ledger entry:

* **`quality_failure`** — a graded dimension failed (segment correctness, claim
  groundedness, or brand voice). Counts in the Wilson window as a failure.
* **`constraint_block`** — the run was clean but blocked by a rule that says nothing
  about readiness for autonomy: **discount over ceiling, rate limit, or a
  never-autonomous segment** (`engaged_180d`, `all_subscribers`,
  `first_time_buyers`, `lapsed`). **Excluded from the Wilson window entirely.**
* **`quality_pass`** — a clean run on an autonomy-eligible band. Counts as a success.

Two refinements are load-bearing:

1. **Quality precedence.** A run that is *both* a quality failure and a constraint
   block counts as a `quality_failure`. Quality judgment is never masked by a
   constraint.

2. **Tier-ineligible-but-clean counts as a success.** A clean run to an
   autonomy-eligible band (`engaged_30d/60d/90d`) that is merely *above the current
   tier* is a `quality_pass`, not a constraint block. The campaign was good and the
   audience is one autonomy could cover at a higher tier — exactly the evidence
   promotion should be built on ("would this have been safe if we had allowed it?").
   The exclusion keys on *why* a run was blocked, not on the fact that it was.

The classification is computed by a single pure function,
`autonomy_ladder.autonomy.routing.decide`, used by both the controller (for the live
ledger) and the keyless regression gate (replayed over the 75 golden cases, which it
reproduces exactly — decision and lane).

## Alternatives considered

**Exclude every constraint block, including tier-ineligibility, literally.** This is
the wording the change was handed down with, and it is unworkable as stated: at Tier
0 *every* clean run is tier-ineligible (Tier 0 can send to nothing), so nothing would
ever count as a success and 0→1 promotion would be impossible. The failure mode is
worth recording — it is why refinement (2) exists.

**Count a clean tier-ineligible run only if its band is reachable at the next tier
up.** More surgical, but it makes a run's Wilson eligibility depend on *when* it
happened relative to the current tier, so the same run counts or does not on replay.
That is harder to explain and harder to reason about against the append-only ledger.
Rejected in favour of the tier-independent rule in refinement (2): quality is judged
relative to the requested segment, and a clean 90-day campaign is competence evidence
regardless of current standing.

## Consequences

Tier standing now measures the agent's quality, not the mix of briefs it received.
Roughly a third of the golden REVIEW cases are constraint blocks, so promotion rates
shift and baselines were regenerated. The Wilson helpers filter constraint blocks;
`OutcomeClass` rides on every run entry and is surfaced in the console.

One known gaming surface (recorded in `docs/open-questions.md`): because a clean
90-day campaign counts toward 0→1, an operator could in principle accrue promotion
evidence entirely from 90-day sends without ever exercising the 30-day case the tier
actually governs. This is backstopped by the probation verification challenge, which
runs the golden subset for the campaign type and would exercise the real case.

Monitoring requirement **M1** (see `docs/autonomy-model.md`) attaches to the related
`GS-PD-16` decision — classifying brief-instructed evasion as a quality failure — and
is instrumented so that rule can be revisited empirically rather than left as a
permanent assumption.
