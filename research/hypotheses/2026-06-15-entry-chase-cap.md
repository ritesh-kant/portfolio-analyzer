---
slug: entry-chase-cap
strategy: news_trader_entry_filter
status: registered
registered_at: '2026-06-15'
finalized_at: ''
decided_at: ''
hypothesis_hash: ''
---

# Hypothesis: Blocking trades where entry price has already risen ≥ 0.5% above signal price improves cohort net P&L

## 1. Mechanism

The news-trader pipeline has a 15-min SQS delay between signal generation and
trade execution. For a subset of signals the market reacts strongly to the
news *before* the delay elapses, so `entry_price > signal_price` by a
meaningful margin. The bet here is narrow: these post-pop entries are buying
into a move that has already finished, so the price is more likely to revert
than to continue — making them disproportionately bad trades vs entries where
the price hasn't moved much yet.

Note: the overall BT9 finding is that the bullish-news signal has NO drift at
any horizon. This hypothesis does NOT dispute that. It only tests whether the
worst-performing pocket (high positive chase) is significantly worse than the
flat-drift average, such that dropping it reduces per-trade loss enough to
matter. It is a risk-management filter, not a new edge.

## 2. Expected effect size

- **Direction**: removal of a bad-entry subset; no directional trade thesis
- **Magnitude (in-sample, n=42, pre-registered estimate)**: +0.5% cap blocks
  5 trades averaging −₹185/trade net vs −₹57/trade for the kept cohort.
  Expected forward saving if the pattern holds: ~₹130/trade avoided, ~10%
  fewer fills/day
- **Units**: net ₹/trade for filtered bucket vs unfiltered bucket; total
  cohort net ₹ with cap applied vs without
- **Caveat**: in-sample n=5 in the filtered bucket — too small to conclude
  anything alone. Forward confirmation is the only valid test.

## 3. Falsification criterion (LOCKED before experimentation)

Forward paper trades only (D11 onward). No re-use of D1–D10 data.

Accumulate forward trades until `n_filtered ≥ 10` (trades that would have
been blocked by the cap) OR 10 forward trading days, whichever comes first.

**Comparison:**
- Group A: forward trades with `entry_chase_pct ≤ 0.5%` (kept by cap)
- Group B: forward trades with `entry_chase_pct > 0.5%` (blocked by cap)

**KILL if** mean gross P&L of Group B ≥ mean gross P&L of Group A.
(If blocked trades are not worse than kept trades, the filter has no basis.)

**SHIP (add the gate to `trade_decision.py`) if** mean gross P&L of Group B
< Group A AND the difference is > ₹30/trade gross (must exceed noise given
small n; this is one cost round-trip as the minimum meaningful bar).

A result of "Group B worse but by < ₹30/trade" is **inconclusive** — defer
and keep accumulating data; do not ship and do not kill.

Thresholds cannot be relaxed after seeing results.

## 4. Data needed

- `nt_positions` collection: `entry_chase_pct`, `gross_pnl`, `net_pnl`,
  `exit_reason`, `entry_at` (to filter D11+)
- No external price data needed — all fields already captured at entry

## 5. Train / dev / hold-out split

- **In-sample reference (do not re-run)**: D1–D10, n=42 trades with
  entry_chase_pct populated. Used only to motivate the hypothesis. Already
  shows n=5 filtered trades avg −₹185/trade vs n=37 kept avg −₹57/trade.
- **Forward test (hold-out)**: D11 onward — this is the only valid window.
  The in-sample result CANNOT be used as the decision criterion.

## 6. Code reference

- Gate location (if shipped): `apps/signal-engine/handlers/trade_decision.py`
  — add after the magnitude gate, before position sizing
- Config flag: `nt_max_entry_chase_pct: float = 0.5` in `config.py`
- No backtest script needed — forward paper data is the test bed

## 7. Result (filled after forward test)

- n_filtered (Group B): <fill>
- n_kept (Group A): <fill>
- Mean gross P&L Group B: <fill>
- Mean gross P&L Group A: <fill>
- Difference (A − B): <fill>
- Decision threshold met (>₹30/trade): <fill>

## 8. Decision

- [ ] **SHIP** — Group B gross/trade < Group A by >₹30; add gate to trade_decision.py
- [ ] **KILL** — Group B gross/trade ≥ Group A (filter has no basis)
- [ ] **INCONCLUSIVE** — difference < ₹30/trade; keep accumulating forward data
