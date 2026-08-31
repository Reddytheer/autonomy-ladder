"""Post-send outcomes — the feedback loop that closes the system (HANDOFF 2).

Every pre-send signal is a *prediction*. This package supplies the deterministic
outcome simulator and the plumbing that feeds real-world deliverability back into
autonomy standing, so a campaign that passed every eval but breached is caught,
demoted against, and — most valuably — flagged as a case the judges got wrong.
"""
