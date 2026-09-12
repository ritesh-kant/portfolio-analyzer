# Does the v3 candlestick detector improve my *actual* trades?

Registered 2026-09-12, before BT29 ran. Author: Claude (agreed scope: user asked
"run a backtest for all the old past trades and tell me whether v3 performs well",
noting explicitly that entries are **not** pattern-driven — patterns would be an
extra confirmation on top of the existing momentum criteria.)

## Why this is a different question from BT28

BT28 asked: *does a pattern, on its own, on any bar, predict the next N minutes?*
Answer: no (0 of 105 cells beat cost + Bonferroni, twice, on two separate years).

BT29 asks a **conditional** question: *given that my momentum system already
decided to enter (gainer + RVOL + setup + trigger), does the presence of a v3
pattern at that moment separate the winners from the losers?* A signal can be
worthless unconditionally and still be informative inside a pre-filtered pool,
so this is not a re-run of a killed hypothesis.

## Population

Every trade this repo has ever simulated with the momentum engine: the union of
`research/backtests/bt17_trades*.csv`, de-duplicated on
`(date, symbol, setup, entry_time, entry)`.

| window | trades |
|---|---:|
| 2022 | 3,809 |
| 2023 | 4,475 |
| 2024 | 1,968 |
| 2025 | 927 |
| 2026 | 689 |
| **total** | **11,868** |

Exit rules differ between the variant runs the trades came from. That adds noise
to the outcome but cannot bias the contrast, because pattern presence is fixed at
entry and is independent of which variant file a trade first appeared in. The
per-window tables are the stability check.

## Exposure

For each trade, re-run `completed_pattern_matches(day_5m, "5m", closed_through=T)`
with the **current v3 rules** for T = entry, entry−5m, entry−10m, entry−15m.
A trade is *pattern-confirmed* if a **bullish** v3 formation completed on any of
those four closed bars. All trades in the pool are long, so:

* `bullish` at entry = confirmation
* `bearish` at entry = contradiction (this is the built-in anti-test)
* `neutral` (doji / spinning top) = reported separately, claims no direction

## Primary hypothesis

H1: mean **gross** return of pattern-confirmed trades exceeds that of
unconfirmed trades.

## Decision rule (fixed now)

Ship the pattern as a live filter only if **all** hold:

1. gross spread (confirmed − unconfirmed) ≥ **+0.30 pp** — the relax bar this
   repo locked for momentum entry-side changes;
2. t-stat of the spread ≥ **2.0**;
3. anti-test behaves: bearish-at-entry trades are **not** better than confirmed;
4. sign holds in at least **3 of 4** windows (2022-23 / 2024 / 2025 / 2026);
5. cost stress: the confirmed subset is net-positive at the **real MIS 0.21%**
   round-trip, not only gross.

Anything less: report the number, do not change the engine.

## Secondary, decided now so it cannot be picked later

* S1: same test per pattern name (reported, Bonferroni-corrected over the cells
  actually reported; no cell is promoted on its own without S1 surviving).
* S2: head-to-head against the tags stored in the CSVs at the time each run was
  made (the pre-v3 detector). Question: does v3 sort the trades better than the
  old detector did? Metric: the same gross spread, on the same trades.
* S3: exit-reason mix (stop / target / false_break / eod) by confirmation status.

## Multiple-testing status

2022-23, 2024 and 2026 are already spent for *entry-side momentum* mining, and
2023 + 2026 are spent for *unconditional candlestick* mining. This test is
pre-registered, single-shot, and reported whatever it says. If it fails there is
no variant #2 on the same data: the pattern-as-filter idea is then closed the
same way the news-trader and PEAD were.
