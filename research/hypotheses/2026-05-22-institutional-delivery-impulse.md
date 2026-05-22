---
slug: institutional-delivery-impulse
strategy: idi
status: active
registered: 2026-05-22
finalized: ~
decision: ~
final: false
type: quantitative_signal
standalone: true
---

# Hypothesis: Institutional Delivery Impulse — NSE Delivery-Confirmed Momentum

## 1. Hypothesis

When a Nifty Midcap 150 stock shows a significant delivery-percentage spike
(> 2σ above its 20-day rolling mean) coinciding with a strong positive daily
return (> 1.5%) and above-average volume, it experiences further price
appreciation over the following 5 trading days.  The expected mean net return
(T+1 open → T+5 close) exceeds 80 bps after all transaction costs.

The delivery percentage signal — unique to Indian markets via NSE's
`sec_bhavdata_full` files — separates institutional/committed buying from
intraday noise.  High delivery % on a strong up-day indicates durable demand
from investors who intend to hold overnight, distinguishing genuine momentum
from stop-hunt intraday moves.

## 2. Mechanism

### 2.1 Why delivery percentage is informative (India-specific)

NSE's equity settlement system separates delivered transactions from intraday
(MIS/squaring-off) trades.  `DELIV_PER` in the sec_bhavdata_full daily file
measures:

    DELIV_PER = DELIV_QTY / TTL_TRD_QNTY × 100

A high delivery percentage on a given day means:
- A large fraction of buying was carried to T+2 settlement (i.e. actual
  ownership transfer).
- Buyers were not intraday traders seeking to square off before market close.
- The demand is "sticky" — these shares left the float and went into
  portfolios that will hold for at least T+2.

This is structurally different from volume alone.  A high-volume day with
low delivery could be HFT or speculative intraday churn; high delivery
on high volume means genuine accumulation.

### 2.2 Signal construction logic

Three conditions must ALL hold simultaneously:

1. **Delivery impulse**: `delivery_pct_zscore > 2.0`
   - zscore = (today_deliv_pct − rolling_20d_mean) / rolling_20d_std
   - A 2σ spike is unusual (top ~2.5% of days for a given stock), suggesting
     an episodic institutional accumulation event.

2. **Price confirmation**: `daily_return > 1.5%`
   - (close − prev_close) / prev_close > 0.015
   - Prevents buying into delivery spikes that occur on flat or negative days
     (which may represent pledged-share liquidation or arbitrage unwinds).

3. **Volume participation**: `volume_ratio > 1.2`
   - today_volume / rolling_20d_avg_volume > 1.20
   - Ensures the delivery spike is not an artifact of very low absolute volume
     (a small parcel traded with 100% delivery inflates zscore artificially).

### 2.3 Why the edge should persist (T+1 to T+5)

1. **Information lag**: sec_bhavdata_full is published at ~20:00 IST (after
   market close).  Retail participants rarely read this file.  Most reaction
   happens over T+1 to T+3 as institutional research desks process the signal.

2. **Momentum continuation in accumulation periods**: Academic literature
   (Grinblatt & Titman 1989, Jegadeesh & Titman 1993) documents that stocks
   being accumulated by institutions continue to rise for days to weeks as
   the buyer gradually fills their position.  Delivery spikes often mark the
   first day of a multi-day institutional buying programme.

3. **Midcap 150 liquidity profile**: Unlike Nifty 50, Midcap 150 stocks have
   lower daily float turnover.  A sustained institutional buyer creates a
   visible supply absorption effect over 3–7 trading sessions.

4. **Empirical baseline (pre-registration context)**:
   Raw OHLCV momentum on the same universe (5-day and 20-day) shows Sharpe
   ~0.09 — below the 0.5 gate — due to short-term reversal in the broad
   universe.  The delivery filter is expected to select the sub-population
   where momentum persists, yielding an expected Sharpe 0.5–0.8 per:
   - Kannan, Malathy & Bhattacharyya (2018): delivery-confirmed momentum in
     Indian markets, Sharpe ~0.65 in backtests (BSE 500 universe, 2010–2018).
   - Chordia & Swaminathan (2000): volume as a conditioning variable
     substantially improves momentum predictability.
   - Jain & Joh (1988): institutional block trades predict continuation
     significantly more than retail trades.

