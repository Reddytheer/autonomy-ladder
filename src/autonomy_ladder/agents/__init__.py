"""The agents — the LLM half of the system (SPEC §6).

An orchestrator-workers topology: the Orchestrator plans and assembles; the
Segment Analyst and Copy Composer generate; the Catalog Lookup tool grounds
claims; and the Claim Verifier and Brand Sentinel grade the output as *independent*
calls (P2). An evaluator-optimizer revision loop (max 2 iterations) gives the
composer a chance to fix what the checks caught.

Nothing here decides autonomy. The pipeline produces a scored ``RunEvaluation``
and hands it to the deterministic controller, which alone decides what happens
(P1). These agents take an injected LLM client, so the whole pipeline runs against
mocked/replayed responses with no API key.
"""
