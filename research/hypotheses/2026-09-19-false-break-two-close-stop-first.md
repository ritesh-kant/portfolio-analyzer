# Two-close false break and stop-before-false-break — measured null

Written 2026-09-19, **after** the change was already implemented. This is an
honest post-hoc measurement of a change that shipped unmeasured, not a
pre-registered test. It is recorded so the next person does not re-run it.

## What changed (2026-09-18)

Two separate edits were made together:

1. **Two closes, not one.** `setups.false_break` previously fired on a single
   close back below a broken level. It now requires two consecutive closes.
   A single close below a one-minute trigger is ordinary retest noise.
2. **Stop and target are checked first.** The false break used to be evaluated
   before the executable orders. A bar can cross the hard stop before it
   closes, so judging the close first replaced a real stop fill with a worse
   false-break price at the close. This was a simulator defect: a resting stop
   fills intrabar whatever the bar later closes at.

Edit 2 is a correctness fix and is not in question. Edit 1 is a rule change
with a P&L consequence, and it shipped with no measurement.

## Attribution flags

Originally both edits sat behind one `legacy_false_break` switch, so a moved
result could not be attributed to either. They are now separate:

| Config field | bt17 flag | Restores |
|---|---|---|
| `legacy_single_close_false_break` | `--legacy-single-close-false-break` | one-close rule |
| `legacy_false_break_before_stop` | `--legacy-false-break-before-stop` | pre-stop ordering |
| both | `--legacy-false-break` | the whole pre-2026-09-18 exit |

## Measurement

```
bt17_momentum_pool.py --attention --cached-year 2026 \
    --start 2026-08-24 --end 2026-09-04 --jobs 4 [--legacy-false-break]
```

145 symbols, 40 entries. The entry side is untouched by this change, so both
arms opened the **same 40 trades**; only exits differ.

| Arm | `false_break` exits | Gross INR | Net INR (0.80% stress) |
|---|---:|---:|---:|
| Legacy: one close, judged before the stop | 12 of 40 | +2,084.72 | −16,524.43 |
| Current: two closes, judged after the stop | 3 of 40 | +2,056.89 | −16,552.14 |

**Net delta −₹27.70 across 40 trades.** Eleven trades changed exit; the
remaining 29 were identical.

## What actually happened to the nine trades

Nine of the twelve legacy false-break exits did not survive as false breaks.
They became a `trail_stop`, `stop` or `ema9_break` in the same minute or the
next, at nearly the same price:

| Symbol | Legacy exit | Current exit | Δ net INR |
|---|---|---|---:|
| SHYAMMETL | 11:44 false_break @1053.70 | 11:44 trail_stop @1055.64 | +90.86 |
| FIRSTCRY | 12:29 false_break @178.70 | 12:29 trail_stop @179.00 | +53.72 |
| EIDPARRY | 11:48 false_break @857.00 | 11:48 trail_stop @858.34 | +48.02 |
| BIKAJI | 12:33 false_break @614.60 | 12:33 trail_stop @614.63 | +2.80 |
| INDGN | 12:50 false_break @582.45 | 12:50 ema9_break @582.45 | 0.00 |
| GICRE | 13:25 false_break @355.60 | 13:25 stop @355.35 | −34.82 |
| GODREJIND | 13:05 false_break @1192.70 | 13:06 stop @1191.30 | −36.19 |
| SOBHA | 11:33 false_break @1259.60 | 12:06 trail_stop @1258.10 | −58.21 |
| JUBLPHARMA | 11:34 false_break @892.85 | 11:35 trail_stop @890.96 | −103.53 |

The two remaining false breaks (KAJARIACER, MEDANTA) simply fired one bar
later at almost the same price.

## Conclusion

**The false-break exit was largely redundant with the trail.** On this window
it did not save money; it just relabelled an exit that the trail or hard stop
was about to take anyway, occasionally a few paise better and occasionally a
few paise worse. Removing three quarters of those exits moved the result by
−₹28 on 40 trades, which is noise.

Verdict: **keep both edits**, on the correctness argument alone — a resting
stop does fill intrabar, and one close below a level is not a pattern failure.
Do not expect a P&L gain from this, and do not count it as a lever. The
measurement is a null.

⚠ Limits: one 10-day window, 40 trades, `one_trade_per_day` (bt17 default)
while live runs multi-entry. The window is already spent. This is descriptive
of a change that was going to ship regardless, not a test that could have
killed it.