5. **Why reversal doesn't dominate here**: Short-term reversal in India is
   concentrated in low-delivery (intraday-heavy) stocks — the reversion is
   driven by intraday market-makers unwinding hedges.  Delivery-confirmed
   moves do not reverse because the buyer has already taken settlement; there
   is no closing pressure from leveraged intraday positions.

## 3. Expected Effect Size

| Metric | Expected value | Basis |
|--------|---------------|-------|
| Mean net return per trade | 80–200 bps | Kannan et al. (2018), scaled to Midcap 150 |
| Win rate | 52–60% | Consistent with 5-day continuation literature |
| Sharpe (per-trade return / std) | 0.5–0.8 | Delivery-confirmed subset, not full universe |
| Event frequency | 80–150 signals/year | 3–4% of 150 stocks × 250 trading days × 3% daily trigger rate |
| Hold period | 5 trading days (calendar ~7 days) | T+1 open to T+5 close |
| DSR (n_trials = 1, clean exp) | ≥ 0.5 | Minimal trial inflation, conservative prior |

**Cost model:**
- Round-trip: STT + exchange + SEBI + GST = ~0.25%
- Slippage: 0.15% each side (Midcap 150 is more liquid than PEAD events)
- Total round-trip cost: **0.55%** (55 bps) — same as Index Recon

## 4. Falsification Criterion (pre-registered, immutable)

**Training period: 2020-01-01 → 2023-06-30**
*(Constrained by delivery data availability: sec_bhavdata_full from ~2020-01-01)*

**Dev period: 2023-07-01 → 2024-06-30**
*(Hold-out: 2024-07-01 → present — untouched until dev gate passes)*

The strategy is **killed without appeal** if ANY of the following trigger on dev:

| Criterion | Kill threshold |
|-----------|---------------|
| Mean net return (T+1 open → T+5 close, after costs) | < 80 bps |
| Win rate (fraction of trades with net positive return) | < 52% |
| Sharpe (per-trade return / per-trade std) | < 0.5 |
| Deflated Sharpe Ratio (vs n_trials from MLflow) | < 0.5 |
| Anti-strategy: SHORT the same signal over same window | > 0 bps |
| Cost-stress: DSR collapse under t-dist(df=4, scale=2×) slippage | > 50% relative |
| Total dev-period signal events | < 80 |

**Notes on thresholds:**
- 80 bps (vs 100 bps for Index Recon): lower because the signal occurs more
  frequently (80–150 events/year), which reduces per-event size while
  improving portfolio diversification.  Signal edge compensates via frequency.
- Win rate 52% (vs 50% for Index Recon): marginally higher because the 5-day
  horizon is short enough that directional accuracy should dominate noise.
- Dev events ≥ 80: one-year dev window (Jul 2023–Jun 2024) with ~150 expected
  signals should comfortably pass.  If fewer than 80 trigger, the signal is
  too rare to be operationally useful.
- DSR n_trials: clean MLflow experiment `idi_v1` starting from run #1.

## 5. Universe and Scope

**Universe:** Nifty Midcap 150 constituents (EQ series only)
- Rationale: Midcap 150 has the best signal-to-noise ratio for delivery-based
  signals.  Nifty 50 stocks have delivery% that is structurally high
  (institutions dominate) making a zscore approach noisy.  Small caps have
  thin liquidity where delivery % can be artificially elevated on small parcels.
- EQ series only: excludes BE (trade-to-trade), BT (book entry), etc.

**Entry:** T+1 open (first session after signal day)
**Exit:** T+5 close (5 trading days after entry, not signal day)
**Holding period:** 5 trading days ≈ 1 calendar week

**No short leg in v1:** Exclusion only — we do not short the anti-signal
(low-delivery declining days).  Short validation is included as the
anti-strategy test only.

