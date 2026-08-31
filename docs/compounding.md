# What happens if this repeats

A single bad send is a bounded, recoverable cost. The reason the [post-send
loop](autonomy-model.md) exists is that repeated bad sends are not bounded — they
compound, and the compounding runs in both directions.

**The downward spiral.** Complaints damage sender reputation. Damaged reputation
lowers inbox placement. Lower placement means fewer people see the email, so
engagement falls. Falling engagement shrinks the engaged segments — and those
segments are precisely the audiences the agent is permitted to send to autonomously.
So the agent's safe operating space narrows, more campaigns route to human review,
the reviewer becomes the bottleneck again, and the value of the system collapses. The
final state is worse than never having automated: a degraded channel *and* a queue.

The mechanism worth internalising is that **deliverability is a shared, slow-to-repair
resource.** Damage takes weeks to recover and is caused in a single send. It behaves
much more like an environmental commons than like a per-campaign cost, and it should
be governed that way.

**The upward spiral.** The same structure runs in reverse. Good sends sustain
engagement. Sustained engagement grows the engaged bands. Larger engaged bands mean
more audience is available for autonomous sending, which means more campaigns ship
without review, which means the reviewer's attention concentrates on genuinely risky
work. Meanwhile every judge blind spot discovered by the loop is added to the golden
set, so the gate that guards promotion gets sharper over time.

**The asymmetry to design for.** Trust is earned in hundreds of clean sends and lost
in one bad one. Promotion is therefore slow and statistical, requiring a Wilson lower
bound over a real sample; demotion is immediate and requires only a single breach.
That asymmetry is not caution for its own sake — it matches the shape of the
underlying harm.

## What this means for the customer relationship

The system's real output is not campaigns. It is a defensible record of how much
autonomy has been earned and on what evidence.

That record is what lets a brand manager say yes to more automation, because they can
point to the trust ledger rather than to a vendor's assurance. It is what an
enterprise buyer's risk function asks for. And it is what makes the difference between
a customer who caps autonomy at Tier 1 forever and one who expands scope every
quarter — which, commercially, is the difference between flat and expanding revenue on
the same account.
