---
slug: lowfloat-gap-momentum
strategy: lowfloat_gap_momentum
status: killed
registered_at: '2026-07-03'
finalized_at: '2026-07-03'
decided_at: '2026-07-03'
hypothesis_hash: ''
---

# Hypothesis: NSE stocks that gap up hard at the open on abnormally high volume ("low float + high demand = rate of change" — the Warrior Trading retail-momentum setup) continue in the gap direction intraday (open→close), and a systematic long-the-up-gappers book captures that continuation — but only gross, and the entire test is whether anything survives the *widest-spread names in the market* plus NSE circuit-band truncation.

> ⚠️ This is a NEW hypothesis prompted by a Ross Cameron / Warrior Trading
> "How To Start Day Trading in 2026" video (low-float, high-relative-volume
> small-cap momentum). It is NOT a variation on the killed RSS news-trader
> (BT7–BT12, committed stop), the killed short-term-reversal (BT14), or the
> registered earnings-surprise PEAD. It is the *opposite sign* of BT14
> (continuation, not reversal) on an *event-selected* subset (gappers), intraday.
>
> ⚠️ STRONG PRIOR TOWARD KILL. Cross-sectional price momentum has been killed
> three times in this repo — `cs-momentum` (anti-DSR 0.913 > 0, = bull-beta),
> `qf-momentum` (anti-DSR 0.777), `qf-momentum-spread` (holdout Sharpe −0.325).
> This is intraday, gap-selected momentum rather than a 6–12-month price factor,
> so it is a genuinely distinct signal — but the same failure mode (the "edge"
> is just long-beta on green tape) is the first thing the anti/beta controls
> below are built to catch. The value of running this is to give the YouTube
> temptation a *falsifiable* answer for one day of compute, not to find an edge.

## 0. Why this, and why expect it to fail

Warrior's thesis: a stock with a small share float and a sudden demand shock
(news, halt-resumption, social) has nothing to absorb the buying, so price
"rate-of-changes" — the +432% MLGO example in the video. The tradeable claim is
**intraday continuation**: the big up-gapper on high relative volume keeps
running from the open. This is real and monetizable *in US microstructure*:
thousands of sub-$5 names, no daily price band, shortable via easy-to-borrow,
and premarket volume you can rank on.

Three reasons to expect it dies on NSE, in order of lethality:

1. **Structural / feasibility (may kill before any P&L).** The NSE analog of a
   Warrior low-float runner is largely in the **Trade-to-Trade (T2T) / periodic
   call-auction segment or under a tight daily price band (2 / 5 / 10 %)**. T2T
   forbids intraday (MIS) trading entirely — no same-day exit, no shorting. Price
   bands *truncate the exact +X% move the strategy monetizes*: when the stock is
   locked limit-up you cannot buy the continuation, and when you're long into a
   band you cannot sell into strength. The setup and the tradability are
   negatively correlated by exchange design.
2. **Costs (the BT14 wall, worse here).** The biggest gappers carry the
   **widest bid-ask spreads and the worst slippage** in the market — cost is
   largest precisely on the names we trade, same as reversal but more extreme.
3. **No distinct edge (the momentum wall).** If up-gappers "continue," most of
   that is just **long-beta on a green day** — the exact artifact that killed
   `cs-momentum`. The continuation must beat both the fade book (is it
   continuation or noise?) and a same-day universe-beta control (is it alpha or
   just being long on an up day?).

So the gate is **cost- and control-first, biased to KILL**, and Phase 0 is
deliberately cheap (daily bars we already cache) so a null costs one day, not 19.

## 1. Mechanism

Low float + demand shock → thin supply → momentum ignition. Behaviorally: retail
FOMO + short-squeeze + momentum-algo piling-on extend the open move through the
session (continuation), rather than the liquidity-provision reversion that BT14
tested. Continuation and reversion are opposite predictions on the same event, so
BT14's short-term-reversal KILL and this continuation test are mutually
falsifying — a clean check that we are not curve-fitting a sign.

