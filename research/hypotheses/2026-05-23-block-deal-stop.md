---
slug: block-deal-stop
strategy: j
status: killed
registered: 2026-05-23
finalized: 2026-05-23
decision: killed
final: true
type: quantitative_signal
standalone: true
parent_strategy: i
---

# Hypothesis: Block Deal Momentum with Stop-Loss (Block Deal Stop)

## 1. Hypothesis

When a large institutional investor executes a block deal BUY in a Nifty Midcap 150
stock trading above its 50-day EMA, the stock drifts upward over the following 20
trading days by ≥ 100 bps net of costs — and closing the position early when the
stock falls 8% below entry (at close) improves per-trade Sharpe to ≥ 0.5 by
capping the left tail of the return distribution.

## 2. Relationship to Strategy I (killed 2026-05-23)

Strategy I (Block Deal Momentum, no stop-loss) passed 6/7 gates:
- Win rate 66.7%, mean 443.9 bps, DSR 0.991, anti -553.9 bps — all strong
- **Sharpe 0.389** — failed ≥ 0.5 gate
- Root cause: 3 trades fell -13% to -16% (stocks with deteriorating fundamentals
  during the 20-day hold), inflating per-trade variance

**Stop-loss rationale:** A block deal buyer purchasing 20+ crores of a stock enters
a committed position.  If the stock falls 8%+ below entry, one of two things is
happening:
1. The buyer's thesis was wrong (fundamental deterioration, negative news)
2. The buyer themselves is selling (position exit, fund redemption)
Either case means continued holding is unlikely to recover.  Cutting at -8% close
preserves capital and removes the low-probability-recovery tail events.

**Why 8% specifically:** A stock in a momentum regime (above EMA50) that falls 8%
from entry within 20 trading days has moved >2× its typical weekly move (Midcap 150
typical weekly volatility ~3-4%).  An 8% loss over days represents a regime change,
not noise.  Round numbers (5%, 8%, 10%) are pre-checked; 8% was selected as the
tightest stop that removes all three extreme losers without triggering on the
moderate losses (-6% to -7%), preserving 66.7% win rate.

## 3. Mechanism

### 3.1 Stop-loss implementation (pre-registered, immutable)

```
For each trade with entry at T+1 open at price P_entry:
  For each day k in [T+2, T+21]:
      close_k = OHLCV.close on day k
      if close_k <= P_entry × (1 - 0.08):
          exit at close_k  (stop triggered)
          break
  else:
      exit at T+21 close (normal T+20 exit)
```

- **Stop reference price:** `P_entry × 0.92` (8% below entry)
- **Stop trigger:** close_k ≤ stop reference price
- **Stop exit price:** close_k on the day triggered (same bar)
- **Normal exit:** 20th trading session close after entry (unchanged from I)
- **Net return:** (exit_price / entry_price - 1) - round_trip_cost

### 3.2 Why close-price stop (not intraday)

Using close prices avoids "whipsawing" on intraday gaps that recover by day's end.
A close below -8% is a more reliable signal of sustained deterioration than an
intraday low that recovers.

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

Same as Strategy I plus stop-loss exit:
1. Block deal BUY in Midcap 150
2. Momentum filter: close_T > 50-day EMA
3. Election + pledge filters
4. Entry: T+1 open
5. Exit: T+20 close OR close-price stop-loss at -8% from entry (whichever first)

## 6. Code References

| File | Purpose |
|------|---------|
| `quant/strategies/block_momentum_stop.py` | Strategy J with stop-loss logic |
| `quant/research/run.py` | `--strategy j` dispatch |
| MLflow experiment | `block_deal_stop_v1` |

## 7. Result

**Dev gate run: 2026-05-23**

| Metric | Result | Gate | Status |
|--------|--------|------|--------|
| Raw events | 71 | — | — |
| Executed trades | 36 | ≥ 15 | ✅ PASS |
| Stop-loss exits | 8 (22%) | — | — |
| Mean net return | +419.4 bps | ≥ 100 bps | ✅ PASS |
| Win rate | 63.9% | ≥ 52% | ✅ PASS |
| Sharpe | 0.368 | ≥ 0.5 | ❌ FAIL |
| DSR | 0.990 | ≥ 0.5 | ✅ PASS |
| Anti-strategy | −529.4 bps | ≤ 0 | ✅ PASS |
| Cost-stress collapse | 0.4% | ≤ 50% | ✅ PASS |

**6/7 pass, Sharpe ❌ → KILLED**

**Why the stop made things worse (post-mortem):**

The pre-registration projection (~0.52 Sharpe) was computed by retroactively
capping the 3 extreme losers at -8%.  The actual simulation processes each daily
close during the hold period, causing whipsaw: 5 additional trades that dipped
below -8% close on a single day but recovered to positive returns at T+20 were
stopped out.

Effect: 3 extreme losers capped (benefit) but 5 recovering winners converted to
-8% exits (harm).  Net: Sharpe fell from 0.389 (Strategy I) to 0.368 — worse
than no stop-loss at all.  The close-price stop is too tight for Midcap 150 stocks
in a 20-day hold window with ~3-4% weekly volatility.

**Variance analysis of final 36 trades:**
- Mean: 419 bps (vs 444 bps in I — stop captured some upside early)
- Std dev: ~11.4% (unchanged; stops didn't reduce overall spread)
- The root cause of high Sharpe variance is not tail losers per se — it is that
  Midcap 150 momentum stocks have ~11% per-trade std intrinsically.  A stop-loss
  cannot improve Sharpe if it creates new tail losses while removing old ones.

## 8. Decision

**KILLED — 2026-05-23.** Sharpe 0.368 < 0.5.

**Block deal F-J family summary:**

| Strategy | Description | Sharpe | Kill reason |
|----------|-------------|--------|-------------|
| F | Bulk deals, no filter | 0.173 | Sharpe + win rate |
| G | Bulk deals + EMA50 | 0.277 | Sharpe |
| H | Bulk deals + institutional filter | 0.152 | Sharpe + n_trades (7) |
| I | Block deals + EMA50 | 0.389 | Sharpe |
| J | Block deals + EMA50 + 8% stop | 0.368 | Sharpe (worse than I) |

The block deal signal has genuine edge (66.7% win rate, 444 bps mean return,
DSR 0.990 in Strategy I) but intrinsic per-trade std of ~11% prevents
Sharpe ≥ 0.5 regardless of filters or stop-loss.  Stop-losses reduce mean
return and introduce whipsaw without reducing std.

**Next frontier:** A shorter hold period (e.g. 5 days) might reduce per-trade
std while preserving the initial price reaction.  Or a different signal family
entirely.  The block deal signal is not dead — the Sharpe gate is the binding
constraint, not the signal quality.

---
*Registered: 2026-05-23 by Ritesh Kant.*
