# BT35 — Absorption base / failed breakdown (spring)

**Registered:** 2026-09-15, BEFORE the run. **Window:** 2022-01-01 .. 2023-12-31 (SPENT
for entry-side mining — see below). **Status:** single-shot, no re-runs.

## Origin

Operator screenshots of four charts showing price at a horizontal level, sellers
probing below it, and the probes failing. Operator's read: "sellers are not able to
break the low and buyer is buying everything."

Correction applied before coding: in three of the four charts the low **is** broken —
the second probe goes *lower* than the first, and the break is then rejected. The
codeable mechanic is therefore a **failed breakdown (spring / stop-run)**, not "support
held". A rule written as `low >= level` would reject the very examples that motivated
it.

## Why this is not a repeat of a prior kill

- Candlestick kills (BT28/BT29) were single-bar labels and pattern-as-filter. This is a
  multi-bar structure with a volume term that **defines its own trade**, not a filter
  on the existing momentum pool.
- BT14 (short-term reversal) had no level and no absorption, and its bounce lived in
  the overnight gap.
- Closest adverse evidence: **"near support" tested −0.118 pp on 2026**. Defence is that
  proximity ≠ defence — but it is a defence, not evidence. Noted as a prior against.
- Closest supportive evidence: BT31 volume-surge top quintile (anti p=0.012) with
  confirmed mechanism `false_break 28.6%→12.9%`, and BT33 refusal-alone (anti p=0.029).
  Level structure has twice shown non-random signal in this repo's own data.
- The seven deployed setups are all continuation/breakout. There has never been a
  base/reversal setup in this system.

## Contamination

2022–23 is spent for entry-side mining. Accepted deliberately: **a contaminated window
can kill but cannot bless.** Multiple testing inflates false positives, not false
negatives. If this comes back at ~0 gross it is dead and no clean window was burned. If
it passes, it is NOT a verdict — it earns a clean confirmation (forward capture, or the
unspent 2021 cache) and nothing more.

Contamination here is also weaker than usual: every prior look at 2022–23 A/B'd subsets
of the same bt17 momentum pool. This scans a population that pool never contained (a
stock basing after a decline fails the 4–8% gainer pre-filter by construction).

## Parameters — fixed a priori, read off the screenshots, NOT from data

| Parameter | Value | Source |
|---|---|---|
| `n_probes` ≥ | 2 | charts 2 and 4 each mark exactly two attempts |
| probe definition | `low < L` and `close > L` | wicks poke through in charts 1, 2, 4 |
| base window | ≤ 20 bars | the shelves run ~8–15 bars |
| close location (mean) | ≥ 0.60 | long lower wicks, small bodies |
| approach | ≥ 1.5 × ATR(14) decline into L | the steep drops preceding every shelf |
| volume sustained | mean(base) ≥ 1.0 × mean(approach) | "buyer is buying everything" |
| no follow-through | zero closes below L in the base | wicks allowed, closes not |

Reused existing repo constants, not chosen here: `PIVOT_K=3`, `CLUSTER_TOL_PCT=0.30`,
`NEAR_PCT=0.35`, `atr(period=14)`.

Trade geometry (system convention, not tuned): entry = resting buy-stop above base
high; stop = lowest probe low (structural, not a 0.54% noise stop); target = 2R;
EOD close 15:15; one trade per symbol-day; arming cutoff 14:45.

Execution assumption reported **both ways, stated before the run**: fill-at-trigger
(primary — justified by live resting-order evidence, +0.03% over trigger, n=11) and
next-bar-open (conservative — bt17 measured +0.209% slippage over trigger).

## Pre-registered criteria

| # | Criterion | Bar |
|---|---|---|
| 1 | Gross/trade | ≥ +0.30% (must clear 0.21% real costs with margin) |
| 2 | Anti-test | p < 0.05 vs random equal-size draws from the ≥1-probe superset |
| 3 | Per-year sign | positive in both 2022 and 2023 |
| 4 | Cost-stress | net positive at real 0.21%; also reported at 0.80% stress |
| 5 | Mechanism | n_probes ≥2 must beat n_probes =1, correct-signed |

Criterion 5 is the one that matters most: if two failed probes are not better than one,
the "sellers can't break it" story is wrong even if the aggregate number looks good —
that would be a number without a mechanism.

**Verdict rule:** all 5 → clean confirmation earned. 3–4 → weak, still needs clean data.
≤2 → KILL, no variant #2 on this window.

## Primary / secondary

