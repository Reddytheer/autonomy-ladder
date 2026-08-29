"""The evaluation layer (SPEC §7).

Two stages, cheapest first (P3): deterministic millisecond checks (schema, bounds,
prohibited terms, link validity, segment/discount constraints) filter obvious
failures before any LLM judge runs. Then one structured judge per quality
dimension scores what remains. Judge calls are cached by prompt hash so the golden
set and the regression gate run with NO API key.
"""
