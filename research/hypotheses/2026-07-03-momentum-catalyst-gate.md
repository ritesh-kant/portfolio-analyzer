---
slug: momentum-catalyst-gate
strategy: momentum_catalyst
status: registered
registered_at: '2026-07-03'
finalized_at: ''
decided_at: ''
hypothesis_hash: 'sha256:a36abe440c7b50c8146cdd2ec4e0564ceb4d305e3dbd2208eb0035a4be8dac91'
---

# Hypothesis: Among *liquid, tradeable-band* NSE stocks that are already moving up intraday on high relative volume, the subset with a **fresh fundamental catalyst** continues (intraday, open→close / entry→close) while the catalyst-less subset does not; a catalyst-gated long book therefore (a) clears realistic cost-stress and (b) beats the catalyst-less subset by a correctly-signed margin. The catalyst is a **quality gate on confirmed momentum**, not a predictive trigger.

> ⚠️ This INVERTS the dead news-trader's data flow. Old: headline → predict
> reaction → buy (BT7–BT12, KILLED — the headline is priced by the time we act).
> New: **price+volume move FIRST (the market has already voted) → confirm a
> catalyst exists → ride continuation.** Confirmation, not prediction.
>
> ⚠️ It is NOT the pure gap-momentum test (`lowfloat-gap-momentum` / bt15): that
> asks "do up-gappers continue?" (the unfiltered pool). THIS asks "does a fresh
> catalyst separate the continuers from the faders inside that pool?" — the
> catalyst-split IS the edge, tested as a spread, exactly like the `event_type`
> A−B test that killed the news-trader.
>
> ⚠️ HARD HEADWIND, stated up front. BT14 (short-term-reversal) already measured
> what the day's top winners do next: they **fade** (short-winners gross +0.27%).
> So "buy the winners" is the losing leg of the one real signal we found. The
> entire burden of this hypothesis is that the **catalyst flag** picks out the
> minority of winners that buck the fade. If it can't, this dies — and that is a
> perfectly good, cheap answer.

## 0. Why this, and why it might (and might not) work

The user's four-step idea: (1) get top gainers, (2) find recent news for them,
(3) filter on the video's criteria (real catalyst + high demand + tradeable),
(4) trade the continuation. Reframed as a falsifiable research question: **does a
fresh catalyst predict intraday continuation among confirmed high-RVOL movers,
after costs, in names we can actually trade?**

Why it *could* work (unlike the news-trader): we no longer forecast the reaction
— we require the move to be underway (momentum confirmation) AND require an
economic reason (catalyst) AND require tradability (liquid, not circuit-locked).
The catalyst-gate is a *selection* claim, and selection can turn a net-negative
pool net-positive if the good subset is genuinely positive (a filter is not just
cost-neutral subsetting — this is why §3 gates the *gated book*, not the pool).

Why it probably *won't* (the three walls, in order):
1. **Entry feasibility (the Gate −1 wall, seen live).** The operator's own
   "Top gainers of the day" screen (2026-07-03) returned Gold Coin +109 % on
   **1,649 shares** (~₹55k traded ALL DAY — a ₹50k order is the whole market),
   and 4 of the top 5 **pinned at the 20 % circuit band** (Spectrum/Vision/Elgi/
   Ghushine — limit-up = zero sellers = no fill). **The EOD top-gainers list is a
   machine for surfacing untradeable names.** The tradeable version must catch
   moderate moves *intraday-early* in the *liquid* universe, before the lock.
2. **The fade (BT14).** Winners fade; the catalyst must overcome −0.27 %/trade
   of adverse drift just to break even on the momentum side.
3. **Costs.** Same wall as every prior result; stressed at +40 bps/side here.

Cost-stress + spread-sign are evaluated FIRST, biased to KILL.

## 1. Mechanism

Momentum ignition with an information anchor: a genuine catalyst (earnings beat,
large order/contract, regulatory approval, corporate action) gives slow-diffusing
buyers a reason to keep lifting the stock through the session, so the *catalyst*
subset exhibits post-move continuation while the *catalyst-less* subset is noise/
mean-reversion (the BT14 fade). This is the smaller-cap slow-diffusion driver
(same family as PEAD) applied intraday to already-confirmed movers. If catalyst
and no-catalyst gappers continue *identically*, there is no information effect —
just volatility — and the gate adds nothing (KILL).

