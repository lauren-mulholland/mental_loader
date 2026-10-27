.PHONY: setup dev doctor test

setup:
	uv sync --locked
	docker compose up -d --wait db
	uv run python -m mental_loader.cli init
	uv run python -m mental_loader.cli setup-hermes

dev:
	uv run python scripts/dev.py

doctor:
	uv run python -m mental_loader.diagnostics

test:
	uv run pytest -q
	uv run ruff check .
