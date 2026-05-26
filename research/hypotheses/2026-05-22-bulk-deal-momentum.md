---
slug: bulk-deal-momentum
strategy: bdm
status: killed
registered: 2026-05-22
finalized: 2026-05-23
decision: killed
final: true
type: quantitative_signal
standalone: true
---

# Hypothesis: Bulk Deal Momentum — Large-Block Buyer Continuation in Nifty Midcap 150

## 1. Hypothesis

When a single entity executes a bulk deal BUY (≥ 0.5% of equity outstanding in one
trading session, as defined by SEBI/NSE) in a Nifty Midcap 150 stock, the stock
drifts upward over the following 20 trading days by ≥ 100 bps net of transaction
costs — driven by the buyer's continued off-threshold accumulation and positive
information signalling to the broader market.

## 2. Mechanism

### 2.1 What is a bulk deal?

SEBI mandates that any entity buying or selling ≥ 0.5% of equity outstanding in a
single exchange session (NSE/BSE) must report the transaction to the exchange by
16:00 that day. NSE discloses bulk deals the same evening; the data is publicly
available via the NSE website.

Importantly, 0.5% is only the **disclosure threshold** — not the buyer's target
allocation. A fund building a 3-5% position will trigger one or more bulk deals
early in its accumulation programme, then continue buying in smaller lots (below
0.5%/session) until it reaches its target. The disclosed bulk deal marks the
beginning of the programme, not the end.

### 2.2 Why the edge exists

1. **Incomplete fill hypothesis**: Any entity executing a bulk deal has a larger
   target than the disclosed quantity. They will continue buying (in non-bulk
   increments) over subsequent sessions, creating sustained demand-side pressure.
   The 20-day window captures the tail of a typical institutional accumulation
   programme in Midcap 150 names.

2. **Information asymmetry**: Bulk deal buyers have done significant due diligence
   before committing a multi-crore position. Their willingness to pay market price
   (or negotiate at a small premium for block deals) signals inside-view conviction
   that the stock is undervalued. Other market participants who observe the
   disclosure update their estimates upward.

3. **Float compression**: A large buyer removing 0.5%+ of equity from the floating
   supply tightens the ask side of the order book. In Midcap 150 names — which are
   less liquid than Nifty 50 — this supply reduction has a larger per-unit price
   impact, creating positive price pressure that persists until the buyer's
   programme is complete.

4. **Why Midcap 150 specifically**: Nifty 50 stocks are more heavily covered and
   have deeper order books; bulk deal signals are quickly arbitraged away. Midcap
   150 stocks have median daily turnover of ₹50–200 crore — a bulk deal of
   0.5% equity (typically ₹5–50 crore) represents a meaningful fraction of daily
   turnover, making the supply shock larger and the signal more durable.

5. **Why 20-day hold**: Earlier empirical work on this universe shows short-term
   reversal dominates at ≤ 10 days (previous strategies A and E were killed
   precisely here). At 20 days, momentum signals begin to dominate over mean
   reversion in Midcap 150. The bulk deal signal has a clear mechanism that should
   persist longer than a purely statistical signal, making 20 days a conservative
   exit that avoids both early reversal and late programme completion.

### 2.3 Academic grounding

- **Chakravarty (2001)**: Institutional trades predict next-day returns significantly
  more than retail trades in US equities. The effect is concentrated in the first
  20 trading days post-trade (~80–150 bps for mid-cap stocks).
- **Grinblatt, Titman & Wermers (1995)**: Fund herding — when multiple funds buy
  simultaneously — amplifies the continuation effect. A publicly disclosed bulk
  deal can trigger herding by other institutional participants.
- **Sias, Starks & Titman (2006)**: Institutional demand shocks in mid/small-cap
  stocks predict returns at the 1-month horizon with Sharpe ~0.6–0.8 in
  out-of-sample tests.
- **Patel & Vaidya (2018)**: India-specific study; bulk deal BUY events in NSE
  midcap stocks show ~250 bps abnormal return over the subsequent 20 trading days
  net of market return, concentrated in stocks with pre-existing positive momentum.

## 3. Expected Effect Size

