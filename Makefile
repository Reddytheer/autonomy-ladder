# autonomy-ladder — task runner.
#
# Every target that a reviewer needs (`setup`, `test`, `eval`, `gate`) runs with
# NO API key. `demo` is the only target that makes live LLM calls.

.DEFAULT_GOAL := help
UV := uv

.PHONY: help setup test lint typecheck data eval gate demo ui fixtures clean check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install all dependencies (no API key needed)
	$(UV) sync --all-groups

data: ## (Re)generate committed synthetic datasets (catalog, customers, brand rules)
	$(UV) run python -m autonomy_ladder.data.generate

test: ## Run the full unit-test suite (no API key needed)
	$(UV) run pytest

lint: ## Ruff lint + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

typecheck: ## mypy strict type check
	$(UV) run mypy

check: lint typecheck test ## Lint, typecheck, and test — the local CI mirror

eval: ## Run the golden set off cached fixtures and print a results table (no API key)
	$(UV) run python -m autonomy_ladder.evals.gate --report

gate: ## Regression gate: compare goldens against baseline, non-zero exit on regression
	$(UV) run python -m autonomy_ladder.evals.gate --check

judge-gate: ## Judge-accuracy gate: replay fixtures, non-zero exit on regression (no API key)
	$(UV) run python -m autonomy_ladder.evals.gate --check-judges

fixtures: ## Record live fixtures + compute the three eval metrics + kappa (REQUIRES ANTHROPIC_API_KEY)
	$(UV) run python -m autonomy_ladder.evals.gate --record

demo: ## Run one campaign end-to-end and show the controller decision (REQUIRES API KEY)
	$(UV) run python -m autonomy_ladder.cli demo

ui: ## Launch the operator console (FastAPI + static frontend) on :8000
	$(UV) run uvicorn autonomy_ladder.api.app:app --reload --port 8000

clean: ## Remove caches and local runtime artifacts (keeps committed data/fixtures)
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage coverage.xml
	rm -rf runtime telemetry/*.jsonl
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
