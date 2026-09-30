.PHONY: help install check lint format types test live corpus schema diagrams clean

help:  ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-10s %s\n", $$1, $$2}'

install:  ## Create .venv with runtime and dev dependencies
	uv sync

check: lint types test  ## Everything CI runs

lint:  ## Lint and format check
	uv run ruff check .
	uv run ruff format --check .

format:  ## Apply formatting and safe fixes
	uv run ruff format .
	uv run ruff check --fix .

types:  ## Strict type check
	uv run mypy

test:  ## Offline tests with coverage (no API key needed)
	uv run pytest --cov --cov-fail-under=85

live:  ## Live tests against the Claude API (needs ANTHROPIC_API_KEY)
	uv run pytest -m live

corpus:  ## Regenerate the synthetic corpus and ground truth
	uv run extractor corpus

schema:  ## Regenerate the published JSON Schema
	uv run extractor schema --out schemas/study_record.schema.json

diagrams:  ## Re-render docs/diagrams/*.png from the Mermaid sources (needs mmdc)
	uv run python scripts/render_diagrams.py README.md docs/GUIDE.md

clean:  ## Remove caches and local run output
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov runs
