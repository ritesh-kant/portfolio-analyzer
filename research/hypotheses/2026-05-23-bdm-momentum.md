---
slug: bdm-momentum
strategy: g
status: killed
registered: 2026-05-23
finalized: 2026-05-23
decision: killed
final: true
type: quantitative_signal
standalone: true
parent_strategy: f
---

# Hypothesis: BDM-Momentum — Bulk Deal Continuation in Nifty Midcap 150 (Momentum Pre-condition)

## 1. Hypothesis

When a single entity executes a bulk deal BUY (≥ 0.5% of equity outstanding in one
trading session) in a Nifty Midcap 150 stock **that is already trading above its
50-day EMA**, the stock drifts upward over the following 20 trading days by ≥ 100 bps
net of transaction costs — driven by the buyer's continued accumulation into an
existing uptrend.

## 2. Relationship to Strategy F (BDM — killed 2026-05-23)

Strategy F (BDM) was killed on the dev gate: mean return 277.8 bps ✅, win rate 49.1%
❌, Sharpe 0.173 ❌.  The directional edge existed (anti-strategy returned −387.8 bps)
but the return distribution was right-skewed: a minority of large winners masked a
majority of losing trades.

**Post-mortem root cause**: the BDM signal mixes two qualitatively different events:
1. Bulk deals in stocks already in positive momentum → buyer is adding to an
   established trend; continued accumulation amplifies existing price pressure.
2. Bulk deals in stocks below their trend average → buyer is positioning
   contra-trend ("bottom fishing"); continuation probability is lower and
   mean-reversion forces oppose the accumulation effect.

Strategy G filters out category 2 with a single additional condition: the stock must
be above its 50-day EMA at the close of signal date T.

## 3. Mechanism

### 3.1 Why the EMA50 pre-condition improves win rate

Patel & Vaidya (2018) found that bulk deal BUY abnormal returns in NSE midcap stocks
are "concentrated in stocks with pre-existing positive momentum."  Stocks below their
medium-term moving average are in a declining or mean-reverting regime where the
mechanical demand shock from the bulk deal is absorbed by sellers who interpret the
disclosure as a contrarian signal ("someone is trying to catch a falling knife"), or
where the stock's momentum already reflects deteriorating fundamentals that the buyer's
due diligence may not have fully captured.

Above the EMA50:
- The stock has already established demand > supply in recent sessions.
- The bulk deal adds to an existing trend rather than fighting mean reversion.
- Information asymmetry is larger: a buyer willing to pay current trend-following
  prices has conviction that upward momentum has further to run.
- Float compression is more impactful: sellers near all-time highs or local highs are
  more reluctant, amplifying the supply-side shock.

### 3.2 Why 50-day EMA specifically

- 50 days ≈ 2.5 months of trading: long enough to confirm a sustained trend, short
  enough to be responsive to recent price action.
- EMA weights recent prices more than SMA, making it more sensitive to recent
  directional moves — appropriate for a signal that acts on current supply/demand.
- Industry convention for medium-term trend classification (widely tracked by
  Indian retail and institutional investors, making it a self-fulfilling focal point).
- 100-day or 200-day MA would impose a longer lookback, reducing the signal's
  responsiveness and potentially missing valid early-stage momentum moves.

### 3.3 Academic grounding

- **Patel & Vaidya (2018)**: Bulk deal BUY returns in NSE midcap stocks are
  concentrated in stocks with pre-existing positive momentum.
- **Jegadeesh & Titman (1993)**: Momentum strategies in US equities earn significant
  abnormal returns, particularly strong over 3–12 month horizons; the 50-day trend
  captures the 2–3 month horizon most relevant to institutional accumulation programmes.
- **Asness, Moskowitz & Pedersen (2013)**: Momentum premium is persistent across asset
  classes and strengthened when combined with value signals; the combination of bulk
  deal (structural event) + momentum (trend regime) mirrors this value+momentum
  combination.

## 4. Falsification Criterion (pre-registered, immutable)

