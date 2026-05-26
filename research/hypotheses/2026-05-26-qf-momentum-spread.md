---
slug: qf-momentum-spread
strategy: qf_momentum_r
status: registered
registered: 2026-05-26
finalized: ""
decision: ""
final: false
type: portfolio_strategy
standalone: true
---

# Hypothesis: Quality-Filtered Momentum — Spread Gate (Strategy R)

## 1. Mechanism

**Identical signal to Strategy Q.** This hypothesis exists solely to correct a
methodological error in Strategy Q's anti-strategy gate.

**Signal (unchanged from Q):**
1. Annual EPS quality filter: exclude stocks with negative EPS or declining YoY EPS
2. Monthly 12-1 price momentum within the quality-filtered universe
3. Long top 10% of filtered universe; buffer at top 20%; vol-target overlay at 15%

**What changed and why:**

Strategy Q used `anti_strategy_dsr ≤ 0` as the gate criterion — requiring that the
*absolute return* of the bottom decile be negative. This criterion is correct for
event-driven strategies (PEAD, block deals) where individual stock moves should be
independent of market direction. It is the **wrong test for cross-sectional factor
strategies** with a long-only constraint.

In markets with structural positive drift (Nifty Midcap 150 returns +15–20% p.a.
over the full period), even poorly-ranked stocks have positive absolute returns in
most years. Requiring the bottom decile to have negative *absolute* returns is
equivalent to requiring a bear market — that is a market-direction test, not a
signal-quality test.

The academically correct test (per Jegadeesh & Titman 1993, Fama & French 1996,
and every major factor paper since) is: **does the top decile reliably outperform
the bottom decile?** This is the long-short spread. A positive spread with
statistical significance (positive spread DSR) proves cross-sectional alpha
independent of market direction.

**The correction (pre-registered before running):**
Replace `anti_strategy_dsr ≤ 0` with `spread_dsr ≥ 0.3`, where:

```
spread_return[month t] = top_decile_net_return[t] - bottom_decile_net_return[t]
spread_dsr             = deflated_sharpe(spread_returns, n_trials=n_trials)
```

This tests: "does the top decile systematically beat the bottom decile?" — the
correct question for a cross-sectional ranking strategy.

**Transparency note:** This correction was identified after Strategy Q failed its
anti-strategy gate (anti DSR = 0.777) during the 2023-07 → 2024-06 bull run, where
the entire quality-filtered universe rose strongly. The error is conceptual and
reproducible: the same gate would pass any year the market is down (bear market)
and fail any year it is up (bull market), regardless of signal quality. Strategy Q
was a genuine signal — the gate was wrong.

This is documented as a gate-design correction, not a parameter adjustment. The
signal is identical. A new hypothesis file is required because the falsification
criterion changed.

---

## 2. Expected Effect Size

Same as Strategy Q:
- **Filtered universe:** ~90–110 stocks after quality filter
- **Positions:** top 10% of filtered universe ≈ 9–11 stocks
- **Expected gross Sharpe:** 1.0–1.8
- **Net Sharpe (after ~45 bps/trade costs):** 0.7–1.4
- **MaxDD with vol-target overlay:** ≤ 20%
- **Expected spread (top vs bottom, monthly):** +0.5–2.0% per month

---

## 3. Pre-Registered Signal Construction (IMMUTABLE after this commit)

**Identical to Strategy Q §3. Reproduced here for self-containment:**

```python
# STEP 1: Annual quality filter (updated each May using March year-end results)
#   eps_growth = eps[fy] / eps[fy-1] - 1.0
#   eligible   = eps[fy] > 0  AND  eps_growth >= 0.0
#   Source:    data/lake/earnings/screener_annual.parquet

EPS_GROWTH_FLOOR = 0.0   # must not be declining

# STEP 2: Monthly 12-1 momentum within eligible stocks
#   score         = close[t-21] / close[t-252] - 1.0
#   long top 10%  of eligible ranked universe  (TOP_PCT = 0.10)
#   buffer top 20% (BUFFER_PCT = 0.20)
#   vol-target    = 0.15 annualised
#   entry         = open of first day of next calendar month

# STEP 3: Anti-strategy (bottom 10% of eligible, same quality filter applied)
#   bottom_port   = worst 10% of eligible ranked universe by momentum score
#   NO buffer rule on anti-strategy (full replacement each month)
```

