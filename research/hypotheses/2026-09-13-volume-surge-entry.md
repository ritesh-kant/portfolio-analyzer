# Only trade a high volume surge

*Registered 2026-09-13, before any surge number was computed. Status: **killed as a filter, but not as a fact** (see §5.3).*

## 1. The claim

Operator's words: *"i only want to trade where there is a high volume surge."*

The system already screens on **day-level RVOL ≥ 3.0** (`RVOL_MIN`) — every trade
in the pool is already an unusually active day. The untested claim is the one a
trader actually reads off a chart: the **breakout bar itself** should carry heavy
volume. A trigger bar that pokes through on thin volume is a fake-out; one that
goes through on 3× normal bar volume is real demand.

Two distinct quantities, and they must not be confused:

| name | meaning | where |
|---|---|---|
| `rvol` (day-level) | today's cumulative volume vs. the same time of day, normally | already screened at ≥ 3.0, recorded per trade |
| `volume_ratio` (bar-level) | this bar's volume ÷ mean of the prior 20 bars | `indicators.volume_ratio`, **not** recorded per trade |

**Prior on the day-level half is already in.** Observation over the clean pool
(n = 7,476): deciles of `rvol` are flat-to-inverted, Spearman rho = **−0.032**
(p = 0.006, wrong sign), and an `rvol ≥ 8` cut is negative in **all four**
windows. More day-level volume does not help; if anything it hurts. This
hypothesis therefore tests **only** the bar-level surge.

## 2. Exposure (no look-ahead)

For every trade in the clean pool, re-open that session's 1-minute cache and
compute, from bars that had **closed before the fill**:

* `trig_vr_5m` — `volume_ratio` of the **trigger bar** (last 5m bar closed at or
  before `trigger_time`); this is the bar whose high the entry broke.
* `entry_vr_1m` — `volume_ratio` of the 1-minute bar the fill happened in,
  using only that bar and the 20 before it.

`volume_ratio` is already backward-looking (`.shift(1)` on the rolling mean), so
neither number can see the outcome.

## 3. Locked decision rule

Primary population: clean pool (1 trade/symbol-day, post-`breakeven-lock`-fix
files only — the BT29 `is_clean_run()` filter, unchanged).
Primary cut: **top tercile of `trig_vr_5m`** — fixed in advance so no threshold
is chosen after seeing the curve. A threshold sweep is reported as observation
only and cannot be used to declare a win.
Primary metric: mean `gross_pct`.

SHIP requires **all four**:

| # | criterion | bar |
|---|---|---|
| C1 | dose-response | Spearman rho(`trig_vr_5m`, `gross_pct`) > 0 at p < 0.05 |
| C2 | size of lift | top tercile − rest ≥ **+0.30 pp** gross (the bar this repo locked for entry-side tweaks; a full real round trip is 0.21%) |
| C3 | not just a smaller sample | anti-strategy p < 0.05 vs 1,000 random same-size subsets, seed 20260913 |
| C4 | not one window's luck | spread positive in ≥ 3 of the 4 windows (2022-23, 2024, 2025, 2026) |

Anything less is a KILL, and there is **no variant #2 on this data** — every
window is already spent for entry-side mining.

## 4. Why the prior is poor

This is the **fourth** selectivity filter tested on this engine. Quality
selectivity (trend + chart + *surge*) came out worse than random (p = 0.97);
pullback ordinal was noise (p = 0.47); the 1-minute agreement gate could not
tell kept trades from removed ones (p = 0.53); the v3 candlestick filter was
wrong-signed. Note the first of those **already contained a volume-surge leg**
(`quality.surge_ok`) — bundled, so this test isolates it, which is the new
information. A fifth independent confirmation would be strong evidence that the
entry pool is not separable at all, and that the remaining loss is execution
(the fill gap: mean **+0.256%** paid over trigger), not selection.

## 5. Result

Run: `uv run research/backtests/bt31_volume_surge.py --jobs 8 --rescan` (2026-09-13).
Measured pool 7,447 scanned / 4,676 with the primary measure defined.

### 5.1 The locked verdict: KILL, 0 of 4

| # | criterion | result | |
|---|---|---|---|
| C1 | Spearman rho(`trig_vr_5m`, gross) > 0, p < 0.05 | **rho = −0.0554**, p < 0.001 | FAIL |
| C2 | top tercile − rest ≥ +0.30 pp | **+0.0182 pp** | FAIL |
| C3 | anti-strategy p < 0.05 | **p = 0.277** | FAIL |
| C4 | spread positive in ≥ 3 of 4 windows | **2 of 4** | FAIL |

