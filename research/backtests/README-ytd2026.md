# 2026 YTD — warrior_strict backtest report

> **Viewing a run:** the standing way is the backtest dashboard (`pnpm bt:dashboard`, see
> `README-dashboard.md` and `AGENTS.md`). The standalone-HTML commands below are the older path
> and write to fixed filenames.

Replays the live `warrior_strict` arm over every symbol with a 2026 1-minute
cache (233 with an eligible day), 2026-01-01 → 2026-09-25, and draws each trade
in the BT32 chart format. **Descriptive** — 2026 is a spent window, not a test.

```bash
pnpm bt:ytd2026     # backtest + report (~5 min backtest, ~3 min report)
pnpm bt:serve       # open http://localhost:8899/ytd2026_warrior_strict_report.html
```

Settings mirror the live Fargate task: `--warrior-strict --multi-entry`
(MT_ONE_TRADE_PER_DAY=false, MT_MACD_OPEN_TOLERANCE=0). Since 2026-09-29/30 `--warrior-strict`
also defaults to the checkpoint stop (BT52) and the 14:00 entry cutoff (BT53); before that it
was the BT50 session-capped target and an 11:00 cutoff, so an older `ytd2026` file is not
comparable with a fresh run unless you pass `--no-checkpoint-stop --peak-hours-end 11:00`. Since
2026-10-03 it also defaults to the BT55 session-levels veto (`--no-session-levels-veto` turns it off). Fill = the arm's default `future_trigger` replay; add `--live-fill`
for the resting-buy-stop model (tag `ytd2026_livefill`).

Each card now leads with four pills — **SUPPORT**, **BUY**, **SELL**,
**RESISTANCE** — and draws the same four as thick labelled lines. The key
support/resistance are bt17's `structural_support` / `structural_resistance`:
the levels the engine froze at the entry bar and read its stop and target
against (no look-ahead). The side panel's *Levels* selector switches between
key-only (other levels faint and unlabelled) and every level labelled. Reports
built from CSVs without those columns fall back to the nearest structural level.

## BT52 — checkpoint stop (2026-09-27, KILLED 1/5)

`pnpm bt:bt52` (needs `pnpm bt:ytd2026` first for the control CSV), then open
`bt52_checkpoint_stop_report.html`. Target stays 2R; the price BT50 would sell
at becomes a checkpoint that lifts the stop 0.15% under it from the next bar.
Hypothesis + result: `research/hypotheses/2026-09-27-resistance-checkpoint-stop.md`.

## 3-year report (2026-09-28)

`live_3y_warrior_strict_report.html` — every trade of the live rule over
2022-09-28 → 2024-12-31 + 2026 YTD (2025 hold-out excluded), built from
`bt17_trades_3y_live.csv` (the per-year `bt17_trades_3y_<year>_ctl.csv` plus
`bt17_trades_ytd2026.csv`). Per-year runs: `bt17 --cached-year <Y> --start …
--end … --warrior-strict --multi-entry [--checkpoint-stop] --tag 3y_<Y>_{ctl,cp}`.
BT52 over the same years: `bt52_checkpoint_stop_3y_report.html`.
