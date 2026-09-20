# BT45 — the cost-aware stop, drawn trade by trade

A BT32-format report that shows, on real charts, what the cost-aware stop does
to a trade. It exists because BT43 measured the rule as a **null** and killed
it, and a table of averages does not let you see *why* a rule that clearly
works still earns nothing.

## The rule in one paragraph

When a trade is up by 1R, the engine lifts the stop to the entry price. That is
not breakeven: the round trip costs about 0.21% in brokerage, taxes and
slippage, so a trade stopped there books a small loss. The cost-aware stop
lifts it slightly higher instead, to the first NSE tick where the sale covers
all real charges, plus one tick. On a ₹392 stock that is ₹393.40 rather than
₹392.50.

## Run it

```bash
pnpm bt:coststop
```

Then serve it (the desktop app will not open large files over `file://`):

```bash
pnpm bt:serve
```

and open <http://localhost:8899/bt45_cost_stop_report.html>.

Flags: `--years 2023,2024`, `--saved N`, `--cut N`, `--output`, `--title`.

It needs `bt17_trades_cs{23,24}_{base,cost}.csv`, produced by:

```bash
pnpm bt -- --attention --cached-year 2023 --start 2023-01-01 --end 2023-12-31 --jobs 3 --tag cs23_base
pnpm bt -- --attention --cached-year 2023 --start 2023-01-01 --end 2023-12-31 --jobs 3 --cost-aware-breakeven --tag cs23_cost
```

(and the same pair for 2024). Each pair takes roughly 45 minutes on cached bars.

## What is on the chart that BT32 does not have

| Mark | Meaning |
| --- | --- |
| **Amber dash-dot line** | the cost stop, labelled with how far above entry it sits and why |
| **Hollow grey ▽ + faint dotted line** | where the same trade exited **without** the rule |
| **Comparison strip** above each chart | both arms' exit price, time and reason, and what the rule was worth in rupees on that trade |
| **Δ ₹ column** in the side table | the rule's value per trade at real charges, not the trade's own net |

Everything else is BT32: candles, EMA9/20/200, VWAP, support and resistance
computed **as of the entry bar only**, MACD, volume with RVOL, candlestick
formations with their strength multiple, per-card full screen, and both cost
models.

## How the 10 trades were chosen, and why that matters

The rule changed the exit on **184 of 3,139** trades (121 better, 58 worse).
The report picks 5 from each side, **spread evenly across the range** rather
than taking the extremes, so the cards show the rule at its typical as well as
its best and worst.

⚠️ **This is a curated sample. Its combined P&L is meaningless.** The page says
so in a banner and carries the real population verdict in the headline chips:
**−₹0.09 per trade, p = 0.893**, across all 3,139 trades. Use the charts to
understand the mechanism, never to judge the rule.

## What the charts show

The mechanism works exactly as designed, and cancels itself out.

* **Saves** — e.g. PARADEEP 2023-09-01. Without the rule the trade scratched at
  its entry price of ₹73.05 and booked the fees as a loss. The cost stop sold
  at ₹73.30 instead. Worth **+₹171**.
* **Cuts** — e.g. LODHA 2023-05-08. The cost stop sold at ₹463.30 at 11:17.
  Without it the trade was still open, exited at ₹471.45 at 11:33, and the
  stock carried on to about ₹490. Worth **−₹879**.

Across the population the saves average about +₹74 and the cuts about −₹165.
There are roughly twice as many saves, which is why the two almost exactly
cancel. The rule also lifts the gross win rate by about 3 points while earning
nothing, which is the standing lesson: on a signal with no drift, a rule that
converts small losses into zeros converts as many small winners into zeros too.

## Status

**Descriptive report of an already-measured, already-killed rule — not a new
test.** `cost_aware_breakeven` defaults to `False`, is set nowhere, and is a
backtest-only flag. See
`research/hypotheses/2026-09-19-cost-aware-breakeven-stop.md` for the
pre-registered criteria and the full result.
