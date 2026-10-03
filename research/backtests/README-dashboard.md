# Backtest dashboard

Every backtest run in one list; click one to see all of its trades on candle
charts, and group them by **exit reason** or **target hit**.

```bash
pnpm bt:dashboard
```

then open <http://localhost:8898/>. (Port 8898, so it does not collide with
`pnpm bt:serve` on 8899. `--port N` to change it, `--list` to print the run table
in the terminal, `--rescan` to ignore the cached statistics, `--unlabelled` — also
`pnpm bt:check` — to list runs nobody has named.)

The server loads `bt_dashboard.py` once at startup: **after editing it, restart the
server** (an old copy keeps serving the old page). Edits to `bt32_assets/` are read
each time a run is opened, so a refresh is enough for those.

## What you see

**Index (`/`)** — one row per run, **newest run first**. *Ran* is when the run's
CSV was last written (its file modification time, with "today / 5 days ago");
*data window* is the market dates it covers — they are different things.
Then trades, gross and net per trade at real costs, win %, **target-hit %**,
net ₹, and a coloured **exit-mix bar** (hover for the counts).

Search, filter by BT, minimum trade count, flat list or grouped by BT (sections
ordered by their newest run). **Every column except "backtest" sorts**: ⇅ marks a
sortable heading, ▲/▼ the active one; click again to reverse. "Exit mix" sorts by
the biggest single exit reason's share of the trades. ⌘/ctrl-click opens a run in a
new tab. Below the table: the bespoke HTML reports already in this folder, which
are pre-built and have no Group by.

**A run (`/run/<csv-stem>`)** — the BT32 report: headline chips, one card per
trade with price (candles, EMA9/20/200, VWAP, support/resistance, BUY / SELL /
stop / target, formations), MACD and volume panes, the full chart controls, a
filter + sortable trade table in the side panel.

**Group by** (side panel, or `?group=exit_reason` / `?group=target_hit` in the URL):

| | |
| --- | --- |
| Exit reason | stop, target, trail stop, macd fade, ema9 break, volume climax, … |
| Target hit | the exit **was** the target fill (`exit_reason == "target"`) vs not |
| Outcome | winner / loser at real costs |
| Symbol · Setup · Month · Weekday · Entry hour | |

Grouping puts a summary table at the top (trades, share, gross / net per trade,
win %, net ₹ per group, plus an *All* row) and a collapsible section per group.
Click a summary row or a section header to open it; **Expand all / Collapse all**
in the side panel. Filters (symbol, exit, outcome) apply first, so the groups
always describe what is filtered. A group of more than 60 trades shows 20 at a
time with a *Show 20 more* button, so opening a 1,800-trade group is instant;
picking a trade in the side table reveals just that trade.

## Why charts are built on click

A trade's chart payload (1-minute bars, as-of-entry levels, candlestick
formations) costs ~0.9 s and ~33 KB to build. Prebuilding the 5,797-trade
BT21 run would be ~85 minutes and a ~190 MB page. So the page ships only the
CSV-derived row per trade (a few hundred bytes; the 5,797-trade page is 3 MB
and opens in under a second) and each card fetches its chart from
`/api/run/<run>/trade/<i>` when it nears the viewport. Results are cached in
`research/backtests/.dashboard_cache/` (git-ignored) keyed by the CSV's mtime, so
a second look is instant and a re-run CSV rebuilds its own charts.

Charts need the 1-minute bar cache (`.cache_upstox/1m/`). A trade whose
symbol/year is not cached shows "No chart for this trade…" and still counts in
every number; the report header says what share of a run is chartable.

## Which CSVs show up, and how runs are named

Any `*.csv` in this folder with the bt17 trade columns (`date, symbol,
entry_time, exit_time, entry, stop, exit, exit_reason, qty, gross_pct, net_pct,
gross_inr, net_inr`). A new `pnpm bt --tag foo` run appears on refresh.
**BT7–BT15 (news/daily-bar tests) are not listed** — their trade files have no
intraday entry/exit times, so there is nothing to draw.

The BT number and title come from `dashboard_runs.json` first (it wins), then from
the ordered `RULES` list at the top of `bt_dashboard.py` (evidence per line: the
analysis script that reads that tag, or the `package.json` command that writes it).
A tag no rule matches is listed as `BT17 · <tag>` — bt17 is the engine every tagged
run replays. `pnpm bt:check` lists runs that are still unnamed. If a label is wrong,
fix the registry entry or rule; labels are not cached, so no rescan is needed.

## What it changed in the shared report code

`bt32_assets/report.js|css` gained Group by, so the reports built by
`bt32_strategy_report.py`, `bt35_to_bt32.py`, `bt47_structural_trade_report.py`,
`bt50_changed_trades_report.py` and `bt52_checkpoint_stop_report.py` get it **the next
time they are regenerated**; already-built `.html` files are untouched. `bt29`,
`bt27`, `bt45`, `bt48`, `bt49` ship their own forked assets and are unchanged.

`bt32_strategy_report.build_day` was split into `summary_fields` (CSV-only) and
`build_detail` (bars/levels/formations); `build_day` still returns the identical
dict (verified trade-for-trade against the previous version).

## Honesty

Descriptive views of runs that were already measured — none is a new test.
Levels are as of the entry bar only; costs are shown twice (bt17's +40 bps/side
stress and the itemised real-cost model). A group's gross/net per trade is not
evidence of an edge: "target hit" trades are winners by construction (the exit
*is* the take-profit), so their average says nothing about whether the target
was worth having — compare the groups' totals, not the labels.
