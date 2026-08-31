# Economics of autonomous sending

> Every number below is a **documented assumption, not a measurement.** The point is
> not the precision — it is that the framework's thresholds should be derived from
> consequences rather than picked.

## What a bad autonomous send costs

**Model inputs (assumptions).** 100,000-subscriber list · $45 annual revenue per
engaged subscriber · 2.5-year average remaining tenure (so ~$112 lifetime value) ·
$0.10 attributable revenue per delivered email · 4 sends per subscriber per month ·
95% baseline inbox placement, falling to 88% after reputation damage · ~6 weeks to
recover · human review at 6 minutes and $60/hour fully loaded.

| | Audience | Unsubscribe loss | Reputation loss | **Total** |
|---|---|---|---|---|
| Tier 1 (engaged_30d) | 8,000 | $5,400 | $3,880 | **$9,280** |
| Tier 2 (engaged_90d) | 35,000 | $23,625 | $3,880 | **$27,505** |
| Unbounded (full list) | 100,000 | $67,500 | $3,880 | **$71,380** |

Two things this makes visible. Unsubscribe loss scales with audience, because each
unsubscribe is a permanently lost contact. Reputation loss is **list-wide regardless
of who you sent to** — a bad campaign to 8,000 people degrades inbox placement for
every future send to all 100,000. Deliverability is a shared resource, and one bad
campaign taxes every campaign that follows.

## The metric the framework does not yet measure

The economically relevant number is not the Wilson pass rate. It is the **escape
rate**: the probability that a campaign is genuinely harmful *and* passes every eval.

The Wilson gate measures how often the agent produces acceptable work. The money is
lost when a bad campaign gets through the judges — which is a function of **judge
recall**, not agent quality. These are different quantities and the current design
only measures the first.

Assuming (assumption) 10% of production briefs are genuinely harmful, and valuing
autonomy at review cost saved plus roughly 20% of campaign revenue that decays while
waiting for approval:

| | Value per campaign | Cost if bad | Max tolerable escape rate | Implied judge recall |
|---|---|---|---|---|
| Tier 1 | $158 | $9,280 | 1.70% | **83%** |
| Tier 2 | $671 | $27,505 | 2.44% | **76%** |

**This is the gap worth naming.** Cohen's kappa tells us judges agree with a human on
labelled cases. It does not tell us what fraction of genuinely harmful campaigns they
catch in production. Recall is the number that should gate promotion, and measuring it
requires exactly the [post-send loop](autonomy-model.md) — because only real outcomes
reveal what the judges missed. The loop's `eval_passed_outcome_failed` flags are the
raw material for estimating it.

## Why graduated tiers, when expected value says otherwise

Run the expected-value math and autonomy looks roughly as justified at full-list
scale as at Tier 1 — value and cost both scale with audience. On that basis you would
skip the ladder.

That reasoning is wrong, and the reason is worth stating plainly: **expected value is
the wrong frame for adoption.** What matters is whether a failure is *survivable*
while trust is still being established.

A $9,280 mistake is a learning event. A $71,380 mistake ends the program — not
because the math changed, but because no brand manager survives explaining it, and
the organisation's willingness to try again is spent. Graduated autonomy is not
primarily about expected loss. It is about keeping early failures small enough that
the programme lives long enough to earn the trust that makes larger autonomy
defensible.

That is also why the constraints matter more than the thresholds. Capping the blast
radius is what makes an achievable accuracy bar good enough.