The driver is tradeable **only if** (a) the names are intraday-tradeable at all
(not T2T), (b) the continuation clears the fat spread, and (c) it is distinct
from long-beta. The controls in §3 isolate exactly these.

## 2. Expected effect size

- **Direction**: long the top-K up-gappers at the open, exit same-day close
  (continuation). Down-gapper short is a reported secondary (usually infeasible
  in cash / T2T).
- **Magnitude (gross, the optimistic claim)**: +0.5 % to +1.5 % per gap-day
  intraday in the US analog; on NSE it is **band-capped** and the realizable
  gross is almost certainly smaller.
- **Units**: % gross / net return per trade; daily cohort net Sharpe.
- **Sample mechanism estimate**: anchored to the momentum-ignition literature
  and Warrior's own examples, NOT to any prior run. Realistic round-trip cost on
  a wide-spread gapper is **~0.6–1.0 %** (STT + stamp + exchange + *stressed*
  slippage 40 bps/side). Threshold below is set **above** that band to bias
  toward KILL.

## 3. Falsification criterion (LOCKED before experimentation)

**Gate −1 — FEASIBILITY, evaluated FIRST.** For the dev window, tag each selected
gapper by NSE segment (T2T / call-auction vs normal rolling) and by whether its
gap-day move hit the daily price band. **If ≥ 50 % of selected gap-days are
either T2T (non-intraday-tradeable) or band-locked at entry or exit → KILL as
structurally non-viable**, before reading any P&L. (Warrior's setup requires
free intraday entry+exit+short; if NSE structurally denies it, gross is moot.)
If segment data is unavailable at run time, proxy band-lock from the daily bar
(|open/prevclose−1| or intraday range pinned at the band) and state the proxy.

> **📊 Gate −1 measured live (2026-07-03, NSE band buckets via chittorgarh).**
> Only a +3 % gap is truncated by the **2 % or 5 %** band; the 10 % / 20 % / no-band
> (F&O) buckets accommodate it. Cross-referencing the current band lists against
> the 504-name universe:
> - **2 % band: 0 stocks** (bucket empty today).
> - **5 % band: 168 stocks, of which only 7 are in NIFTY 500 (1.4 %)** —
>   `ADANIENT, BBTC, GMDCLTD, GODREJIND, KEI, RKFORGE, SAREGAMA` (surveillance/ASM
>   placements that rotate; ADANIENT is F&O so its entry is a data quirk). The
>   other 161 are BE-series / defunct microcaps **outside** NIFTY 500.
> - **T2T (BE-series) inside NIFTY 500: ≈ 0** — the index's top-800-turnover /
>   90 %-trading-days screen excludes BE names by construction.
>
> **⇒ For the NIFTY 500 universe, Gate −1 PASSES trivially (~98.6 % of names have
> a 10 %/20 %/no band that a +3 % gap clears).** That is not good news — it means
> the daily NIFTY-500 test measures **liquid-name gap continuation, NOT the
> low-float-runner phenomenon.** Warrior's actual setup (thin float, +30–400 %
> days) lives in the **SME / T2T / 5 %-band microcap segment that NIFTY 500
> excludes** — and there Gate −1 bites hard (most such names are 5 % / BE / ASM,
> i.e. no free intraday entry+exit+short). **The honest conclusion is a
> contradiction: on NSE the names that produce the moves are the names you
> structurally cannot trade the moves on.** A NIFTY-500 pass or fail therefore
> can neither confirm nor deny the Warrior thesis; a genuine test would need the
> excluded microcap universe, where the structural KILL is near-certain. This is
> the single strongest "does not port to NSE" datum in the file.

**Gate 0 — COST-STRESS, evaluated SECOND.** Primary config = long top-K
up-gappers, enter at open[D], exit at close[D], intraday MIS. Apply the
production intraday cost model **plus stress slippage of +40 bps/side** (wide
spreads on the biggest movers — 4× the BT14 reversal stress), and **cap the exit
fill at the down-band** if the name closed limit-down after a failed gap. **If
the dev mean NET return per trade ≤ 0 under cost-stress → KILL immediately**,
before any other metric.

