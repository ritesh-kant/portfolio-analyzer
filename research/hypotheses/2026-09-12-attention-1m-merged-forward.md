# Single merged forward arm: attention entries, resting fills, resistance-state exits, one reclaim

**Registered:** 2026-09-12, after reviewing nine BT17 charts and the first 12
closed forward trades, but before this arm's first live paper session.
**Status:** registered; forward paper only.
**Start:** first NSE session on or after 2026-09-14.
**Supersedes:** [attention-1m-forward](2026-09-09-attention-1m-forward.md),
[attention-resistance-state-forward](2026-09-10-attention-resistance-state-forward.md),
[attention-false-break-reclaim-forward](2026-09-10-attention-false-break-reclaim-forward.md).
Those three arms are retired unreviewed at n=6, n=6 and n=0 closed trades; none
reached its review gate and none may be reported as a result.

## Disclosure and claim

The operator asked for one arm rather than three parallel ones, to iterate on a
single configuration. This file exists because that decision bundles features
that were registered separately, and the bundle must be declared before data
accumulates rather than reconstructed afterwards.

**The bundling is a deliberate, disclosed loss of attribution.** This arm turns
on the resistance-breakout entry requirement, the resistance-state exits and the
false-break reclaim together. A result here measures the bundle. It can never
attribute an outcome to any single component, and no component may later be
credited or blamed from this sample.

The claim is only that the bundle, at real MIS costs, produces a positive
net return per trade. There is no historical backtest of this configuration and
none will be manufactured: 2022–23, 2024 and 2026 are spent for entry-side work
and 2025 was read at pool level.

## What the chart review did and did not establish

The 2026-09-12 review of nine BT17 trades (KEI, EMAMILTD, CCL, SUNTV,
WOCKPHARMA, MAZDOCK, BBTC, ACUTAAS, SUMICHEM) is diagnostic. It produced one
measurement worth recording and one rejected idea:

* **Recorded.** In `bt17_trades_vs_b.csv` (n=3,966, next-open fills) the mean
  entry paid **+0.209%** over its trigger, median **+0.177%**, with 39% of
  trades paying more than 0.25% — roughly one full round trip of the measured
  0.21% real MIS cost, lost at the door. This is the same effect
  [entry-fill-latency](2026-09-05-entry-fill-latency.md) measured at +0.235 pp
  on 2024 and is why every arm here uses `fill_mode="future_trigger"`.
* **Rejected.** The hypothesis that the 2R target is systematically further away
  than the stock can travel does **not** survive: among the 3,966 trades the
  target sat 1.21% away on trades that reached it and 1.10% away on trades that
  stopped out. Target distance does not separate winners from losers. It was
  pattern-matched from a sample of five losing charts and is abandoned, not
  carried forward as a rule.

The 12 closed forward trades of 2026-09-09..09-11 are likewise diagnostic and
are **excluded** from this arm's evidence. Their only recorded use is the fill
measurement below.

## Fill quality already observed (diagnostic, excluded from the gate)

Across the 11 `attention_1m_confirmation` fills of 2026-09-09..09-11 the live
resting buy-stop filled a mean **+0.03%** above trigger, 7 of 11 exactly at the
trigger, worst +0.099% — against +0.209% for the replayed next-open fill. The
entry leak looks closed. Two caveats are part of the record: n=11, and a paper
fill is taken at the observed quote, so this measures decision latency, not
execution against a real book. Real orders add queue position and spread.

## Frozen forward paper protocol

Unchanged from the three superseded arms except where they conflicted:

- **Entry:** the two-stage attention path — day change ≥ +1.5%, time-of-day
  RVOL ≥ 1.5×, five-minute trend context, promotion on a named pattern, then a
  separate later one-minute confirmation candle (green body, close in the upper
  40%, ≥ 2.5× its trailing 20-bar volume). Promotion is never entry.
- **Entry requires a structural-resistance breakout state** (from the
  resistance-state arm).
- **Execution:** buy-stop armed at the confirmation candle high for three
  minutes, filled only from a quote observed strictly after the decision at or
  above the trigger; cancelled above the 1% chase cap, on an insane stop, on
  expiry, or at the position cap. No below-trigger or next-open substitution.
- **Exit:** `trend_resistance_state` — trend exits with structural-resistance-
  only rejection requiring a failed break, plus the hard stop and 15:15 close.
- **Re-entry:** at most one volume-backed reclaim after an attention
  `false_break`, then done for the symbol-day.
- **Risk:** paper only; existing per-trade risk/notional and five-position caps;
  one trade per symbol-day apart from that single reclaim; no new entries after
  14:30 IST.
- **Records:** `strategy="attention_1m_merged"` on every document, and a
  dedicated forward log. Not mixed with the superseded arms' rows.

## Locked review gate

Review after at least **30 closed paper trades and 20 trading sessions**.
Report: trade count, actual-cost net return/trade, gross return/trade, win rate,
median, 95% bootstrap interval, worst trade, maximum daily drawdown,
attention-to-entry conversion, every rejection reason, event-time data gaps, and
**fill-vs-trigger mean/median** (`research/backtests/mt_forward_fill_quality.py`).

Killed if mean actual-cost net return per trade is not positive, or if its 95%
interval excludes the pre-cost viability level of **+0.35% gross** on the
downside.

No threshold, pattern-set, fill or exit change is permitted inside this sample.
Any revision gets a new file and a new forward log. Passing is permission for a
larger paper sample, not live-money approval.

## Result

Pending forward accumulation from 2026-09-14.
