---
slug: largecap-fade
strategy: news_trader_largecap_fade
status: killed
registered_at: '2026-06-11'
finalized_at: '2026-06-11'
decided_at: '2026-06-11'
hypothesis_hash: ''
---

# Hypothesis: Fading high-conf moderate/major news signals on NIFTY50 stocks (short bullish news, long bearish news) is gross-positive intraday

## 1. Mechanism

Heavily covered, highly liquid large-caps fully price news before our
15-minute-delayed entry (BT5: NIFTY50 momentum-side trades were
gross-NEGATIVE, −₹363 on n=7 — there was no drift left). If the initial
move also overshoots (retail/algo pile-in on headlines), the post-entry
path mean-reverts, making the contrarian side positive rather than merely
zero. BT6 logged this as an n=7 in-sample observation with explicit
"do NOT build" status; this experiment is the pre-registered test on the
full signal-level cohort (~50+ NIFTY50-named high-conf signals, most never
traded).

## 2. Expected effect size

- **Direction**: fade — short on bullish news, long on bearish news;
  intraday only
- **Magnitude**: +0.2–0.4% gross per trade over ≤90 min
- **Units**: % gross per trade; ₹ at ₹9,400/position
- **Sample mechanism estimate**: if large-cap momentum side is ~−0.5%
  gross (BT5 measured −₹363/7 trades ≈ −0.55%/trade), the fade captures
  some fraction of it; haircut to 0.2–0.4% for noise

## 3. Falsification criterion (LOCKED before experimentation)

Single shot. Same mirrored deployed exit policy (TP +1%, SL 3%, time-stop
90 min, EOD 15:15 IST), entry at first 5-min bar ≥ signal + 15 min,
entries 09:30–14:30 IST. No sweeps.

- **KILL if** gross expectancy per trade ≤ 0
- **KILL if** the momentum side (trading WITH the signal, same cohort,
  same exits) is ALSO gross-positive — then "fade" is just noise riding
  market beta, not an overreaction effect
- **NOT VIABLE if** net expectancy < −₹10/trade under 2× slippage stress
- Thresholds cannot be relaxed after seeing results

## 4. Data needed

- `nt_signals`: confidence=high, magnitude ∈ {moderate, major}, signal ∈
  {bullish, bearish}, stocks[] containing ≥1 NIFTY50 member, 2026-06-02 →
  2026-06-11 (only the NIFTY50 names in each signal are traded)
- NIFTY50 set: `apps/signal-engine/src/news_trader/nifty50.py`
- Price paths: Yahoo Finance 5-min bars; cost model as deployed (shorts
  cost model for fade-shorts, long model for fade-longs)

## 5. Train / dev / hold-out split

Same caveat as news-shorts-replay: 8-day dev window, true hold-out is
forward data. Overlap warning: 7 of the BT5 trades (the genesis
observation) are inside this cohort — results will be reported with and
without those 7 symbols-days to show the untainted subset.

## 6. Code reference

- Replay script: `research/backtests/bt8_largecap_fade.py`
- Dedupe: one position per symbol at a time

## 7. Result (filled after experiment — single run 2026-06-11, no re-runs)

- Gross expectancy/trade: **−₹3.3 (−0.026%)** full / **−₹6.2 (−0.056%)**
  excluding the 22 BT5-overlap symbol-days — both ≤ 0
- Net expectancy/trade: −₹22.8 nominal / −₹32.3 under 2× slippage
- Win rate: 39/85 (46%)
- Momentum-side gross (control, must be ≤ 0): **+₹2.7/trade (+0.019%)** —
  positive → criterion 2 ALSO triggered; the BT5 "large-cap gross-negative"
  finding does not reproduce at signal level (n=7 trade-level was noise)
- n trades: 85 (65 signals; drops: 60 entity-merge dedupe — large-caps get
  heavy duplicate coverage — 14 position-open, 4 outside window)
- Both directions ≈ zero gross → large-caps are efficient w.r.t. this news
  feed: no drift AND no overreaction. NIFTY50 exclusion stays justified
  (nothing to capture), but a fade lane is dead too
- Trade-level data: `research/backtests/bt8_trades_{fade,momentum}.csv`

## 8. Decision

- [ ] **SHIP** (= promote to its own paper-trading lane before any build)
- [x] **KILL** — criterion 1 (fade gross ≤ 0) and criterion 2 (momentum side
      gross-positive) both triggered. Never re-open
- [ ] **ITERATE** — new hypothesis file required