If Gates −1 and 0 pass, the standard battery (dev), all must hold:
- gross continuation ≥ **+0.50 %/trade** (must clear the fat round-trip with margin)
- **beats its anti-strategy**: the FADE book (short the up-gappers, same
  open→close) gross must be ≤ 0 AND strictly below continuation gross. If fade is
  also positive, the "edge" is volatility harvesting, not continuation.
- **beats the beta control (the cs-momentum trap)**: continuation gross minus the
  **same-day equal-weight universe mean return** ≥ **+0.30 %/trade**. If the edge
  vanishes once you subtract "long everything on an up day," it is long-beta, not
  a gapper edge.
- daily cohort net Sharpe (stressed) ≥ **0.5**
- n trade-days ≥ **30**

**Hold-out (2026-01-01 → present; touched once, only if all dev gates pass):**
stressed-net > 0 AND daily cohort Sharpe ≥ **0.4** AND beta-control alpha > 0.
Else KILL.

**🔒 No-relax rules (locked):**
- Thresholds cannot be loosened after seeing results.
- **The daily-bar test can only REJECT or LICENSE, never SHIP.** It uses
  gap%+prior-volume as a proxy for Warrior's premarket-RVOL + low-float filter,
  and enters at the official open (both optimistic — see §4). A daily PASS is
  therefore **not a ship**; it is the *only* thing that licenses Phase 2 (intraday
  5-min bars + real free-float + true premarket RVOL + VWAP-timed entry). A daily
  KILL ends it. No live money on a daily-proxy PASS.
- **No overlay rescue of a dead base.** If the daily gapper signal fails, we do
  NOT add float / news / VWAP-timing / sector filters to resurrect it — that is a
  new hypothesis with an explicit delta, registered separately, and only if the
  base showed a gross pulse (the rule that killed pead-yoy/ml/n).
- Single shot. Primary = long up-gappers open→close. Down-gapper short and any
  multi-day hold are reported secondaries, not extra shots at PASS.
- Universe = NIFTY 500 (the liquid set). We do NOT widen to SME / microcap /
  T2T to manufacture a bigger gross number — that is where the effect is largest,
  least tradeable, and most band-locked; a capacity-stressed different hypothesis.

## 4. Data needed

- **Daily OHLC*V* bars** for NIFTY 500 — have (Yahoo via yfinance, `1d`,
  disk-cached; BT14 already cached most of the window). **Volume is required**
  (BT14 ignored it) for the relative-volume rank.
- **Gap & rank inputs (PIT):** `gap_D = open[D]/close[D−1] − 1` (known at open D);
  prior-volume surge `vol[D−1] / mean(vol[D−6..D−1])` (known before open D). True
  intraday/premarket RVOL is NOT knowable from daily bars → **Phase 2 only**. The
  daily proxy is a *weaker* signal than Warrior's, so a daily KILL is conservative
  and robust; a daily PASS understates and merely licenses the intraday build.
- **Free float / shares outstanding** — NOT held. The low-float filter is
  **Phase 2** (Trendlyne StratQ / NSE). Phase 0 uses gap%+volume as the
  observable proxy for the latent low-float cause.
- **NSE segment (T2T / call-auction) + price-band file** — needed for Gate −1.
  **RESOLVED 2026-07-03 (see §3 Gate −1 box):** band buckets read live — only
  7/504 NIFTY 500 names in the 5 % band, 0 in the 2 % band, ~0 T2T. The list is
  date-specific (surveillance rotates), so a production Gate −1 should re-pull the
  daily band file (NSE archives block scripted fetch → use a browser/mirror, or
  the chittorgarh circuit-filter report). For the dev screen the daily-bar
  band-lock proxy + the 7-name overlap constant in bt15 are sufficient.
- **Cost model** — production intraday MIS (`trailing_sl.calc_costs`, imported not
  copied) + STRESS_SLIP 40 bps/side + band-capped exit fill.

