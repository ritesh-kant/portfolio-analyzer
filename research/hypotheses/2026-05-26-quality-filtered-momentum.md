---
slug: quality-filtered-momentum
strategy: qf_momentum
status: registered
registered: 2026-05-26
finalized: ''
decision: ''
final: false
type: portfolio_strategy
standalone: true
---

# Hypothesis: Quality-Filtered Momentum on Nifty Midcap 150

## 1. Mechanism

**What it does (plain language):** Every April, check each stock's year-over-year
EPS (earnings per share) growth using the latest annual results. Remove any stock
with declining EPS from the investable universe for the next 12 months — these are
companies whose business is getting _worse_. Then, each month within that filtered
universe, run the same 12-1 price momentum signal as Strategy P to pick the top
holdings.

In other words: "only buy momentum in companies that are also fundamentally improving."

**The core intuition (SWE analogy):** Strategy P (pure price momentum) failed the
anti-strategy check because in a bull market, even the _worst_ stocks went up — the
rising tide lifted all boats. This strategy adds a fundamental quality gate, like a
linter that rejects obviously broken code before the optimiser runs. The market may
lift all boats, but companies with declining earnings tend to underperform even in
bull markets — their float investors keep selling.

**Two separate signals, one simple rule:**

1. **Annual quality filter** — each April, tag stocks with `eps_growth_yoy < 0` as
   ineligible. This uses Screener.in annual P&L (free, no auth).
2. **Monthly price momentum** — within eligible stocks only, rank by 12-1 momentum,
   long top 10%, same buffer/vol-target logic as Strategy P.

**Why this should survive the anti-strategy gate:**
In Strategy P, the anti-strategy (bottom decile of price momentum) was also
profitable during the 2023-07 → 2024-06 bull run — every stock rose. But companies
with genuinely declining EPS in FY2023 (April 2023 annual results) have headwinds:
insiders sell, analysts downgrade, institutional mandates force exits. Even in a
bull market, these stocks typically underperform the index. Excluding them from the
universe means the "anti-portfolio" within the filtered universe is less likely to
be also profitable.

**Academic support:**

- Piotroski (2000): F-Score shows fundamental improvement separates future winners
  from losers even in value investing.
- Asness, Frazzini, Pedersen (2019): "Quality" (profitability + growth) combined
  with momentum produces more stable out-of-sample Sharpe than either factor alone.
- Novy-Marx (2013): Gross profitability combined with momentum is robust across
  international markets including emerging economies.
- India-specific: Varma (2021, IIMA) documents that fundamental filters improve
  momentum in BSE 500 universe.

---

## 2. Expected Effect Size

- **Direction:** long-only (no shorting — not possible in cash delivery at Zerodha)
- **Filterable universe:** ~90–110 stocks after excluding ~30% with declining EPS
- **Long positions:** top 10% of filtered universe ≈ 9–11 stocks (vs 15 in Strategy P)
- **Expected excess return vs Nifty Midcap 150 TRI:** +4–10% per year
- **Gross Sharpe:** 1.0–1.8
- **Net Sharpe (after cost drag):** 0.7–1.4
- **MaxDD with vol-target overlay:** ≤ 20%

Cost reference (Zerodha 2026, delivery): same as Strategy P — ~45 bps round-trip.
Fewer positions (10 vs 15) means slightly higher DP charge per unit of capital,
but lower turnover (universe filter changes annually, not monthly).

---

## 3. Pre-Registered Signal Construction (IMMUTABLE after this commit)

```python
# ── STEP 1: Annual quality filter (runs once each April, point-in-time) ───────
#
# Source: data/lake/earnings/screener_annual.parquet
#   columns: symbol, fiscal_year (e.g. 2023), eps
#
# On the FIRST signal date on or after April 30 each year (after March year-end
# results are typically all filed):
#
#   eps_growth = (eps[fiscal_year] / eps[fiscal_year - 1]) - 1.0
#
# A stock is ELIGIBLE if:
#   (a) eps[fiscal_year] > 0           — must be profitable at all
#   (b) eps_growth >= EPS_GROWTH_FLOOR — must not be declining
#
# The eligibility set is held fixed for the next 12 months (May → April).
# It changes only on the next April signal date.

EPS_GROWTH_FLOOR = 0.0   # no declining EPS; threshold is zero (not e.g. −5%)

# ── STEP 2: Monthly price momentum (identical to Strategy P) ──────────────────
#
# On the last trading day of each month (t_end):
#
#   t_skip     = t_end − 21 trading days
#   t_lookback = t_end − 252 trading days
#
#   momentum_score = (close[t_skip] / close[t_lookback]) − 1.0
#
# Rank ONLY eligible stocks by momentum_score (descending).
# A stock missing price data on t_skip or t_lookback → excluded this month.

LOOKBACK_DAYS = 252
SKIP_DAYS     = 21
TOP_PCT       = 0.10    # long top 10% of filtered eligible universe
BUFFER_PCT    = 0.20    # stay until rank drops below top 20% of filtered universe
VOL_TARGET    = 0.15    # 15% annualised portfolio vol target
VOL_WINDOW    = 20      # rolling days for realised-vol estimate

# Entry: open of first trading day of following calendar month (NO look-ahead bias)
# Exit:  replaced at next rebalance OR rank drops below buffer in filtered universe

# ── PIT (point-in-time) discipline ────────────────────────────────────────────
# Annual EPS data used: the fiscal_year whose results are available by April 30.
# For stocks with March year-end (majority of Midcap 150): FY2023 results filed
# by April 30, 2023 → used from May 2023 signal date onwards.
# For non-March year-ends: use the most recent fiscal year with reported data
# on the signal date, with a 60-day publication lag assumed.
```

