"""LLM access with prompt-hash fixture caching (SPEC §7, §8).

Why this exists: judges are LLM calls, but a reviewer must be able to run
``make eval`` and ``make gate`` and see real results *with no API key*. So every
call is keyed by a hash of (model, system, user) and cached under
``evals/fixtures/``. In replay mode (the default) a cache miss is an error, never
a silent live call — reproducibility is the whole point. ``make fixtures`` runs in
record mode, which calls the API and writes the cache.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

from autonomy_ladder.config import REPO_ROOT, require_api_key

FIXTURES_DIR = REPO_ROOT / "evals" / "fixtures"


def prompt_hash(model: str, system: str, user: str) -> str:
    """Stable content hash identifying one LLM request."""
    h = hashlib.sha256()
    h.update(model.encode())
    h.update(b"\x00")
    h.update(system.encode())
    h.update(b"\x00")
    h.update(user.encode())
    return h.hexdigest()


class MissingFixtureError(RuntimeError):
    """Raised in replay mode when no cached response exists for a prompt."""


class LLMClient(Protocol):
    def complete(self, *, model: str, system: str, user: str) -> str: ...


class FixtureStore:
    """Reads/writes cached completions as one JSON file per prompt hash."""

    def __init__(self, directory: Path = FIXTURES_DIR) -> None:
        self._dir = directory
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def get(self, key: str) -> str | None:
        p = self._path(key)
        if not p.exists():
            return None
        payload = json.loads(p.read_text())
        response: str = payload["response"]
        return response

    def put(self, key: str, *, model: str, system: str, user: str, response: str) -> None:
        # Store the request alongside the response so the cache is auditable and a
        # reviewer can see exactly what produced it. Content lives here, not in
        # span attributes (SPEC §8).
        self._path(key).write_text(
            json.dumps(
                {"model": model, "request": {"system": system, "user": user}, "response": response},
                indent=2,
            )
        )


class ReplayClient:
    """Replay-only client (keyless). A cache miss raises."""

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or FixtureStore()

    def complete(self, *, model: str, system: str, user: str) -> str:
        key = prompt_hash(model, system, user)
        cached = self._store.get(key)
        if cached is None:
            raise MissingFixtureError(
                f"No cached fixture for prompt {key[:12]}… (model={model}). "
                "Run `make fixtures` with ANTHROPIC_API_KEY to record it."
            )
        return cached


class RecordingClient:
    """Live client that caches every response (used by `make fixtures`)."""

    def __init__(self, store: FixtureStore | None = None, max_tokens: int = 2048) -> None:
        self._store = store or FixtureStore()
        self._max_tokens = max_tokens
        from anthropic import Anthropic  # imported lazily; only needed with a key

        self._client = Anthropic(api_key=require_api_key())

    def complete(self, *, model: str, system: str, user: str) -> str:
        key = prompt_hash(model, system, user)
        cached = self._store.get(key)
        if cached is not None:
            return cached
        message = self._client.messages.create(
            model=model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=self._max_tokens,
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        self._store.put(key, model=model, system=system, user=user, response=text)
        return text
