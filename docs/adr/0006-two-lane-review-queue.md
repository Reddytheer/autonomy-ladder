# 0006 — Two-lane review queue

Status: accepted. Implements SPEC §5.

## Context

Everything the controller does not auto-send lands in front of a human. If that is
one long list, the reviewer pays the same attention tax on a batch of clean,
low-risk drafts as on a single campaign with a critical flag — and the scarce
resource here is *the reviewer's attention*, not screen space or compute. A single
sorted list also forces one ordering to serve two incompatible purposes: skimming a
homogeneous batch for a fast approve-all, versus scrutinizing heterogeneous
high-risk items one at a time. The design has to protect attention by separating
those modes.

## Decision

Split review into **two lanes** (SPEC §5):

- **Batch lane** — items with no critical flag and `brand_voice ≥ 0.75`. Presented
  as a group with a single approve-all action and a diff view, **not individually
  sorted**, because they are interchangeable enough to clear together.
- **Judgment lane** — anything with a critical-dimension flag, a low confidence
  score, or a constraint violation. **Sorted by risk score descending**, because
  here order is the whole point: the reviewer should meet the most dangerous item
  first.

**Age is treated as an SLA, not a sort key.** Each item carries a
`send_window_expires_at`. Items within 20% of their window escalate to the top of
their lane and are visually flagged; once past the window they move to `EXPIRED` and
drop out of both lanes. Time pressure changes *urgency*, so it reorders within a
lane and flags — but it never changes an item's *risk*, so it never moves an item
between lanes or outranks risk as the judgment lane's primary sort.

## Alternatives considered

**One list sorted by risk** was rejected because it drowns a large clean batch in
the same ranked view as the dangerous items, destroying the fast approve-all path
that makes the batch lane cheap. **Sorting primarily by age** ("oldest first") was
rejected as the classic queue mistake: it lets a trivial old item outrank a fresh
critical one, optimizing for inbox-zero instead of for risk. **Auto-expiring
silently** without a distinct `EXPIRED` state was rejected because a campaign that
missed its window is an outcome a reviewer must be able to see and account for, not
a disappearance.

## Consequences

The reviewer spends attention where risk actually is: bulk-clears the batch lane in
one action and works the judgment lane top-down. The SLA mechanism guarantees
time-sensitive items surface before they lapse without ever letting the clock
override risk. The cost is a small amount of routing and lane-assignment logic, plus
the modeling of a real risk score for the judgment lane — deliberately more work
than a naive `ORDER BY`, and the point of the design.
