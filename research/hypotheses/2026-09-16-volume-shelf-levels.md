# Volume shelves — the level type pivots cannot see

*Registered 2026-09-16. Status: **KILLED 2026-09-16 on Q4-2024, 0 of 3
criteria** (§7). Stays shipped OFF behind `EngineConfig.volume_shelf_levels`.*

## 1. Provenance — a defect, not a strategy idea

The operator drew three resistance lines on EIHOTEL for 2026-09-16 — 289.75,
290.65, 291.60 — and asked why the system bought at 290.60, inside that zone.

Reproducing the engine's own level set from the 118 one-minute bars it had at
the 11:16 decision bar:

```
 300.000  round       touches=1   STRUCTURAL   <- what the gate used
 293.500  pivot_high  touches=1   discarded (touches < 2)
 289.708  pivot_high  touches=2   STRUCTURAL
 289.160  pivot_high  touches=4   STRUCTURAL
 ...
 nearest structural resistance above trigger 290.40  ->  Rs300.00
```

The headroom test therefore saw **9.6R of clear air** above a trigger with
1R = Rs1.15, and let the trade through. The operator's 290.65 line — 0.25 above
the trigger — did not exist in the level set at all.

It was not hindsight. Touch counts from bars available **before** the entry:

| line | touches before 11:16 | when |
|---|---|---|
| 289.75 | 9 | 10:00-11:14 |
| **290.65** | **7** | 10:00, 10:01, 10:02, 10:04, 10:05, 10:06, 10:07 |
| 291.60 | 4 | 10:03-10:06 |

And 27% of the session's volume had already traded between the trigger and the
2R target.

### 1.1 A wrong diagnosis, corrected by the operator

The first write-up of this also proposed rescuing single pivots whose *bar*
volume was in the session's top decile, to make the 293.50 day high structural.
The operator rejected it: 293.50 is one spike wick.

| level | bars touching (±0.05) | rejections | minutes within ±0.25 | % of day volume within ±0.25 |
|---|---|---|---|---|
| **293.50** | **1** | **1** | **1** | **0.6%** |
| 291.60 | 21 | 6 | 45 | 11.8% |
| 290.65 | 35 | 13 | 65 | 13.7% |
| 289.75 | 14 | 4 | 30 | 6.1% |

`is_structural()`'s `touches >= 2` rule was **right** to discard it. The error
was using the 10:03 bar's 47,486 shares as evidence for its wick tip — that
volume belongs to the whole 290.80→293.50 sweep, and volume-by-price puts most
of it at 291.00. **Crediting a bar's volume to a price it merely passed through
is the same mistake the pivot detector makes, in miniature.** That rule is
unchanged; only the shelf detector was built.

## 2. Mechanism — why a pivot cannot see this

A pivot high needs `PIVOT_K = 3` lower bars on each side. Inside a vertical run
every bar makes a higher high, so none is a local maximum; and the one impulse
bar then **wins the ±3-bar window of every bar around it**. On EIHOTEL the
entire 10:04→10:18 collapse produced zero pivot highs. The 10:05 bar (high
291.70, upper wick 33% of its range) and the 10:06 bar (high 291.60) are the
rejections the operator drew 291.60 from; both sit within 3 bars of 10:03.

The blind spot is exactly where post-spike supply is built.

## 3. The frozen rule

`levels.volume_shelf_levels(bars, atr)` — default **off** in `derive_levels`
(`add_shelves=False`) and in `EngineConfig.volume_shelf_levels`.

1. Spread each bar's volume **evenly across its high-low range** into buckets.
2. Bucket width = the symbol's own 1-minute ATR (`SHELF_BUCKET_MIN` 0.05 floor,
   0.15%-of-price fallback). A band narrower than one bar's range is not a
   price the market argued over.
3. A **shelf** is a bucket that is a local maximum over ±1 bucket and holds at
   least `SHELF_MIN_VOLUME_MULT = 1.5x` the mean occupied bucket.
4. Shelves are in `STRUCTURAL_KINDS`, so they can block an entry. Everything
   else about the level set, including `touches >= 2` for pivots, is unchanged.

Look-ahead safe: the profile is built only from the bars passed in, and the
engine passes bars strictly before the confirmation bar.

Reproduce the audit: `apps/signal-engine/.venv/bin/python
research/backtests/bt37_level_audit.py --date 2026-09-16`.
Backtest opt-in: `bt17_momentum_pool.py --volume-shelves`.

## 4. What it does to 2026-09-16 (DESCRIPTIVE ONLY, n=4)

| symbol | gate's next resistance, as shipped | with shelves | verdict | net at real charges |
|---|---|---|---|---|
| EIHOTEL | 300.00 (round, +3.3%) | **290.89 (shelf)** | **REFUSE** (headroom 0.49 < 1R 1.15) | −₹240 |
| J&KBANK | 150.00 (round) | 150.00 (round) | take | −₹66 |
| GODREJIND | 1150.00 (round) | 1148.80 (shelf) | take (headroom 5.40 vs 1R 5.30) | −₹179 |
| ABDL | 660.00 (round) | 660.00 (round) | take | −₹244 |