**Training period: 2015-01-01 → 2023-06-30**
**Dev period: 2023-07-01 → 2024-06-30**
*(Hold-out: 2024-07-01 → present — untouched until dev gate passes)*

The strategy is **killed without appeal** if ANY of the following trigger on dev:

| Criterion | Kill threshold |
|-----------|---------------|
| Mean net return (T+1 open → T+20 close, after costs) | < 100 bps |
| Win rate (fraction of trades with net positive return) | < 52% |
| Sharpe (per-trade return / per-trade std) | < 0.5 |
| Deflated Sharpe Ratio (vs n_trials from MLflow) | < 0.5 |
| Anti-strategy: SHORT same events over same window | > 0 bps |
| Cost-stress: DSR collapse under t-dist(df=4, scale=2×) slippage | > 50% relative |
| Total dev-period signal events (after momentum filter) | < 30 |

**Notes on thresholds:**
- Event count threshold reduced from 40 (F) to 30: the momentum filter will reduce
  the raw event count.  30 is the minimum for statistical validity of a binary
  win/loss proportion test at 5% significance.
- All other thresholds identical to F: the mechanism and cost model are the same;
  only the pre-condition changes.

## 5. Signal Construction

```python
# On each business date T:
# 1. Load bulk deals for date T (disclosed same evening)
# 2. Filter: symbol in Midcap150_members(T), side == "BUY", value_cr >= 1.0
# 3. Aggregate (symbol, date) duplicates → one event
# 4. Compute 50-day EMA of close for each symbol using dates [T-74, T] OHLCV
# 5. MOMENTUM FILTER: keep event only if close_T > EMA50_T
# 6. Apply pledge filter and election filter
# 7. Entry: T+1 open; Exit: T+20 close
```

**EMA computation:**
- Span = 50 (standard pandas ewm span parameter, adjust=False)
- Uses close prices from the PIT-correct OHLCV (as_of_timestamp = 18:00 IST ≥ bulk
  deal disclosure at 16:00 IST → no lookahead)
- Requires at least 25 prior trading days of close data to compute a meaningful EMA;
  events without sufficient history are **skipped** (conservative)

## 6. Universe and Scope

**Universe:** Nifty Midcap 150 constituents (EQ series only)
**Event type:** BUY-side bulk deals only (same as F)
**Entry:** T+1 open; **Exit:** T+20 close (same as F)
**Cost model:** 55 bps round-trip (same as F, pre-registered)

**Filters applied before entry (same as F, plus new momentum filter):**
1. Momentum filter (NEW): close_T > EMA50_T (using 50-day span EMA)
2. Election filter: skip events within ±30 calendar days of Lok Sabha first phase
3. Pledge filter: `is_pledge_flagged(symbol, event_date)` — fail-open
4. Minimum deal value ≥ ₹1 crore

## 7. Data Required

All data already on disk:
- Bulk deals: `data/lake/bulk_deals/nse_bulk_deals.parquet` (from Strategy F pipeline)
- OHLCV: Bhavcopy parquet via `pit_loader.load()` (pre-existing)
- Midcap 150: `data/lake/midcap150_constituents.csv` (pre-existing)

**OHLCV lookback:** Must load OHLCV from ~75 trading days before `start` to warm up
the 50-day EMA.  In practice, load from `start - 100 calendar days` to `end`.

## 8. Code References

| File | Purpose |
|------|---------|
| `quant/strategies/bdm_momentum.py` | Strategy G: build_events_momentum(), simulate_trades(), gate metrics |
| `quant/research/run.py` | `--strategy g` dispatch, `run_gate_check_g()` |
| MLflow experiment | `bdm_momentum_v1` (clean slate, n_trials = 1) |

## 9. Differentiation from Prior Strategies

| Dimension | BDM (F — killed) | BDM-Momentum (G) |
|-----------|-----------------|-----------------|
| Signal | Bulk deal BUY ≥ 0.5% | Same |
| Momentum filter | None | close > 50-day EMA |
| Expected win rate | ~49% (observed) | ≥ 52% (hypothesis) |
| Expected Sharpe | ~0.17 (observed) | ≥ 0.50 (hypothesis) |
| Expected events/year | ~80–160 | ~50–100 (filtered) |

