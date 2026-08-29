"""Synthetic data package for the reference implementation.

Why this package exists: the framework is domain-agnostic, but a working
reference implementation needs a *concrete* world to act on — a product catalog
to write copy about, customers to segment, and brand rules to check against. All
of it is fictional ("Northbay Supply", outdoor gear) so the repo stays clean-room
(SPEC §0, §12) and so a reviewer can run every downstream layer with no external
data source and no secrets.

The generator here is deterministic (fixed RNG seed): re-running it produces
byte-identical files, so the committed datasets under ``data/synthetic/`` are a
reproducible artifact rather than a one-off dump.
"""

from __future__ import annotations
