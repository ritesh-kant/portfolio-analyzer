---
slug: bdm-institutional
strategy: h
status: killed
registered: 2026-05-23
finalized: 2026-05-23
decision: killed
final: true
type: quantitative_signal
standalone: true
parent_strategy: g
---

# Hypothesis: BDM-Institutional — Bulk Deal Continuation by Institutional Buyers in Nifty Midcap 150

## 1. Hypothesis

When a classified **institutional** entity (domestic mutual fund, FII/FPI, or
insurance company) executes a bulk deal BUY (≥ 0.5% of equity outstanding) in a
Nifty Midcap 150 stock that is **trading above its 50-day EMA**, the stock drifts
upward over the following 20 trading days by ≥ 100 bps net of costs, with a
per-trade Sharpe ≥ 0.5.

## 2. Relationship to Prior Strategies (F, G)

| Strategy | Filter | Win rate | Mean return | Sharpe | Result |
|----------|--------|----------|-------------|--------|--------|
| F (BDM) | None | 49.1% | 277.8 bps | 0.173 | Killed: win rate, Sharpe |
| G (BDM-M) | Momentum (close > EMA50) | 52.2% | 463.9 bps | 0.277 | Killed: Sharpe |
| H (BDM-I) | Momentum + Institutional | TBD | TBD | TBD | Active |

**Root cause of G's failure:** Sharpe = 0.277, per-trade std ~16.7%.  The bulk deal
dataset mixes institutional continuation buyers (who continue accumulating in smaller
lots over 20+ days) with retail/HNI/promoter buyers (one-off tactical positions with
no follow-through, contributing to fat-tailed losses).  Filtering to institutional
buyers should reduce variance while maintaining or increasing mean return.

## 3. Mechanism

### 3.1 Why institutional buyers have higher continuation probability

A mutual fund or FII establishing a new position in a Midcap 150 stock faces a
different decision structure than a retail/HNI buyer:

1. **Target allocation size**: institutional mandates require positions of 1–5% of
   AUM in a single stock.  A bulk deal of 0.5% equity is rarely the full target — the
   fund has committed to a much larger position and will continue buying in sub-disclosure
   lots.  A retail/HNI buyer has no such mandate and may be done after the disclosed lot.

2. **Due diligence depth**: institutional investors have research teams, management
   access, and valuation models.  Their willingness to cross the bulk deal threshold
   represents a higher-conviction signal than a retail/HNI buyer acting on a tip or
   chart pattern.

3. **Regulatory constraints**: FIIs and domestic MFs are SEBI-regulated entities with
   fiduciary duties.  Their bulk deal disclosures are verifiable and consistent.
   Promoter/HNI bulk deals can be motivated by tax restructuring, related-party
   arrangements, or portfolio balancing — not signal of conviction.

4. **Float impact**: institutional buyers often hold to lock-in periods or target weights,
   which provides longer-term float compression compared to retail buyers who may flip
   within days if the position moves against them.

### 3.2 Entity classification method

NSE bulk deal data includes a free-text `client_name` field.  Rather than ML-based
entity resolution (which requires a training corpus), we use a deterministic
keyword classifier:

**INSTITUTIONAL** (pass) — client_name contains ANY of:
  - "MUTUAL FUND" or "MF " (mutual funds)
  - "FII" or "FPI" (foreign institutional/portfolio investors)
  - "INSURANCE" or "INSUR" (insurance companies)
  - "PENSION" (pension funds)
  - "PROVIDENT FUND" or "PF " (provident/gratuity funds)
  - "TRUST" (investment trusts; note: excludes promoter family trusts via heuristic)
  - " FUND" as suffix (catches edge cases like "HDFC BALANCED ADVANTAGE FUND")

**NON-INSTITUTIONAL** (skip) — everything else (retail HNI, promoters,
  corporate treasuries, NRIs, proprietary books)

This classifier is intentionally conservative: it may falsely exclude some
institutional entities whose names don't contain keywords (false negatives), but
it avoids including non-institutional entities (false positives which hurt Sharpe).

The keyword list is **pre-registered and immutable**.  Adding keywords after any
experiment run is a process violation.

### 3.3 Academic grounding

- **Chakravarty (2001)**: Institutional trades predict next-day returns more than
  retail trades; the effect is concentrated in first 20 days (~80–150 bps mid-cap).
- **Grinblatt, Titman & Wermers (1995)**: Fund herding — when multiple funds buy
  simultaneously — amplifies the continuation effect.  Institutional buyer filter
  captures the herding-eligible subset.
- **Sias, Starks & Titman (2006)**: Institutional demand shocks in mid/small-cap
  predict returns at 1-month horizon with Sharpe ~0.6–0.8 (institutional only).
- **Patel & Vaidya (2018)**: Bulk deal BUY returns are "concentrated in stocks with
  pre-existing positive momentum" — the motivation for the EMA50 filter retained from G.

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
| Total dev-period signal events (after all filters) | < 20 |

