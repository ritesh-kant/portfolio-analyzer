# BT49 — US watchlist audit

"The watchlist had N stocks and nothing traded — was anything actually tradeable?"

`bt49_us_watchlist_audit.py` reads the day's `mt_us_watchlist` + `mt_us_candidates`
from Mongo, pulls that session's 1-minute bars from Yahoo, and for every
watchlisted stock:

1. finds every guide-shaped **micro pullback** (engine's own `micro_pullback`,
   no extra gates) 09:30–15:10 ET;
2. judges all 14 entry rules **independently** with the engine's functions
   (the live log short-circuits at the first failure);
3. simulates the trade with **every rule waived** — armed exactly as
   `us_session` arms it, managed by `engine.step`, real US costs. Where the
   fill-time cost gate refused it, it is re-run with that gate lifted too.

`bt49_us_watchlist_report.py` turns the JSON into a BT32-format HTML page
(per-stock verdict table + one chart card per setup).

```bash
MONGODB_URI=... pnpm bt:uswatch          # defaults to --date 2026-09-24
pnpm bt:serve                            # http://localhost:8899/bt49_us_watchlist_2026-09-24_report.html
```

Other dates: `bt49_us_watchlist_audit.py --date YYYY-MM-DD` then
`bt49_us_watchlist_report.py --input research/backtests/bt49_us_watchlist_YYYY-MM-DD.json`.
Yahoo keeps 1-minute bars for ~30 days, so run it within a month of the session.

Descriptive, one session at a time — never a basis on its own for changing a rule.

## 2026-09-24 (first full US session)

18 watchlisted, 3 had no Yahoo data (warrants GLNDW, BESS-WT, NWCLW).
104 setups, **0 passed every rule; the closest failed 2** (GRML 12:10: breakout
volume 1.97× vs 2.5×, and a 3rd pullback — it would have stopped out, −$54.74).
Every rule waived: 58 fills, gross −$331.53 **before** costs, costs $1,125.97,
net −$1,457.51, 17/58 winners, median −$42.55/trade.
