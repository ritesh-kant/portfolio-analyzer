# BT57 — US live rule over the 2026 IBKR cache (descriptive)

No hypothesis, no tuning, no registration beyond the dashboard label. Script: `bt57_us_live_replay.py`; CSV `bt57_trades_us_live_2026_20261003.csv`; coverage in `bt57_us_live_2026_20261003_summary.json`.

**Data.** IBKR regular-hours 1-minute TRADES bars, 2026-01-01 to 2026-10-02, 78 contracts (5.3M bars), cached in `.cache_ibkr_us/1m/`. Six symbols did not resolve (`AMPX-WT`, `BCTXL`, `BESS-WT`, `KCAC-WT`, `KRSP-WT`, `ZZZTT`). Several names only listed mid-year (AIIR 05-18, GYGY 07-30, CHWM 10-01, LABT 04-23) so their earlier pages are legitimate "no data" gaps; LABT timed out twice on its pre-listing window.

**Run.** Deployed US long rule (current arm of BT56), every cached session of every cached symbol replayed from the 09:30 open: 13,949 symbol-days, 113 missing, 1,078 with fewer than 15 prior sessions for the volume profile.

**Result.** 1 trade (TNON, 2026-02-09, stopped out two minutes after entry): net −$55.32. Mean, median, win rate and drop-top-N are meaningless at n=1. The engine's refusals were dominated by `attention_red_or_flat` (45,874), `attention_low_1m_volume` (10,226) and `attention_weak_close` (3,905).

**Limits.** No historical US screen exists before 2026-09-24, so the engine's own attention gates were the only selection. The pool is the late-September watchlist names (survivorship-biased: they were movers at the end of the window, not on each day). No daily guardrails or size ladder. One-minute later-close fill approximation. This says the live rule almost never fires on these names; it cannot say whether the rule makes money.

## Funnel check (`bt57_funnel.py`, `bt57_funnel_days.json`)

Of 13,984 symbol-days, 1,696 had a high ≥10% above the prior close at a $1–20 prior close. 591 of those (35%) were promoted to attention (day change ≥10%, RVOL ≥1.5, a promotion candle/setup); 1,105 never were and logged no refusal. **0 of the 591 produced an entry.** On promoted days the refusals were `attention_red_or_flat` (587 days), `attention_low_1m_volume` (480), `attention_removed:below_vwap` (431), `attention_no_micro_pullback` (408), `attention_weak_close` (388). The lone BT57 trade (TNON, $29) is outside the $1–20 band, so the in-band count is 0.

Hand check of PMAX 2026-01-13 (+25%, steady climb above VWAP, no pause bar) and BNKK 2026-01-15 (+81% open, then chop around VWAP) agrees with the refusals on their 5-minute tables. Two days do not prove the engine is faithful to live.