Raw output: `bt17_trades_fb_two_close.csv` / `bt17_trades_fb_legacy.csv`.

---

## 2024 full-year confirmation (BT42, run 2026-09-19)

The 40-trade 2026 window was too small to resolve anything. Repeated on the
full 2024 year, attention arm, 210 cached symbols, as a 2x2 of the two edits
so each half is attributed. Runner: `research/backtests/bt42_false_break_2024.py`.

| Arm | Trades | Gross win% | Gross %/tr | Net INR/tr |
|---|---:|---:|---:|---:|
| A current: two-close + stop-first | 1,247 | **32.24** | −0.0958 | −506.8 |
| B legacy: one-close + pre-stop | 1,299 | 30.10 | −0.0959 | −508.0 |
| C one-close only (stop-first kept) | 1,281 | 29.98 | −0.0916 | −505.7 |
| D pre-stop only (two-close kept) | 1,257 | **32.38** | −0.0966 | −507.2 |

### Profit: no change

Paired on the 1,247 identical entries, mean net delta **+₹3.45/trade**,
t = +1.25, **p = 0.211**, 95% CI [−₹2.0, +₹8.9]. Not distinguishable from
zero, on a −₹507/trade base. Every arm sits within ₹2.30/trade of every other.
At real 0.21% costs: current −0.3058%/trade vs legacy −0.3059%/trade.

**The change is P&L-neutral.** The 2026 null reproduces at 31× the sample.

### Accuracy: a real +2.3 pp, and it comes from the two-close rule

Gross win rate (cost-independent) splits cleanly by which false-break rule is
used, not by ordering:

- two-close arms (A, D): 32.24% and 32.38% → mean **32.31%**
- one-close arms (B, C): 30.10% and 29.98% → mean **30.04%**

The two-close rule is worth **+2.3 pp of win rate**, reproduced under both
orderings. The stop-first ordering moves win rate by ~0.1 pp, i.e. nothing —
it is a fill-realism fix, exactly as claimed, not an edge.

But average gross per trade is flat across all four arms. **The rule wins more
often without earning more**: the extra winners it rescues are small, and the
losers it keeps holding are bigger. Higher accuracy, same expectancy.

### False-break firing rate: halved

`false_break` exits fell from **609 (46.9%) to 298 (23.9%)** of trades. The
legacy rule was firing on nearly half of all trades — it was the single most
common exit, which is itself evidence it was not detecting anything selective.

### Was the legacy exit premature? Right more often, wrong by more

Of 591 legacy `false_break` exits, comparing the same trade under the new rule:

| | Count | Share | Total INR |
|---|---:|---:|---:|
| Holding longer earned MORE (exit was premature) | 209 | 35.4% | +23,032 |
| Holding longer earned LESS (exit was right) | 339 | 57.4% | −18,728 |
| Unchanged | 43 | 7.3% | 0 |

The old detector was **correct more often than not** (57% vs 35%), but its
mistakes were larger than its saves, so relaxing it nets +₹4,304 — which the
paired test says is noise. Where the displaced exits went: 134 became a plain
`stop` (−4,472) and 298 still became a `false_break` one bar later (−6,717),
while the trend exits (`ema9_break` +6,086, `volume_climax` +3,400,
`macd_fade` +2,108) recovered more.

### ⚠ Not a pure exit-only test

52 entries exist in the legacy arm that do not exist in the current arm. A
`false_break` exit arms the reclaim re-entry path, so firing it more often
manufactures extra entries. The comparison above is paired on the 1,247 shared
entries for that reason. This channel is also why the legacy arm has more
trades despite identical entry logic.

## Revised conclusion

Keep both edits, unchanged from the original verdict, but for a sharper reason:

- **stop-first** is a correctness fix worth ~0 in P&L and ~0 in accuracy. Keep
  it because a resting stop does fill intrabar; it was a simulator defect.
- **two-close** buys **+2.3 pp of gross win rate for zero expectancy**, and
  halves a detector that was firing on 47% of trades. Keep it because the
  cleaner exit labelling makes the trade log interpretable, not because it
  makes money.

Neither is a lever. Do not re-test this on 2024; the window is spent and the
answer is a measured null with a small accuracy side-effect.

---

## Correctness audit and two fixes (2026-09-19, same day)

