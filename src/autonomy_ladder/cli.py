"""Command-line entry point.

``autonomy-ladder demo`` runs one campaign end-to-end with live agents and prints
the controller's decision and a model-routing report (needs an API key).
``autonomy-ladder seed-ui`` populates the operator console's state with no key.
"""

from __future__ import annotations

import argparse

from autonomy_ladder.autonomy.controller import AutonomyController
from autonomy_ladder.autonomy.ledger import Ledger
from autonomy_ladder.config import get_settings, load_brand_policy, load_tiers_config
from autonomy_ladder.domain import CampaignType, SegmentBand
from autonomy_ladder.observability.cost import CostTracker
from autonomy_ladder.records import CampaignBrief
from autonomy_ladder.service import AutonomyService


def _run_demo() -> int:
    """Run one campaign through the live pipeline and show the decision (needs key)."""
    from autonomy_ladder.agents.orchestrator import Orchestrator
    from autonomy_ladder.evals.llm import RecordingClient
    from autonomy_ladder.observability.otel import setup_telemetry

    setup_telemetry()
    tiers = load_tiers_config()
    controller = AutonomyController(Ledger(":memory:"), tiers, load_brand_policy())
    client = RecordingClient()  # raises a clear error if ANTHROPIC_API_KEY is unset
    orch = Orchestrator(client, tiers.constraints)
    cost = CostTracker()

    brief = CampaignBrief(
        campaign_type=CampaignType.NEWSLETTER.value,
        goal="Announce fresh 3-season shelters to our most engaged subscribers.",
        requested_segment=SegmentBand.ENGAGED_30D,
        discount_pct=0.0,
        product_ids=["NB-0001"],
    )
    tier = controller.effective_tier(controller.state(CampaignType.NEWSLETTER))
    run = orch.run(brief, tier=tier, cost=cost)
    decision = controller.process_run(run.evaluation)

    print("\n=== CAMPAIGN ===")
    print(f"subject: {run.content.subject}")
    print(f"segment: {run.content.target_segment.value}   revisions: {run.revisions}")
    print("\n=== DIMENSIONS ===")
    for dim, res in run.evaluation.dimensions.items():
        print(f"  {dim.value:<22} {res.verdict.value:<5} {res.score:.2f}  {res.reasoning[:60]}")
    print(f"\npassed: {run.evaluation.passed}   deterministic ok: {run.deterministic.ok}")
    print("\n=== CONTROLLER DECISION ===")
    print(f"  decision: {decision.decision.value}")
    print(f"  effective tier: {decision.effective_tier.value} ({decision.effective_tier.name})")
    for line in decision.rationale:
        print(f"  - {line}")

    report = cost.routing_report(accuracy_note="See docs/evaluation.md for per-dimension kappa.")
    print("\n=== ROUTING REPORT ===")
    for u in report.per_model:
        print(
            f"  {u.model:<28} calls={u.calls} "
            f"in={u.input_tokens} out={u.output_tokens} ${u.cost_usd:.4f}"
        )
    print(f"  total ${report.total_cost_usd:.4f}")
    print(
        f"  Haiku spans cost ${report.haiku_cost_usd:.4f}; on Sonnet they would cost "
        f"${report.haiku_cost_if_sonnet_usd:.4f} (saved ${report.savings_usd:.4f})."
    )
    return 0


def _seed_ui() -> int:
    from autonomy_ladder.demo import seed_demo

    service = AutonomyService.default()
    seed_demo(service)
    print("Seeded demo state. Run `make ui` and open http://localhost:8000.")
    dash = service.dashboard()
    for row in dash:
        print(f"  {row['campaign_type']:<22} tier={row['tier']} standing={row['standing']}")
    service.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autonomy-ladder")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("demo", help="run one campaign end-to-end (needs ANTHROPIC_API_KEY)")
    sub.add_parser("seed-ui", help="populate the operator console with demo state (no key)")
    sub.add_parser("calibrate", help="print the judge calibration report")
    args = parser.parse_args(argv)

    if args.command == "demo":
        return _run_demo()
    if args.command == "seed-ui":
        return _seed_ui()
    if args.command == "calibrate":
        from autonomy_ladder.evals.calibration import main as calibration_main

        return calibration_main()
    get_settings()  # unreachable; keeps mypy happy about the return path
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