## 2. Expected effect size

- **Direction**: long the catalyst-gated up-movers, intraday entry→close.
- **Magnitude**: catalyst-minus-no-catalyst continuation **spread +0.4 to +1.0 %/
  trade**; catalyst-gated absolute continuation +0.3 to +0.8 %/trade gross.
- **Units**: %/trade gross & net; catalyst-vs-nocatalyst spread; cohort Sharpe.
- **Sample mechanism estimate**: anchored to PEAD slow-diffusion (0.5–2 % over
  days in low-coverage names, a fraction realized intraday) and momentum-ignition
  literature — NOT to any past run. Round-trip cost on a wide-spread mover ≈
  0.6–1.0 %; thresholds set above that to bias toward KILL.

## 3. Falsification criterion (LOCKED before experimentation)

**Gate 0 — COST-STRESS on the catalyst-gated book, evaluated FIRST.** Long the
catalyst-present up-movers, enter at the scan-trigger price (forward: the flag
bar; historical proxy: day open), exit at close, intraday MIS. Production cost
model **+40 bps/side** stress; cap exit fill at the band if the name locked. **If
the catalyst-gated mean NET return per trade ≤ 0 under cost-stress → KILL**, before
any other metric.

If Gate 0 passes, all must hold (dev/forward):
- **Catalyst spread ≥ +0.40 %/trade AND correctly signed**: mean continuation
  (catalyst) − mean continuation (no-catalyst) ≥ +0.40 %. *(The `event_type`
  lesson: the spread there was WRONG-signed — info drifted worse than PR noise.
  A spread ≤ 0 here = catalyst adds nothing → KILL.)*
- **Beats the label-shuffle anti-strategy**: permute catalyst/no-catalyst labels
  across the mover pool (group sizes preserved); the shuffled spread must be
  indistinguishable from 0 (p ≥ 0.10 that real > shuffled → KILL: the split is
  random, not the catalyst).
- **Beats the beta control**: catalyst-gated continuation − same-day equal-weight
  universe return ≥ **+0.30 %/trade** (else it is long-beta, the cs-momentum trap).
- catalyst-gated cohort net Sharpe (stressed) ≥ **0.5**
- n catalyst-gated trades ≥ **30**

**Hold-out / forward-confirm (touched once, only if the above pass):** stressed
catalyst-gated net > 0 AND spread > 0 AND beta-alpha > 0. Else KILL.

**🔒 No-relax rules (locked):**
- Thresholds cannot be loosened after seeing results.
- **The catalyst definition is frozen (see §4) BEFORE capture.** We do not, after
  a null, redefine "catalyst" to whatever subset happened to work — that is
  relabelling to fit, the pead-yoy/ml/n violation. A new catalyst definition = a
  new hypothesis file.
- **Tradeable universe = NIFTY 500; tradeable band only** (gap in [+2 %, +10 %],
  not circuit-locked). We do NOT reach into SME/T2T/microcaps to find bigger
  movers — that is the untradeable segment (see Gate −1 in `lowfloat-gap-momentum`),
  a different capacity-stressed hypothesis.
- Single shot. Primary horizon = entry→close intraday. 1 %-target / stop and
  next-day variants are reported secondaries, not extra shots at PASS.
- **No live money on a forward PASS without a hold-out confirm window** (same as
  PEAD): a forward Gate-0 pass licenses a second confirm window, not capital.

## 4. Data needed & the frozen catalyst definition

- **Daily OHLCV, NIFTY 500** — have (Yahoo, `1d`, cached from bt14/bt15). Base
  pool + historical proxy.
- **Intraday context for tradeable-band + RVOL + early entry** — forward only
  (Yahoo 5-min only ~60 days back; a broad historical intraday panel is not held).
  So the catalyst-gate is a **FORWARD-CAPTURE** test (like PEAD/bt13), not a
  2025-H2 backtest. The base pool (bt15, daily) runs now for context.
- **Catalyst flag — FROZEN definition (locked now, before capture):** a mover is
  `catalyst=1` iff, within the 24 h before the scan trigger, there is a **hard
  corporate event**: results/earnings, an order/contract win, a regulatory
  approval/clearance, or a board-decided corporate action (M&A, buyback,
  bonus/split, fundraise). `catalyst=0` = price/volume move with **no** such event
  (pure technical run, or only promoter/PR/rumour chatter). Source: the existing
  RSS/`nt_*` classifier + NSE announcements; ambiguous → `catalyst=0` (conservative,
  shrinks the treatment group, biases toward KILL). **No sentiment/direction from
  the classifier is used** — only the presence of a hard event (the news-trader's
  sentiment step is exactly what failed).