| Metric                                  | Expected value                     | Basis                                                |
| --------------------------------------- | ---------------------------------- | ---------------------------------------------------- |
| Mean net return (T+1 open → T+20 close) | 100–250 bps                        | Patel & Vaidya (2018), scaled to Midcap 150          |
| Win rate                                | 53–60%                             | Consistent with 20-day institutional momentum        |
| Sharpe (per-trade)                      | 0.5–0.8                            | 20-day hold reduces per-day cost drag vs 5-day       |
| Event frequency                         | 80–160 signals/year                | NSE Midcap 150 bulk deals (BUY side), historical avg |
| Hold period                             | 20 trading days ≈ 28 calendar days | T+1 open to T+20 close                               |

**Cost model (pre-registered, immutable):**

- STT + exchange + SEBI + GST = ~0.25%
- Slippage: 0.15% each side (Midcap 150 liquidity, conservative)
- Total round-trip: **0.55%** (55 bps)
- Cost-per-day: 55 / 20 = **2.75 bps/day** — far lower than IDI (11 bps/day) or PEAD (5.5 bps/day)

## 4. Falsification Criterion (pre-registered, immutable)

**Training period: 2015-01-01 → 2023-06-30**
**Dev period: 2023-07-01 → 2024-06-30**
_(Hold-out: 2024-07-01 → present — untouched until dev gate passes)_

The strategy is **killed without appeal** if ANY of the following trigger on dev:

| Criterion                                                       | Kill threshold |
| --------------------------------------------------------------- | -------------- |
| Mean net return (T+1 open → T+20 close, after costs)            | < 100 bps      |
| Win rate (fraction of trades with net positive return)          | < 52%          |
| Sharpe (per-trade return / per-trade std)                       | < 0.5          |
| Deflated Sharpe Ratio (vs n_trials from MLflow)                 | < 0.5          |
| Anti-strategy: SHORT same events over same window               | > 0 bps        |
| Cost-stress: DSR collapse under t-dist(df=4, scale=2×) slippage | > 50% relative |
| Total dev-period signal events                                  | < 40           |

**Notes on thresholds:**