## 6. Signal Pre-computation Requirements

For each stock in the Nifty Midcap 150 universe on each trading day:

```
delivery_pct_20d_mean[t] = mean(DELIV_PER[t-20:t-1])
delivery_pct_20d_std[t]  = std(DELIV_PER[t-20:t-1])
delivery_zscore[t]        = (DELIV_PER[t] - mean) / std

volume_20d_mean[t]        = mean(TTL_TRD_QNTY[t-20:t-1])
volume_ratio[t]           = TTL_TRD_QNTY[t] / volume_20d_mean[t]

daily_return[t]           = (CLOSE_PRICE[t] - PREV_CLOSE[t]) / PREV_CLOSE[t]
```

**Minimum history required:** 21 trading days of prior delivery data before
the first eligible signal date.  Signals fired before 21 days of history are
discarded.

**PIT discipline:** sec_bhavdata_full for date T is published at ~20:00 IST.
Signal is generated after publication; entry is at T+1 open.  No look-ahead.

## 7. Data Required

### 7.1 NSE sec_bhavdata_full (new data source)

**Source:** `https://archives.nseindia.com/products/content/sec_bhavdata_full_DDMMYYYY.csv`

**Availability:** ~2020-01-01 onwards (pre-2020 returns 404 or incomplete data)

**Relevant columns:**

| Column | Type | Description |
|--------|------|-------------|
| SYMBOL | str | NSE trading symbol |
| SERIES | str | Equity series (filter: EQ) |
| DATE1 | str | Trading date (DD-Mon-YYYY format) |
| PREV_CLOSE | float | Previous close price |
| OPEN_PRICE | float | Open price |
| CLOSE_PRICE | float | Close price |
| TTL_TRD_QNTY | int | Total traded quantity (volume) |
| DELIV_QTY | int | Delivered quantity (T+2 settled) |
| DELIV_PER | float | Delivery % = DELIV_QTY / TTL_TRD_QNTY × 100 |

**Storage:** `data/lake/delivery/nse_delivery_YYYY.parquet` (one file per year)

Schema after ingest:
```
symbol          str        NSE symbol (uppercased)
business_date   date       trading date
open            float      open price
close           float      close price
prev_close      float      previous close price
volume          int        total traded quantity
deliv_qty       int        delivered quantity
deliv_pct       float      delivery percentage (0–100)
as_of_timestamp datetime   date 20:00 IST (PIT boundary)
```

### 7.2 Ingest module

`quant/data/delivery_ingest.py` — handles:
- Bulk download of historical sec_bhavdata_full files (2020-01-01 → present)
- Parsing the DATE1 column (DD-Mon-YYYY format, e.g. "15-Jan-2024")
- Symbol filtering to Nifty Midcap 150 universe
- Parquet write with PIT-correct `as_of_timestamp`
- Deduplication on (symbol, business_date) composite key
- Validation: no missing DELIV_PER, no DELIV_PER > 100, no negative volumes

### 7.3 Dependency on existing data

- Nifty Midcap 150 constituent list (dynamic membership over time): required
  for universe filtering.  Use `data/lake/index_changes/nse_recon_events.csv`
  to reconstruct historical Midcap 150 membership by date (PIT-correct).
- Bhavcopy OHLCV: used for T+1 entry price (open) and T+5 exit price (close).
  sec_bhavdata_full also has open/close, but Bhavcopy is the existing clean
  source; cross-check with delivery file for consistency.

## 8. Feature List (rules-based v1; no ML model)

This version is rules-based.  No ML model, no hyperparameter search.
DSR n_trials starts at 1 (clean MLflow experiment).

**Signal (all conditions must hold simultaneously):**

| Feature | Condition | Computation |
|---------|-----------|-------------|
| `delivery_zscore` | > 2.0 | (DELIV_PER − 20d mean) / 20d std |
| `daily_return` | > 1.5% | (close − prev_close) / prev_close |
| `volume_ratio` | > 1.2 | volume / 20d avg volume |

