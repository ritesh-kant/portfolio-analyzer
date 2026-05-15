# Signal Engine

Autonomous AI trading signal pipeline for Indian markets (NSE/BSE).

Built with **FastAPI** + **LangGraph** + **Motor** (async MongoDB). Runs daily at 06:00 IST on weekdays, producing paper-trade signals with confidence scores and half-Kelly position sizing.

## Architecture

```
POST /pipeline/run
        │
        ▼
  LangGraph StateGraph (TradingState)
  ┌─────────────────────────────────────────────────────────┐
  │  news_agent → sector_agent → stock_selector             │
  │      → [technical_agent ‖ market_agent] (parallel)      │
  │      → guard_agent                                      │
  │          ├─ any passed → signal_agent → order_agent     │
  │          └─ all blocked ──────────────────┐             │
  │                                    audit_agent          │
  └─────────────────────────────────────────────────────────┘
```

Each agent writes its output to MongoDB immediately — the dashboard polls live progress.

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- MongoDB (local Docker or Atlas)
- Redis (for BullMQ queue — used by Node.js side)

### Install

```bash
cd apps/signal-engine
uv sync --extra dev
```

### Environment

Copy `.env.example` from the repo root and fill in values:

```env
MONGODB_URI=mongodb://localhost:27017/portfolio_analyzer
AI_PROVIDER=ollama          # ollama | anthropic | openai | gemini
ANTHROPIC_API_KEY=...       # required when AI_PROVIDER=anthropic
OPENAI_API_KEY=...          # required when AI_PROVIDER=openai
TRADING_MODE=paper
```

### Run

```bash
# Development (auto-reload)
uv run uvicorn src.main:app --reload --port 8000

# Or via Makefile
make dev
```

### Health check

```bash
curl http://localhost:8000/health
```

### Trigger a pipeline run manually

```bash
curl -X POST http://localhost:8000/pipeline/run \
  -H "X-API-Key: local-dev-key-change-in-prod" \
  -H "Content-Type: application/json" \
  -d '{"ai_provider": "ollama"}'
```

## Tests

```bash
# Run all tests
uv run pytest

# With verbose output
uv run pytest -v

# Single file
uv run pytest tests/test_scoring.py -v
```

### Test coverage

| File | What it tests |
|------|--------------|
| `test_scoring.py` | `_score_base`, `_half_kelly`, `_calc_position`, `_has_positive_news` — pure functions, no I/O |
| `test_dedup.py` | News article hash deduplication — determinism, case normalisation |
| `test_guard_logic.py` | All kill-switch checks (VIX, Nifty drop, ASM/GSM, earnings, price move) with mocked NSE calls |
| `test_integration.py` | `SignalAgent` + `OrderAgent` end-to-end with mocked LLM and mocked MongoDB repositories |

## Confidence Scoring

Signals are scored across 9 weighted signals (max 100 pts):

| Signal | Weight | Trigger |
|--------|--------|---------|
| RSI oversold | 12 | RSI < 40 |
| MACD positive | 12 | histogram > 0 |
| Price > EMA20 | 10 | short-term trend |
| Price > EMA50 | 12 | medium-term trend |
| Volume elevated | 10 | > 1.5× 20-day avg |
| News catalyst | 14 | positive article for stock/sector |
| Sector bullish | 12 | sector score > 60 |
| Market positive | 8 | Nifty up + FII buying |
| LLM bonus | 0–10 | AI reasoning quality |

Signals with `confidence >= MIN_SIGNAL_CONFIDENCE` (default 60) are passed to order_agent.

## Kill-Switch Guards

Any of these blocks a signal (no order placed):

- India VIX > 22
- Nifty 50 down > 1.5% today
- Stock on NSE ASM or GSM list
- Earnings announcement within 5 calendar days
- Stock already moved > 5% since news (news priced in)

## Position Sizing

Half-Kelly formula with `REWARD_TO_RISK = 2.0`:

```
p  = confidence / 100
f  = (p × r - (1-p)) / r    # full Kelly
f½ = max(0, f / 2)           # half Kelly (safer)
position_value = portfolio_value × f½
  capped at POSITION_SIZE_PCT% of portfolio (default 12%)
  capped at available cash
```

## Linting & Type Checking

```bash
# Lint
uv run ruff check src tests

# Format
uv run ruff format src tests

# Type check (strict)
uv run mypy src
```

## AI Provider Selection

Set `AI_PROVIDER` env var to switch providers — no code changes required:

| Value | Model | Notes |
|-------|-------|-------|
| `ollama` | `llama3.1` (default) | Local, no API key needed |
| `anthropic` | `claude-sonnet-4-5` | Requires `ANTHROPIC_API_KEY` |
| `openai` | `gpt-4o` | Requires `OPENAI_API_KEY` |
| `gemini` | `gemini-2.0-flash` | Requires `GEMINI_API_KEY` |

## Data Sources

All free, no paid APIs:

- **RSS**: Economic Times, Moneycontrol, LiveMint, Business Standard, BusinessLine
- **NSE API**: FII/DII flows, ASM/GSM lists, earnings calendar, corporate announcements
- **BSE API**: Corporate announcements
- **yfinance**: Nifty 50, India VIX, stock OHLCV + technical indicators
