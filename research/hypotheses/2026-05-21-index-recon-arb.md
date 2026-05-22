---
slug: index-recon-arb
strategy: index_recon
status: killed
registered: 2026-05-21
finalized: 2026-05-22
holdout_run: 2026-05-22
decision: killed
hypothesis_hash: ""
final: true
---

# Hypothesis: Index Reconstitution Arbitrage — Nifty Index Front-Running

## 1. Hypothesis

When NSE announces additions to the Nifty 50, Nifty Next 50, or Nifty
Midcap 150 indices, the added stock drifts upward between announcement
and the effective rebalancing date by ≥ 150 bps net of transaction costs —
driven by mechanical demand from index-tracking funds that must buy at
(or before) the effective-date close.

## 2. Mechanism

Index-tracking funds (ETFs + passive mutual funds) with mandates to
replicate a Nifty index must hold the new constituent from the effective
date.  Because they must buy at or before the effective-date close and
cannot anticipate the change before NSE's public announcement, they face
inelastic buying pressure concentrated in a narrow time window.  Informed
front-runners buy on announcement day; passive funds buy at effective-date
close regardless of price — creating a predictable demand cliff.

**Why this is structural, not behavioural:**
The buying is not optional.  ETF creation/redemption mechanics and SEBI
tracking-error limits force Indian passive funds to execute before the
close of the effective date.  Unlike PEAD (which relies on under-reaction),
recon arb is driven by a rule-mandated institutional transaction.  The
mechanism does not decay as long as passive AUM grows (which it is —
Indian ETF AUM has grown 5× since 2019).

**Academic grounding:**
- Harris & Gurel (1986), Shleifer (1986): S&P 500 additions show 3–5%
  excess returns over announcement window.
- Beneish & Whaley (1996): "S&P game" — more sophisticated front-running
  compresses but does not eliminate the effect.
- Sehgal & Rajput (2012): Nifty 50 reconstitution effect confirmed; ~3–5%
  abnormal return for inclusions in the announcement-to-effective window.
- Chen, Noronha & Singal (2004): Effect is stronger for smaller, less liquid
  stocks — consistent with Midcap 150 having larger per-event returns than
  Nifty 50.

## 3. Expected Effect Size

- **Direction**: long inclusions only (no short exclusions in v1)
- **Indices**: Nifty 50, Nifty Next 50, Nifty Midcap 150
- **Holding period**: announcement T+1 open → effective date close (~15–25 trading days)
- **Expected gross return per inclusion**: 200–500 bps (3–5× wider cost model than PEAD events)
- **Expected net return after costs**: 150–400 bps
  - Round-trip cost model (same as PEAD): STT + exchange fees + SEBI fee +
    GST + 0.20% slippage both sides (higher slippage assumed for less liquid
    Midcap 150 names near announcement) ≈ 0.55% total
- **Estimated event count**: 4–8 Nifty 50/Next 50 changes per semi-annual
  review × 2 reviews + 8–15 Midcap 150 changes × 2 = **30–50 events/year**
- **Sharpe (per-trade return / std)**: expected ≥ 0.8 (lower variance than PEAD
  because demand is mechanical, not behavioural)

## 4. Falsification Criterion (pre-registered, immutable)

**Dev period: 2023-07-01 → 2024-06-30**
(Hold-out: 2024-07-01 → present — untouched until dev gate passes)

The strategy is **killed without appeal** if ANY of the following trigger on dev:

| Criterion | Kill threshold |
|-----------|---------------|
| Mean net return (T+1 open → eff_date close) | < 100 bps |
| Win rate (fraction of inclusions with net positive return) | < 50% |
| Sharpe (per-trade return / per-trade std) | < 0.5 |
| Deflated Sharpe Ratio (vs n_trials from MLflow) | < 0.5 |
| Anti-strategy: short inclusions same window net return | > 0 |
| Cost-stress: DSR collapse under t-dist(df=4, scale=2×) slippage | > 50% |
| Total dev-period events | < 15 |

