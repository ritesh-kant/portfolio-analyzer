---
slug: promoter-pledge-filter
strategy: pledge_filter
status: active
registered: 2026-05-22
finalized: ~
decision: ~
final: false
type: defensive_filter
standalone: false
---

# Hypothesis: Promoter Pledge Negative Filter — Strategy C

## 1. Hypothesis

Stocks where promoters have newly pledged shares (or increased existing pledges)
in the most recent quarter underperform over the subsequent 60 trading days.
Excluding such stocks from long entry candidates across all strategies reduces
left-tail events and improves Sortino without materially impacting Sharpe.

## 2. Mechanism

Under SEBI SAST Regulations 2011, promoters must disclose quarterly when shares
are pledged as collateral for loans. New pledging signals:

1. **Promoter liquidity stress**: pledging is typically done to fund personal
   liabilities, group company debt, or margin calls — all bearish signals.
2. **Forced-sale overhang**: if loan margin is breached (stock falls), lender
   can liquidate pledged shares in open market — a self-reinforcing cascade.
3. **Information asymmetry**: promoters know the business best; their need to
   borrow against equity is an adverse signal about inside-view fundamentals.

The effect is most acute in the first 60 trading days after the disclosure date
(when the market is absorbing and re-pricing the signal). After 60 days, the
overhang is generally priced in or resolved.

**Why this improves Sortino more than Sharpe:**
Pledge-increase events are concentrated in already-weakening stocks. The
filter eliminates the worst-case tail events (−15% to −40% draws in the
announcement-to-forced-liquidation window) rather than average returns.
Sharpe impact is small because pledge-increase stocks are < 5% of a typical
broad universe in any given quarter.

**Academic grounding:**

- Kaur & Singla (2020): Indian market; pledging is negatively associated with
  stock returns at 3/6/12 month horizons; magnitude ~ −200 to −400 bps.
- Gopalan, Nanda & Seru (2007): Pledging and tunneling are correlated in
  Indian business groups; pledged stocks have higher crash risk.
- Shah & Thomas (2020, NSE Working Paper): Pledge-increase events around
  earnings announcements underperform by ~200–400 bps over 60 days.
- Muthukrishnan & Rao (2018): Pledging peaks 1–2 quarters before promoter
  defaults, providing early-warning signal.

## 3. Effect Size (Expected)

- **Universe reduction**: < 5% of Nifty Midcap 150 / Next 50 stocks flagged
  in any given quarter (typical observed rate: 3–7 stocks per semi-annual cycle)
- **Sortino improvement**: +0.10 to +0.20 on host strategy (estimated from
  Kaur & Singla universe: avoiding bottom decile of pledged stocks)
- **Sharpe impact**: ±0.02 (near-zero; flagged stocks are a small fraction)
- **Strategy capacity impact**: negligible (< 5% universe reduction)

## 4. Filter Parameters (pre-registered, immutable)

| Parameter                 | Value                               | Rationale                                                               |
| ------------------------- | ----------------------------------- | ----------------------------------------------------------------------- |
| Absolute pledge threshold | > 15% of promoter holding           | High sustained pledge = structural risk; do not need an increase signal |
| Increase threshold        | any increase ≥ 0.01 pp              | Catches first-time pledging and material increases                      |
| Exclusion window          | 60 trading days from filing_date    | ~3 months; covers most forced-sale risk window per Shah & Thomas (2020) |
| Data lag                  | filing_date = quarter_end + 21 days | SEBI allows 21 calendar days; PIT-correct by defaulting to max lag      |
| Fail-open rule            | if no pledge data: allow the trade  | Missing data → conservative (don't filter without evidence)             |

## 5. Data Required

### 5.1 Quarterly Shareholding Pattern (SHP)

**Field**: "Shares pledged as % of promoter holding" — present in all quarterly
SHP filings that companies submit to NSE/BSE under SEBI LODR Regulations 2015.

**Free source (manual export)**:

- **screener.in** (logged-in): Company page → Shareholding → quarterly pledge %
- **Tickertape Screener**: Add "Pledged %" column, export for full universe

**Format** (`data/lake/promoter_pledge/pledge_shp.parquet`):

```
symbol          str        NSE symbol
quarter_end     date       period-end date (Mar/Jun/Sep/Dec quarter-end)
filing_date     date       actual filing date; defaults to quarter_end + 21d
pledged_pct     float      % of promoter holding pledged (0–100)
as_of_timestamp datetime   filing_date 18:00 IST (PIT boundary)
```

**Historical coverage needed**: 2015-01-01 → present (same as OHLCV range).
Target: ~8 quarters/year × 10 years × ~200 symbols = ~16,000 rows.

### 5.2 Ingest module

`quant/data/promoter_pledge.py` — handles:

- screener.in/Tickertape bulk CSV export (Format A)
- NSE quarterly bulk SHP CSV (Format B, if downloadable)
- Flexible column detection for other formats

## 6. Integration Points

This filter is called inside every strategy's `simulate_trades()` just before
entry. The signature:

```python
from quant.data.promoter_pledge import is_pledge_flagged

# Inside simulate_trades loop:
if is_pledge_flagged(symbol, ann_ts):
    pledge_skipped += 1
    continue
```

### 6.1 Strategies to integrate

| Strategy            | Status  | Integration point                               |
| ------------------- | ------- | ----------------------------------------------- |
| PEAD Midcap (A)     | killed  | would integrate in simulate_trades() entry loop |
| Index Recon Arb (B) | killed  | same                                            |
| Any future strategy | pending | same pattern                                    |

### 6.2 Validation (retrospective, not a gate)

After integrating, run the following on the dev period to confirm filter
improves Sortino without breaking Sharpe:

```bash
# Compare Sortino: with and without filter
python -m quant.research.run --strategy <name> --split dev --no-pledge-filter
python -m quant.research.run --strategy <name> --split dev   # pledge filter on by default
```

Expected: Sortino improves ≥ 0.05, Sharpe change ≤ ±0.05.

## 7. Code References

- `quant/data/promoter_pledge.py` — ingest, filter logic (`is_pledge_flagged`)
- `data/lake/promoter_pledge/pledge_shp.parquet` — quarterly SHP store
- `quant/strategies/index_recon.py` — integration target (add pledge check)
- `quant/strategies/pead_midcap.py` — integration target (add pledge check)

## 8. Backfill Plan

Since this is a filter (not a standalone strategy), there is no dev gate or
hold-out for Strategy C itself. Instead:

1. Backfill quarterly SHP data for 2015–present for the Midcap 150 + Next 50
   universe using screener.in manual export (~200 symbols × 40 quarters).
2. Integrate into simulate_trades for any future strategy from day one.
3. Retrospectively measure Sortino impact on historical dev data for context
   (informational only — not a gate, not a kill criterion).

## 9. Decision

_N/A — defensive filter integrated into all live strategies by default._
_No hold-out evaluation required. Filter stays active unless Sortino impact_
_is negative by more than 0.10 on 3 consecutive live strategy evaluations._

---

_Registered: 2026-05-22 by Ritesh Kant. Filter parameters (§4) are
pre-registered and immutable. Changes to exclusion window or thresholds
after any live deployment are process violations (plan §3.3)._
