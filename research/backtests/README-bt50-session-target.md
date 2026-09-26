# BT50 — session-resistance target

Hypothesis and result: `research/hypotheses/2026-09-25-session-resistance-target.md`.

The fixed target is capped at the nearest structural resistance, which now also
includes the highs and repeated 5-minute pivot highs of the 10 sessions before
today, and every capped target sits 0.15% under its level. Entries are
untouched, so control and new rule share every entry.

## Re-run

```bash
B=research/backtests/bt17_momentum_pool.py
uv run $B --cached-year 2025 --start 2025-09-22 --end 2025-12-31 --warrior-strict --session-levels 0 --target-buffer-pct 0 --tag bt50_25_ctl
uv run $B --cached-year 2025 --start 2025-09-22 --end 2025-12-31 --warrior-strict --tag bt50_25_new
uv run $B --cached-year 2026 --start 2026-01-01 --end 2026-09-22 --warrior-strict --session-levels 0 --target-buffer-pct 0 --tag bt50_26_ctl
uv run $B --cached-year 2026 --start 2026-01-01 --end 2026-09-22 --warrior-strict --tag bt50_26_new
pnpm bt:bt50      # criteria + chart report
pnpm bt:serve     # then open http://localhost:8899/bt50_changed_trades_report.html
```

`--warrior-strict` now defaults to the new rule (10 sessions, 0.15%), matching
the live `warrior_strict` config. `--session-levels 0 --target-buffer-pct 0`
replays the BT47 control.
