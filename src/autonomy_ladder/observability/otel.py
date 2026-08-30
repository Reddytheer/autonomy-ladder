"""OpenTelemetry setup and GenAI span helpers (SPEC §8).

A local-first tracing setup: spans go to the console and to a JSONL file so a
reviewer can inspect every LLM/tool call with no external collector. The helpers
here enforce the two rules that matter: use the ``gen_ai.*`` attribute names, and
keep prompt/completion *content* in span events, not attributes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import Span, Tracer

from autonomy_ladder.config import get_settings

_TRACER_NAME = "autonomy_ladder"
_configured = False


class JSONLFileSpanExporter(SpanExporter):
    """Append each finished span to a JSONL file — one span per line.

    Deliberately minimal (no external backend, SPEC §8). We serialise the fields a
    reviewer needs: name, ids, timings, status, the gen_ai.* attributes, and events
    (which is where prompt/completion content lives).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._path.open("a", encoding="utf-8") as fh:
            for span in spans:
                fh.write(json.dumps(self._to_dict(span)) + "\n")
        return SpanExportResult.SUCCESS

    @staticmethod
    def _to_dict(span: ReadableSpan) -> dict[str, Any]:
        ctx = span.get_span_context()
        trace_id = f"{ctx.trace_id:032x}" if ctx else ""
        span_id = f"{ctx.span_id:016x}" if ctx else ""
        return {
            "name": span.name,
            "trace_id": trace_id,
            "span_id": span_id,
            "start_time": span.start_time,
            "end_time": span.end_time,
            "status": span.status.status_code.name if span.status else "UNSET",
            "attributes": dict(span.attributes or {}),
            "events": [
                {"name": e.name, "attributes": dict(e.attributes or {})} for e in span.events
            ],
        }


def setup_telemetry(force: bool = False) -> TracerProvider:
    """Install a TracerProvider exporting to JSONL (+ console). Idempotent."""
    global _configured
    provider = trace.get_tracer_provider()
    if _configured and not force and isinstance(provider, TracerProvider):
        return provider

    settings = get_settings()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(JSONLFileSpanExporter(settings.telemetry_path)))
    if settings.otel_console_export:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    _configured = True
    return provider


def get_tracer() -> Tracer:
    return trace.get_tracer(_TRACER_NAME)


@contextmanager
def llm_span(name: str, *, model: str) -> Iterator[Span]:
    """Open a span for one LLM call with the request model set (gen_ai.*)."""
    with get_tracer().start_as_current_span(name) as span:
        span.set_attribute("gen_ai.system", "anthropic")
        span.set_attribute("gen_ai.request.model", model)
        yield span


@contextmanager
def tool_span(name: str, *, tool: str) -> Iterator[Span]:
    """Open a span for one tool call."""
    with get_tracer().start_as_current_span(name) as span:
        span.set_attribute("tool.name", tool)
        yield span


def record_usage(
    span: Span,
    *,
    input_tokens: int,
    output_tokens: int,
    finish_reasons: Sequence[str] = ("stop",),
) -> None:
    """Attach GenAI usage attributes to an LLM span (SPEC §8)."""
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    span.set_attribute("gen_ai.response.finish_reasons", list(finish_reasons))


def record_content(span: Span, *, system: str, user: str, completion: str) -> None:
    """Record prompt/completion as span EVENTS, never attributes (SPEC §8: PII/size)."""
    span.add_event("gen_ai.content.prompt", {"system": system, "user": user})
    span.add_event("gen_ai.content.completion", {"completion": completion})