Primary population: intraday 5-min bars (matches the deployed architecture and gives a
tradeable structural stop). Secondary, descriptive only: daily bars, since the pattern
is timeframe-agnostic in principle. Both reported regardless of outcome.

---

# RESULT — 2026-09-15. KILLED, 0 of 5 criteria. Single shot, no re-runs.

Window 2022-01-01..2023-12-31 · 277 symbols scanned, 270 produced trades · 23,505
symbol-days · 9,460 strict trades (fill-at-trigger). Real cost measured 0.183%
(₹1L notional hits the ₹20 brokerage cap favourably; bt17's 0.21% figure reproduced).

| # | Criterion | Bar | Result | |
|---|---|---|---|---|
| 1 | gross/trade | ≥ +0.30% | **−0.020%** (se 0.009, t=−2.27) | ✗ |
| 2 | anti-test | p < 0.05 | **p = 0.856** — worse than random | ✗ |
| 3 | per-year sign | + in both | 2022 −0.041%, 2023 +0.000% | ✗ |
| 4 | net at real costs | positive | **−0.203%** (−1.003% stressed) | ✗ |
| 5 | mechanism (≥2 probes > 1) | correct-signed | **inverted** (see below) | ✗ |

Exits: 47.6% eod, 39.1% stop, 13.3% target. Win rate 37.3%. Mean R 0.88%.

## Criterion 5 is the one that matters — the mechanism is inverted

The thesis was "sellers keep failing to break the low, so buyers must be absorbing."
The data says the opposite: **more failed probes is worse, not better.**

    >=1 probe (loose superset, n=23,011)   gross -0.013%
    >=2 probes (strict rule,  n= 9,460)    gross -0.020%

Filtering *for* repeated failed breaks made the population worse. The anti-test says
the same thing from the other side: random draws of 9,460 from the ≥1-probe superset
average −0.013% (5–95pct [−0.024, −0.002]); the rule scores −0.020%, p=0.856. The rule
is not selecting — it is selecting slightly badly. This is the fifth selectivity kill
in this system (quality p=0.979, 1m-agreement p=0.526, max-move, pullback-ordinal
p=0.469) and the first where the *operator's own visual mechanism* was the thing tested.

The honest reading: a level probed twice is not a level being defended. It is a level
that is about to go.

## Daily arm (secondary) — a bull-beta trap, caught

Daily bars read **+2.360% gross, t=+5.40, positive in both years** — superficially the
best number this repo has produced. It is entirely market drift:

    strict absorption base (n=  319)   +2.360%
    loose >=1 probe        (n=2,241)   +2.596%
    RANDOM 20-day hold, same symbols   +2.463%   <- the benchmark
    edge over simply being long        -0.103 pp
    anti-test                          p = 0.668

Mean R was 9.35% and 60% of trades hit max-hold, so the daily arm was a 20-day
buy-and-hold wearing a pattern costume. Recorded because it is a clean worked example
of the anti-DSR>0 / bull-beta trap: a t-stat of +5.40 that means nothing.

## What was real in the data (all far too small, none actionable)

- Base **length** has a correct-signed gradient: 2–3 bars −0.046%, 11–20 bars +0.032%.
  Real, but it is a "quiet consolidation" effect, not a "failed break" effect — and it
  is ~10× below the 0.183% cost floor.
- Volume-ratio top quartile +0.008% vs bottom −0.052%: directionally consistent with
  BT31's volume finding, same order of magnitude, same verdict.
- Wide-R trades show better gross (+0.046%) and *fewer* targets (6.9% vs 19.2%) — that
  is stop-distance mechanics, not edge.

## Status

**KILLED. No variant #2 on this window.** The tight structural stop (mean R 0.88% vs
the 0.54% noise stop) was the architecturally attractive part and it did not rescue a
population with no drift. No clean window was spent: 2021 and forward capture remain
untouched, and neither is owed to this idea.

---

# AMENDMENT — 2026-09-15, same day. The 0/5 KILL was run on an UNTRADEABLE UNIVERSE.

**Raised by the operator**, from a chart: CESC 2022-02-08, ₹83.50 entry, **day change
0.1%**, whole-day range ~₹1. "This is not a volatile stock… hope these trades are not
done with my filters."

They were not. The pre-registered run applied only `universe.passes_dynamic` (price
₹60–2,000, 20-day turnover ₹3–50 cr). It did **not** apply the gainer gates that the
engine actually runs on:

    legacy / warrior path   day change 4-8%   AND  RVOL >= 3.0     (engine.py:40-42)
    attention (DEPLOYED)    day change >=1.5% AND  RVOL >= 1.5     (engine.py:70-71)

