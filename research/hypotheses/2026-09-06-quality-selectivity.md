---
slug: quality-selectivity
strategy: momentum_trader
status: killed
registered_at: '2026-09-06'
finalized_at: '2026-09-06'
decided_at: '2026-09-06'
hypothesis_hash: ''
parent: momentum-catalyst-upstox-v2
---

# Hypothesis: Refusing to trade unless the stock is in an uptrend, its chart is well-formed enough for indicators to mean anything, and a genuine volume surge has already happened, raises mean gross return per trade by at least +0.60 percentage points over the current unselective entry — even though it will trade far less often.

> ⚠️ **Second look at 2022–2023, disclosed.** Upstox 1-minute history begins in
> 2022 (probed 2026-09-06: 2017–2021 return zero bars), so the complete inventory
> is 2022–2023 (used once by `multi-entry-same-stock`), 2024 (used four times,
> spent), and 2025 (sealed). There is no unused window. Dev here is a **second**
> read of 2022–2023. That is a real cost and is why the four operator requests
> are bundled into **one** hypothesis with **one** primary criterion rather than
> tested separately.
>
> ⚠️ **The 2025 hold-out is the arbiter and has never been touched.** It is not
> to be read unless the dev primary passes, and the operator is to be asked
> before it is read. This is the situation it was reserved for.

## 0. The operator's requirement

Four requests, one idea: *trade rarely, only the good ones.* Stated verbatim:

1. if the stock is in a downtrend and indicators point down, don't trade it;
2. some stocks have malformed charts because nobody trades them — indicators
   don't work there, so drop them from the watchlist;
3. don't blindly enter; wait for a signal of a big surge;
4. after entering, don't act immediately — wait for a negative signal.

Plus the operating constraint: **"i don't expect trades everyday, so its fine
even if i don't trade for days."** Trade count falling sharply is an intended
consequence, not a failure — subject to §3's feasibility floor.

## 1. The filters (frozen)

Applied **in addition to** the existing universe and setup rules, which are
unchanged. Every filter below is evaluated only on information available at the
moment the setup fires.

### F1 — uptrend required (request 1)

Reuses `exits.EMA_FAST=9`, `exits.EMA_SLOW=20`, `indicators.session_vwap`. All
three must hold on the setup's own timeframe at the trigger bar:

```
EMA9 > EMA20                     short-term trend is up
close > session VWAP             holding above the day's average price
prev_close >= SMA20(daily close) the stock is not in a daily downtrend
```

No new constants. The daily SMA20 uses the daily frame the backtest already
builds for its turnover pre-filter.

### F2 — chart must be well-formed (request 2)

Measured over the **prior 20 sessions**, so it is knowable before the day starts:

```
minute_coverage  = median over sessions of (bars with volume > 0) / 375
flat_bar_share   = median over sessions of (bars where high == low)

require minute_coverage >= 0.85  AND  flat_bar_share <= 0.30
```

**These two thresholds are the only new tunable numbers in this entire test.**
They are set from the measured distribution across 495 stock-years of cached
2022–2023 data (2026-09-06): coverage has median 0.992 and 10th percentile
0.840; flat-bar share has median 0.059 and 90th percentile 0.309. The thresholds
therefore exclude approximately the worst decile on each measure. They were
chosen from the shape of the data-quality distribution **before any P&L was
computed on the filtered set**, and are frozen here.

Worst observed case, for scale: JSWDULUX 2022 traded in 35.7% of session minutes
with 73.5% of its bars flat. EMAs, MACD and swing pivots computed on that chart
are mostly reading their own padding.

### F3 — a real surge must already have happened (request 3)

Reuses `pullback.first_pole` (itself `setups.POLE_MIN_PCT=2.0` /
`POLE_MAX_BARS=6`) and `exits.CLIMAX_VOL_RATIO=2.5`:

```
a pole must have formed today before the setup fires, AND
at least one bar in that pole had volume_ratio >= 2.5
```

No new constants. Supporting evidence: `pullback-ordinal` (2026-09-06) found the
82 trades where **no pole ever formed** were the worst bucket in that table at
−0.284% gross, the only directionally sensible observation it produced.

### F4 — exit on a negative signal, not a target (request 4) — SECONDARY

Reuses `exits.MODE_TREND_MIN`, already built and tested. This is carried as a
**pre-registered secondary comparison, not part of the primary.**

`trend-exit-vs-fixed-target` (2026-09-05) KILLED indicator exits at +0.004 pp
against a +0.30 bar and instructed that any re-test "must justify why an exit
change should matter when two opposite exits already came out equal". The
justification: that test ran on the **unselective** pool, whose gross is ~0.00%.
An exit that stops capping winners can only pay when winners exist. Measuring it
on a flat pool bounded nothing about a pool selected for quality. It is
therefore re-measured **only on the filtered entries**, and only as a secondary,
because if the filtered entries have no drift the exit question is moot again.

