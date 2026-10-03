# GitHub Copilot instructions

## Backtest reports (full rules: AGENTS.md → "Backtest reports")

When asked for a backtest, report, results or trade review:
- Do NOT hand-write a standalone HTML/notebook/chart. All backtests are viewed in the
  dashboard: `pnpm bt:dashboard` → http://localhost:8898/ (candle chart per trade,
  Group by exit reason / target hit).
- Produce a trade CSV `research/backtests/bt17_trades_<tag>.csv`:
  `pnpm bt --tag <tag> <flags>`. ALWAYS pass --tag (none overwrites bt17_trades.csv); the
  file must not already exist; NEVER put `--` after a pnpm script or a `# comment` on the line.
  It takes ~5-8 min (run in background) and needs UPSTOX_ACCESS_TOKEN in .env — if auth
  fails, stop and tell the user; never create or print a token.
- "Backtest 2026" with no strategy named = the live rule, descriptive only:
  `pnpm bt --cached-year 2026 --start 2026-01-01 --end 2026-09-25 --warrior-strict
  --multi-entry --tag live_2026_<YYYYMMDD>` (auto-labelled "Live rule"). Say you assumed it.
- A change to the strategy is a hypothesis test: next BT number, pre-register in
  research/hypotheses/ + INDEX.md first, control + arm as two tagged runs, then register
  the runs in `research/backtests/dashboard_runs.json` (bt, title, hypothesis path).
- Do NOT use `pnpm bt:ytd2026`, `bt:bt52`, `bt:bt50`, `bt:demo` (fixed tags overwrite baselines).
- Verify: `pnpm bt:check` prints 0 unlabelled and no NOT LISTED lines.
- Give the user `http://localhost:8898/run/<csv-stem>?group=exit_reason` (start
  `pnpm bt:dashboard` in the background if it is not up); quote net at REAL costs with the
  median beside the mean; say whether it is descriptive or a pre-registered test.
- Extend `research/backtests/bt32_assets/` and `bt_dashboard.py`; never fork them.
- Never run a checkpoint restore, `git reset`, `git clean`, `git checkout .` or `git stash`
  without checking `git status` first: other agents share this checkout and uncommitted
  work is lost for good.
