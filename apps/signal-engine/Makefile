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

# ── momentum-trader (Upstox paper scanner) ────────────────────────────────────

mt-test:
	uv run pytest tests/momentum_trader/ -v

# Legacy OAuth only. Paper scanning prefers UPSTOX_ANALYTICS_TOKEN, which does not need daily login.
mt-login:
	uv run python -m src.momentum_trader.upstox_auth $(CODE)

# Run the live paper scanner locally (needs UPSTOX_ANALYTICS_TOKEN or legacy OAuth token).
mt-scan:
	uv run python -m src.momentum_trader.scanner

# Registered forward-only test: MA9 first pullback, then compare catalyst vs no-catalyst.
mt-scan-first-pullback:
	MT_STRATEGY=catalyst_first_pullback MT_LOG_CSV=research/backtests/mt_catalyst_first_pullback_forward_log.csv uv run python -m src.momentum_trader.scanner

# Safe dependency check: authenticates market data but sends no order or paper trade.
mt-dry-run-first-pullback:
	MT_STRATEGY=catalyst_first_pullback MT_LOG_CSV=research/backtests/mt_catalyst_first_pullback_forward_log.csv uv run python -m src.momentum_trader.scanner --dry-run

# Two-list forward paper strategy: broad scan -> attention -> 1-minute confirmation.
mt-scan-attention:
	MT_STRATEGY=attention_1m MT_LOG_CSV=research/backtests/mt_attention_1m_forward_log.csv uv run python -m src.momentum_trader.scanner

mt-scan-attention-resistance-state:
	MT_STRATEGY=attention_1m_resistance_state MT_LOG_CSV=research/backtests/mt_attention_1m_resistance_state_forward_log.csv uv run python -m src.momentum_trader.scanner

# Separate paper arm: one volume-backed reclaim after an attention false break.
mt-scan-attention-false-break-reclaim:
	MT_STRATEGY=attention_1m_false_break_reclaim MT_LOG_CSV=research/backtests/mt_attention_1m_false_break_reclaim_forward_log.csv uv run python -m src.momentum_trader.scanner

mt-dry-run-attention:
	MT_STRATEGY=attention_1m MT_LOG_CSV=research/backtests/mt_attention_1m_forward_log.csv uv run python -m src.momentum_trader.scanner --dry-run

# Forward-log gate screen (hypothesis v2 §3); --status while accumulating
mt-screen:
	cd ../.. && uv run research/backtests/bt18_momentum_forward_screen.py --status

# Pool backtest smoke test (50 symbols, one year) — full run: see bt17 docstring
mt-backtest-smoke:
	cd ../.. && uv run research/backtests/bt17_momentum_pool.py --start 2025-01-01 --end 2025-06-30 --limit 50