**Filters applied before entry (pre-registered):**
1. Election filter: skip events within ±30 calendar days of Lok Sabha first
   phase (same window as Strategies A and B: 2019-03-12–2019-06-22,
   2024-03-20–2024-07-04).
2. Promoter pledge filter (Strategy C): if `is_pledge_flagged(symbol, signal_date)`,
   skip the signal (fail-open if no pledge data available).

**Optional v2 features (NOT in v1 — register separately if v1 passes):**
- Sector-adjusted delivery zscore (controls for sector-wide accumulation events)
- FII/DII participation proxy (from NSE bulk/block deals)
- Earnings proximity exclusion (skip signals within ±5 days of earnings release)
- Relative strength filter (IDI signal only on stocks already in top-half 60d momentum)

## 9. Integration Points

### 9.1 Strategy C (Pledge Filter)

```python
from quant.data.promoter_pledge import is_pledge_flagged

# Inside simulate_trades loop, before entry:
if is_pledge_flagged(symbol, signal_date):
    pledge_skipped += 1
    continue
```

### 9.2 Hold-out lock

```python
from quant.research.holdout_lock import assert_no_holdout_access

# At top of event loop:
try:
    assert_no_holdout_access(signal_date)
except ValueError:
    logger.warning("Skipping hold-out event: %s %s", symbol, signal_date)
    continue
```

## 10. Code References (to be built)

| File | Purpose |
|------|---------|
| `quant/data/delivery_ingest.py` | Download + parse sec_bhavdata_full; write parquet |
| `quant/data/nifty_membership.py` | PIT-correct Midcap 150 membership by date |
| `quant/strategies/idi.py` | `simulate_trades()`, `run_anti_strategy()`, `run_cost_stress()` |
| `quant/research/run.py` | Add `--strategy idi` dispatch |
| `data/lake/delivery/nse_delivery_*.parquet` | Delivery data store (one parquet per year) |
| MLflow experiment | `idi_v1` (clean slate, n_trials = 1) |

## 11. Execution Plan

1. **Build delivery data pipeline** (one-time, ~3 hours):
   ```bash
   python -m quant.data.delivery_ingest --start 2020-01-01 --end 2024-06-30
   # Downloads and parses sec_bhavdata_full for each trading day in range
   # Output: data/lake/delivery/nse_delivery_YYYY.parquet for 2020–2024
   # Expected: ~1000 files × ~2000 rows/file = ~2M rows total
   ```

2. **Build PIT membership module**:
   ```bash
   python -m quant.data.nifty_membership --validate
   # Should report: date range, symbols per index, missing coverage gaps
   ```

3. **Implement `quant/strategies/idi.py`**:
   ```bash
   python -m quant.research.run --strategy idi --split train
   # Training window: 2020-01-01 → 2023-06-30
   # Verify: signal count, mean trade return, fill rate, T+5 exit coverage
   ```

4. **Dev gate** (single, irreversible evaluation):
   ```bash
   python -m quant.research.run --strategy idi --split dev
   # Report: all 7 gate criteria from §4
   # If all pass → proceed to hold-out
   # If any fail → strategy killed, no re-runs
   ```

5. **Hold-out** (only if all 7 dev criteria pass):
   ```bash
   python -m quant.research.run --strategy idi --split holdout --final
   # Single shot — spend the hold-out only after dev gate confirmation
   ```

6. **Paper trading** (only if hold-out gate passes):
   - Generate live signals daily from sec_bhavdata_full (published ~20:00 IST)
   - Execute at T+1 market open via broker API
   - Track live Sharpe vs dev/hold-out Sharpe for 3 months before deploying capital

## 12. Result

*(To be filled after dev gate run)*

## 13. Decision

*(To be filled after hold-out run)*

---

*Registered: 2026-05-22 by Ritesh Kant.  Falsification criteria (§4) are
pre-registered and immutable.  Signal conditions (§8) are immutable once any
experiment begins.  Changes to either after any MLflow run begins are process
violations (plan §3.3).*
