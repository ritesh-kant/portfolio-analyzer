---
slug: momentum-catalyst-upstox-v2
strategy: momentum_trader
status: registered
registered_at: '2026-09-05'
finalized_at: ''
decided_at: ''
hypothesis_hash: ''
supersedes: momentum-catalyst-gate
---

# Hypothesis: Among liquid, tradeable-band NSE small/mid-caps that are already up 4–8% intraday on ≥3× time-of-day relative volume, entering on a Warrior-Trading continuation setup (bull flag, flat-top break, MA/VWAP pullback, ORB, red-to-green, micro-pullback) and managing to a 2:1 reward:risk is net-positive after stressed costs **only when a fresh hard catalyst is present**; catalyst-less movers on the same setups are not.

> **Supersedes `2026-07-03-momentum-catalyst-gate.md`**, which never captured a
> row (bt16 log empty 2026-07-04 → 2026-09-05). Same mechanism, same catalyst
> definition, same gates. What changed, all *before* first capture:
> 1. **Trigger band 4–8%** (was "in +2..+10%"); 5%/2%-band + ASM/GSM names excluded.
> 2. **"Float" criterion made NSE-native** (free-float mcap, promoter holding,
>    turnover) — the US "<10M shares" rule is an empty set here (0/120 sampled).
> 3. **Entry = a frozen Warrior setup** (spec v1), not "the scan bar". Stop =
>    pattern low, target = 2× stop distance, plus the false-break exit.
> 4. **Data = Upstox** (user's account): real-time WS feed, 1-min history to 2022.
>    That makes the *pool* backtestable; the catalyst split stays forward.
> 5. **Paper fill = open of the bar after the trigger bar.** Never the trigger
>    quote (the BT9 artifact).
>
> Everything in the July file's "why it probably won't work" still applies:
> BT14/BT15 measured that NSE winners **fade** intraday. The setups do not
> change that prior; they change *where* in the move we enter (pullback, not
> chase), which is the one lever those daily-bar tests could not see.

## 0. Frozen definitions

- **Spec:** `research/specs/warrior-patterns-nse.md` v1 — universe filter (§1),
  seven long setups with thresholds (§2), candle tags (§3), risk rules (§4),
  exclusions (§5), capture schema (§6).
- **Catalyst:** unchanged from v1 §4 — `catalyst=1` iff a hard corporate event
  (results / order-contract win / regulatory approval / board corporate action)
  in the 24h before the trigger; ambiguous → 0; **no sentiment**.
- **Universe:** NIFTY 500 ∪ NIFTY Smallcap 250 ∪ NIFTY Microcap 250, then the
  spec §1 filters. Wider than v1's NIFTY 500 because the ₹500–5,000 cr free-float
  band is thin inside NIFTY 500 (79 names under ₹20 cr turnover). The turnover
  floor (₹3 cr) keeps it tradeable.

## 1. Mechanism

Unchanged: momentum ignition with an information anchor. A genuine catalyst
gives slow-diffusing buyers a reason to keep lifting a small-cap through the
session; the catalyst-less mover is noise that mean-reverts (BT14 fade). The
setups add a second, mechanical claim from Warrior: **entering on the pullback
/ consolidation break rather than the vertical bar** captures continuation
with a defined, small stop, which is what makes 2:1 achievable.

## 2. Expected effect size

- Direction: long, intraday, entry → target/stop/false-break/15:15 close.
- Catalyst-gated **net** (stressed) per trade: +0.3% to +0.8%.
- Catalyst − no-catalyst spread (gross): +0.4% to +1.0%.
- Win rate needed at 2:1 to break even: 33% (Warrior's table). Round-trip cost
  on these names ≈ 0.4–0.8%; thresholds sit above it.

## 3. Falsification criterion (LOCKED — identical to v1)

**Gate 0 first — cost-stress on the catalyst-gated book:** production MIS costs
+ 40 bps/side, band-capped exits. **Mean net/trade ≤ 0 → KILL.**

Then all must hold:
- Spread (catalyst − no-catalyst, gross) ≥ **+0.40%/trade** and signed +.
- Label-shuffle anti-strategy: p(shuffled spread ≥ observed) < **0.10**.
- Beta control: catalyst-gated continuation − same-day NIFTY 500 o→c ≥ **+0.30%**.
- Catalyst-gated stressed net Sharpe ≥ **0.5**.
- n(catalyst-gated trades) ≥ **30**.

**Hold-out / forward-confirm** (once, only if all pass): stressed net > 0, spread
> 0, beta-alpha > 0. Else KILL.

**Secondaries (reported, NOT extra shots at PASS):** per-setup breakdown,
per-candle-tag breakdown, win rate vs the 33% line, false-break exit frequency,
1%-fixed-target variant (to compare against the news-trader's exit design).

🔒 **No-relax rules:** thresholds fixed; spec v1 thresholds fixed; catalyst
definition fixed; universe fixed; one position per symbol per day, first setup
in spec order wins; single shot. A per-setup result that looks good is a *new
hypothesis file*, not a filter added here. No live money on a forward PASS
without the confirm window.

## 4. Data

- **Upstox API** (free with account): WebSocket v3 market feed, LTPC mode, up to
  5,000 instruments → 1-min bars built in-process, 5-min resampled.
  Historical v3: 1-min candles back to 2022-01 for the pool backtest.
  Token expires 03:30 IST daily; no headless login → a manual approve step each
  morning writes the token to SSM; scanner alerts via Telegram if absent by 09:10.
- **Circuit bands / series / ASM-GSM:** NSE daily files, pulled pre-open.
- **Free-float mcap, promoter holding:** Trendlyne StratQ export (paid, held),
  refreshed quarterly with shareholding patterns.
- **Catalyst:** existing `nt_*` RSS + NSE announcements + Gemini classifier's
  `event_type` (presence only). NSE announcement archive for the historical
  earnings-date subset.
- **Costs:** `news_trader.trailing_sl.calc_costs` (MIS) + 40 bps/side stress.

PIT: catalyst frozen at/before trigger; bands as-of the morning file; universe
membership as-of the date; fill = next bar open.

## 5. Split

1. **Pool backtest (context, not a gate):** 2022-01 → 2025-12 on Upstox 1-min,
   criteria 1/2/4/5 + band exclusion + the seven setups. Reports per-setup
   continuation, stressed net, and — where results dates are known — the
   earnings-catalyst subset. Tells us the fade the catalyst must overcome.
2. **Dev / accumulation (forward):** first capture day → n(catalyst) ≥ 30.
3. **Hold-out / confirm:** the next equal-or-longer window; boundary fixed the
   day dev reaches n = 30; never read before dev passes.

## 6. Code

- Detectors, tags, risk: `apps/signal-engine/src/momentum_trader/{setups,candles,risk,indicators}.py`.
- **Shared decision engine** `engine.py` — `step()` per closed 1-min bar; the live
  scanner and bt17 call the same function (pending → fill at next bar open →
  false-break / stop / target / 15:15 exits). One trade per symbol per day.
- **Upstox** `upstox.py` (instrument master, historical/intraday V3 candles with
  monthly pagination + parquet cache, V3 FULL-mode WebSocket via the SDK),
  `upstox_auth.py` (daily login → SSM), `bars.py` (tick → 1-min bars, volume from
  `vtt` deltas), `universe.py` (spec §1 filters), `catalyst.py` (nt_signals
  Group-A lookup, 24h), `ledger.py` (Mongo `mt_candidates`/`mt_positions` + spec
  §6 CSV), `scanner.py` (Fargate loop). Infra: `Dockerfile.scanner`,
  `infrastructure/ecs-scanner.yml` (EventBridge 09:05 IST → Fargate task).
- **Pool backtest** `research/backtests/bt17_momentum_pool.py` (2026-09-05 note:
  Upstox historical V3 responds WITHOUT a token, so this runs with no login).
- Tests: `tests/momentum_trader/` — 44 passing (setups 27 + engine/bars/upstox/
  universe/catalyst 17). ruff + mypy clean.
- **Forward screen** `research/backtests/bt18_momentum_forward_screen.py` — the
  §3 gates over `mt_forward_log.csv`; runs only at n(catalyst) ≥ 30, adds the
  +40 bps/side stress (the scanner logs real-cost P&L).
- **Not yet built:** `research/data/mt_universe.csv` (StratQ export) — until it
  exists the scanner runs NIFTY 500 with float filters skipped and stamps
  `float_filter_applied=0`; the Smallcap/Microcap 250 symbol lists.

## 7. Result — (forward gates: empty until n ≥ 30)

**Pool backtest context (bt17, run 2026-09-05, NOT a gate):** 2024-01-01 → 2025-12-31,
NIFTY 500 → 246 names pass the daily pre-filter (price ₹60–2,000, 20-day turnover
₹3–50 cr; the other 251 are large caps) → 237 names traded, 464 trading days,
**1,876 trades** from 2,948 fired setups (the rest cancelled by the chase guard /
stop-sanity band at fill).

| | value |
|---|---|
| gross %/trade | **0.00** (p10 −1.08 / p50 −0.40 / p90 +1.71) |
| net %/trade, production MIS costs only | −0.20 |
| net %/trade, +40 bps/side stress (Gate-0 basis) | **−1.00** |
| win rate | 19.7% (33% needed at 2:1) |
| exits | stop 47% (−0.73 gross) · target 25% (+1.61) · false-break 19% (−0.52) · EOD 8% (+0.51) |
| by year | 2024 −1.04 / 2025 −0.96 stressed — stable, not a regime artefact |
| by setup (gross) | ma9_pullback +0.13 (n=315) · orb15 +0.16 (n=16) · vwap_reclaim +0.06 (n=144, best win rate 31%) · bull_flag +0.05 (n=201) · flat_top 0.00 (n=245) · micro_pullback −0.06 (n=955) — none net-positive |
| by trigger hour / day-change bucket | flat everywhere (−0.9 to −1.2 stressed); 14:00–14:30 worst |
| prev-day gainer (n=252) | −0.09 gross vs fresh +0.02 — the "continuation setup" exception adds nothing |

Reading: **the pool has zero gross drift and costs are the entire loss** — the
same shape as BT9 (news-trader) and BT14/BT15. Pullback entries did lift gross
from BT15's −0.35% (open→close) to 0.00%, so *where* you enter matters, but not
enough to create an edge. The 2:1 structure is not realised: average target win
+1.61% vs average stop −0.73% ≈ 2.2:1 per trade, but only 25% of trades reach
the target while 66% exit for a loss.

Implication for the forward test: the catalyst flag must lift a **flat-gross**
pool to ≥ +1.0% gross/trade to pass Gate 0 under stress. BT12 measured the
information-event spread at −0.27% (wrong-signed) for the news-trader; the
pre-registered gates stand, but the prior is poor. Cheapest next step within
§5 step 3: StratQ results dates for these 237 names → `bt17 --events` (cached,
~10 min) → historical earnings-catalyst spread *before* spending on forward capture.
Not applied here (forward-only): free-float / promoter / band filters; Smallcap
250 + Microcap 250 not included. Outputs: `research/backtests/bt17_trades.csv`,
`bt17_candidates.csv`, `bt17_run.log`.

## 8. Decision — (empty)