PIT discipline: rank on `gap_D` and prior-volume surge (both known at/before open
D); enter at open[D]; exit at close[D]; no same-bar look-ahead beyond the open
print. **Optimism to disclose:** assuming a fill at the official open overstates
achievable entry on a gapper — another reason a KILL is robust and a PASS is only
a license.

## 5. Train / dev / hold-out split

- **Dev**: 2025-07-01 → 2025-12-31 (~125 trading days; reuses BT14's cached bars)
- **Hold-out**: 2026-01-01 → present (NEVER touched until dev gates pass; the
  bt15 script runs **dev only** and hard-codes the dev window)

No train set — no fitted parameters. K (top gappers/day) and the horizon (intraday
open→close) are fixed by the hypothesis, not tuned. (Tuning K, the gap threshold,
or the horizon requires a train set and a new hypothesis file.) **Locked knobs:**
K = 10, minimum gap = +3 %, prior-volume-surge ≥ 1.5×.

## 6. Code reference

- Backtest: `research/backtests/bt15_lowfloat_gap.py` (self-contained `uv run`;
  scaffolded, **not yet run** — running requires status: registered). Reuses
  BT14's `fetch_daily` + `NIFTY_500` + `calc_costs`; adds Volume, the gap/RVOL
  rank, the fade & beta-control books, and band-lock tagging.
- Cost model: `apps/signal-engine/src/news_trader/trailing_sl.py` (`calc_costs`).
- Trades dump: `research/backtests/bt15_trades_dev.csv`.

## 7. Result (single dev run, 2026-07-03, no re-runs)

Universe 499/504 with bars; DEV 2025-07-01→2025-12-31; K=10, gap≥+3 %, vol-surge≥1.5×.
**41 trade-days, 62 trades** (gap+volume candidates are sparse in liquid names).

- Gate −1 feasibility — tight-band (2 %/5 %) fraction: **0.0 %** (band-lock proxy 3.2 %) → **PASS trivially** (as predicted — confirms the test is tradeable but is measuring liquid-name continuation, NOT low-float runners).
- **Gate 0 cost-stress net (long up-gappers, +40 bps/side): ₹−690.7/trade → FAIL → KILL.**
- Dev gross continuation: **−0.3517 %/trade** (vs +0.50 % floor → FAIL). The up-gappers **fade** intraday — they close *below* the open on average, gross, before costs.
- Beta-control alpha: +0.0423 %/trade (cont −0.352 − univ −0.394; vs +0.30 floor → FAIL) — the tiny edge over the universe is because the whole tape drifted down that period, not because gappers continue.
- Daily cohort net Sharpe (stressed): **−3.607** (floor 0.5 → FAIL).
- n trade-days ≥ 30: PASS (only criterion met).
- ⚠️ Reporting note: the printed "fade anti" (−0.065 %) is a *per-day* mean while continuation (−0.352 %) is a *per-trade* mean — different weightings, so they are not exact mirrors; losses concentrate on high-gapper-count days. Does not change the KILL (net −₹691, Sharpe −3.6 are unambiguous). Trades → `bt15_trades_dev.csv`.
- Hold-out: **NOT touched** (dev failed Gate 0).

## 8. Decision

- [ ] **SHIP** — Gates −1 & 0 passed AND all dev gates passed AND hold-out passed
      (note: daily-proxy PASS is NOT ship; it only licenses Phase 2)
- [x] **KILL (daily-proxy REJECT)** — Gate 0 failed: stressed net ₹−691/trade,
      gross continuation −0.35 %/trade (gappers FADE, not continue), Sharpe −3.6.
      Gate −1 passed trivially (0 % tight-band) — so this is a clean, tradeable-
      universe result: **liquid-name open→close gap-momentum has negative drift.**
      Consistent with BT14 (winners fade). No overlay rescue. Note this KILLs the
      *daily open→close proxy*; the intraday-early-entry + catalyst-gated variant is
      the separately-registered `momentum-catalyst-gate` (forward test) — see its §7.
- [ ] **LICENSE Phase 2** — daily proxy passed all gates → build the intraday
      (5-min + real float + premarket RVOL + VWAP entry) test as a new registered
      hypothesis. Not a ship, not live money.
