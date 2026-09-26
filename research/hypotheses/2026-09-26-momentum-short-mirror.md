---
slug: momentum-short-mirror
strategy: momentum_trader
status: killed
registered_at: '2026-09-26'
finalized_at: '2026-09-26'
decided_at: '2026-09-26'
hypothesis_hash: ''
---

# Hypothesis: NSE stocks already down 4–8% on heavy volume keep falling after a bear-flag breakdown, by enough to pay for a short

Written in plain language on purpose. Registered **before** any full run.
Only a 25- and an 80-symbol smoke test (plumbing checks, 11 trades, 2024-H1)
were run before this file; their numbers are not evidence and are not used.

## 0. What is being tested, in one paragraph

The operator asked for selling "similar to buying". The long arm buys a
stock that is already up 4–8% on the day on 3x normal volume, waits for a
small pullback, and buys the moment the pullback ends (price breaks the
pause candle's high), with a stop under the pause and a target twice as far
away. The short arm does the exact mirror: a stock already DOWN 4–8% on 3x
volume, a small bounce, and a short sale the moment the bounce fails (price
breaks the pause candle's LOW), stop above the bounce, target 2R below.
It is not a new rule set. It is the same engine fed a price chart flipped
upside down around yesterday's close (`src/momentum_trader/short_side.py`),
so every rule the long side has — the seven setups, the chase guard, the
0.3–3% stop band, the structural exits, the 15:15 close — applies to the
short in mirror image, and nothing about the long side changes.

## 1. Mechanism

Why could selling-into-weakness work when buying-into-strength has not?

* **The asymmetric-fear story.** Holders of a falling stock sell in waves
  (stop-losses, margin calls, funds cutting losers before a reporting date),
  and bad news tends to be digested more slowly than good news. If that is
  true, downside moves persist longer than upside ones.
* **Evidence from this repo, both directions:**
  - BT14 (2026-06-29, NIFTY 500, 2025-H2): "long-losers" earned **−0.18%
    gross** intraday — the day's losers kept falling the next session. That
    is the right sign for this short, but measured on a different entry
    (next-day open, no pattern) and a different window.
  - BT15/BT17: the day's GAINERS fade (up-gappers −0.35% open→close). That
    is mean-reversion, and if it is symmetric, losers bounce — the WRONG
    sign for this short. The prior is genuinely mixed.
  - BT7 (2026-06-11) shorted on bearish NEWS, not on price, and found no
    bearish drift (+0.045% gross). Different trigger; it does not decide
    this.
* **Why it could still fail:** the long pool's gross is ≈ 0.00%/trade with
  costs (~0.21% round trip) as the entire loss. Nothing structural makes the
  mirror image different except the fear asymmetry above. A null result is
  the base rate.

## 2. Expected effect size

- **Direction**: short.
- **Needed**: mean gross ≥ **+0.35%/trade** (the repo's frozen viability
  bar — real MIS cost ≈ 0.21% round trip plus a margin; BT23).
- **Honest prior**: somewhere between −0.1% and +0.2% gross, i.e. most
  likely a KILL.

## 3. Falsification criteria (LOCKED before the full run)

**Primary arm P** — the legacy pool, the same configuration the long side's
BT36-corrected baseline used, mirrored:

```
uv run research/backtests/bt17_momentum_pool.py --start 2024-01-01 --end 2024-12-31 \
    --side short --live-fill --tag short_mirror_2024
```

PASS requires **all five**:

| # | Criterion | Threshold |
|---|---|---|
| C1 | Mean net per trade at **real** costs (no research stress) | > ₹0 |
| C2 | Mean gross per trade | ≥ +0.35% |
| C3 | 95% confidence interval of mean gross (t) | lower bound > 0 |
| C4 | Trades | n ≥ 300 |
| C5 | Robustness: gross > 0 in BOTH halves of 2024, AND mean real-cost net still > 0 after dropping the 20 best trades | both |

- Any failure → **KILL** the short pool as an edge claim. The short
  capability stays in the code (it is the same engine), live default OFF,
  and no threshold, setup list or window is re-tuned on 2024.
- All five pass → **one** run of the identical command on 2025 (never read
  for shorts), judged on C1–C3. Single shot.
- **Anti-strategy check**: the inverse of this trade is buying the same
  entries at the same moments, whose gross is exactly the negative. If the
  short's real-cost net is ≤ 0, the inverse loses too, and the pool is not
  a signal — that is C1 + C3.
- **Cost stress** (repo Gate 0 shape): the bt17 report's +40 bps/side
  stressed net is printed beside the real-cost net. Not a separate gate here
  because C1–C2 are already stricter than the viability bar derived from it.
- Median real-cost net and drop-top-N are printed beside every mean
  (2026-09-20 lesson).

**Secondary arm S** (descriptive, NOT a gate — two arms on one window is
already a selection): the deployed live arm's entry rules, mirrored:

```
uv run research/backtests/bt17_momentum_pool.py --start 2024-01-01 --end 2024-12-31 \
    --side short --warrior-strict --tag short_warrior_2024
```

**Context (not a gate)**: the long pool, same command without `--side
short`, so the two sides are compared on identical code, universe and
window.

## 4. Data

Upstox 1-minute candles, NIFTY 500 base list, the repo's cache
(`research/backtests/.cache_upstox/`). Universe rules identical to the long
pool: price ₹60–2,000, 20-day turnover ₹3–50 cr, 4–8% day change (down,
here), RVOL ≥ 3x time-of-day. Circuit-band / T2T names cannot be shorted
intraday on NSE; band and series filters are forward-only here, as for the
long pool, which slightly FLATTERS this backtest (a band-locked loser could
not have been shorted at all).

## 5. Window

- Dev: **2024-01-01 → 2024-12-31**. Used four times for LONG entry-side
  work; this is the first time its LOSERS are read. A spent window can kill
  but not bless (2026-09-15 lesson) — a PASS here only earns the 2025 shot.
- Hold-out: 2025 — never read for shorts.

## 6. Known approximations (disclosed before the run)

- Percentage-of-price rules (0.3–3% stop band, 1% chase guard, 0.35% level
  band) are measured on the reflected price. Relative error on the threshold
  = 2 × |day change|: a 0.30% minimum stop is ~0.33% in real terms on a
  stock down 5%. Round-number levels ARE on the real grid
  (`indicators.reflected`).
- NSE has no short-sale uptick rule and intraday MIS shorts need no borrow.
  The US has both (SEC Rule 201 SSR and a locate); the US arm models SSR as
  a refusal and cannot model the locate (no data) — not tested here.

## 7. Result — KILL (2026-09-26, BT51)

Scored by `research/backtests/bt51_short_mirror_gates.py` (criteria copied
from §3, real-cost net recomputed per row with the side's own MIS model).

### Primary arm P, 2024 (the registered dev run)

| | Short (P) | Long, same config (context) |
|---|---:|---:|
| Trades | 171 | 982 |
| Mean gross / trade | **+0.149%** (95% CI +0.023 … +0.276) | +0.086% (CI +0.029 … +0.144) |
| Median gross | +0.136% | −0.145% |
| Mean net at REAL costs | **−₹30.73** | −₹53.67 |
| Median real net | −₹34.16 | −₹137.74 |
| Real net, drop best 20 | −₹107.02 | −₹72.76 |
| Stressed net (+40 bps/side) | −₹395.56 | −₹418.58 |
| Gross H1 / H2 | +0.194% / +0.061% | +0.080% / +0.094% |

C1 FAIL (−₹30.73) · C2 FAIL (+0.149 < +0.35) · **C3 PASS** (CI lower
bound +0.023) · C4 FAIL (171 < 300) · C5 FAIL (drop-top-20 −₹107) → **KILL**.
No 2025 hold-out run is earned.

The short side was the better of the two on 2024 — the first sub-pool in this
line of work whose gross CI excludes zero — but only by ~0.06 pp, and at
real costs it still lost money. The pool is small because 2024 was a bull
year: 1,965 turnover-eligible −4% days vs 3,354 +4% days, and losers yielded
0.21 candidates/day vs 0.47.

### Operator-requested follow-up: trailing 12 months (2025-09-26 → 2026-09-25)

Run AFTER the kill, at the operator's request ("run the backtest on last
year data"). Descriptive only: it cannot un-kill the arm. ⚠️ It reads Q4-2025,
part of the 2025 hold-out; for shorts that hold-out is now partly spent.

| | Short (P) | Long (context) | Short warrior_strict | Long warrior_strict |
|---|---:|---:|---:|---:|
| Trades | 261 | 975 | 37 | 178 |
| Mean gross / trade | **+0.024%** (CI −0.083 … +0.131) | +0.109% (CI +0.047 … +0.170) | +0.007% | +0.045% |
| Mean net at real costs | **−₹81.17** | −₹41.05 | −₹96.92 | −₹78.25 |
| Median real net | −₹198.72 | −₹148.51 | | |
| Gross H1 / H2 | +0.097% / −0.112% | +0.156% / +0.065% | | |

All five criteria fail on this window too. **The 2024 short-over-long gap did
not replicate** — over the most recent year the short side is WORSE than the
long side, and its second half is negative gross. Same shape as every other
measurement here: no drift, costs are the loss.

Secondary arm S (warrior_strict, mirrored), 2024: n=20, gross −0.023%, real
net −₹100.03/trade. Too few trades to read either way.

### Where the edge is and is not (observation, not a finding)

`flat_top_breakout` (mirrored = flat-bottom breakdown) was the best short
setup on BOTH windows (+0.335% gross n=34 in 2024, +0.253% n=62 trailing), the
only one net-positive on both. Two looks at a sub-group selected after the
fact — do not trade it; if pursued, it needs its own registration and an
unspent window.

### What stays

The capability stays: the short side is the long engine on a reflected tape,
the long path was verified byte-identical against `origin/main` (244 + 34
long trades, 379 + 46 candidates, every field equal), and it is OFF in both
live arms (`MT_ENABLE_SHORTS=false`, `MT_US_ENABLE_SHORTS=false`). Turning it
on for paper collects forward data; nothing above says it will make money.
