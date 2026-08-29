"""autonomy_ladder — earn/enforce per-task-type autonomy for AI agents.

This package exists to answer one question that every agent product eventually
hits: not "can the agent do this" but "has the agent *earned the right* to do
this unsupervised, for this specific kind of task, on this specific evidence."

It has three parts, kept deliberately separate:

* ``autonomy``  — deterministic governance (tiers, Wilson-interval promotion,
  controller, append-only ledger, constraints, probation). No LLM lives here.
* ``agents``    — the LLM workers that generate and independently grade campaigns.
* ``evals``     — deterministic checks, LLM judges, calibration, and the gate.

The boundary between ``autonomy`` and ``agents`` is the whole point of the
project (see SPEC §2, P1): the agent produces work; the controller decides what
happens to it, and the agent can never argue its way into a higher tier.
"""

__version__ = "0.1.0"
