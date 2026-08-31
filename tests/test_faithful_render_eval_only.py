"""Faithful-render is eval-only and must not be reachable from production.

Faithful-render makes the Copy Composer emit known-false content on purpose (to
measure judge recall). If a production path could enter it, the system could send
deliberately false campaigns. These tests pin the boundary (ADR 0011):

* the production ``Orchestrator.run`` has no ``faithful`` parameter at all;
* ``compose`` defaults to ``faithful=False``;
* no production-serving module references the eval-only entry point or the flag.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from autonomy_ladder.agents import copy_composer
from autonomy_ladder.agents.orchestrator import Orchestrator

_SRC = Path(__file__).resolve().parents[1] / "src" / "autonomy_ladder"

# Modules that serve real campaigns (generate content that could be sent). The
# eval harness (evals/*) is intentionally excluded — it is the only permitted
# caller of the faithful path.
_PRODUCTION_FILES = [
    _SRC / "service.py",
    _SRC / "cli.py",
    *(_SRC / "api").glob("*.py"),
]


def test_production_run_has_no_faithful_parameter() -> None:
    params = inspect.signature(Orchestrator.run).parameters
    assert "faithful" not in params, "production run() must not expose faithful-render"


def test_compose_defaults_to_grounded() -> None:
    assert inspect.signature(copy_composer.compose).parameters["faithful"].default is False


def test_production_modules_cannot_reach_faithful_render() -> None:
    for path in _PRODUCTION_FILES:
        src = path.read_text()
        assert "run_eval" not in src, f"{path.name} reaches the eval-only orchestrator entry"
        assert "faithful" not in src, f"{path.name} references faithful-render"
