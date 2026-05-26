---
slug: cross-sectional-momentum
strategy: cs_momentum
status: registered
registered: 2026-05-25
finalized: ""
decision: ""
final: false
type: portfolio_strategy
standalone: true
---

# Hypothesis: Cross-Sectional Momentum on Nifty Midcap 150

## 1. Mechanism

**What it does (plain language):** Every month, rank all 150 midcap stocks by
how much they went up over the past year (excluding the most recent month). Buy
the top 15. Rebalance next month. Repeat.

**Why this works economically:**

1. **Analysts are slow to upgrade.** When a stock starts doing well, sell-side
   analysts take months to raise their ratings and price targets. This creates
   a lag between business performance and market recognition → the upward trend
   continues after it starts.

2. **Institutional funds chase performance.** Active fund managers are measured
   against benchmarks quarterly. A stock that performed well last quarter attracts
   new allocations next quarter → self-reinforcing flow.

3. **Retail under-reacts to trends.** Individual investors anchor to their
   purchase price and are slow to buy "already expensive" stocks even when the
   business keeps improving.

**Why skip the most recent month (12-1 and not 12-0):**
The very last month tends to *reverse* — stocks that went up last month often
pull back slightly in the current month. This is called the "short-term reversal
effect" (documented by Jegadeesh 1990). Including it adds noise and hurts the
signal. The "skip one month" convention is standard in academic momentum research
since Jegadeesh-Titman 1993.

**Why Midcap 150 specifically:**
- Lower analyst coverage than Nifty 50 → slower information dissemination → drift lasts longer
- Enough liquidity to execute at ₹50L scale
- Higher return dispersion than large caps → stronger cross-sectional signal

**Academic support:**
- Jegadeesh & Titman (1993): Original 12-1 momentum paper on US equities; later
  replicated globally including India
- NSE paper (2014–2021): Physical momentum portfolios on NSE 500 outperform Nifty 50
- SSRN 5744965 (Arnav Kumar, 2012–2025): Reproducible vol-scaled long-only momentum
  on Nifty-50 shows out-of-sample Sharpe 2.9 (note: Nifty-50 survivorship bias
  likely inflates this; realistic expectation for Midcap 150 is Sharpe 0.8–1.5)

---

## 2. Expected Effect Size

- **Direction:** long-only (no shorting — not possible in cash delivery at Zerodha)
- **Expected excess return vs Nifty Midcap 150 TRI:** +5–12% per year
- **Gross Sharpe (before costs):** 1.2–2.0
- **Net Sharpe (after ~2.5% annual cost drag at 30–50% monthly turnover):** 0.8–1.5
- **MaxDD with vol-target overlay:** targeted ≤ 20% (raw momentum without overlay: ~40%)

Cost reference (Zerodha 2026, delivery):
```
Per round-trip on ₹50K position:
  STT:       10 bps (₹50)      — sell-side only
  Stamp:      1.5 bps (₹7.50)  — buy-side only
  Exchange:   0.65 bps (₹3.25) — both sides
  DP charge: ₹15.34 flat       — sell day, per stock
  Slippage:  30 bps (est.)     — 15 bps per side, conservative for midcap
  Total:     ~45 bps round-trip
```

Annual drag estimate at 40% monthly turnover:
```
~6 full portfolio turns/year × 45 bps = 270 bps ≈ 2.7% per year
```

At 8–12% gross alpha, net = 5–9%. Feasible.

---

## 3. Pre-Registered Signal Construction (IMMUTABLE after this commit)

```python
# On the last trading day of each month (t_end):

# Step 1: Compute 12-1 momentum score for each symbol in Midcap 150 universe
# t_end    = last trading day of current month (signal date, PIT-correct)
# t_skip   = t_end - 21 trading days  (skip most recent month)
# t_start  = t_end - 252 trading days (12 months ago)
#
# A stock is eligible only if it has price data on BOTH t_start and t_skip.
# Missing either → excluded from this month's ranking.

momentum_score = (close[t_skip] / close[t_start]) - 1.0

# Step 2: Rank eligible stocks descending by momentum_score
# Step 3: Select top 10% = top 15 stocks (150 × 0.10 = 15)
# Step 4: Buffer rule — a stock in the portfolio stays until it drops below top 20%
#         (= top 30 stocks). This reduces unnecessary turnover.
# Step 5: Entry at OPEN of first trading day of the following month (NOT end-of-month close)

# Vol-target overlay (applied to portfolio gross exposure, not per-stock):
# realized_vol = annualized std of last 20 portfolio daily returns
# scalar = min(1.0, VOL_TARGET / realized_vol)   # never use leverage
# weight_per_stock = scalar / n_positions        # equal weight × scaled exposure
VOL_TARGET = 0.15  # 15% annualized portfolio volatility target
TOP_PCT    = 0.10  # top 10% of ranked universe
BUFFER_PCT = 0.20  # stay in portfolio until dropping below top 20%
```