**Notes on thresholds:**
- 100 bps minimum return is higher than PEAD's 40 bps because the mechanism
  is more certain (forced buying).  If 15–25 trading days of forced institutional
  demand can't produce 1% net return, the passive AUM in the affected indices is
  too small to be exploitable at Midcap 150 liquidity.
- Win rate ≥ 50% replaces "median trades per fold" because purged k-fold is
  ill-suited to a strategy with only 30–50 events/year.  Instead, we require the
  majority of individual trade outcomes to be positive.
- DSR n_trials: clean MLflow experiment `index_recon_v1` starting from run #1.

## 5. Universe and Scope

**Indices in scope (v1):**
- Nifty 50 — highest tracking AUM; largest forced buying; smallest per-event return
- Nifty Next 50 — significant ETF AUM; moderate per-event return
- Nifty Midcap 150 — fastest-growing passive segment; expected largest per-event return

**Event type:** inclusions only (stocks added to the index).
Exclusions are NOT traded in v1: selling pressure is lower (fewer exclusion-tracking
funds; some ETFs hold exclusions beyond effective date due to creation unit timing).

**Entry**: T+1 open after NSE announcement (announcement is made post-market or
intra-day; entry is the first full session after the announcement is public).
**Exit**: effective-date close (the night when passive funds must rebalance).

## 6. Data Required

### 6.1 Index Change Announcements (new — must build)

NSE publishes reconstitution notices on:
  - NSE Indices (formerly IISL): https://www.niftyindices.com/indices/equity
  - NSE circular archive: https://www.nseindia.com/regulations/circulars

Required fields per event:
  - `announcement_date` — date of public NSE press release
  - `effective_date` — date when the change takes effect
  - `index_name` — Nifty 50 / Nifty Next 50 / Nifty Midcap 150
  - `event_type` — inclusion / exclusion
  - `symbol` — NSE symbol of the added/removed stock

Historical coverage needed: 2015-01-01 → 2024-06-30.
Storage: `data/lake/index_changes/nse_recon_events.csv` (one row per event).

The NSE archives go back to at least 2010 for Nifty 50.  Midcap 150 was
launched in 2016; Midcap 150 events will be available from 2016 onward.

**Manual bootstrap approach**: The historical CSV can be assembled manually
from archived NSE press releases; ~80–100 events over 8 years.  This is
a one-time data build, not ongoing scraping.

### 6.2 OHLCV Data (existing)

NSE Bhavcopy 2015-01-01 → 2024-06-30 (already in the OHLCV pipeline).
No additional data needed for price/volume features.

### 6.3 PIT Discipline

Announcement date is the NSE public press release date — PIT-correct by
definition (information is public at that moment).  No look-ahead risk.

## 7. Feature List (rules-based v1; no ML model)

This version is rules-based.  No feature engineering, no ML model, no
hyperparameter search.  DSR correction = minimal (n_trials starts at 1).

The single trading rule:
  1. On `announcement_date + 1` open: buy the inclusion at market open.
  2. On `effective_date` close: sell at market close.
  3. Exclude events within ±30 calendar days of a Lok Sabha election date
     (same regime filter as PEAD, applied for consistency).

**Optional v2 features (NOT in v1, to be registered separately if v1 passes):**
- Index AUM as size proxy (larger AUM → larger forced buying → smaller per-unit return)
- Days-to-effective (longer window → more crowding → smaller return at effective date)
- Stock liquidity (bid-ask spread proxy for slippage)
- Sector concentration (avoid over-trading same sector in same review cycle)

## 8. Code References (to be built)

- `quant/data/nse_index_changes.py` — ingest and validate the recon event CSV
- `quant/strategies/index_recon.py` — `simulate_trades()`, `run_anti_strategy()`,
  `run_cost_stress()` (mirrors pead_midcap.py structure)
- `quant/research/run.py` — extend with `--strategy index_recon` dispatch
- MLflow experiment: `index_recon_v1` (clean slate, n_trials = 1)
- `data/lake/index_changes/nse_recon_events.csv` — event dataset

## 9. Execution Plan

