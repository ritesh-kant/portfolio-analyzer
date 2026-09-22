# Two-close false-break replay: 2026 H1

Run 2026-09-21 at the operator's request. This is a descriptive, six-month
historical replay of the existing exit rule, not a pre-registered experiment
and not a new strategy arm. It cannot bless a parameter or authorize a live
change. Its purpose is to check whether the two-close rule is plainly harmful
on a larger, recent sample and to quantify the small-pullback concern.

## Fixed comparison

Both arms replay the existing `--attention` engine configuration on the same
cached 1-minute candles from **2026-01-01 through 2026-06-30**:

| Arm | False-break condition | Stop/target ordering |
|---|---|---|
| Current | Two consecutive closes below the defended level | Executable orders first |
| Control | One close below the defended level | Executable orders first |

The control sets only `legacy_single_close_false_break`; it deliberately does
**not** restore the old false-break-before-stop simulation defect. Entries,
level derivation, reclaim path, fills, sizing, costs, trend exits and every
other engine option are held unchanged.

## Data, timing, and scope

- 219 symbols had a local 2026 one-minute parquet cache; 150 spanned the
  complete January–June calendar range. The replay evaluates every cached
  eligible symbol-session in range, so symbols with partial cache coverage are
  retained when their session is usable.
- The current arm produced 676 trades across 198 symbols and 116 entry dates.
  The one-close control produced 684 trades over the same 198 symbols and 116
  dates.
- The full 2026 window was previously examined by entry-side research. Treat
  this as a diagnostic exit replay on already-exposed data, not a fresh
  holdout.
- A false-break can arm a reclaim entry. The two arms can therefore have
  different entry sets. Primary P&L attribution is restricted to the 675
  identical `(date, symbol, setup, entry_time, entry)` rows; arm-wide totals
  are descriptive only.
- This is a per-symbol replay, not a portfolio backtest: shared capital,
  scanner-wide risk guards and tick chronology are not modeled.

## Results

### Whole-arm description

| Arm | Trades | Gross INR | Costs INR | Net INR | Gross % / trade | Net % / trade | Net win rate | False-break exits |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Current two-close | 676 | −11,975.34 | 314,850.82 | −326,826.16 | −0.0406% | −1.0464% | 5.47% | 160 (23.67%) |
| One-close control | 684 | −11,815.95 | 318,387.58 | −330,203.53 | −0.0383% | −1.0441% | 5.12% | 226 (33.04%) |

Two-close removed 66 false-break labels (a 29.2% reduction versus the
one-close arm), but full-arm P&L is not an apples-to-apples outcome because
the reclaim path changed the entry set: one current-only entry and nine
one-close-only entries.

### Same-entry paired result

| Measure | Two-close minus one-close |
|---|---:|
| Identical entries | 675 |
| Entries with a changed exit | 213 |
| Net INR | −106.54 |
| Net INR / shared trade | −0.16 |
| Gross percentage points / shared trade | −0.0023 pp |
| Net win rate | 5.33% vs 5.04% (+0.30 pp) |
| Paired delta t-statistic / two-sided p-value | −0.068 / 0.946 |

There is no detectable P&L difference. Monthly paired net deltas are mixed:
January +₹13.88, February −₹85.62, March −₹568.30, April −₹381.38, May
+₹1,208.99, and June −₹294.12. The apparent large positive full-arm total
for two-close is therefore not attributed to the exit rule; the valid
same-entry comparison is effectively zero.

Of 226 one-close false-break exits on shared entries, holding under the
two-close rule earned more in 73, earned less in 126, and was unchanged in 27;
the paired net effect was −₹235.69. The later exits were mainly another
`false_break` (159), then `stop` (22), `ema9_break` (21), `trail_stop` (15),
`volume_climax` (7), and `macd_fade` (2). This is consistent with the
two-close rule usually delaying or relabelling an exit rather than discovering
a distinct continuation signal.

### Rising Three and small-pullback diagnostic

BT45 retrospectively scanned all 160 current-rule `false_break` exits for a
five-minute Rising Three that subsequently completed, with the exit located in
its three-red pullback:

| Population | Count |
|---|---:|
| Current two-close false-break exits | 160 |
| Cache-readable exits | 157 |
| Missing cache (unclassified) | 3 |
| Completed Rising Three overlaps | **0** |

This diagnostic necessarily uses the final green candle after the exit and is
not available to a live rule. It therefore cannot justify a pattern exception.
It only says that the specific Rising Three scenario did not occur in this
six-month sample.

## Decision

**Keep the current two-close rule unchanged.** It reduces false-break churn
without a measurable six-month paired P&L penalty, and the exact Rising Three
failure mode was absent from the cache-readable exits. This is not proof of an
edge and does not settle broader ordinary-pullback behavior. No production
configuration, threshold, or pattern exemption was changed.

Any future change must be evaluated prospectively with the defended level,
both confirming closes, and post-exit 5/15/30-minute path logged at the time of
the exit. Do not tune another historical exception from this already-exposed
window.

## Reproducibility

```bash
uv run research/backtests/bt17_momentum_pool.py \
  --cached-year 2026 --start 2026-01-01 --end 2026-06-30 \
  --attention --tag fb26h1_two_close --jobs 8

uv run research/backtests/bt17_momentum_pool.py \
  --cached-year 2026 --start 2026-01-01 --end 2026-06-30 \
  --attention --legacy-single-close-false-break \
  --tag fb26h1_one_close --jobs 8

uv run research/backtests/bt45_two_close_continuation_audit.py \
  --current research/backtests/bt17_trades_fb26h1_two_close.csv \
  --legacy research/backtests/bt17_trades_fb26h1_one_close.csv \
  --out research/backtests/bt45_two_close_continuation_2026h1.csv
```

The raw CSV outputs are intentionally ignored as backtest artifacts:
`bt17_trades_fb26h1_two_close.csv`,
`bt17_trades_fb26h1_one_close.csv`, and
`bt45_two_close_continuation_2026h1.csv`.