**What is NOT allowed to change after registration:**
- Lookback windows (252 / 21 trading days)
- Universe percentile thresholds (10% / 20%)
- Vol-target level (15%)
- Entry timing (first-day-of-month open)
- Cost model used in simulation

---

## 4. Falsification Criteria (LOCKED — cannot be relaxed after running)

**Dev period gate (2023-07-01 → 2024-06-30). ALL must pass:**

| Criterion | Kill if... | Notes |
|-----------|-----------|-------|
| Dev DSR (n_trials from MLflow) | < 0.6 | Raised from plan default 0.5 due to 16 prior experiments |
| Dev Sharpe (net of costs) | < 0.7 | Annualized, monthly returns |
| Sharpe under 2× cost-stress | < 0.5 | Slippage drawn from t-dist(df=4, scale=2×nominal) |
| Capacity-adjusted DSR @ ₹50L | < 0.3 | Linear impact model at ₹3.3L per stock |
| Anti-strategy DSR (bottom decile) | > 0 | If shorting losers also makes money → not a signal |
| Max drawdown (vol-targeted portfolio) | > 20% | Measured on dev period equity curve |
| Excess return vs Nifty Midcap 150 TRI | ≤ 0 | Net alpha, not gross |
| Monthly rebalances in dev | < 12 | Statistical significance floor |

**Hold-out gate (2024-07-01 → present). Single shot:**

| Criterion | Kill if... |
|-----------|-----------|
| DSR | < 0.4 |
| Excess return vs Nifty Midcap 150 TRI | ≤ 0 |
| Max drawdown | > 25% |

**This file becomes immutable once status = "registered". Results go in §7.**

---

## 5. Data Needed

All data is already on disk. No new acquisition required.

| Source | Path | Use |
|--------|------|-----|
| NSE Bhavcopy OHLCV | `data/lake/nse_bhavcopy/*.parquet` | Prices for momentum score + simulation |
| Midcap 150 constituents | `data/lake/midcap150_constituents.csv` | Universe definition |
| Nifty Midcap 150 TRI | Derived from Bhavcopy (equal-weight benchmark proxy) | Alpha computation |

**PIT discipline:**
- Momentum score computed from OHLCV close prices, available same evening
- Signal date = last trading day of month (t_end) at market close
- Entry = next business day's open → no look-ahead bias
- Constituent list: currently static CSV; for production, use point-in-time versioned list

**Important caveat on static constituents:**
The `midcap150_constituents.csv` is a snapshot of *current* constituents, not
historical. This introduces mild survivorship bias (companies that survived to
today look slightly better than the true historical universe). For dev/holdout
this is acceptable (the recent past is what matters for live trading). For a
full 2015–2023 train backtest, this understates costs and overstates performance.
Flag this in the gate report; don't adjust the gate for it.

---

## 6. Train / Dev / Hold-out Split

Standard plan splits apply:
- **Train:** 2015-01-01 → 2023-06-30 (hyperparameter exploration, NOT gate)
- **Dev:** 2023-07-01 → 2024-06-30 (gate evaluation — 12 monthly rebalances)
- **Hold-out:** 2024-07-01 → present (NEVER touched until status = final)

Note: momentum requires a 12-month lookback, so the *first signal date* in the
dev period is 2023-07-31 (using prices back to 2022-07). The Bhavcopy covers
back to 2015, so this is fully satisfied.

---

## 7. Code References

| File | Purpose |
|------|---------|
| `quant/strategies/cs_momentum.py` | Signal construction, simulation, gate metrics |
| `quant/research/run.py` | `--strategy momentum` dispatch |
| MLflow experiment | `cs_momentum_v1` (n_trials starts at 1, fresh experiment family) |
| Hypothesis hash | (computed at finalization by holdout_lock.py) |

---

## 8. Result (filled after dev gate run)

| Criterion | Result | Gate | Status |
|-----------|--------|------|--------|
| DSR | — | ≥ 0.6 | — |
| Sharpe (net of costs) | — | ≥ 0.7 | — |
| Sharpe under cost-stress | — | ≥ 0.5 | — |
| Capacity DSR @ ₹50L | — | ≥ 0.3 | — |
| Anti-strategy DSR | — | ≤ 0 | — |
| Max drawdown (vol-targeted) | — | ≤ 20% | — |
| Excess return vs benchmark | — | > 0 | — |
| Monthly rebalances in dev | — | ≥ 12 | — |

---

## 9. Decision

- [ ] **SHIP** — all gate criteria passed on dev; final hold-out passed
- [ ] **KILL** — falsification criterion triggered; no second look
- [ ] **ITERATE** — only allowed on train; requires new hypothesis file

---

*Registered: 2026-05-25.*
*Signal construction (§3), falsification criteria (§4) are pre-registered and immutable.*
*First genuinely new mechanism after A–O kill log. DSR gate raised to 0.6 due to 16 prior experiments.*