- **Mover scan (steps 1–2 of the idea):** daily, the NIFTY 500 up-movers in the
  tradeable band with RVOL ≥ 3× (forward: intraday; proxy: gap + prior-vol surge).
  Capture ticker, trigger time/price, gap, RVOL, catalyst flag, close.
- **Cost model** — production intraday MIS + 40 bps/side stress + band-cap.

PIT discipline: catalyst flag frozen at/before the trigger (no post-hoc news);
entry at the trigger bar; exit at close; universe membership as-of the date.

## 5. Train / dev / hold-out split

Forward-only temporal split (catalyst data accrues live), as in PEAD:
- **Dev/accumulation**: from 2026-07-04 forward until ≥ 30 catalyst-gated trades.
- **Hold-out/confirm**: the subsequent equal-or-longer window (NEVER read until
  dev passes). Split boundary fixed at the moment dev reaches n = 30.
- Base pool (bt15, daily gap-momentum) uses the 2025-H2 dev window for CONTEXT
  only — it is not a gate on the catalyst hypothesis (a filter can rescue a
  negative pool), but it tells us the fade magnitude the catalyst must overcome.
- No fitted parameters (thresholds frozen in §3/§4), so no train set.

## 6. Code reference

- Base pool: `research/backtests/bt15_lowfloat_gap.py` (daily gap-momentum
  cost-stress; run for the unfiltered continuation number).
- Catalyst-gate: `research/backtests/bt16_momentum_catalyst.py` (forward logger +
  the LOCKED Gate-0 / spread / shuffle-anti screen; reads the capture log, fetches
  Yahoo prices, computes entry→close continuation, catalyst-vs-nocatalyst spread,
  and the label-shuffle p-value at n ≥ 30). Mirrors bt13's forward pattern.
- Capture log: `research/backtests/bt16_catalyst_log.csv` (accumulating; one row
  per scanned mover: date, symbol, trigger_time, trigger_px, gap_pct, rvol,
  catalyst, close_px, event_type_note).
- Cost model: `apps/signal-engine/src/news_trader/trailing_sl.py` (`calc_costs`).

## 7. Result (filled after experiment — single dev run, no re-runs)

- Base pool (bt15, daily 2025-H2) unfiltered continuation net (context): **KILLED 2026-07-03 — gross −0.35 %/trade (gappers FADE), net ₹−691, Sharpe −3.6.** The pool the catalyst filter draws from has *negative* intraday drift, so the catalyst must flip faders into >+0.9 % continuers (to clear +40 bps stress + the +0.40 % spread floor) — a high bar. ⚠️ But bt15 buys the FULL gap at the open and holds to close (captures opening-gap mean-reversion); the catalyst hypothesis enters *intraday-early on building RVOL*, a different entry — so this is a strong negative prior on the pool, NOT a kill of the forward catalyst test.
- Gate 0 catalyst-gated stressed net/trade (must be > 0): <fill>
- Catalyst spread (catalyst − no-catalyst, %/trade; floor +0.40, must be signed +): <fill>
- Label-shuffle anti p-value (real > shuffled; must be < 0.10): <fill>
- Beta-control alpha (floor +0.30 %/trade): <fill>
- n catalyst-gated trades / no-catalyst trades: <fill>
- Cohort net Sharpe (stressed): <fill>
- Hold-out/confirm: <NOT touched until dev passes>

## 8. Decision

- [ ] **SHIP-to-confirm** — Gate 0 passed AND spread ≥ +0.40 % signed AND anti
      p < 0.10 AND beta-alpha ≥ +0.30 % AND n ≥ 30 → open the hold-out confirm
      window (NOT live money yet).
- [ ] **KILL** — Gate 0 net ≤ 0, OR spread ≤ 0 / < +0.40 %, OR shuffle-anti not
      beaten, OR beta-alpha < +0.30 %, OR (after confirm) hold-out failed. No
      second look; no catalyst-definition redefinition to fit.
- [ ] **ITERATE** — requires a NEW hypothesis file with an explicit delta,
      registered before any further capture.