## 2. Arms

| Arm | Entries | Exit |
|---|---|---|
| `base` | current, unfiltered | `fixed_2r` |
| `sel` (**primary**) | F1 + F2 + F3 | `fixed_2r` |
| `sel_trend` (secondary) | F1 + F2 + F3 | `trend_min` |

`base` is the `single`-arm result already produced by `multi-entry-same-stock`
on this window (2,408 trades, gross −0.046%/trade, stressed net −1.051%). It is
re-run rather than reused, so all three arms come from one code state.

`one_trade_per_day` stays `True` in all arms — measured on 2026-09-06 as the
better rule by 2.5× in money per opportunity.

## 3. Feasibility precondition

**n(`sel` trades) ≥ 150** on 2022–2023. Below that the window cannot separate
signal from luck and the honest answer is **"not testable"**, recorded as such.
It may **not** be rescued by loosening a threshold, dropping a filter, or
widening the window — that would be selecting the filter set by trade count.

The operator has accepted low trade frequency, so a large drop from 2,408 is
expected and is not itself a problem. 150 over two years is roughly one trade
every three trading days.

## 4. Falsification criterion (LOCKED before any code is written)

Dev **2022-01-01 → 2023-12-31**. **All four must pass; any one failing is a KILL.**

- **PRIMARY-lift:** mean gross %/trade, `sel` − `base`, ≥ **+0.60** percentage
  points, positive sign.
- **PRIMARY-level:** mean gross %/trade of `sel` ≥ **+0.60%**. Selectivity that
  merely loses less is not an edge; the stressed round trip costs ~1.0%, so a
  filtered set must clear a meaningful fraction of that on its own to be worth
  anything. Carried forward from `pullback-ordinal`, where a real effect was
  useless because both sides still lost.
- **anti:** among `base` trades, shuffle the pass/fail filter labels 5,000
  times; p(shuffled lift ≥ observed) < **0.10**.
- **n:** `sel` ≥ 150 trades (§3).

**Secondary, reported not gating:** `sel_trend` − `sel` gross %/trade; trade
count and trades-per-month for every arm; each filter's individual pass rate and
marginal contribution; stressed net %/trade; win rate; exit mix; per-setup table.

**Hold-out (2025), read ONCE, only if all four dev criteria pass AND the operator
agrees to spend it:** PRIMARY-lift ≥ **+0.30** pp and `sel` gross ≥ **+0.30%**,
both correctly signed, with n ≥ 60. Otherwise KILL.

🔒 **No-relax rules:**
- The three arms above, and no others.
- F1/F2/F3 are a **bundle**. If the bundle fails, "which filter did the work" is
  an observation for a future hypothesis on different data, **not** a licence to
  ship the subset that looked good here. Reporting marginal contributions is
  allowed; selecting on them is not.
- The F2 thresholds (0.85 / 0.30) are frozen. Moving them after seeing P&L is
  the exact failure this file exists to prevent.
- A per-setup, per-month or per-filter subset that looks good is an observation.
- Passing licenses forward capture with these filters. It does **not** license
  live money.
- 2024 stays spent. This does not reopen it.

## 5. Data

- **Dev:** 2022-01-01 → 2023-12-31, cache already populated, no fetches needed.
- **Hold-out:** 2025, sealed, requires operator consent to read.
- **Unavailable:** pre-2022 (probed 2026-09-06, Upstox returns zero bars).
- Costs: production intraday MIS plus +40 bps/side, identical across arms.

## 6. Code

- `quality.py` — F1 `uptrend_ok`, F2 `chart_quality` / `chart_ok`, F3 `surge_ok`.
- `engine.py` — `EngineConfig.require_quality`; when set, a setup that fails any
  filter is recorded on the candidate with its reason and **not traded**.
- Runner: `bt17_momentum_pool.py --quality`.
- Analysis: `bt22_quality_selectivity.py` applies the four criteria.
- Tests: `tests/momentum_trader/test_quality.py`.

## 7. Result — 2022–2023 dev, run 2026-09-06

### Code-state disclosure — read first

The first run of all three arms carried the breakeven-lock leak in `fixed_2r`
(see the post-hoc disclosure appended to the four 09-05/09-06 files). It was
found **during this test**, while decomposing where the losses come from. The
fix was applied and `base` and `sel` were **re-run**; `sel_trend` uses
`trend_min`, where the lock is a designed feature, so it was not affected.
Numbers below are the corrected runs. The pre-fix verdict was also a KILL on the
same three criteria (lift −0.115 pp, level −0.161%, anti p = 0.966), so the fix
changed the numbers, not the outcome.

