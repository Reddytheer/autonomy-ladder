"""The review queue — two lanes, because the reviewer's attention is scarce.

Why this subpackage exists (SPEC §5): when the agent cannot act autonomously, the
work goes to a human. A single sorted list wastes the one resource that does not
scale — reviewer attention. So the queue splits into a *batch* lane (safe,
look-alike items approved as a group) and a *judgment* lane (anything risky,
sorted by risk). Age is treated as an SLA, not a sort key: items near their send
window escalate; expired items drop out entirely.
"""
