---
slug: short-term-reversal
strategy: short_term_reversal
status: killed
registered_at: '2026-06-29'
finalized_at: '2026-06-29'
decided_at: '2026-06-29'
hypothesis_hash: 'sha256:765201e633788bf12c1dd94ef05df3b356eaaa2453f739d89ccfb8ed9d7f1f6c'
---

# Hypothesis: NSE stocks among the day's largest losers bounce, and the day's largest winners fade, over the next 1 trading day; a daily cross-sectional book that buys the bottom-N and shorts the top-N captures this reversal — but only gross, and the open question (the entire test) is whether anything survives realistic round-trip costs.

> ⚠️ This is a NEW hypothesis, not a variation on the killed RSS news-trader
> (BT7–BT12, committed stop) nor on the registered earnings-surprise PEAD
> (different signal: price-only reversal, no fundamentals in the base test).
> The fundamental/sector overlay the operator proposed is explicitly **Phase 2**
> and is NOT part of this hypothesis — we test the raw price signal first so the
> overlay cannot be used to rescue a dead base (the discipline that killed the
> pead-yoy/ml/n proxies: no knob-adding after a null base result).

## 0. Why this, and why expect it to fail on costs

Short-term reversal (Jegadeesh 1990; Lehmann 1990) is one of the most-replicated
return anomalies: yesterday's relative losers out-perform yesterday's relative
winners over horizons of 1 day to 1 week. **Gross**, the effect is real and has
been documented on NSE as well (Sehgal & Balasubramanian and others).

It is also the textbook example of an edge that **dies net of transaction costs.**
Avramov, Chordia & Goyal (2006) show reversal profits are concentrated in
high-turnover, illiquid, high-spread names and are *largely consumed by trading
costs* — and the strategy demands **daily rebalancing of ~20 positions**, so it
pays round-trip cost ~20×/day, every day. The biggest daily movers also carry the
**widest bid-ask spreads** — cost is largest precisely on the names we trade.

This is the same wall the news-trader hit six times: gross ≈ flat-to-positive,
costs turned it net-negative. So the falsification gate below is **cost-first**:
if the stressed-cost net is ≤ 0 we KILL before reading any other metric. The
test is deliberately cheap (daily bars we already fetch) so a null result costs
one day, not 19.

## 1. Mechanism

Two non-exclusive drivers:
1. **Liquidity provision / overreaction.** A large 1-day move is often a liquidity
   demand shock (forced selling, a headline, an index flow). Liquidity providers
   who absorb it require compensation, which reverts the price partially over the
   next day as the shock clears. Behaviourally, retail over-extrapolates the move
   and over-shoots.
2. **Microstructure (bid-ask bounce).** Closing prints alternate between bid and
   ask; a stock that closed on the bid (a "loser") mechanically prints higher next
   tick. This component is **pure illusion** — it does not survive crossing the
   spread, which is exactly why the cost gate is the whole ballgame.

Driver 1 is tradeable if it dominates driver 2 *and* clears costs. The base test
cannot separate them; the cost-stress gate is what protects us from trading
driver 2.

## 2. Expected effect size

- **Direction**: spread — long bottom-N (losers), short top-N (winners), daily
- **Magnitude**: gross long-short **+10 to +40 bps per day** in liquid names
  (literature: 1-day reversal is small in large/mid caps, larger but un-tradeable
  in micro-caps). We claim the front of that range for NIFTY 500.
- **Units**: bps of gross / net return per trade; daily cohort Sharpe
- **Sample mechanism estimate**: tied to the reversal literature above, NOT to any
  prior run. Realistic round-trip cost (delivery STT 0.2% + stamp + exchange +
  stressed slippage on volatile movers) is **~0.4–0.6%**, so for the spread to be
  net-positive the gross reversal must exceed that — which the literature says it
  usually does not in tradeable names. Threshold below is set to bias toward KILL.

## 3. Falsification criterion (LOCKED before experimentation)

**Gate 0 — COST-STRESS, evaluated FIRST (the lesson from every prior result).**
Primary config = **Config A (intraday next-day): enter D+1 open, exit D+1 close**,
both legs feasible as intraday MIS (shorts allowed intraday). Apply the production
intraday cost model **plus stress slippage of +10 bps/side** (wider spreads on the
biggest movers). **If the dev mean long-short NET return per trade ≤ 0 under
cost-stress → KILL immediately**, before reading any other metric.

If Gate 0 passes, the standard battery (dev), all must hold:
- gross long-short reversal ≥ **+0.30%/trade** (must clear realistic costs with margin)
- **reversal beats its anti-strategy**: the anti book (short losers / long winners
  = momentum) gross must be ≤ 0 AND strictly below the reversal gross. If the anti
  book is also positive, the "edge" is volatility harvesting / noise, not reversal.
- daily cohort net Sharpe ≥ **0.5** (annualised, on the stressed-net daily series)
- n trading days ≥ **30**

**Hold-out (2026-01-01 → present; touched once, only if all dev gates pass):**
stressed-net long-short > 0 AND daily cohort Sharpe ≥ **0.4**. Else KILL.

**🔒 No-relax rules (locked):**
- Thresholds cannot be loosened after seeing results.
- **No fundamental/sector overlay in this hypothesis.** If the base price signal
  fails, we do NOT add fundamentals to rescue it — that is a NEW hypothesis with
  an explicit delta, registered separately, and only if the base showed a pulse.