**What is NOT allowed to change after registration:**

- Lookback windows (252 / 21 trading days)
- EPS growth floor (0.0 — exactly zero, not a negative threshold)
- Universe percentile thresholds (10% / 20% of _filtered_ universe)
- Vol-target level (15%)
- Entry timing (first-day-of-month open)
- Publication lag assumption (60 days for non-March year-ends)
- Cost model used in simulation

---

## 4. Falsification Criteria (LOCKED — cannot be relaxed after running)

**Dev period gate (2023-07-01 → 2024-06-30). ALL must pass:**

| Criterion                                       | Kill if... | Notes                                                                                  |
| ----------------------------------------------- | ---------- | -------------------------------------------------------------------------------------- |
| Dev DSR (n_trials from MLflow)                  | < 0.65     | Raised from 0.6 due to 17 prior experiments                                            |
| Dev Sharpe (net of costs)                       | < 0.7      | Annualized, monthly returns                                                            |
| Sharpe under 2× cost-stress                     | < 0.5      | Slippage drawn from t-dist(df=4, scale=2×nominal)                                      |
| Anti-strategy DSR (bottom of filtered universe) | > 0        | Within the quality-filtered universe, shorting worst-momentum stocks should not profit |
| Max drawdown (vol-targeted portfolio)           | > 20%      | Measured on dev period equity curve                                                    |
| Excess return vs Nifty Midcap 150 TRI           | ≤ 0        | Net alpha                                                                              |
| Monthly rebalances in dev                       | < 12       | Statistical significance floor                                                         |
| Avg positions per month                         | < 5        | Filter too aggressive → undiversified                                                  |

**Hold-out gate (2024-07-01 → present). Single shot:**

| Criterion                             | Kill if... |
| ------------------------------------- | ---------- |
| DSR                                   | < 0.4      |
| Excess return vs Nifty Midcap 150 TRI | ≤ 0        |
| Max drawdown                          | > 25%      |

**This file becomes immutable once status = "registered". Results go in §7.**

---

## 5. Data Needed

Both data sources are on disk.

| Source                  | Path                                         | Use                                    |
| ----------------------- | -------------------------------------------- | -------------------------------------- |
| NSE Bhavcopy OHLCV      | `data/lake/nse_bhavcopy/*.parquet`           | Prices for momentum score + simulation |
| Screener.in annual P&L  | `data/lake/earnings/screener_annual.parquet` | EPS quality filter                     |
| Midcap 150 constituents | `data/lake/midcap150_constituents.csv`       | Universe definition                    |

**PIT discipline:**

- Annual EPS: March year-end results are public by April 30 each year.
  The filter is applied starting from the May signal date onwards.
- Price momentum: computed at market close, entered next-day open.
- No look-ahead: the EPS for FY2023 is first used on 2023-05-31 signal date,
  NOT on the 2023-04-30 date (conservative; actual filings are complete by ~April 25).

**Survivorship caveat:** Same as Strategy P. Static constituent list introduces mild
upward bias on 2015–2023 train. Acceptable for dev/hold-out gate evaluation.

---

## 6. Train / Dev / Hold-out Split

- **Train:** 2015-05-01 → 2023-06-30 (EPS filter starts May 2015 using FY2015 results)
- **Dev:** 2023-07-01 → 2024-06-30 (gate — 12 monthly rebalances)
- **Hold-out:** 2024-07-01 → present (NEVER touched until status = final)

Note: The first annual EPS update in dev uses FY2023 results (March 2023 year-end,
available May 2023). So the quality filter entering the dev period reflects FY2023
earnings — the strategy "knows" which companies had declining EPS in FY2023.

---

## 7. Code References

| File                              | Purpose                                       |
| --------------------------------- | --------------------------------------------- |
| `quant/strategies/qf_momentum.py` | Signal construction, simulation, gate metrics |
| `quant/research/run.py`           | `--strategy qf_momentum` dispatch             |
| MLflow experiment                 | `qf_momentum_v1`                              |

---

## 8. Result (filled after dev gate run)

| Criterion                  | Result | Gate   | Status |
| -------------------------- | ------ | ------ | ------ |
| DSR                        | —      | ≥ 0.65 | —      |
| Sharpe (net of costs)      | —      | ≥ 0.7  | —      |
| Sharpe under cost-stress   | —      | ≥ 0.5  | —      |
| Anti-strategy DSR          | —      | ≤ 0    | —      |
| Max drawdown               | —      | ≤ 20%  | —      |
| Excess return vs benchmark | —      | > 0    | —      |
| Monthly rebalances         | —      | ≥ 12   | —      |
| Avg positions per month    | —      | ≥ 5    | —      |

---

## 9. Decision

- [ ] **SHIP** — all gate criteria passed on dev; final hold-out passed
- [ ] **KILL** — falsification criterion triggered; no second look
- [ ] **ITERATE** — only allowed on train; requires new hypothesis file

---

_Registered: 2026-05-26._
_Signal construction (§3) and falsification criteria (§4) are pre-registered and immutable._
_Strategy Q — 17 prior experiments; DSR gate raised to 0.65._
