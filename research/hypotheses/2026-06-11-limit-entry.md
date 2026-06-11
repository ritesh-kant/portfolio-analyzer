---
slug: limit-entry
strategy: news_trader_limit_entry
status: shipped
registered_at: '2026-06-11'
finalized_at: '2026-06-11'
decided_at: '2026-06-11'
hypothesis_hash: ''
---

# Hypothesis: Replacing market entry with a 15-min resting limit order at the would-be entry price improves total cohort net P&L

## 1. Mechanism

Slippage is modeled at 5 bps/side = 49% of the ~₹19.4 round-trip cost, and
it is an unmeasured assumption. A limit order resting at the price a market
order would have paid eliminates entry-leg slippage on fills. The cost is
adverse selection: fills occur disproportionately when price dips below the
limit (weak starts), and the strongest momentum winners run away unfilled.
Whether the slippage saved exceeds the expectancy of missed winners is an
empirical question — both effects are real and opposite-signed.

## 2. Expected effect size

- **Direction**: long, intraday; execution change only, same signals/exits
- **Magnitude**: +₹5/trade saved slippage on fills vs loss of unfilled
  winners; expected net effect −₹5 to +₹8 per signal — genuinely uncertain
- **Units**: total cohort net ₹, compared per-SIGNAL (missed trades count)

## 3. Falsification criterion (LOCKED before experimentation)

Single shot, on the SAME cohort as bullish-signal-replay (bullish high-conf
moderate/major, non-NIFTY50). Limit = entry bar OPEN; order rests through
the entry bar + next 2 bars (15 min); fill requires bar LOW strictly below
the limit (conservative — touching doesn't fill); fill price = limit, entry
slippage removed from the cost model (exit stays a market order with full
slippage). Unfilled signals are skipped. Exits and all other parameters
identical to the deployed policy. No re-runs with different rest windows or
limit offsets.

- **KILL if** total cohort net P&L (limit) ≤ total cohort net P&L (market)
- **Descriptive only, no gate**: fill rate, P&L of missed trades
- Thresholds cannot be relaxed after seeing results

## 4. Data needed

Same as bullish-signal-replay (signals + 5-min bars). 5-min bars are coarse
for fill simulation — the strict-inequality fill rule is the conservatism
chosen in advance to compensate.

## 5. Train / dev / hold-out split

Same 8-day dev window; forward paper trading is the true hold-out. Note:
this experiment shares its cohort with bullish-signal-replay — it is an
execution-layer comparison on identical trades, not an independent signal
test, so it does not add a multiple-testing trial on the signal itself.

## 6. Code reference

- `research/backtests/bt10_limit_entry.py` (+ `replay_lib.py` entry_mode)

## 7. Result (filled after experiment — single run 2026-06-11, no re-runs)

- Market-entry cohort total net: −₹2,273 (n=52)
- Limit-entry cohort total net: **−₹1,930** (n=50) — better by ₹343
- Fill rate: 50/52 (96%) — adverse selection nearly absent because the
  cohort has no upward drift (price comes back to the limit almost always)
- Missed-trade counterfactual gross: −₹67 on 2 trades (both would have
  LOST — the limit filter skipped losers, not winners, this window)
- Per-fill saving: ~₹6.9/trade ≈ the modeled 5 bps entry slippage
- Caveat: benefit scales with the slippage ASSUMPTION; with real fills the
  saving could be larger or smaller. 96% fill rate may drop in a cohort
  with genuine upward drift (which is when missing fills costs most)

## 8. Decision

- [x] **SHIP** (= use limit entries in the live Kite order-placement
      design; no Lambda change needed now since paper fills are simulated)
- [ ] **KILL**
- [ ] **ITERATE** — new hypothesis file required
