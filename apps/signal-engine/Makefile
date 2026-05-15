.PHONY: dev install typecheck lint test

dev:
	uv run uvicorn src.main:app --reload --port 8000

install:
	uv sync --all-extras

typecheck:
	uv run mypy src

lint:
	uv run ruff check src

lint-fix:
	uv run ruff check src --fix

test:
	uv run pytest