- 100 bps (higher than IDI's 80 bps): the 20-day hold and structural mechanism
  justify a higher threshold. If 20 days of continued accumulation can't produce
  1% net return, the mechanism isn't operating as hypothesised.
- Dev events ≥ 40: one year of Midcap 150 bulk deals (BUY side) should comfortably
  produce 80–160 events; 40 is the floor for statistical validity.
- DSR n_trials: clean MLflow experiment `bdm_v1` starting from run #1.

## 5. Universe and Scope

**Universe:** Nifty Midcap 150 constituents (EQ series only), PIT-correct.

**Event type:** BUY-side bulk deals only.

- SELL-side excluded: seller motivation is ambiguous (portfolio rebalancing, fund
  redemptions, margin calls); not expected to have continuation.
- Both legs (institutional + retail) included in v1: buyer identity is not
  systematically classifiable from NSE bulk deal disclosures without a reference
  entity database. Future v2 can layer in entity classification.

**Entry:** T+1 open (bulk deals disclosed ~16:00 same day; entry at next-day open)
**Exit:** T+20 close (20th trading session after entry)

**Filters applied before entry:**

1. Election filter: skip events within ±30 calendar days of Lok Sabha first phase
   (2019-03-12–2019-06-22, 2024-03-20–2024-07-04).
2. Pledge filter (Strategy C): `is_pledge_flagged(symbol, event_date)` — fail-open
   if no pledge data available.
3. Minimum deal value ≥ ₹1 crore: removes micro-deals where 0.5% equity is very
   small in absolute terms (low-price stocks with tiny float), which may not reflect
   genuine institutional intent.

## 6. Data Required

### 6.1 NSE Bulk Deal Archive (new data source)

**Source:** NSE historical bulk deal data (2004 onwards)

**Download URL pattern:**

```
https://archives.nseindia.com/content/equities/bulk.csv          # current day
# Historical: via NSE website bulk deals section with date parameters, OR
# API-style: https://www.nseindia.com/api/historical/bulk-deals?from=DD-MM-YYYY&to=DD-MM-YYYY
```

**Raw CSV columns:**

| Column                         | Type  | Description                          |
| ------------------------------ | ----- | ------------------------------------ |
| Date                           | str   | Trade date (DD-Mon-YYYY)             |
| Symbol                         | str   | NSE trading symbol                   |
| Security Name                  | str   | Company name                         |
| Client Name                    | str   | Buyer/seller entity name (free text) |
| Buy / Sell                     | str   | "BUY" or "SELL"                      |
| Quantity Traded                | int   | Number of shares traded              |
| Trade Price / Wght. Avg. Price | float | Execution price                      |

**Storage:** `data/lake/bulk_deals/nse_bulk_deals.parquet` (single file, all years)

Schema after ingest:

```
symbol          str        NSE symbol (uppercased)
business_date   date       trade date
client_name     str        buyer/seller name (retained for future entity classification)
side            str        "BUY" or "SELL"
quantity        int        shares traded
price           float      trade price
value_cr        float      approximate deal value in crore (quantity × price / 1e7)
as_of_timestamp datetime   business_date 16:00 IST (PIT boundary)
```

### 6.2 Ingest module

`quant/data/bulk_deals.py` — handles:

- Historical download from NSE archives (2015-01-01 → present)
- Column normalisation, date parsing, deduplication
- Deal value computation
- BUY/SELL classification standardisation

### 6.3 Nifty Midcap 150 PIT membership

`quant/data/nifty_membership.py` (pre-requisite from IDI hypothesis, §7.3).
Required to filter bulk deals to only current Midcap 150 constituents at the
time of the deal (PIT-correct — avoid look-ahead from future inclusions).

Until this module is built, use the static constituent list
`data/lake/midcap150_constituents.csv` as a conservative approximation.

### 6.4 OHLCV for entry/exit pricing

Existing Bhavcopy parquet pipeline — no new data needed.

## 7. Signal Construction

```python
# On each business date T:
# 1. Load bulk deals for date T (disclosed same evening)
# 2. Filter: symbol in Midcap150_members(T), side == "BUY"
# 3. Filter: value_cr >= 1.0 (minimum deal value ₹1 crore)
# 4. Deduplicate: if multiple bulk deals for same (symbol, date), aggregate
#    (count as one event; total quantity = sum; price = weighted avg)
# 5. Apply pledge filter and election filter
# 6. Entry: T+1 open from Bhavcopy OHLCV
# 7. Exit: T+20 close from Bhavcopy OHLCV (20 trading sessions after T+1)
```

**Aggregation rule** for multiple bulk deals on same stock same day:
If multiple entities each buy 0.5%+ on the same day, treat as one event
(stronger signal: multiple buyers = herding). Use the earliest-filed deal's
price for event characterisation, but it is still one trade.

## 8. Integration Points

### 8.1 Strategy C (Pledge Filter)

```python
from quant.data.promoter_pledge import is_pledge_flagged

# In simulate_trades loop, before entry:
if is_pledge_flagged(symbol, event_date):
    pledge_skipped += 1
    continue
```

### 8.2 Hold-out lock

```python
from quant.research.holdout_lock import assert_no_holdout_access

try:
    assert_no_holdout_access(event_date)
except ValueError:
    continue
```

## 9. Code References (to be built)

| File                                          | Purpose                                                       |
| --------------------------------------------- | ------------------------------------------------------------- |
| `quant/data/bulk_deals.py`                    | Download + parse NSE bulk deal archive; write parquet         |
| `quant/data/nifty_membership.py`              | PIT-correct Midcap 150 membership by date                     |
| `quant/strategies/bdm.py`                     | `simulate_trades()`, gate metrics, anti-strategy, cost-stress |
| `quant/research/run.py`                       | Add `--strategy bdm` dispatch                                 |
| `data/lake/bulk_deals/nse_bulk_deals.parquet` | Single consolidated parquet                                   |
| MLflow experiment                             | `bdm_v1` (clean slate, n_trials = 1)                          |

## 10. Execution Plan

1. **Build bulk deal data pipeline** (one-time, ~2 hours):

   ```bash
   python -m quant.data.bulk_deals --start 2015-01-01 --end 2024-06-30
   # Expected: ~300 trading days/year × 9.5 years = ~2,850 files
   # Midcap 150 events per year: ~80–160 BUY events
   ```

2. **Build PIT membership module** (required if not already built from IDI):

   ```bash
   python -m quant.data.nifty_membership --validate
   ```

3. **Implement `quant/strategies/bdm.py`**:

   ```bash
   python -m quant.research.run --strategy bdm --split train
   # Check: event count, fill rate, T+20 exit coverage, mean return in 2015-2023
   ```

4. **Dev gate** (single, irreversible evaluation):

   ```bash
   python -m quant.research.run --strategy bdm --split dev
   # Report all 7 gate criteria from §4
   ```

5. **Hold-out** (only if all 7 dev criteria pass):

   ```bash
   python -m quant.research.run --strategy bdm --split holdout --final
   ```

6. **Paper trading** (only if hold-out passes):
   - Monitor NSE bulk deal disclosures daily (~16:00 IST)
   - Generate signals from Midcap 150 BUY deals ≥ ₹1 crore
   - Enter at next-day open; track 20-session position

## 11. Differentiation from Prior Killed Strategies

| Dimension       | IDI (E — killed)                | BDM (F)                                  |
| --------------- | ------------------------------- | ---------------------------------------- |
| Signal          | Delivery % zscore (statistical) | Disclosed bulk deal (structural event)   |
| Mechanism       | Statistical pattern             | Institutional continuation buying        |
| Hold period     | 5 days                          | 20 days                                  |
| Cost drag/day   | 11 bps                          | 2.75 bps                                 |
| Event frequency | ~925/year                       | ~80–160/year (smaller, higher quality)   |
| Selectivity     | Top 2.5% delivery days          | Actual disclosed positions ≥ 0.5% equity |

The key failure of IDI was zero directional edge — both long and short lost. That implies the delivery % zscore has no predictive content. Bulk deals are directional by construction (someone made a large, deliberate, disclosed purchase) and the continuation mechanism is structural rather than statistical.

## 12. Result

**Dev gate run: 2026-05-23**
**Dev period: 2023-07-01 → 2024-06-30**

| Metric                   | Result     | Gate      | Status  |
| ------------------------ | ---------- | --------- | ------- |
| Raw signal events        | 231        | —         | —       |
| Executed trades          | 173        | ≥ 40      | ✅ PASS |
| Mean net return          | +277.8 bps | ≥ 100 bps | ✅ PASS |
| Win rate                 | 49.1%      | ≥ 52%     | ❌ FAIL |
| Sharpe (per-trade)       | 0.173      | ≥ 0.5     | ❌ FAIL |
| DSR (n_trials=1)         | 0.992      | ≥ 0.5     | ✅ PASS |
| Anti-strategy return     | −387.8 bps | ≤ 0       | ✅ PASS |
| Cost-stress DSR collapse | 0.8%       | ≤ 50%     | ✅ PASS |

**5/7 gates pass, 2/7 fail → KILLED**

**Root cause:** The bulk deal signal produces a strongly positive mean return (277.8 bps)
with a directional edge confirmed by the anti-strategy (−387.8 bps), but the return
distribution is highly right-skewed: a minority of large winners dominate the mean while
49.1% of trades close negative. The mechanism (institutional continuation buying) is
real but unevenly distributed across events — not all bulk deal disclosures precede
sustained accumulation programmes. The Sharpe of 0.173 reflects this noise: the edge
exists in aggregate but is not reliably repeatable at the per-trade level.

The 2024 election period filter removed 53/231 events (23%) from the dev period due to
the 2024 Lok Sabha election window (2024-03-20 → 2024-07-04) overlapping heavily with
the dev period (2023-07-01 → 2024-06-30). This significantly reduces the effective
sample size and may have skewed the win-rate downward by removing a large block of
structurally different events.

## 13. Decision

**KILLED — 2026-05-23**

Win rate (49.1%) and Sharpe (0.173) fail pre-registered thresholds. Per §4, the
strategy is killed without appeal. Parameters may not be adjusted to attempt a re-run.

**Post-mortem for Strategy G design:**

- Mean return is high → the _signal is directional_ but noisy
- Win rate < 50% → need a filter to select only the continuation-likely subset
- Candidate improvements for a new hypothesis (not re-runs of this one):
  1. **Entity quality filter**: classify buyer as institutional (FII/MF/insurance) vs
     retail/HNI. Institutional bulk deals likely have stronger continuation.
  2. **Momentum pre-condition**: require stock to be in positive momentum regime
     (e.g., above 50-day MA, or Patel & Vaidya §2.3 pre-condition) before entry.
  3. **Multiple disclosure filter**: require ≥ 2 bulk deal disclosures within a rolling
     window (herding signal) rather than single events.
     These are hypotheses for Strategy G — they must be pre-registered before any run.

---

_Registered: 2026-05-22 by Ritesh Kant. Falsification criteria (§4) are
pre-registered and immutable. Signal conditions (§5/§7) are immutable once
any MLflow experiment begins. Changes to either after any run begins are
process violations (plan §3.3)._
