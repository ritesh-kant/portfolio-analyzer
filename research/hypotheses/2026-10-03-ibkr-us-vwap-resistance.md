# BT56 — US watchlist replay: VWAP buffer and fill resistance

Registered before the first paired US replay on 2026-10-03. The only recorded US watchlist available is 2026-09-24 through 2026-10-02 (seven sessions); the 2025 hold-out remains sealed. This is a diagnostic on a spent, small window and cannot validate either rule for deployment.

**Result (2026-10-03): INCONCLUSIVE.** All four arms produced zero trades on 93 covered symbol-days (of 104 selected). Net P&L was $0 in each arm; mean, median, win rate and drop-top-five comparisons are undefined. Neither rule can be called helpful or harmful from this window. Six selected symbols did not resolve as US stock contracts in IBKR, and one `CHWM` symbol-day lacked session bars. See [BT56 findings](../backtests/README-bt56-ibkr-us-paired.md).

## Locked comparison

Use recorded first-passed watchlist times, regular-hours IBKR one-minute bars, the deployed US long strategy and US cost model. Replay four arms on identical symbol-days: current rules (control), buy-time 1R resistance veto only, 0.20% VWAP attention-retention buffer only, and both. The VWAP change retains existing attention while a completed five-minute close is no more than 0.20% below VWAP; entries still require a completed five-minute close above VWAP. The buy-time veto is applied at the observed fill price. No tuning after seeing these results.

## Decision rule

Reject an arm as an improvement if its net USD per trade at the modeled US costs is no higher than control, or if its drop-top-five net USD total is no higher. Report trades, win rate, mean and median net USD/trade, total net USD, and coverage for each arm. With fewer than 30 changed trades, even a positive result is inconclusive. Missing bars, unresolvable contracts, and absent historical screen snapshots must be shown; no filling gaps with Yahoo.

## Limitations fixed in advance

One-minute future close approximates a post-decision quote; no tick tape or exact live fill chronology. The replay does not recreate account-wide daily guardrails or size ladder. The recorded watchlist is available for only seven sessions, not all of 2026. Historical catalyst and float checks are not independently reconstructed. The 20-session volume profile may be incomplete for symbols with sparse data.
