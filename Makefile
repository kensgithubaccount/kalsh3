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
	@uv run bandit -c pyproject.toml -r services || { rc=$$?; if [ $$rc -ne 1 ]; then exit $$rc; fi; echo "Bandit findings above are informational; the high-severity gate follows."; }
	uv run bandit -c pyproject.toml -r services --severity-level=high
	uv run detect-secrets scan --all-files

verify: lint type test security
