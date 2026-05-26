---
slug: block-deal-5day
strategy: k
status: killed
registered: 2026-05-24
finalized: 2026-05-24
decision: killed
final: true
type: quantitative_signal
standalone: true
parent_strategy: i
---

# Hypothesis: Block Deal Momentum — 5-Day Hold (Block Deal 5D)

## 1. Hypothesis

When a large institutional investor executes a block deal BUY in a Nifty Midcap 150
stock trading above its 50-day EMA, the stock drifts upward over the following **5
trading days** by ≥ 100 bps net of costs, with a per-trade Sharpe ≥ 0.5.

## 2. Relationship to Strategy I (killed 2026-05-23)

Strategy I (20-day hold) produced Sharpe 0.389 — failed the ≥ 0.5 gate.
Root cause: per-trade std ~11% over 20 days.  Strategy J (8% stop-loss) made things
worse (Sharpe 0.368) by whipsawing 5 recovering trades.

**The binding constraint is hold-period variance, not signal quality.**

Block deal continuation buying is front-loaded:
- The disclosing entity has a large target allocation (typically ₹200–500 Cr) and
  buys in tranches — the first 5 trading days see the heaviest follow-on buying.
- Information cascade: other market participants react to the public block deal
  disclosure within days 1–3, not weeks.
- By day 5, the initial price discovery from the institutional signal is largely
  complete; continuing to hold adds stock-specific noise without adding signal.

**Why 5 days specifically:**
- 5 trading days ≈ 1 calendar week: the natural "news absorption" window for
  institutional disclosures in Indian midcap stocks.
- Per-trade std should compress to ~4–6% (from ~11% at 20 days), as noise
  accumulates roughly as √T.  Expected Sharpe: (444 bps / 2) × (11% / 5%) ≈ 0.49–0.55.
  (Conservative estimate: even if mean halves, std should compress more than 2×.)
- Round numbers (3, 5, 10 days) were the candidates; 5 is the tightest that still
  captures the full information cascade (3 days may be too short post-block-deal
  disclosure processing lag).

**No stop-loss** (Strategy J proved stop-losses introduce more whipsaw than they cure
over a 20-day window; a 5-day hold has naturally bounded downside).

## 3. Mechanism

Same as Strategy I except:
- **Hold period: 5 trading days** (T+1 open → T+5 close)
- No stop-loss
- All other filters identical (election, pledge, EMA50 momentum)

### 3.1 Trading rule (pre-registered, immutable)

```
For each block deal BUY in Nifty Midcap 150 on date T:
  1. Momentum filter: close_T > 50-day EMA at close of T
  2. Election filter: skip ±30 calendar days of Lok Sabha general election
  3. Pledge filter: skip flagged promoter pledge stocks
  4. Entry: T+1 open price
  5. Exit: T+5 close price (5th trading day after entry)
  6. Net return: (exit / entry - 1) - 0.0055  (round-trip costs)
```

### 3.2 Cost assumption

Same 55 bps round-trip as Strategies I and J.  With a 5-day hold, the cost is a
larger fraction of expected mean return — if the 5-day mean is ~200 bps, costs are
~28% of gross return (vs ~12% for the 20-day hold).  This is the main risk to
the hypothesis.

## 4. Falsification Criterion (pre-registered, immutable)

**Dev period: 2023-07-01 → 2024-06-30** (same as prior strategies)

| Criterion | Kill threshold |
|-----------|---------------|
| Mean net return | < 100 bps |
| Win rate | < 52% |
| Sharpe (per-trade) | < 0.5 |
| DSR (n_trials from MLflow) | < 0.5 |
| Anti-strategy return | > 0 bps |
| Cost-stress DSR collapse | > 50% |
| Total dev events (after all filters) | < 15 |

## 5. Signal Construction

```python
# On each business date T:
# 1. Load block deals for date T (disclosed at open ~9:00 AM)
# 2. Filter: symbol in Midcap150_members(T), side == "BUY"
# 3. Aggregate (symbol, date) → one event per stock per day
# 4. MOMENTUM FILTER: close_T > 50-day EMA at close of T
# 5. Apply pledge filter and election filter
# 6. Entry: T+1 open; Exit: T+5 close
```

## 6. Code References

| File | Purpose |
|------|---------|
| `quant/strategies/block_momentum_5day.py` | Strategy K with 5-day hold |
| `quant/research/run.py` | `--strategy k` dispatch |
| MLflow experiment | `block_deal_5day_v1` |

## 7. Result

**Dev gate run: 2026-05-24**

| Metric | Result | Gate | Status |
|--------|--------|------|--------|
| Raw events | 71 | — | — |
| Executed trades | 36 | ≥ 15 | ✅ PASS |
| Mean net return | +194.4 bps | ≥ 100 bps | ✅ PASS |
| Win rate | 52.8% | ≥ 52% | ✅ PASS |
| Sharpe | 0.254 | ≥ 0.5 | ❌ FAIL |
| DSR | 0.953 | ≥ 0.5 | ✅ PASS |
| Anti-strategy | −304.4 bps | ≤ 0 | ✅ PASS |
| Cost-stress collapse | 2.6% | ≤ 50% | ✅ PASS |

**6/7 pass, Sharpe ❌ → KILLED**

**Why Sharpe is worse at 5 days than 20 days:**

The block deal signal is a slow institutional accumulation drift, not a fast
price reaction.

| Hold | Mean return | Implied std | Sharpe |
|------|------------|-------------|--------|
| 5 days | 194 bps | ~7.6% | 0.254 |
| 20 days | 444 bps | ~11.4% | 0.389 |

Std compressed ~33% when moving from 20→5 days, but mean compressed ~56%.
The bulk of the return materialises in weeks 2–4, driven by the institutional
buyer's continued open-market accumulation.  A 5-day window captures only the
initial price discovery; the signal hasn't fully played out yet.

Conclusion: the block deal signal is a slow drift.  There is no hold period
that achieves Sharpe ≥ 0.5:
- Short hold (5d): drift not yet materialised → low mean → low Sharpe
- Long hold (20d): drift captured but noise accumulates → high std → low Sharpe
- Stop-loss (J): whipsaw destroys more winners than it saves losers

## 8. Decision

**KILLED — 2026-05-24.** Sharpe 0.254 < 0.5.

**Block deal / bulk deal family (F–K) final verdict: EXHAUSTED**

| Strategy | Signal | Hold | Sharpe | Kill reason |
|----------|--------|------|--------|-------------|
| F | Bulk, no filter | 20d | 0.173 | Sharpe + win rate |
| G | Bulk + EMA50 | 20d | 0.277 | Sharpe |
| H | Bulk + institutional | 20d | 0.152 | Sharpe + n<20 |
| I | Block + EMA50 | 20d | 0.389 | Sharpe |
| J | Block + EMA50 + stop | 20d | 0.368 | Sharpe (whipsaw) |
| K | Block + EMA50 | 5d | 0.254 | Sharpe (drift too slow) |

The block deal signal has genuine edge (clean anti-strategy, DSR ~0.99, win
rates 53–67%) but is structurally incompatible with the Sharpe ≥ 0.5 gate.
The intrinsic per-trade variance at the Midcap 150 level, combined with the
slow-drift nature of the signal, caps achievable Sharpe at ~0.4.

**Recommended next direction:** Different signal family — either shorter-cycle
price signals (overnight gaps, opening range) or a fundamentals-driven approach
that generates higher mean return per event.

---
*Registered: 2026-05-24 by Ritesh Kant.*