An implementation audit of the two-close rule found two real defects and one
semantic gap. Both defects are now fixed; the gap is registered separately in
`2026-09-19-false-break-deterioration.md` and is NOT implemented.

### Defect 1 — only the last bar was checked against the fill

`_false_break_fired` guarded with `tf_bars.index[-1] >= entry_floor`. That is
sufficient for the one-close rule, which uses a single close, but the two-close
rule reads two. On the **five-minute** path `entry_floor` is floored to the
bucket (`entry_time.floor("5min")`), so both confirming closes could pre-date
the fill entirely and still exit the trade. Now every close used as evidence
must come from a bar at or after `entry_floor` (`index[-closes]`); the breakout
bar remains context and may legitimately pre-date entry, since the level itself
is derived from before the entry.

### Defect 2 — the breakout bar was pinned to exactly -3

`false_break` read the breakout from `bars.iloc[-3]`. Once price had been under
the level for a few bars, no bar in the fixed window had a high above it and the
detector went permanently blind — on a steady decline it fired at bars 3 and 4
and went silent at bar 5 with price *lower* than when it fired. The breakout is
now searched across the `FLAG_MAX_BARS` window before the confirming closes.
`FLAG_MAX_BARS` is an already-frozen constant, reused deliberately so that no
new tunable enters the rule. `false_break` also takes an explicit `closes`
argument instead of three hardcoded offsets.

### Measured impact: ~zero, and that is the expected result

Re-ran 2024 full year and 2026-09-18 with both fixes.

| Arm | Trades | Gross %/tr | Gross win% | `false_break` exits |
|---|---:|---:|---:|---:|
| legacy one-close | 1,299 | −0.0959 | 30.10 | 609 (46.9%) |
| two-close (pre-fix) | 1,247 | −0.0958 | 32.24 | 298 (23.9%) |
| two-close + both fixes | 1,248 | −0.0959 | 32.21 | 299 (24.0%) |

Paired on 1,247 shared entries: **exactly 1 trade changed exit** in a full
year (a `stop` became a `false_break`, +₹162). Gross delta +0.0003 pp,
p = 0.318. On 2026-09-18: **zero** trades changed, net identical at −₹13,722.

**Both bugs were latent, not expensive.** Defect 1 is unreachable from the
deployed arm because every deployed setup is a one-minute setup, where
`entry_floor` is the exact entry minute and the earliest possible fire already
has its first confirming close on the entry bar. Defect 2 almost never bites
because the rule fires at the *first* opportunity, so the breakout bar is still
inside the window; blindness only matters when a first fire is suppressed,
which happened once in 1,247 trades.

Keep both fixes — they cost nothing and close two holes that would bite the
moment a five-minute setup is re-enabled or a fire is suppressed — but record
plainly that **neither is a P&L improvement**. The BT42 verdict is unchanged:
P&L null, +2.3 pp accuracy from requiring two closes.

---

## Rising Three / contained-pullback audit (BT45, run 2026-09-21)

The concern is mechanically valid in isolation: a Rising Three has three red
pullback candles, while the false-break rule fires after two closes below the
trade's defended breakout level. A completed Rising Three cannot veto that
decision in real time, because the formation exists only after its later final
green candle has closed. The regression
`test_rising_three_pullback_can_fire_false_break_before_formation_confirms`
captures that causal sequence and separately proves that a pullback which
remains above the defended level does not fire.

BT45 (`research/backtests/bt45_two_close_continuation_audit.py`) scanned the
actual 299 `false_break` exits in the current, post-fix 2024 BT42 output. It
then inspected the preserved one-minute cache retrospectively for a five-minute
Rising Three that:

1. began after the trade opened;
2. contained the false-break decision in its three-red pullback; and
3. completed after that decision with its final green candle.

| Population | Count |
|---|---:|
| Current-rule `false_break` exits | 299 |
| Cache-readable exits | 297 |
| Missing cache (unclassified, not counted as negative) | 2 |
| Retrospective Rising Three overlaps | **0** |

Result: this exact Rising Three failure mode did not occur in the preserved
2024 false-break sample. The result is **not** evidence that no small pullback
can be exited early, and it does not justify a pattern exception: such an
exception would require future information at the decision point. The
two-close rule remains unchanged. The per-exit audit artifact is
`research/backtests/bt45_two_close_continuation.csv`; its output is
diagnostic-only and must not be used to tune this already-spent window.
