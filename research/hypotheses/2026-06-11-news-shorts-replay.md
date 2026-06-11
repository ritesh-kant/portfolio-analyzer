---
slug: news-shorts-replay
strategy: news_trader_shorts
status: killed
registered_at: '2026-06-11'
finalized_at: '2026-06-11'
decided_at: '2026-06-11'
hypothesis_hash: ''
---

# Hypothesis: Shorting bearish high-conf moderate/major news signals on non-NIFTY50 NIFTY500 stocks is gross-positive intraday

## 1. Mechanism

Negative news produces post-news drift that survives a 15-minute delay in
mid/small-cap names because (a) short-sale frictions and lower analyst
coverage slow price incorporation, and (b) the documented asymmetry that
bad-news drift is stronger and faster than good-news drift. BT5 showed the
long side of this exact pipeline is gross-positive only outside NIFTY50
(news fully priced in large-caps before our entry), so the same liquidity
screen is applied here a priori, not data-mined.

This cohort (74 bearish high-conf moderate/major signals, 2026-06-02 →
2026-06-11) was **never traded** — the long-only gate discarded all of them
— so no exit/filter parameter was ever tuned on its price paths.

## 2. Expected effect size

- **Direction**: short, intraday only (MIS-compatible, EOD close 15:15 IST)
- **Magnitude**: +0.3–0.6% gross per trade over ≤90 min hold
- **Units**: % gross move per trade; ₹ expectancy at ₹9,400/position
- **Sample mechanism estimate**: mirror of the measured long-side
  entry-anchored drift (+0.46% at 60–90 min, n=33), scaled up modestly per
  the bad-news-drift asymmetry literature; NOT derived from any backtest of
  this cohort

## 3. Falsification criterion (LOCKED before experimentation)

Single shot. One exit policy (the deployed one, mirrored for shorts:
TP +1%, initial SL 3%, time-stop 90 min, EOD close 15:15 IST). No
parameter sweeps. Entry at first 5-min bar open ≥ signal_time + 15 min,
entries only 09:30–14:30 IST.

- **KILL if** gross expectancy per trade ≤ 0
- **KILL if** anti-strategy (going LONG the identical signal set, same
  exits mirrored) is also gross-negative while the short side is positive
  only marginally (< +0.15%/trade) — that pattern = transaction-cost noise,
  not signal
- **NOT VIABLE (even if signal real) if** net expectancy < −₹10/trade under
  cost stress (slippage at 2× nominal, i.e. 0.10%/side)
- Thresholds cannot be relaxed after seeing results

## 4. Data needed

- `nt_signals` (MongoDB): bearish, confidence=high, magnitude ∈
  {moderate, major}, 2026-06-02 → 2026-06-11, stocks[] non-empty
- Universe: NIFTY500 whitelist minus NIFTY50
  (`apps/signal-engine/src/news_trader/nifty500.py`, `nifty50.py`)
- Price paths: Yahoo Finance 5-min bars (`<SYMBOL>.NS`) for signal dates.
  PIT note: bars are exchange-timestamped; entry uses the first bar opening
  at/after signal created_at + 15 min (mirrors live SQS delay)
- Cost model: intraday MIS as deployed D7 (brokerage min(0.03%, ₹20)/leg,
  STT 0.025% sell leg, stamp 0.003% buy leg, exch 0.00297%, SEBI, GST 18%,
  slippage 0.05%/side nominal)

## 5. Train / dev / hold-out split

Not applicable in the rebuild sense — this is an 8-day paper-trading
window, all of it used as the dev sample. **The true hold-out is forward
paper trading**: if this passes, the decision is to keep/enable paper
shorts (already deployed) and judge on forward closed shorts, not to
re-run this replay with tweaks. Same 8 days as BT1–BT6, but this cohort's
price paths were never used in any prior analysis.

## 6. Code reference

- Replay script: `research/backtests/bt7_shorts_replay.py` (this experiment)
- Deduplication: one open position per symbol at a time; a later signal on
  a symbol already in a simulated position is skipped (mirrors live gate)
- Portfolio capacity/sector caps NOT simulated — this measures signal
  quality per trade, not portfolio construction

## 7. Result (filled after experiment — single run 2026-06-11, no re-runs)

- Gross expectancy/trade: **+₹4.6 (+0.045%)** — positive but far below the
  +0.15% marginality floor
- Net expectancy/trade (nominal costs): **−₹14.6**
- Net expectancy/trade (2× slippage stress): **−₹24.0** (viability floor was
  −₹10 → breached)
- Win rate: 22/41 (54%)
- Anti-strategy (long same signals) gross: **−₹15.0/trade (−0.151%)** —
  gross-negative, so criterion 2 pattern (noise, not signal) is met
- n trades: 41 (69 signals → expand to non-NIFTY50 NIFTY500 symbols; drops:
  6 outside entry window, 7 entity-merge dedupe)
- Exit mix: 34 time_stop / 6 target_hit / 1 sl_hit — price paths are flat;
  bearish news in this window produced no monetizable downward drift
- Trade-level data: `research/backtests/bt7_trades_{short,anti}.csv`

## 8. Decision

- [ ] **SHIP** (= keep paper shorts enabled; validate forward)
- [x] **KILL** — falsification triggered (criterion 2: marginal gross + anti
      gross-negative = cost-noise; criterion 3: cost-stress −₹24 < −₹10).
      Recommend disabling `nt_enable_shorts` (config change left to owner —
      live forward paper shorts would at least produce out-of-window data,
      but per pre-registration the replay verdict is KILL)
- [ ] **ITERATE** — requires a new hypothesis file
