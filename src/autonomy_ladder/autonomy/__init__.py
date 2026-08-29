"""Deterministic autonomy governance — the safety-critical half of the project.

Nothing in this subpackage calls an LLM. It decides tiers, evaluates statistical
promotion evidence, enforces hard constraints, records an append-only ledger, and
runs probation. The agent (``autonomy_ladder.agents``) produces work; the code
here decides what happens to that work and can never be argued out of it
(SPEC §2, P1 — complete mediation).
"""
