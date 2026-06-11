---
slug: bullish-signal-replay
strategy: news_trader_long
status: killed
registered_at: '2026-06-11'
finalized_at: '2026-06-11'
decided_at: '2026-06-11'
hypothesis_hash: ''
---

# Hypothesis: The deployed long strategy (bullish high-conf moderate/major, non-NIFTY50) has gross expectancy ≥ +0.25%/trade at signal level — enough to clear the ~0.21% cost floor

## 1. Mechanism

Post-news drift in mid/small-caps (lower coverage → slow incorporation),
already measured on the traded subset: entry-anchored drift +0.46% at
60–90 min (n=33, D7 analysis). The traded subset is capacity-censored —
sector caps and portfolio_full blocked many signals on busy days — so the
realized 48 trades under-sample the cohort. Replaying ALL eligible bullish
signals measures the strategy's true per-trade expectancy at roughly 2× n.

**Pre-registered secondary split (one, fixed in advance):** magnitude
`major` vs `moderate`. Mechanism: larger surprises take longer to price in
(bigger underreaction), so majors should carry more drift. This is the only
split that will be read; no other slicing of this cohort will be performed.

## 2. Expected effect size

- **Direction**: long, intraday
- **Magnitude**: +0.2–0.4% gross per trade cohort-wide; majors +0.3–0.6%
- **Units**: % gross per trade; ₹ at ₹10,000/position
- **Sample mechanism estimate**: prior-measured MFE drift +0.46% with the
  deployed TP1%/t90 policy capturing roughly half of it

## 3. Falsification criterion (LOCKED before experimentation)

Single shot. Deployed exit policy (TP +1%, SL 3%, time-stop 90 min, EOD
15:15), entry first 5-min bar ≥ signal + 15 min, 09:30–14:30 IST entries,
universe NIFTY500 minus NIFTY50, entity-merge dedupe. No sweeps.

- **PASS if** cohort gross expectancy ≥ +0.25%/trade (clears cost floor)
- **KILL if** cohort gross expectancy < +0.10%/trade — the current
  selectivity cannot become profitable at retail costs; new edge source or
  cost reduction required, more paper days will not change that
- **Between +0.10% and +0.25%**: NOT VIABLE at current costs but signal
  real — decision is "attack costs, do not add filters"
- **Majors selectivity recommendation requires** majors gross ≥ +0.30%/trade
  AND n(majors) ≥ 15; otherwise no config change
- In-sample contamination control: results reported separately for the
  subset of signals that actually produced live positions (exit policy was
  tuned on those paths) vs never-traded signals. The never-traded subset is
  the weightier number

## 4. Data needed

- `nt_signals`: bullish, high conf, moderate/major, 2026-06-02 → 2026-06-11
- Yahoo Finance 5-min bars; production cost model (long direction)
- `nt_positions` symbols+dates to tag the traded-overlap subset

## 5. Train / dev / hold-out split

8-day dev window; true hold-out = forward paper days. Partial overlap with
the 48 traded positions is explicitly tagged (see §3).

## 6. Code reference

- `research/backtests/bt9_bullish_replay.py` (+ shared `replay_lib.py`)

## 7. Result (filled after experiment — single run 2026-06-11, no re-runs)

- Cohort gross expectancy/trade: **−0.251% (−₹24.0)** — far below the
  +0.10% kill line, let alone the +0.25% pass line
- Never-traded subset gross: **−0.296%/trade** (n=27) — the clean subset is
  WORSE, so capacity censoring was flattering the traded numbers
- Traded-overlap subset gross: −0.202%/trade (n=25)
- Majors vs moderates: majors **−0.335%** (n=20) vs moderates −0.198%
  (n=32) — mechanism INVERTED; no selectivity upgrade exists here
- Net −₹43.7/trade; cost-stress −₹53.3/trade
- n trades: 52 (121 signals; 16 merge-dedupe, 7 outside window)
- Engine validated by spot-check against recorded fills (BHEL 06-11 replay
  −₹28.6 gross vs actual −₹32.5, same exit reason). Divergences from prior
  positive readings explained: BT5/D7 numbers came from capacity-censored
  traded paths recorded via 15-min-DELAYED quotes, largely under the old
  multi-day exit policy — real exchange bars over the full cohort do not
  show the drift

## 8. Decision

- [ ] **PASS** — gross ≥ +0.25%: system viable as-is, focus on execution
- [ ] **NOT VIABLE AT CURRENT COSTS** (+0.10–0.25%) — attack costs only
- [x] **KILL** (< +0.10%) — current selectivity dead on this window. The
      bullish high-conf news cohort as classified today has no monetizable
      drift; exit/execution tuning cannot fix a negative-gross signal.
      Path forward is signal-side: better feeds / multi-source
      corroboration / event-type classification — or accept the forward
      paper run as the final word if it agrees