Two things must be said about this table and neither is optional:

* **Four trades on one session cannot measure a rule**, and the bucket-width
  choice was made while these outcomes were visible.
* **GODREJIND is a coin flip.** On the engine's own recorded bars the shelf
  lands at 1148.80 and the trade is taken by ₹0.10 of headroom; on Yahoo bars
  for the same session it lands at 1147.30 and the trade is refused. A verdict
  that flips on the data vendor is not a verdict.

The honest summary is narrower: **in 4 of 4 entries the binding level was a
round number before this change, and in 2 of 4 it became a tape-derived level
after it.** That is a statement about the level set, not about P&L.

## 5. Pre-registered test — NOT YET RUN

No window has been spent. When one is allocated:

* **Arm**: `bt17 --attention --volume-shelves` against the same run without it,
  identical symbol list, `--jobs` fixed.
* **Primary**: gross %/trade difference, and the anti-test — a random refusal of
  the same number of trades must NOT reproduce the lift. Three of the last four
  selectivity ideas here died on exactly that test (quality p=0.979,
  1m-agreement p=0.526, pullback-ordinal p=0.469).
* **Criteria**: ship only if the lift exceeds the 0.21% real round-trip cost in
  the direction that matters *and* the anti-test clears, in **both** halves of
  the window.
* **Prior**: poor. The refusal rate on 2026-09-16 was 25-50%; a rule that
  removes a quarter of trades from a pool with no drift saves money by trading
  less, which BT30 already showed is not selection.

⚠️ 2022-23, 2024 and 2026 are spent for entry-side mining. 2025 is currently
reserved for the locked `resistance_veto_v2` C1-C3 confirmation. **This needs
the operator to decide which window it gets, or to wait for forward data.**

## 6. Related

* [[2026-09-13-resistance-headroom-v2]] — the headroom test this feeds.
* [[2026-09-13-volume-surge-entry]] — the other volume-based entry study;
  killed as a filter but the first non-random one.


## 7. Result — KILL (0 of 3), Q4 2024

Run: `bt17 --start 2024-10-01 --end 2024-12-31 --attention --first-candidate-only
[--volume-shelves]`, 170 fully-cached symbols, `--jobs 10`. Decided by
`research/backtests/bt38_volume_shelves.py`.

| | shelves OFF | shelves ON |
|---|---|---|
| trades | 157 | 148 (94.3% survive) |
| gross %/trade | −0.0844 | −0.0607 |
| net at real 0.21% | −0.294 | **−0.271** |
| win % | 34.4 | 37.2 |

| criterion | result | |
|---|---|---|
| C1 survivors clear the 0.21% cost | gross −0.061% | **FAIL** |
| C2 anti-test p < 0.05 | **p = 0.403** | **FAIL** |
| C3 lift positive in both halves | +0.072 / −0.005 pp | **FAIL** |

### 7.1 The headline lift is mostly not the rule

The +0.0237 pp looks like the filter working. It is not. Decomposed:

| source | contribution |
|---|---|
| refusing 16 trades — **the actual filter** | **+0.0037 pp** |
| exits changing on 9 surviving trades | +0.0068 pp |
| 7 NEW trades the OFF arm never took | +0.0132 pp |

Shelves are in `STRUCTURAL_KINDS`, so they also feed `resistance_reject` exits
and the attention arm's resistance-breakout entry test. **The ON arm is
therefore not a subset of OFF** — it is a different strategy, not a filter.
Scoring it against random subsets of OFF (the naive anti-test) credits those
side effects as selectivity and returns a spurious p = 0.011. Scored correctly
— on the refused trades alone, where `kept` *is* a subset — the rule gets
**p = 0.403: indistinguishable from deleting 16 trades at random.**

This is the 5th selectivity idea to die on the anti-test, after quality
(p=0.979), 1m-agreement (p=0.526), pullback-ordinal (p=0.469) and
pattern-as-filter.

### 7.2 Honest limits of this run

* **n = 157 is small** and the window is 3 months. This can kill but not bless
  ([[feedback_dirty_window_can_kill]]); a small real effect is not excluded.
* **The rule barely fires here** — 5.7% refusal vs the 25–50% seen on
  2026-09-16. Q4 2024 may simply not be the tape that builds shelves.
* Symbols restricted to the 170 with a full 2022–2024 parquet cache. That is
  selection on data availability, not on outcomes, but it is not the full pool.
* Q4 2024 sits inside the already-spent 2024 entry-side window. 2025 (reserved
  for `resistance_veto_v2` C1–C3) and 2021 (reserved for the BT35 absorption
  single-shot) were **not** touched.

### 7.3 Two script defects found and fixed while running this

Both were in `bt38_volume_shelves.py`, written for this test:

1. C2 was computed against the whole ON arm, which is not a subset of OFF —
   it read p = 0.011 (PASS) when the honest figure is 0.403.
2. C3 counted **calendar years**, so a 3-month window had one year and passed
   vacuously. It now splits the window at its date midpoint, and fails.

**A criterion that cannot fail on the window you ran is not a criterion.**