**Note on event count threshold:**
Reduced to 20 (from G's 30) because the institutional filter is expected to remove
~60–70% of events.  20 events is the minimum viable sample for a binary win/loss
test.  If fewer than 20 events survive, the hypothesis fails on insufficient data.

## 5. Signal Construction

```python
# On each business date T:
# 1. Load bulk deals for date T (disclosed same evening)
# 2. Filter: symbol in Midcap150_members(T), side == "BUY", value_cr >= 1.0
# 3. Aggregate (symbol, date) duplicates → one event
# 4. INSTITUTIONAL FILTER: keep event only if any buyer entity is institutional
#    (keyword match on client_name)
# 5. MOMENTUM FILTER: keep event only if close_T > EMA50_T
# 6. Apply pledge filter and election filter
# 7. Entry: T+1 open; Exit: T+20 close
```

**Filter order**: institutional filter first (entity check), then momentum filter
(EMA check), then pledge/election filters.  This order maximises skip-log clarity.

**Aggregation and multi-buyer events:**
When multiple buyers exist for the same (symbol, date), the event is KEPT if ANY
of the buyers is classified as institutional.  The `is_institutional` flag is set
True if at least one buyer is institutional.

## 6. Institutional Entity Keyword Classifier (pre-registered, immutable)

```python
_INSTITUTIONAL_KEYWORDS = [
    "MUTUAL FUND", " MF ", "MF-", "MF ",
    "FII", "FPI",
    "INSURANCE", "INSUR",
    "PENSION",
    "PROVIDENT FUND", "PROVIDENT",
    " FUND",           # catches "HDFC BALANCED ADVANTAGE FUND" etc.
    "LIFE INSURANCE",
    "GENERAL INSURANCE",
    "ASSET MANAGEMENT",
    "AMC",
    "INVESTMENT TRUST",
    "NATIONAL PENSION",
    "EMPLOYEES' STATE INSURANCE",
    "LIC",             # Life Insurance Corporation (India's largest institutional)
    "NEW INDIA ASSURANCE",
    "UNITED INDIA INSURANCE",
    "SBI LIFE",
    "HDFC LIFE",
    "ICICI PRUDENTIAL LIFE",
    "KOTAK MAHINDRA LIFE",
]
```

Classification function:
```python
def is_institutional(client_name: str) -> bool:
    name_upper = client_name.strip().upper()
    return any(kw.upper() in name_upper for kw in _INSTITUTIONAL_KEYWORDS)
```

## 7. Data Required

All data already on disk:
- Bulk deals: `data/lake/bulk_deals/nse_bulk_deals.parquet`
- OHLCV: Bhavcopy parquet via `pit_loader.load()`
- Midcap 150: `data/lake/midcap150_constituents.csv`

## 8. Code References

| File | Purpose |
|------|---------|
| `quant/data/entity_classifier.py` | `is_institutional()`, `classify_bulk_deal_events()` |
| `quant/strategies/bdm_institutional.py` | Strategy H: simulate_trades(), gate metrics |
| `quant/research/run.py` | `--strategy h` dispatch, `run_gate_check_h()` |
| MLflow experiment | `bdm_institutional_v1` (clean slate, n_trials = 1) |

## 9. Result

**Dev gate run: 2026-05-23**
**Dev period: 2023-07-01 → 2024-06-30**

| Metric | Result | Gate | Status |
|--------|--------|------|--------|
| Raw events | 231 | — | — |
| Executed trades | 7 | ≥ 20 | ❌ FAIL |
| Mean net return | +128.3 bps | ≥ 100 bps | ✅ PASS |
| Win rate | 71.4% | ≥ 52% | ✅ PASS |
| Sharpe | 0.152 | ≥ 0.5 | ❌ FAIL |
| DSR | 0.632 | ≥ 0.5 | ✅ PASS |
| Anti-strategy | −238.3 bps | ≤ 0 | ✅ PASS |
| Cost-stress collapse | 2.6% | ≤ 50% | ✅ PASS |

**5/7 gates pass, 2/7 fail (event count + Sharpe) → KILLED**

**Root cause:** The keyword classifier filtered 164/178 eligible events as
non-institutional (92%).  NSE Midcap 150 BUY bulk deals are dominated by
algorithmic and proprietary trading firms (GRAVITON RESEARCH CAPITAL LLP alone
accounts for 29.5% of all deals — 2706/9173 deals in the dev period), not
institutional investors.  Only ~4% of bulk deal events are classifiable as
institutional via keyword match.

**Critical dataset insight (affects F, G, H):**
The BDM family hypothesis was predicated on bulk deals being driven by institutional
continuation buyers.  Data analysis shows this is wrong for Nifty Midcap 150 bulk
deals — the dataset is overwhelmingly dominated by algorithmic trading firms.
The genuine institutional signal exists in a DIFFERENT dataset: **block deals**
(pre-negotiated off-market transactions ≥ ₹10 crore, executed at the block deal
window 8:45-9:00 AM), which are:
- Always institutional (block deal buyers are always large investors)
- Larger in value (minimum ₹10 crore vs ₹1 crore for bulk deals)
- Less dominated by algorithmic activity

Strategy I pre-registration: Block Deal Momentum using block deals (not bulk deals).

## 10. Decision

**KILLED — 2026-05-23**

Insufficient events (7 < 20) and Sharpe failure. The keyword-based institutional
classifier is ineffective for NSE bulk deal data which is dominated by algorithmic
traders rather than institutional investors.

---

*Registered: 2026-05-23 by Ritesh Kant.  Falsification criteria (§4) and the
keyword list (§6) are pre-registered and immutable.  Changes to either after
any MLflow experiment run begins are process violations (plan §3.3).*
