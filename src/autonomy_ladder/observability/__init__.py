"""Observability — OpenTelemetry spans with GenAI semantic conventions (SPEC §8).

Every LLM call and tool call becomes a span carrying ``gen_ai.*`` attributes.
Prompt and completion *content* is recorded as span events, never as span
attributes: attributes are indexed and size-limited and would leak PII (SPEC §8).
Spans export to the console and to a local JSONL file — no external backend is
required to run the project.
"""
