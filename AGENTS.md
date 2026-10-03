## Imported Claude Cowork project instructions

## Backtest reports — one workflow, whichever agent you are

This section applies to **every** coding agent (Claude Code, GitHub Copilot, Cline,
Cursor, Codex, Gemini CLI, …). `CLAUDE.md`, `GEMINI.md`,
`.github/copilot-instructions.md`, `.clinerules` and `.cursor/rules/` all point
here; this file is the source of truth.

**When asked for a backtest, a report, "results", or to look at trades: do NOT
hand-write a standalone HTML/notebook/chart script.** Every backtest is viewed in
the **backtest dashboard** (`pnpm bt:dashboard` → <http://localhost:8898/>). It lists
every run, opens any run as one candle chart per trade (EMA9/20/200, VWAP, MACD,
volume, support/resistance, BUY/SELL/stop/target), and can group the trades by exit
reason, target hit, outcome, symbol, setup, month, weekday or entry hour (with
trades, gross/net per trade, win %, **median ₹/trade** and ₹ per group). Full
manual: `research/backtests/README-dashboard.md`.

### Shell gotchas (each one has already burned someone)

- **Never put `--` after a pnpm script**: `pnpm bt --tag x`, not `pnpm bt -- --tag x`.
  pnpm 9 forwards the literal `--` to the script and argparse rejects it.
- **Never put a `# comment` on a command line** you hand to the user or run: pnpm passes
  it to the script as arguments.
- **A bt17 run takes ~5–8 minutes** (235 symbols, 8 workers). Run it in the background and
  poll its log; do not block on it or retry it.
- **`pnpm bt` needs a valid `UPSTOX_ACCESS_TOKEN` in `.env`.** The 1-minute bars come from the
  cache, but it still calls the Upstox daily-candle API once per symbol, and Upstox access
  tokens are short-lived. If it fails with an authentication error, **stop and tell the user
  the token needs refreshing** — do not look for, create or print a token. (`pnpm bt:report`
  and the dashboard read only the cache.)
- **Always pass `--tag`.** bt17 writes `bt17_trades_<tag>.csv`; with no tag it overwrites
  `bt17_trades.csv` (the 2024–25 pool). It never asks before overwriting any file.

### To produce a report

1. **Decide what kind of run it is.**
   - *Descriptive* — re-running the live rule (or any already-measured configuration) over a
     window that has already been measured, just to look at the trades. No BT number and no
     registration. Tag it `live_<window>_<YYYYMMDD>` (e.g. `live_2026_20261004`): the registry
     already labels `bt17_trades_live_*` as "Live rule". **Say it is descriptive** — a spent
     window can kill an idea, never bless one.
   - *Hypothesis test* — any change to the strategy ("with a VWAP stop"). It needs the **next
     BT number** (highest `BTnn` in `research/hypotheses/` and `research/backtests/bt*.py`,
     plus one) and must be **pre-registered before running**: write
     `research/hypotheses/YYYY-MM-DD-slug.md` with falsification criteria locked, and add a
     bullet at the **top** of the list in `research/hypotheses/INDEX.md`. Run a control and an
     arm as two tagged runs (`bt<NN>_<what>_<window>_ctl` / `_new`). Never open the sealed 2025
     hold-out unless the hypothesis file says the hold-out run is the one being done.
2. **Run it so it writes a trade CSV in `research/backtests/`.** For the strategy this is bt17:
   `pnpm bt --tag <tag> <flags>` → `research/backtests/bt17_trades_<tag>.csv` (flags:
   `research/backtests/README-bt32.md`). **Before running, check the file does not exist**
   (`ls research/backtests/bt17_trades_<tag>.csv`): other studies read fixed CSVs and would
   silently change. A different analysis script must write **the same columns**, directly in
   `research/backtests/` (not a sub-folder), named `bt<NN>_trades_<tag>.csv` — required:
   `date, symbol, entry_time, exit_time, entry, stop, exit, exit_reason, qty, gross_pct,
   net_pct, gross_inr, net_inr`; used when present: `trigger, setup, target, target_source,
   day_chg_pct, rvol, structural_support[_kind], structural_resistance[_kind]`. A CSV with a
   column missing is silently not listed — `pnpm bt:check` names it. `exit_reason == "target"`
   is what the dashboard calls *target hit*.
3. **Register it** (hypothesis tests; descriptive `live_*` runs are labelled already) — add an
   entry to `research/backtests/dashboard_runs.json` (key = CSV stem or a `*` glob; `bt`,
   `title`; `hypothesis` = path of the write-up, optional for descriptive runs):
   ```json
   "bt17_trades_bt56_vwap_exit_*": {
     "bt": "BT56", "title": "VWAP exit · {rest}",
     "hypothesis": "research/hypotheses/2026-10-04-vwap-exit.md" }
   ```
   No Python edit is needed. The file wins over the built-in labels in `bt_dashboard.py`.
4. **Verify**: `pnpm bt:check` must print `0 unlabelled run(s)` and no `NOT LISTED` lines
   (it runs `check_dashboard_registry.py` and `bt_dashboard.py --unlabelled`).
5. **Show the user**: make sure the dashboard is up (`curl -s -o /dev/null -w '%{http_code}'
   http://localhost:8898/` → 200; otherwise start `pnpm bt:dashboard` **in the background** — it
   is a foreground server; if the port is taken it is probably already running). A new CSV or
   registry edit needs no restart; a change to `bt_dashboard.py` does. Give the user
   `http://localhost:8898/run/<csv-stem>?group=exit_reason` (or `?group=target_hit`). Quote the
   headline numbers **at real costs** (net per trade, ₹, win %) with the **median** beside the
   mean — never only gross.
6. **Write the findings** in the hypothesis file / a `README-bt<NN>.md`.

### Common requests — what to run when the prompt is short

- **"Run the backtest and make the report for 2026"** (no strategy named) = the **live rule**
  over the cached 2026 window. The cache ends **2026-09-25**; use that as `--end` unless newer
  bars exist (`ls -t research/backtests/.cache_upstox/1m/*_2026.range.json | head -1`):
  ```bash
  pnpm bt --cached-year 2026 --start 2026-01-01 --end 2026-09-25 \
          --warrior-strict --multi-entry --tag live_2026_<YYYYMMDD>
  ```
  `--warrior-strict` defaults to today's live configuration (checkpoint stop on since
  2026-09-29, 14:00 entry cutoff since 2026-09-30, BT55 session-levels veto since 2026-10-03),
  so a fresh run is the CURRENT live rule. Runs made before 2026-10-03 (e.g.
  `bt17_trades_ytd2026.csv`, `live_2026_20261003*`) were without the veto; add
  `--no-session-levels-veto` to reproduce them. Add `--live-fill` only if asked (resting-buy-stop fills: more
  faithful to live, different numbers). Say in the reply that you **assumed the live rule**.
  It is **descriptive only**. If the prompt names a change to test ("with the 5-minute
  exits"), that is a hypothesis test: step 1.
- **Do NOT use `pnpm bt:ytd2026`, `bt:bt52`, `bt:bt50`, `bt:demo`** for a new report. They
  write to fixed tags — overwriting frozen baselines such as `bt17_trades_ytd2026.csv` (the
  control of the 5-minute-exit test) and `bt17_trades_ytd2026_checkpoint.csv` (BT53's control)
  — and build the old standalone HTML.
- Only symbols with a cached 1-minute file for the year are replayed. Do not start a
  full-NIFTY-500 fetch (hours, many API calls) without asking.

### Rules that make the report honest (already built in — do not bypass)

- Levels and indicators are computed **as of the entry bar only**, never the day's final
  state. Costs are shown **twice**: bt17's +40 bps/side stress and the realistic itemised
  MIS model. Do not add a flat-percentage cost.
- Judge on net at real costs, print the **median** and drop-top-N beside any mean, and for
  anything that changes position size compare per rupee deployed, not per trade.
- The backtest does **not** replay the live daily guardrails (3 strikes, size ladder); say so
  when comparing a backtest with live paper P&L.

### Where things live — extend, don't fork

| | |
| --- | --- |
| Server, run discovery, index page | `research/backtests/bt_dashboard.py` |
| Run names / BT numbers | `research/backtests/dashboard_runs.json` (new) + `RULES` in `bt_dashboard.py` (historic) |
| Per-trade chart page and **Group by** | `research/backtests/bt32_assets/report.js` + `report.css` (shared) |
| Per-trade data (summary + bars/levels/formations) | `summary_fields` / `build_detail` in `bt32_strategy_report.py` |
| Convention checker | `research/backtests/check_dashboard_registry.py` (runs in the pre-commit hook) |

A new grouping is one entry in the `GROUPS` table in `report.js`. A new indicator or
overlay goes in `report.js`/`bt32_strategy_report.py` so **every** report gets it. Do not
copy `bt32_assets/` into a new folder (bt27/29/45/48/49 did; they are frozen).

### Limits — say so instead of working around them

- Charts need the 1-minute bar cache (`research/backtests/.cache_upstox/1m/`, git-ignored,
  ~1.4 GB, never delete it). A trade whose symbol/year is not cached shows no chart but
  still counts in every number.
- Long trades on the NSE cache only. Short-side (BT51) and US runs are not listed; the
  US report is the bespoke `bt49_us_watchlist_report.py`.
- News / daily-bar backtests (BT7–BT15) have no intraday entry/exit times → no chart.
  Report those as a README + table. If you must ship a bespoke HTML, put it in
  `research/backtests/`; the dashboard lists every `*.html` there under "Other
  pre-built reports", but it will have no lazy loading and no Group by.
- Per-trade charts are built on first view (~1 s) and cached in `.dashboard_cache/`
  (git-ignored). Do not prebuild thousands of charts into one HTML file.

### Do not wipe other people's uncommitted work

Several agents and the user can share this one checkout. Cline's "checkpoint restore" runs
`git reset` and deletes untracked files, which silently destroys everything uncommitted by
anyone, including other agents' work. Before any restore, reset, `git clean`,
`git checkout .` or `git stash`: run `git status`, and if there is work that is not yours,
stop and ask. Prefer your own `git worktree` for experiments.