### Feasibility

**250 `sel` trades** — the §3 floor of 150 is cleared. This is a real answer.
Trade frequency fell from 100/month to **10/month**, which is the "not every
day" the operator asked for.

### Arms

| arm | n | gross %/trade | stressed net %/trade | win % (gross) | target % | trades/month |
|---|---|---|---|---|---|---|
| `base` | 2,408 | **+0.025** | −0.981 | 33.9 | 24.7 | 100 |
| `sel` (F1+F2+F3) | 250 | **−0.115** | −1.120 | ~24 | 16.0 | 10 |
| `sel_trend` (secondary) | 250 | −0.162 | −1.167 | — | 0.0 | 10 |

### All three testable criteria FAILED

| criterion | required | observed | verdict |
|---|---|---|---|
| PRIMARY-lift | ≥ +0.60 pp | **−0.1397 pp** | FAIL |
| PRIMARY-level | ≥ +0.60% | **−0.1150%** | FAIL |
| anti (random subset of same size) | p < 0.10 | **0.9794** | FAIL |
| n(`sel`) | ≥ 150 | 250 | PASS |

The anti result is the decisive one: a **random** 250-trade subset of `base`
out-performed the filtered 250 in 98 of every 100 draws. The bundle did not
merely fail to help — it selected *against* return.

### Why setups were refused (19,405 candidates examined; **1.9% passed**)

| reason | share | request |
|---|---|---|
| surge had no ≥2.5× volume bar | 35.7% | 3 |
| EMAs not warmed up (early session) | 16.9% | 1 (side-effect) |
| below daily SMA20 | 16.6% | 1 |
| thin tape (<85% minutes traded) | 15.4% | 2 |
| EMA9 ≤ EMA20 | 7.1% | 1 |
| below VWAP | 3.3% | 1 |
| >30% flat bars | 2.1% | 2 |
| no pole formed | 0.9% | 3 |

Each filter removed what it was designed to remove. None of them correlated
with what the next 30 minutes did.

### Two defects in the run, neither of which changes the verdict

1. **`sel` is not a strict subset of `base`** — 220 shared, **30 `sel`-only**.
   Cause: `one_trade_per_day`. When the filters refuse a stock's first setup,
   the engine keeps scanning and may take a later one that `base` had no room
   for. The 30 substitutes averaged −0.157%, the same as the rest. The unit test
   asserting the subset property used a fixture whose first setup passed, so it
   never exercised this path. The anti test's framing ("better than a random
   subset") is therefore slightly off; the sign and magnitude are not in doubt.
2. **16.9% of refusals are `ema_warmup`** — the trend filter declining to judge
   before 9 and 20 bars exist, not the downtrend rule the operator asked for.
   Defensible, but it is a different thing and it is a large share of the cut.

### Request 4 (secondary): indicator exits on the same 250 filtered entries

`sel_trend` − `sel` = **−0.0465 pp**. Third identical result for this
comparison (2024 unfiltered: +0.004 pp; 2022–23 filtered pre-fix: −0.001 pp).
The exit rules fired as designed and did not matter, because the entries they
were applied to have no drift. Moot by the logic pre-registered in §1.

## 8. Decision

- [x] **KILL** — three of four locked criteria failed: lift −0.14 pp vs +0.60,
      level −0.115% vs +0.60%, anti p = 0.979 vs < 0.10.

**Hold-out (2025) NOT touched.** The operator was not asked because there was
nothing to confirm.

**The bundle is not shipped.** Per §4, the subset that looked least bad
(`flat_top_breakout`, −0.024% on 47 trades) is an observation, not a rescue.
`require_quality` stays `False`; the code and the refusal-reason logging are
retained.

**What this test was worth beyond its own verdict.** Decomposing the loss to
answer the operator's "what went wrong" question surfaced (a) the breakeven-lock
leak, now fixed, and (b) the cost split: real MIS costs are **0.21%** of
notional and the +40 bps/side stress is **0.80%**. The corrected strategy at
real costs is **−0.18%/trade**, not −1%. Those two findings matter more than the
filter result.

**Scoreboard on `momentum-catalyst-upstox-v2`.** Seven measurements: entries ×2,
exits ×1 (now ×3 as secondaries, all ≈0), execution ×1, entry timing ×1,
re-entry ×1, quality selectivity ×1. No edge found in how these stocks are
traded. The **catalyst gate is the only untested component**, and 2022–24 are
all used, so it is also the only remaining test that does not re-read data.