- Single shot. Primary horizon = intraday next-day (Config A). The overnight
  long-only and D+1→D+3 variants are reported secondaries, not extra shots at PASS.
- Universe = NIFTY 500 (the liquid set in `nifty500.py`). We do NOT widen to
  micro-caps to manufacture a bigger gross number — that is where the effect is
  largest *and* least tradeable, and would be a different (capacity-stressed)
  hypothesis.

## 4. Data needed

- **Daily OHLC bars** for the NIFTY 500 universe — have (Yahoo via yfinance,
  `interval="1d"`, disk-cached). Bhavcopy is the production-grade alternative if
  Yahoo coverage proves thin; not needed for the screen.
- **Universe file** — `news_trader/nifty500.py` (504 names). ⚠️ Membership is
  **as-of today** applied to a 2025 window → mild **survivorship bias** (favours
  the strategy). A KILL under this favourable bias is therefore robust; a PASS must
  be re-checked with a point-in-time universe before any further build.
- **Cost model** — production intraday MIS (`trailing_sl.calc_costs`) for Config A;
  a local **delivery** cost model (STT 0.1%/side both sides, stamp 0.015% buy,
  exchange/SEBI, stressed slippage) for the overnight long-only secondary.
- No fundamentals, no consensus EPS, no Trendlyne — Phase 2 only.

PIT discipline: ranking on day D uses only close[D] / close[D−1] (known at D close);
entry is D+1 open (strictly after the ranking information); no same-bar look-ahead.

## 5. Train / dev / hold-out split

- **Dev**: 2025-07-01 → 2025-12-31 (~125 trading days)
- **Hold-out**: 2026-01-01 → 2026-06-27 (NEVER touched until dev gates pass; the
  bt14 script runs **dev only** and hard-codes the dev window)

No train set needed — there are no fitted parameters; N (=10) and the horizon are
fixed by the hypothesis, not tuned. (If a future variant tunes N or horizon, that
requires a train set and a new hypothesis file.)

## 6. Code reference

- Backtest: `research/backtests/bt14_reversal.py` (self-contained `uv run` script).
- Cost model: `apps/signal-engine/src/news_trader/trailing_sl.py` (`calc_costs`,
  imported, not copied) + a documented local delivery model in bt14 for the
  overnight leg.
- Trades dump: `research/backtests/bt14_trades_*.csv`.

## 7. Result (single dev run, 2026-06-29, no re-runs)

Universe coverage: 500/504 NIFTY 500 names had Yahoo daily bars. Dev window
2025-07-01→2025-12-31: **126 trade-days, 2,520 trades**, ₹50,000/name.

- **Gate 0 — Config A stressed-net mean per-trade (long-short): ₹−177.3 (−0.3547%/trade) → FAIL → KILL.**
- Config A gross long-short: **+0.0448%/trade** (vs +0.30% floor → FAIL; ~10× too small to clear the ~0.4% round-trip cost)
- Daily cohort net Sharpe (stressed): **−7.71**
- Anti-strategy (momentum) gross: −0.0448%/trade (exact mirror — the spread is so close to zero that flipping sides just negates it; no distinct reversal edge)
- n trade-days ≥ 30: PASS (only criterion that passed)
- Secondary — overnight long-only losers (D+1 open→D+2 open, delivery, stress): **₹−254.6/trade (−0.51%)**
- Secondary — D+1→D+3 long-only losers (delivery, stress): **₹−393.9/trade (−0.79%)** (worse — drift is *down*, not a bounce)
- **Leg decomposition (the diagnostic):**
  - long-losers gross **−0.1775%/trade** — losers kept *falling* through the next session. Any overnight bounce (D-close→D+1-open gap) is gone by the time we can enter at D+1 open; intraday they resume sliding (falling knives, exactly the `sl_hit` pattern from the live news log).
  - short-winners gross **+0.2671%/trade** — winners *do* fade next day (the one real, correctly-signed effect), but +0.27% < the ~0.35% stressed round-trip → net **₹−67.5/trade**. The edge exists and is still eaten by costs.
- Hold-out: **NOT touched** (dev failed Gate 0; hold-out lock never released).

## 8. Decision

- [ ] **SHIP** — Gate 0 passed AND all dev gates passed AND hold-out passed
- [x] **KILL** — Gate 0 failed: stressed-net −₹177/trade, gross spread +0.045%/trade
      (~10× below costs), Sharpe −7.71. Every config (intraday, overnight, 3-day) is
      net-negative under stress. The textbook cost-fragile-anomaly outcome, confirmed
      on real NSE bars in one day. No second look; **no fundamental-overlay rescue of
      a dead base** (the locked no-relax rule — the Phase-2 overlay was contingent on a
      gross pulse, and there is none: the spread is +0.045% ≈ noise).
- [ ] **ITERATE** — requires a NEW hypothesis file with an explicit delta, registered
      before any further run.

**Note for any future revisit:** the *only* correctly-signed leg was short-winners
(+0.27% gross), still < costs. The long-loser leg was gross-*negative* intraday —
the reversal lives in the overnight gap we cannot enter before. A version that
entered at the D close (not D+1 open) to capture the gap would change the entry
assumption and is a different, separately-registered hypothesis — and it trades the
illiquid close auction, where slippage is worst. Not pursued.