### 5.2 Two defects in the primary measurement, both disclosed

1. **A 20-bar lookback cannot exist before ~10:10**, so `trig_vr_5m` is undefined
   for **2,771 of 7,447 trades (37.2%)** — every 09:xx entry (n = 2,327) plus the
   10:00–10:10 ones. The opening hour is where momentum trading lives, so the
   primary test is blind to the best part of the day. Re-measured against the
   **session so far** (`trig_vr_sess`, defined from the 4th bar on, 84% coverage)
   the conclusion does not move: rho = −0.0515, tercile spread +0.046 pp,
   anti p = 0.056.
2. **`entry_vr_1m` is look-ahead and is retained only as a warning.** With
   `next_open` fills the entry is the *open* of that minute, so the minute's
   volume accrues after the decision. That contaminated column is the only one
   that looks like a winning rule — top decile +0.244% gross, 44.4% win, the
   single **net-positive** cell in the whole study (+0.034% after real costs).
   The clean version (last minute *closed* before the fill) shows nothing:
   top decile +0.052%, 32.7% win. The entire apparent edge was knowing how much
   volume traded *after* we bought.

### 5.3 Why this kill is not like the other four

Every prior selectivity filter was **indistinguishable from deleting trades at
random** (quality p = 0.97, ordinal p = 0.47, 1-min agreement p = 0.53, v3
patterns wrong-signed). This one is not. On the session-anchored measure the
means are **monotone across quintiles**, and the top quintile beats its own
anti-strategy:

| quintile | vr range | n | win % | avg win | avg loss | gross % |
|---|---|---|---|---|---|---|
| 1 | 0.00–0.30 | 1,251 | 28.8% | +1.188 | −0.523 | −0.0304 |
| 2 | 0.30–0.57 | 1,251 | 29.3% | +1.246 | −0.566 | −0.0345 |
| 3 | 0.57–1.01 | 1,250 | 31.1% | +1.285 | −0.610 | −0.0199 |
| 4 | 1.01–2.32 | 1,251 | 33.6% | +1.359 | −0.691 | −0.0027 |
| 5 | 2.32+ | 1,251 | **36.7%** | +1.598 | −0.831 | **+0.0601** |

* anti-strategy on the top quintile: **p = 0.012** (2,000 draws, seed 20260913)
  — the first entry filter in this repo to pass one;
* positive in **4 of 4** windows (2022-23 +0.018, 2024 +0.284, 2025 +0.095,
  2026 +0.156 pp);
* the **mechanism is confirmed**: `false_break` exits fall from **28.6% → 12.9%**
  in the top quintile. Heavy volume really does mean fewer fake-outs.

**So why is it still a KILL?** Because avoiding a fake-out is not the same as
making money. The trades the surge rescues do not become winners — they become
**all-day holds** (`eod_close` 8.0% → 21.4%), and the target rate is flat
(22.9% → 23.1%). Meanwhile the losers it keeps are **59% bigger** (−0.523 →
−0.831), because heavy volume moves price in both directions. Net of that
trade-off the best available cut is worth **+0.082 pp**, against a **0.21%** real
round trip. Top quintile net = **−0.150%/trade**. It makes losing slower, not
profitable.

C1's failure is a statistic artefact worth naming: Spearman ranks trades, and
higher surge pushes the *typical* trade down (bigger losses) while pushing a
minority far up, so rank correlation is negative while the **mean** is monotone
increasing. Locked is locked — C1 fails as written — but the honest reading is
C2: the effect exists and is **~2.5× too small**.

The `vr >= 2.5` sweep row (p = 0.045) is the best of seven thresholds and is not
evidence; it is what a sweep produces.

## 6. Disposition

**Not shipped.** No code change, no new config flag. A filter that cuts 80% of
trades to improve gross by 0.08 pp while still losing 0.15% per trade is not a
rule, and adding it would repeat the `MT_REQUIRE_1M_AGREEMENT` mistake of
shipping a frequency dial as if it were selection.

Two things this does buy, both recorded rather than acted on:

1. **The separability question is now answered differently.** Four tests said
   the pool cannot be sorted at all. This one says it *can* be sorted, just not
   by enough to clear costs. That is a better-specified problem.
2. **The one stack worth forward-capturing.** Top-quintile surge gross is
   +0.060%; the measured resting-order entry gain is +0.235 pp (fill gap is
   still mean +0.256% over trigger). If those were additive the combination
   clears 0.21% costs. **They have not been tested together and must not be** —
   2022-23, 2024, 2025 and 2026 are all spent for entry-side mining. This is a
   forward-capture question only.

No variant #2 on this data.