## 10. Result

**Dev gate run: 2026-05-23**
**Dev period: 2023-07-01 → 2024-06-30**

| Metric | Result | Gate | Status |
|--------|--------|------|--------|
| Raw events (pre-momentum) | 231 | — | — |
| Momentum-filtered trades | 136 | ≥ 30 | ✅ PASS |
| Mean net return | +463.9 bps | ≥ 100 bps | ✅ PASS |
| Win rate | 52.2% | ≥ 52% | ✅ PASS |
| Sharpe (per-trade) | 0.277 | ≥ 0.5 | ❌ FAIL |
| DSR (n_trials=1) | 1.000 | ≥ 0.5 | ✅ PASS |
| Anti-strategy return | −573.9 bps | ≤ 0 | ✅ PASS |
| Cost-stress DSR collapse | 0.0% | ≤ 50% | ✅ PASS |

**6/7 gates pass, 1/7 fails → KILLED**

**Momentum filter effect:**
Compared to Strategy F (BDM, no momentum filter):
- Win rate: 49.1% → 52.2% ✅ (fixed as hypothesised)
- Mean return: 277.8 → 463.9 bps (dramatically improved)
- Sharpe: 0.173 → 0.277 (improved but still insufficient)
- Anti-strategy: −387.8 → −573.9 bps (stronger directional edge)
- Events: 173 → 136 (momentum filter removed 37 events = 21% of sample)

**Root cause of Sharpe failure:** The momentum filter successfully selects stocks with
higher expected returns, but the per-trade standard deviation remains ~16.7%:

  Sharpe = mean/std = 0.0464 / 0.1674 ≈ 0.277

For a 20-day hold in Nifty Midcap 150, a 16.7% per-trade std is characteristic of
the universe: individual stocks can easily move 20–40% in a 20-day window.  Even
with a strong mean return of 464 bps, the noise overwhelms the signal at the per-trade
level.

**The problem is variance, not bias.** All directional indicators are extremely strong
(anti-strategy −574 bps, DSR 1.000, win rate > 52%) but the distribution has fat
tails in both directions.  To reach Sharpe ≥ 0.5, need either:
  1. A filter that selects only the highest-conviction subset (fewer events,
     much lower variance per trade), OR
  2. A larger mean return relative to the same variance (needs a stronger signal), OR
  3. A shorter hold period (less time for noise to accumulate — but F showed ≤10 days
     means reversion dominates, and 5 days gave Sharpe 0.17).

The residual problem is that "bulk deal in a momentum stock" still captures both
institutional continuation programmes AND one-off tactical buys by HNIs/promoters
that have no follow-through.  The key filter needed for Strategy H: **distinguish
institutional buyers from retail/HNI/promoter buyers.**

## 11. Decision

**KILLED — 2026-05-23**

Sharpe 0.277 fails the pre-registered threshold of ≥ 0.50.  Per §4, the strategy
is killed without appeal.

**Post-mortem for Strategy H design:**
The signal has genuine edge — 6/7 gates pass cleanly.  The missing piece is entity
classification: an institutional bulk buyer (FII, domestic MF, insurance) has a
larger target allocation and is more likely to continue accumulating, producing a
narrower, more consistent return distribution (higher Sharpe).  A retail/HNI buyer
may be a one-off tactical trade with no follow-through, contributing to the fat tails.

Strategy H pre-registration requirements:
- Signal: BDM + momentum pre-condition (same as G) + institutional entity filter
- Entity classifier: keyword-based using client_name field
  (MUTUAL FUND / FII / FPI / INSURANCE / PENSION → institutional)
- Hypothesis must be pre-registered before any experiment run
- New MLflow experiment: bdm_institutional_v1 (n_trials = 1)

---

*Registered: 2026-05-23 by Ritesh Kant.  Falsification criteria (§4) are
pre-registered and immutable.  Signal conditions (§5/§6) are immutable once
any MLflow experiment begins.  Changes to either after any run begins are
process violations (plan §3.3).*
