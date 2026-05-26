.PHONY: dev install typecheck lint lint-fix test \
        nt-test nt-ingester nt-classifier nt-trade nt-monitor nt-pipeline

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

# ── news-trader local testing ──────────────────────────────────────────────

# Run only the news-trader test suite (fast, no I/O)
nt-test:
	uv run pytest tests/news_trader/ -v

# Invoke individual handlers against local MongoDB
# Requires: docker compose up -d mongodb  (from repo root)
nt-ingester:
	uv run python scripts/invoke_news_trader.py ingester

nt-classifier:
	@test -n "$(ID)" || (echo "Usage: make nt-classifier ID=<news_id>"; exit 1)
	uv run python scripts/invoke_news_trader.py classifier $(ID)

nt-trade:
	@test -n "$(ID)" || (echo "Usage: make nt-trade ID=<signal_id>"; exit 1)
	uv run python scripts/invoke_news_trader.py trade $(ID)

nt-monitor:
	uv run python scripts/invoke_news_trader.py monitor

# Full end-to-end: ingester → classifier → trade_decision (skips SQS delay)
nt-pipeline:
	uv run python scripts/invoke_news_trader.py pipeline
