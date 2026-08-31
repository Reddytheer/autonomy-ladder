# Open questions & spec deviations

This file records (a) places where `SPEC.md` was silent and the simplest option
was chosen, and (b) deviations from the spec that were explicitly approved by the
owner. Per `SPEC.md` §0, thresholds are never changed silently — anything here
that touches a number was either owner-approved or is a documentation-only note.

---

## Resolved — owner-approved

### OQ-1. Wilson 0→1 example was inconsistent with the 0.85 threshold
**Status: resolved (owner-approved, keep threshold).**

SPEC §4 locks the 0→1 promotion bar at *Wilson lower bound > 0.85*, but SPEC §4
prose and the §14 acceptance checklist both say "47/50 does promote." Those are
inconsistent: with 95% confidence, 47/50 → **0.8378**, which is *below* 0.85. The
true boundary in a 50-run window is **48/50 → 0.8654**.

Resolution: **keep 0.85** (the sourced threshold wins, per §0). The "47/50"
figure was an arithmetically loose illustration; the canonical example is now
**48/50 promotes, 47/50 does not.** The acceptance tests
(`tests/test_promotion.py`) prove the *mechanism* §14 intends — a large
near-perfect sample promotes, a tiny perfect one does not — using the real
boundary.

Wilson lower bounds for reference (z=1.96):

| runs | lower bound | vs 0.85 |
|---|---|---|
| 10/10 | 0.7225 | fail |
| 20/20 | 0.8389 | fail |
| 25/25 | 0.8668 | pass |
| 47/50 | 0.8378 | fail |
| 48/50 | 0.8654 | pass |

### OQ-2. Minimum-runs floor for 0→1 was non-binding
**Status: resolved (owner-approved change, 20 → 25).**

SPEC §4 sets `min_runs = 20` for the 0→1 transition. But that floor is never the
binding constraint: at n=20, even a perfect 20/20 gives a Wilson lower bound of
0.8389 < 0.85, so the statistical threshold already rejects everything the
min-runs floor would. A non-binding floor is dead configuration.

Resolution (owner-directed): raise the 0→1 `min_runs` from **20 to 25**. At n=25
a perfect record gives 25/25 → 0.8668 > 0.85, so the floor now marks the earliest
point at which promotion is even arithmetically possible. `tests/test_promotion.py`
asserts that 20/20 does **not** promote, documenting that the gate is statistical,
not a streak counter. The 1→2 floor (min_runs=50, threshold 0.92) is left as
specified; it is binding (50/50 → 0.9286 > 0.92).

---

## Choices made where the spec was silent

### OQ-3. Model list prices for the routing report
SPEC §6 asks for a routing report showing the cost delta of the Haiku
assignments, but does not give prices. The cost table in
`src/autonomy_ladder/observability/cost.py` uses USD-per-million-token list
prices (Haiku 4.5: 1.00 in / 5.00 out; Sonnet 4.6: 3.00 in / 15.00 out) as a
single editable constant. These are operational inputs, not spec thresholds; the
report's value is the methodology and the delta, which hold regardless of the
exact numbers. Update the table if prices change.

### OQ-4. "Low confidence" threshold for the judgment lane
SPEC §5 routes "low confidence" items to the judgment lane but does not define
the cutoff. We use the weakest single dimension score < 0.60
(`queue/lanes.py: LOW_CONFIDENCE_THRESHOLD`). Chosen as the simplest defensible
default; easily tuned.

### OQ-6. Wilson exclusion keys on *why* a run was blocked (owner decision)
The HANDOFF listed "segment ineligible for the tier" as a constraint_block excluded
from the Wilson window. Taken literally that breaks 0→1 promotion (at Tier 0 every
clean run is tier-ineligible, so nothing would count as a success). Owner decision
(Option 1): exclude only **discount over ceiling, rate limit, and never-autonomous
segments**; a clean run to an autonomy-eligible band merely above the current tier
counts as a **quality success**. Full rationale in
[ADR 0008](adr/0008-constraint-blocks-excluded-from-tier-standing.md).

**Known gaming surface (accepted).** Because a clean 90-day campaign counts toward
0→1, an operator could in principle accrue promotion evidence entirely from 90-day
sends without ever exercising the 30-day case the tier actually governs. This is
backstopped by the probation verification challenge, which runs the golden subset
for the campaign type and would exercise the real case. Left as a monitored known
issue rather than special-cased.

### OQ-7. Review-queue lanes diverge from SPEC §5 for brand-only failures
SPEC §5 defines the batch lane as "no critical flag and brand_voice ≥ 0.75." The
updated golden set puts brand-voice-only failures in the **batch** lane (they are
mild, groupable) and reserves the **judgment** lane for critical failures,
over-ceiling, and never-autonomous blocks. The goldens are the newer authored
truth, so the lane logic follows them; validated by the routing gate reproducing all
75 authored `expected_lane` values. This supersedes the literal §5 batch predicate.

### OQ-8. Monitoring requirement M1 (brief-instructed vs agent-originated failures)
The `GS-PD-16` decision classifies a brief-instructed discount-stacking evasion as a
*quality failure* ("the brief told me to" is not reasoning an autonomous agent
should use). This is defensible but not obviously correct, so every quality failure
records a `failure_origin` (`brief_instructed` | `agent_originated`) and the console
surfaces the ratio. Resolution path is stated in
[docs/autonomy-model.md](autonomy-model.md): if a material share of quality failures
turn out to be brief-instructed, reclassify to `constraint_block` and flag the
requester rather than the agent.

### OQ-5. Send-window duration in the demo
SPEC §5 makes age an SLA via `send_window_expires_at` but does not fix a default
window length. The demo/seed uses a 48-hour window; the queue logic itself is
window-agnostic (it works off whatever expiry each item carries).
