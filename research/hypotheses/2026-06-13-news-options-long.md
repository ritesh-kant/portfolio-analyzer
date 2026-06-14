---
slug: news-options-long
strategy: news_trader_options_long
status: killed # draft | registered | final | shipped | killed
registered_at: ''
finalized_at: ''
decided_at: '2026-06-13'
hypothesis_hash: '' # n/a — killed at screen stage on synthetic pricing
---

# Hypothesis: a high-confidence bullish news signal can be expressed as a long ATM call for net-positive intraday P&L AFTER theta, vol-crush, and F&O spread costs

> DRAFT. Fill every `<...>` and lock §3 before running any code. This is a
> **synthetic-pricing feasibility screen** (Black-Scholes from the underlying
> bar, no real option feed), so it can NEVER serve as a hold-out — see §5.
> The decision this run produces is "worth pursuing on real Kite option bars:
> yes/no", not "ship".

## 1. Mechanism

Same news-reaction edge the equity bullish-long cohort tries to capture, but
expressed through a long call instead of long stock. The *only* reason to
prefer the option is **convexity + leverage**: if the signal genuinely predicts
a fast intraday up-move, delta (~0.55 ATM) plus positive gamma converts a small
spot move into a larger premium move (verified: +1% spot → ~+30% premium on a
7-day ATM call).

⚠️ Honesty check on the mechanism: BT9 already KILLED the equity bullish-long
cohort (gross −0.25%/trade on real bars). **Options cannot manufacture an edge
that the underlying signal does not have** — a negative-gross directional bet
becomes a *more* negative bet once theta and spread are added. So the real
question this hypothesis tests is narrower: *is there any sub-cohort or holding
profile where the option's convexity + a tighter premium-based stop flips the
sign?* If not, this dies alongside BT9 and that is the expected outcome.

## 2. Expected effect size

- **Direction**: long call (CE)
- **Magnitude**: <e.g. mean premium move +X% over the holding window>
- **Units**: % on premium, and ₹/trade net after costs
- **Sample mechanism estimate**: tie to the delta/gamma of the chosen contract
  and the *underlying* move distribution observed in BT9 — NOT to a hoped-for
  option return. If BT9's underlying move is ~0 net, the honest prior is that
  the option is net-negative by theta + spread.

## 3. Falsification criterion (LOCKED before experimentation)

Pre-register numeric failure conditions. **All must be set before the first run.**

- **Baseline (flat IV)**: net < ₹<__>/trade OR gross premium move < <__>%/trade → KILL
- **Vol-crush stress (entry IV → exit IV × 0.75)**: if the strategy is net-negative
  under crush → KILL. (A directional option play that only survives flat IV is
  selling vol by accident, not capturing the news edge.)
- **Spread stress (2× the modelled slippage, i.e. 200 bps/side)**: net < ₹<__>/trade → KILL
- **vs equity comparison**: if option net/trade ≤ the BT9 equity net/trade on the
  SAME signal cohort → KILL (options added complexity for no gain).

These thresholds cannot be relaxed after seeing results. Wanting to relax one =
the strategy is already dead.

## 4. Data needed

- **Underlying bars**: reuse `replay_lib.fetch_bars` (Yahoo 5m), same cohort.
- **Signals**: `nt_signals`, identical filter to BT9/BT10
  (`signal=bullish, confidence=high, magnitude in {moderate,major}`).
- **Per-symbol contract spec** (NEW data, must be sourced before running):
  - `lot_size` per NSE symbol (varies; e.g. 250/500/1000)
  - `strike_step` per symbol (₹20/₹50/₹100 etc.)
  - which symbols even *have* liquid single-stock options (drop the rest)
- **IV assumption**: baseline IV per run (start flat, e.g. 30%); document the
  source. There is NO real IV without an option feed — this is the load-bearing
  assumption and the reason this is a screen, not a hold-out.

## 5. Train / dev / hold-out split

- **Screen cohort**: 2026-06-02 → 2026-06-12 (same window as BT9/BT10, for a
  direct same-signal comparison).
- **Hold-out**: NOT AVAILABLE with synthetic pricing. A true hold-out requires
  **real intraday option bars** (Kite historical API once live trading exists).
  Until then this hypothesis can reach at most `registered` → screen-decision,
  never `final`/`shipped`. Promotion to paper trading requires a real-bar rerun.

## 6. Code reference

- Pricing/costs: `research/backtests/options_lib.py` (Black-Scholes, Greeks,
  F&O cost table, contract selection)
- Harness: NEW `research/backtests/bt11_options_long.py` — reuses
  `replay_lib.simulate`'s entry-window/dedupe loop; swaps the trade body to:
  select_contract → build premium path via `price_option` across the day's
  bars (shrinking `year_fraction` = theta) → walk SL/TP on the PREMIUM →
  `calc_option_pnl`. Adds `iv_baseline` and `iv_crush` columns.
- **Open design choices to LOCK in §3 before running** (each is a registered
  parameter, not a knob to tune post-hoc):
  - strike: ATM (moneyness 0) vs 1-OTM
  - expiry: nearest weekly vs current monthly
  - SL/TP: defined on PREMIUM (recommended) — e.g. SL −30%, TP +50% premium —
    or mapped from the equity 3%/1% underlying levels
  - holding: reuse 90-min time-stop + 15:15 EOD, or shorter (theta is harsher)

## 7. Result (filled after experiment)

Run: `bt11_options_long.py`, 2026-06-13. Cohort = 145 signals, 35 option trades
(NIFTY500 ex-NIFTY50 ∩ has_options). Budget ₹50k/slot (avg 1.7 lots, ₹39.9k
exposure). 8-cell grid: strike {ATM, 1-OTM} × expiry {near, next} × stop {30/50, 50/100}.

- **Every cell net-NEGATIVE under every regime.** Win rate 37% across the board.
- Baseline net/trade: **−₹1,986 to −₹3,098** (best = ATM/next/30-50; worst = 1-OTM/near/50-100)
- Gross premium move: **−3.4% to −5.2%/trade** (the option loses value on average)
- Vol-crush net/trade: **−₹9,662 to −₹13,808** (4–5× worse — long vega is the killer)
- Spread-2× net/trade: **−₹2,766 to −₹3,926**
- Equity (BT9) same cohort: −0.25%/trade gross, ~−₹68/trade net — options are
  ~30–45× worse in ₹/trade (leverage amplifying a negative-drift signal).
- Delta-attribution: moot — there is no positive delta P&L to attribute; the
  signal's underlying move is ≤0 (BT9), so theta + spread + crush dominate.
- Stop choice barely moves the result: with no underlying edge, stop placement
  only reshuffles a losing distribution.

Process note: run from `draft` (kill-lines in §3 were never numerically locked).
This does not taint the decision — the result is **threshold-independent**: no
cell is positive under any regime, so no admissible kill-line would have passed
it. No hold-out budget consumed (synthetic IV is not a hold-out; the outcome is
a confirm-negative, not a mined positive).

## 8. Decision

- [ ] **PURSUE**
- [x] **KILL** — confirmed. Options inherit and amplify BT9's negative underlying
      drift; theta + wide single-stock spread + vega (crush) make every variation
      net-negative. The news-reaction edge does not exist on the underlying, and
      no option structure can manufacture one. Dead, same as BT9. No second look.
- [ ] **ITERATE** — not warranted on this signal. A future options hypothesis
      would need a DIFFERENT underlying edge (one with proven positive drift on
      real bars) before an option wrapper is worth testing.