1. **Build event dataset** (manual step — one-time):
   - Download NSE index change circulars from NSE/IISL archives for 2015–2024
   - Extract: announcement_date, effective_date, index_name, symbol, event_type
   - Validate: every event must have matching OHLCV data ± 5 days around both dates
   - Store as `data/lake/index_changes/nse_recon_events.csv`
   - Target: ≥ 200 events covering 2015–2024 (train + dev + partial hold-out)

2. **Implement `nse_index_changes.py`**:
   ```bash
   python -m quant.data.nse_index_changes --validate
   # Should report: N events, date range, symbols with missing OHLCV
   ```

3. **Implement `index_recon.py`**:
   ```bash
   python -m quant.research.run --strategy index_recon --split train
   # Verify: mean trade return in historical window, trade count, fill rate
   ```

4. **Dev gate**:
   ```bash
   python -m quant.research.run --strategy index_recon --split dev
   # Report: all 7 gate criteria from §4
   ```

5. If all 7 gate criteria pass on dev → single hold-out run:
   ```bash
   python -m quant.research.run --strategy index_recon --split holdout --final
   ```

## 10. Result

*Dev gate evaluated 2026-05-22 on corrected nse_recon_events.csv (36 dev trades)*

| Metric | Value | Threshold | Pass? |
|--------|-------|-----------|-------|
| Mean net return | 572.9 bps | ≥ 100 bps | ✓ PASS |
| Win rate | 63.9% | ≥ 50% | ✓ PASS |
| Sharpe (per-trade) | 0.526 | ≥ 0.5 | ✓ PASS |
| DSR (n_trials=6) | 0.971 | ≥ 0.5 | ✓ PASS |
| Anti-strategy return | −682.9 bps | ≤ 0 | ✓ PASS |
| Cost-stress DSR collapse | 1.5% | ≤ 50% | ✓ PASS |
| Total dev events | 36 | ≥ 15 | ✓ PASS |

**ALL 7 GATE CRITERIA PASS** — proceeding to single hold-out evaluation.

## 11. Decision

*Hold-out run: 2026-05-22 — single shot, irreversible.*

**KILLED — hold-out gate failed on 4 of 7 criteria.**

| Metric | Hold-out value | Threshold | Result |
|--------|----------------|-----------|--------|
| Mean net return | 23.5 bps | ≥ 100 bps | ✗ FAIL |
| Win rate | 60.0% | ≥ 50% | ✓ PASS |
| Sharpe | 0.065 | ≥ 0.5 | ✗ FAIL |
| DSR (n_trials=9) | 0.081 | ≥ 0.5 | ✗ FAIL |
| Anti-strategy return | −133.5 bps | ≤ 0 | ✓ PASS |
| Cost-stress DSR collapse | 22.0% | ≤ 50% | ✓ PASS |
| Total hold-out events | 5 | ≥ 15 | ✗ FAIL |

**Root cause analysis (post-mortem, not grounds for revival):**
1. Hold-out data was Nifty 50-only (6 events, 1 skipped for missing T+1 price). The Sep 2024, Mar 2025, Sep 2025 Nifty Next 50 and Nifty Midcap 150 reconstitution data was never populated into nse_recon_events.csv. The ≥15 events threshold was missed purely due to data incompleteness.
2. Even the 5 Nifty 50 events that did execute returned only 23.5 bps — below the 100 bps threshold. This is a genuine signal weakness on the highest-cap index, likely reflecting increased front-running by sophisticated participants.
3. Dev mean return of 572.9 bps was driven largely by Midcap 150 inclusions (higher per-event return due to lower liquidity and less crowded trade). The hold-out tested a degraded version of the strategy.

**Decision: [✗] Kill — per §4, no re-runs, no parameter adjustments.**

*The strategy died partly from data incompleteness and partly from genuine Nifty 50 signal decay. Even a fully-populated hold-out might have passed on Sharpe if Midcap 150 events showed the same dev-period returns — but we cannot know, and the hold-out is spent.*

---

*Registered: 2026-05-21 by Ritesh Kant.  Immutable after this commit.
Changes to §4 (falsification criteria) after this commit are process
violations (plan §3.3).  Changes to §7 (feature list) after any
experiment begins are also violations.*
