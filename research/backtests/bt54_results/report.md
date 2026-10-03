# 2026 comparison: fill resistance and 0.20% VWAP watchlist buffer

## Selected strategy — 3 October 2026

Keep the buy-time resistance check: require at least 1R of room at the actual fill price. Exclude the 0.20% VWAP watchlist buffer. The active engine retains its original completed five-minute VWAP watchlist rule; the buffer existed only in the research overlay below. The resistance change is local and has not been deployed.

## Backtest results

Period requested: 2026-01-01 to 2026-09-25. 235 cached NSE stocks, 10409 eligible symbol-days. Runtime: 9.9 minutes.

Normal paper costs; fixed ₹500 risk and ₹50,000 notional cap. Current warrior_strict setup/exit rules and 14:00 entry deadline.

| Rules | Trades | Net P&L | Win rate | Average net/trade |
|---|---:|---:|---:|---:|
| before | 197 | ₹-15,648.47 | 31.5% | ₹-79.43 |
| resistance_only | 133 | ₹-8,833.71 | 31.6% | ₹-66.42 |
| vwap_only | 200 | ₹-16,122.73 | 31.0% | ₹-80.61 |
| both | 135 | ₹-9,139.91 | 31.1% | ₹-67.70 |

Combined change in net P&L: ₹+6,508.57.
Buffer's incremental effect with resistance check enabled: ₹-306.20.

## Monthly net P&L

| Month | before | resistance_only | vwap_only | both | improvement |
|---|---:|---:|---:|---:|---:|
| 2026-01 | -630.73 | -112.23 | -630.73 | -112.23 | 518.50 |
| 2026-02 | -4,622.50 | -2,614.36 | -4,788.40 | -2,780.26 | 1,842.24 |
| 2026-03 | -2,804.40 | -1,736.34 | -2,804.40 | -1,736.34 | 1,068.06 |
| 2026-04 | -2,255.79 | -1,575.96 | -2,255.79 | -1,575.96 | 679.83 |
| 2026-05 | 1,499.29 | 1,211.99 | 1,499.29 | 1,211.99 | -287.30 |
| 2026-06 | -504.71 | -810.29 | -645.01 | -950.59 | -445.88 |
| 2026-07 | -3,142.79 | -1,123.09 | -3,310.85 | -1,123.09 | 2,019.70 |
| 2026-08 | -1,036.46 | -689.34 | -1,036.46 | -689.34 | 347.12 |
| 2026-09 | -2,150.39 | -1,384.09 | -2,150.39 | -1,384.09 | 766.30 |

## Interpretation limits

- Cached stocks only; per-stock date coverage varies.
- One-minute future close approximates post-decision quotes; no tick tape.
- Fixed configured risk, no account-wide daily guardrails or size ladder.
- Historical float/circuit/catalyst eligibility not reconstructed.
- VWAP retention buffer is a temporary research overlay, not a source edit.

No production deployment was performed. Before excludes both changes; both includes both. Promotion stays above VWAP; the 0.20% band only keeps existing attention. New signals remain blocked until the completed five-minute close reclaims VWAP.
