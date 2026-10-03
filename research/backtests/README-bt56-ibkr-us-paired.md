# BT56 — IBKR US watchlist comparison

Hypothesis registration: [2026-10-03-ibkr-us-vwap-resistance.md](../hypotheses/2026-10-03-ibkr-us-vwap-resistance.md).

This replay uses the seven recorded US watchlist sessions from 2026-09-24 through 2026-10-02. It starts considering each symbol only after its recorded `first_passed_at`, with prior regular-hours IBKR one-minute bars for context. Four arms share the deployed US strategy and its dollar cost model: current, buy-time 1R resistance veto, 0.20% VWAP attention-retention buffer, and both. Entries remain gated above completed five-minute VWAP; the buffer changes watchlist retention only.

The bars came from the user's read-only IB Gateway connection. No orders were submitted. The pull completed without authentication or pacing failure. Of 84 selected symbols, 78 resolved to a cached US stock contract; six did not resolve (`AMPX-WT`, `BCTXL`, `BESS-WT`, `KCAC-WT`, `KRSP-WT`, `ZZZTT`). The replay covered **93 of 104** selected symbol-days. `CHWM` lacked 2026-10-01 session bars, and the unresolved names account for the other ten missing symbol-days. Three covered symbol-days had fewer than 15 previous sessions for a volume profile.

| Rule arm | Trades | Net USD | Mean net/trade | Median net/trade | Win rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| Current (before) | 0 | $0 | undefined | undefined | undefined |
| Buy-time resistance only | 0 | $0 | undefined | undefined | undefined |
| 0.20% VWAP retention only | 0 | $0 | undefined | undefined | undefined |
| Both | 0 | $0 | undefined | undefined | undefined |

**Finding:** neither rule produced a trade in this replay, so there is no measurable gain or loss in trade outcomes. This does not validate either change. The current-rule replay's most frequent logged refusals were `attention_red_or_flat` (2,153), `attention_low_1m_volume` (698), and `attention_weak_close` (317). These are per-minute refusals, not independent trade opportunities.

The four zero-row trade CSVs are registered in the dashboard as BT56 runs. Their pages explain that mean, median and win rate are undefined; there are no candle charts because there are no trades. The exact coverage and per-symbol diagnostics are in `bt56_us_20260924_1002_summary.json`.

Limits fixed before replay: seven available sessions, sparse or unresolved contracts, one-minute later-close approximation of live quote fills, no account-level daily guardrails or size ladder, and no independent historical catalyst/float reconstruction. Positive results here cannot validate a production rule.