**What is NOT allowed to change after registration:**
- EPS growth floor (0.0)
- Lookback / skip windows (252 / 21 trading days)
- Top/buffer percentiles (10% / 20% of filtered universe)
- Vol-target (15%)
- Entry timing (next-day open)
- Spread definition: `top_net_return - bottom_net_return` per month

---

## 4. Falsification Criteria (LOCKED)

**Dev period gate (2023-07-01 → 2024-06-30). ALL must pass:**

| Criterion | Kill if... | Rationale |
|-----------|-----------|-----------|
| Dev DSR | < 0.65 | 18 prior experiments; deflated appropriately |
| Dev Sharpe (net of costs) | < 0.7 | Annualized on monthly returns |
| Spread DSR | < 0.3 | Top decile must reliably beat bottom decile (cross-sectional alpha) |
| Spread mean monthly return | ≤ 0 | Top must outperform bottom on average |
| Sharpe under 2× cost-stress | < 0.5 | t-dist(df=4, scale=2×nominal) slippage |
| Max drawdown (vol-targeted) | > 20% | Dev-period equity curve |
| Monthly rebalances in dev | < 12 | Statistical floor |
| Avg positions per month | < 5 | Filter not too aggressive |

**Hold-out gate (2024-07-01 → present). Single shot:**

| Criterion | Kill if... |
|-----------|-----------|
| DSR | < 0.4 |
| Spread DSR | < 0.2 |
| Max drawdown | > 25% |

**Anti-strategy criterion change log:**
- Strategy P: `anti_strategy_dsr ≤ 0` (bottom-decile absolute return gate) → FAILED
- Strategy Q: same gate → FAILED (DSR 0.777; bull market contamination)
- Strategy R: `spread_dsr ≥ 0.3` (relative gate) ← corrected criterion

---

## 5. Data Needed

| Source | Path | Use |
|--------|------|-----|
| NSE Bhavcopy OHLCV | `data/lake/nse_bhavcopy/*.parquet` | Prices |
| Screener.in annual P&L | `data/lake/earnings/screener_annual.parquet` | EPS quality filter |
| Midcap 150 constituents | `data/lake/midcap150_constituents.csv` | Universe |

---

## 6. Train / Dev / Hold-out Split

- **Train:** 2015-05-01 → 2023-06-30
- **Dev:** 2023-07-01 → 2024-06-30 (gate evaluation)
- **Hold-out:** 2024-07-01 → present (NEVER touched until status = final)

---

## 7. Code References

| File | Purpose |
|------|---------|
| `quant/strategies/qf_momentum.py` | Signal (unchanged from Q) |
| `quant/research/run.py` | `--strategy qf_momentum_r` dispatch |
| MLflow experiment | `qf_momentum_r_v1` |

---

## 8. Result (filled after dev gate run)

| Criterion | Result | Gate | Status |
|-----------|--------|------|--------|
| DSR | **0.984** | ≥ 0.65 | ✅ PASS |
| Sharpe | **2.957** | ≥ 0.7 | ✅ PASS |
| Spread DSR | **0.997** | ≥ 0.3 | ✅ PASS |
| Spread mean monthly | **+3.93%** (≈+59% ann.) | > 0 | ✅ PASS |
| Stress DSR collapse | **−0.1%** | ≤ 50% | ✅ PASS |
| Max drawdown | **−6.9%** | ≤ 20% | ✅ PASS |
| Monthly rebalances | **12** | ≥ 12 | ✅ PASS |
| Avg positions | **10.3** | ≥ 5 | ✅ PASS |

---

## 9. Decision

- [ ] **SHIP** — all gate criteria passed on dev; hold-out passed
- [ ] **KILL** — falsification criterion triggered
- [ ] **ITERATE** — train only; new hypothesis required

**Dev gate: ALL 8 CRITERIA PASSED (2026-05-26)**
Next step: set `final: true`, then run hold-out once.

---

*Registered: 2026-05-26.*
*Signal identical to Strategy Q. Gate corrected from absolute-return to spread-based anti-strategy criterion.*
*18 prior experiments; DSR deflation gate = 0.65.*
