.PHONY: bootstrap format lint type test security verify

bootstrap:
	uv sync --locked --all-groups

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .
	uv run ruff format --check .

type:
	uv run mypy

test:
	uv run pytest

security:
	@if command -v bandit >/dev/null; then bandit -c pyproject.toml -r services; else echo "bandit unavailable"; fi
	@if command -v detect-secrets >/dev/null; then detect-secrets scan --all-files; else echo "detect-secrets unavailable"; fi

verify: lint type test security
