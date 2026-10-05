# BT58 — US attention day-change floor +10% → +5%

Registered 2026-10-04, before the arm was run. Control = the BT57 descriptive replay (floor +10%, deployed value): 1 trade.

**Result (2026-10-04): INCONCLUSIVE (criterion 1).** Control (+10%): 1 trade, −$55.32 (reproduces BT57). Arm (+5%): 3 trades, net −$21.40, mean −$7.13, median −$55.32, win 1/3. Trades: TNON 02-09 −$55.32 (stop, $29 — outside the $1–20 band), WW 05-26 −$57.68 (stop), WW 09-29 +$91.60 (target); the in-band two net +$33.92. n=3 is far below the locked 30, so no verdict on mean, median, drop-top-5 or the in-band subset; this does not validate the lower floor. Files: `bt58_trades_bt58_floor5_2026_new.csv`, `bt58_trades_bt58_floor10_2026_ctl.csv`.

**Hypothesis.** The US arm's attention floor (`attention_day_chg_min`, NSE uses +1.5%) is so high that the engine only looks at a stock after the move is largely over; lowering it to +5%, everything else unchanged, produces enough entries to judge, and those entries are net-positive at the modelled US costs.

**Arm.** `USUniverseConfig(day_chg_min_pct=5.0)` in the BT56 per-day replay machinery; no other change. Control and arm replay every cached 2026 session of the 78 cached IBKR symbols (2026-01-01..2026-10-02) from the 09:30 open with the same costs, sizing and fills. One value only: no 3%, 4%, 7%, 8% follow-ups from this window.

**Locked criteria** (arm, modelled US costs, USD):
1. n ≥ 30 arm trades, otherwise INCONCLUSIVE.
2. Mean net per trade > 0 and drop-top-5 total net > 0, otherwise KILLED.
3. Median net per trade is printed beside the mean; a positive mean with a non-positive median is reported as top-heavy, not as a pass.
4. Trades outside the $1–20 prior-close band (the replay does not enforce price) are shown separately and the in-band subset must also be net-positive.

**Limits fixed in advance.** The 2026 symbol pool is the late-September watchlist (survivorship-biased) and no historical US screen exists, so the engine's gates are the only selection; the +5% floor also bypasses the live screen's own +10% criterion. No daily guardrails or size ladder; one-minute later-close fill approximation. This window can kill the idea, never bless it. The 2025 hold-out stays sealed.