My §Contamination argument — "a stock basing after a decline fails the 4–8% gainer
pre-filter by construction" — was **wrong**, and it was load-bearing. 711 trades survive
the attention gate and 111 survive warrior. The absorption base forms as a *pullback
inside an up-day* (PRESTIGE 2022-06-21 was +4.8% on the day), not only after a
breakdown.

Shipped as `--universe {none|attention|warrior}`; `none` verified to reproduce the
pre-registered CSV **identically** on a 10-symbol regression.

## Re-run, 2022-23

| gate | n | gross | t | net @ real | win | mean R |
|---|---|---|---|---|---|---|
| none (as pre-registered) | 9,460 | −0.020% | −2.27 | −0.203% | 37.3% | 0.83% |
| attention ≥1.5% / 1.5× | 711 | **+0.114%** | 1.97 | −0.069% | 44.4% | 1.83% |
| warrior 4–8% / 3× | 111 | **+0.204%** | 1.20 | **+0.021%** | 42.3% | 2.70% |

Mean R triples because the gate selects names that can actually travel — on CESC a 2R
target was the entire day's range.

**The C5 mechanism also un-inverts.** Ungated, ≥2 probes was *worse* than ≥1 (−0.007 pp).
Gated it is correct-signed: **+0.041 pp** (attention), **+0.026 pp** (warrior).

## Drift benchmark — and a benchmark error worth recording

First attempt sampled random entries only on days that *produced a trade*, which
conditions on the base forming and breaking upward. It read +0.92%/+1.63% drift and
would have made the pattern look catastrophic. Re-run over **all** gate-passing
symbol-days:

    attention  random entry -> EOD  -0.1485%  (n=175,134)   pattern edge  +0.262 pp
    warrior    random entry -> EOD  -0.0183%  (n= 38,156)   pattern edge  +0.222 pp

So this is **not** the bull-beta trap the daily arm was. On the tradeable universe the
setup genuinely beats holding. (Same class of error as the daily arm, opposite sign —
always ask what the benchmark population was conditioned on.)

## Revised scorecard

| # | criterion | none | attention | warrior |
|---|---|---|---|---|
| 1 | gross ≥ +0.30% | ✗ −0.020% | ✗ +0.114% | ✗ +0.204% |
| 2 | anti-test p < 0.05 | ✗ 0.853 | ✗ 0.197 | ✗ 0.418 |
| 3 | positive both years | ✗ | ✓ | ✓ |
| 4 | net positive @ real | ✗ −0.203% | ✗ −0.069% | ✓ +0.021% |
| 5 | ≥2 probes > ≥1 | ✗ inverted | ✓ +0.041 | ✓ +0.026 |
| | **total** | **0/5 KILL** | **2/5 KILL** | **3/5 WEAK** |

Pre-registered verdict rule: 3–4 → "weak, still needs clean data."

## What still fails, and why this is NOT a resurrection

- **The anti-test still fails under every gate.** Decomposition: the gate supplies
  **68%** (attention) / **88%** (warrior) of the improvement; the absorption terms add
  +0.041 / +0.026 pp, inside the noise band (sd 0.048 / 0.142). The edge is
  *gated + a probed level + a buy-stop breakout* — **not** "probed twice with absorption".
- **n=111 cannot carry this.** This repo has already been burned once: a 15-symbol smoke
  test read the wrong sign on 200 trades. t=1.20 is not evidence.
- **Three gates were tried and the best is being quoted.** Textbook multiple testing on a
  spent window. Per the standing rule, a dirty window **can kill but cannot bless**.
- **It is not a business even if real.** 111 trades over two years (1.1/week) at +0.021%
  net = **₹2,477 total** on ₹1L sizing. The attention arm, which trades 6.8/week, loses
  **₹48,913**.

## Status — revised

**No longer a clean kill. Now UNRESOLVED on the tradeable universe**, and the honest
statement is: the pre-registered test answered a question about a universe the operator
does not trade. The kill stands for `--universe none`; for `warrior` the result is
3/5 on n=111, which is a signal to *get clean data*, not to ship anything.

Earned next step (needs operator go-ahead): the unspent **2021 cache** (183 symbols) with
`--universe warrior`, criteria C1–C5 locked exactly as above, single shot. That is the
only way this becomes evidence rather than a spent-window artifact.
