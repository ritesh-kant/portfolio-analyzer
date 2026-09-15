# warrior_strict: the transcript's entire entry checklist, enforced

**Registered:** 2026-09-15, before this arm's first live paper session.
**Status:** registered; forward paper only.
**Start:** first NSE session on or after 2026-09-16.
**Supersedes:** [attention-1m-merged-forward](2026-09-12-attention-1m-merged-forward.md),
retired at **n=12** closed trades of its registered 30. That arm did not reach
its review gate, so its numbers (net −₹2,412, 2W/10L) are descriptive only and
may not be reported as a result.

## Why this exists

A 2026-09-15 audit compared the Warrior Trading transcript the strategy is
derived from against the code that is actually deployed, and against two live
sessions in Mongo. Eleven rules the guide states were found to be either absent
or recorded-but-not-enforced:

| Guide rule | Before | Now |
|---|---|---|
| Micro pullback before entry | any strong candle entered | required; entry moves to the pullback high |
| Light volume on the pullback | bull-flag only | required on the micro pullback |
| MACD positive and open (1-min) | recorded | required |
| First or second pullback only | recorded | required |
| Peak hours, avoid midday | 14:30 cutoff | 11:00 cutoff |
| 2:1 target | trend exits only, no target | target AND trend exits |
| 3 strikes | absent | `discipline.py` |
| 50% give-back | absent | `discipline.py` |
| Starter size | absent | `discipline.py` |
| Size down after a loss | absent | `discipline.py` |
| 200 EMA on the chart | absent | recorded + drawn (never gated) |

The operator asked for all of them ("match all these"). This file records that
decision and its costs before data accumulates.

## Disclosure

**1. Attribution is forfeited, again and harder.** The superseded arm bundled
three features. This one moves eleven at once on top of those three. A result
here measures the bundle. No component may ever be credited or blamed from this
sample, and the bundle cannot be decomposed after the fact.

**2. Three components are already-killed ideas.** This repo has measured, on its
own data, that:

* the pullback ordinal does not separate good trades from bad (BT17: 1-3 vs 4+
  spread +0.009 pp against a locked +0.50, anti-strategy p=0.469, and ordinal 5
  was the best bucket);
* the quality/selectivity bundle is worse than deleting trades at random
  (p=0.979);
* the 1-minute agreement gate cuts 43% of trades and moves gross by −0.0003 pp,
  anti-strategy p=0.526 (BT30).

The prior for "add more entry filters" in this system is therefore poor, and
the honest expectation is that `warrior_strict` reduces trade count far more
than it improves per-trade expectancy. It is being run because the operator
asked for the guide to be followed, not because the evidence points this way.

**3. No historical backtest will be manufactured to support it.** 2022–23, 2024
and 2026 are spent for entry-side mining and 2025 was read at pool level. A
`bt17 --warrior-strict` run is available and is *descriptive* — useful to see
how many trades survive the checklist, never as evidence the checklist works.

## What this arm claims

That the bundle, at real MIS costs (measured 0.21% round trip), produces a
positive net return per trade. Nothing weaker counts: the system's measured
gross edge is ≈ +0.02%/trade, an order of magnitude below costs, and every
filter tried so far has moved it by hundredths of a percentage point.

## Descriptive replay, 2026-09-15 (declared descriptive BEFORE it was run)

`bt17 --warrior-strict` vs `bt17 --attention`, 2024, the same 198 cached
symbols, identical everything but the checklist. 2024 was already spent for
entry-side mining, which is exactly why it was chosen: this run cannot
contaminate an unspent window, and it was registered above as descriptive, so
it is **not** a verdict on the forward hypothesis. It is, however, the strongest
available prior, and it is not encouraging.

| | control (`attention_1m_merged`) | `warrior_strict` |
|---|---|---|
| trades | 1,288 | 126 |
| gross %/trade | −0.0957 | −0.0839 |
| net %/trade at real 0.21% costs | −0.3016 | −0.2898 |
| win rate (real costs) | 20.3% | 22.2% |
| mean fill gap vs trigger | +0.179% | **+0.411%** |
| trades reaching the 2R target | 0.00% | 7.14% |

**The checklist cut 90.2% of trades and moved gross by +0.0118 pp** — 5.7% of a
single round trip.

**Anti-strategy test: p = 0.394.** Twenty thousand random 126-trade subsets of
the control pool have mean gross −0.0960% (sd 0.0508); the checklist's 126
trades come in at −0.0839%, inside the 5–95 band [−0.176, −0.010]. **Selecting
trades with the entire Warrior checklist is indistinguishable from deleting 90%
of them at random.** This is the fourth selectivity result of this shape in this
repo, after quality-selectivity (p=0.979), the 1-minute agreement gate
(p=0.526) and the pullback ordinal (p=0.469). The new information is that the
*source* being an expert's stated rule set, rather than a mined one, does not
change the outcome.

**The checklist also made execution worse.** Moving the buy-stop down to the
pullback high — the guide's own entry — more than doubled the fill gap, from
+0.179% to +0.411%. That −0.232 pp swamps the +0.012 pp of gross it bought.

Two findings worth keeping:

* **The 2:1 target is reachable and the control never sees it.** 7.14% of strict
  trades exited at target for +1.435% gross apiece; the control, which has no
  target, hits one 0.00% of the time. Keeping the target alongside the trend
  exits is the one component that demonstrably changes outcomes.
* **The loss is at the door, not in the selection.** Filling at the trigger
  instead of +0.411% above it would put the arm at +0.327% gross / **+0.121%
  net at real costs** — the first net-positive configuration measured in this
  system. That number is a CEILING and is not attainable as built: the engine
  decides when the breakout candle closes, by which time price is already
  through the pullback high, so a resting order there fills at the market. The
  guide buys intra-bar. Capturing that would mean arming the stop order on the
  **pause** bar, before the breakout prints — implementable, a different
  strategy, and it needs its own hypothesis file rather than a change here.

## Review gate

**30 closed trades**, or 2026-12-31, whichever comes first. Until then no
interim result may be reported as a finding, and no threshold in
`EngineConfig`'s guide block may be changed — changing one after seeing results
is the p-hacking pattern this directory exists to prevent.

At the gate, report:

1. mean gross %/trade and mean net %/trade at real costs;
2. trade count against the superseded arm's rate (12 trades in 2 sessions), so
   the frequency cost of the checklist is stated, not buried;
3. the rejection histogram by reason — `attention_no_micro_pullback`,
   `attention_heavy_pullback_volume`, `attention_macd_not_positive`,
   `attention_macd_not_open`, `pullback_not_allowed`, `pullback_no_anchor` —
   which is the only per-rule information this bundled design can produce;
4. how many trades the daily guardrails prevented (`halted:*` rejections) and
   the size ladder's distribution.

**Kill condition.** Net per trade ≤ 0 at n=30 kills the arm. There is no
variant #2 of the checklist: the guide has one version, it has now been
implemented, and re-tuning it against forward results would be fitting.

## The one new number, stated plainly

`PEAK_HOURS_END = 11:00 IST` is a judgement call, not a measurement, and it is
the only threshold here not quoted from the transcript or reused from frozen
code. The guide trades 07:00–10:00 EST, a window that ends 30 minutes after the
09:30 US open and is mostly pre-market. NSE has no continuous pre-market, so
the guide's window has no literal translation. 11:00 is the first 105 minutes
of the NSE session — its morning volume peak. It was chosen from session-volume
shape, before any P&L was computed on the filtered set, and it is configurable
(`MT_PEAK_HOURS_END`) so that fact stays visible. It is frozen for the duration
of this hypothesis.

## Four defects fixed on the way in

Both were found while implementing this and are not part of the hypothesis;
they are recorded because they change what earlier numbers meant.

1. **The live scanner passed no warm-up bars at all.** Every exit indicator ran
   cold live while bt17 replayed them warm, so the forward log and the backtest
   were not measuring the same exits. More seriously, the 5-minute trend context
   needed 20 bars of *today*, which put the earliest possible promotion at 10:54
   — confirmed in Mongo, where the first attention event on both 2026-09-10 and
   2026-09-11 was 10:44 and nothing at all happened before it. **Every live
   trade this system has ever taken was a midday trade by the guide's
   definition.** The peak-hours rule is unsatisfiable without this fix, which is
   why `warm_context` is part of the arm rather than a separate change.
2. **`bt17`'s inner daily pre-filter used the 4% legacy floor** even under
   `--attention`, whose own floor is 1.5%, so attention replays silently
   discarded most of the days they were meant to cover. `eligible_span` already
   used the right floor; `simulate_symbol` did not. Any `--attention` run before
   2026-09-15 under-counted.
3. **The resistance-headroom test counted a level the breakout had already
   cleared.** `_resistance_aware_attention_confirmation` searched for the next
   ceiling starting at the buy-stop. That was harmless while the buy-stop was
   the confirmation bar's own high, which is always at or above its close. The
   moment `require_micro_pullback` moved the buy-stop DOWN to the pullback high
   — the guide's actual entry — the search started below the close and returned
   the very level the confirmation candle had just broken, so the rule refused
   its own breakout. Found by the first `--warrior-strict` replay: **0 trades
   against the deployed arm's 58 on the same 15 symbols in 2024**, every refusal
   `attention_wait_next_resistance_break`. The search now starts at
   `max(buy-stop, confirmation close)`; for every arm registered before today
   that is exactly the buy-stop, so nothing else moves.
4. **The live scanner could be taken down by its own cache.** A zero-byte
   parquet on the shared EFS volume ended the 2026-09-14 session inside
   `prepare()`, before the feed opened. Cache reads now treat any unreadable
   file as a miss and refetch, and writes are atomic (temp + rename), so a
   concurrent writer can never expose a partial file again. `market_calendar`
   also gained the Ganesh Chaturthi holiday it was missing — and, because that
   list is hand-maintained and still incomplete, the scanner now exits on its
   own if no symbol in the universe has printed a bar by 09:45.

## Code

* `src/momentum_trader/engine.py` — the checklist switches, all default OFF;
  `GuideGates`, `_pullback_ordinal_gate`, `_macd_open_state`, `_trend_ema`,
  `EngineConfig.entry_deadline`.
* `src/momentum_trader/setups.py` — `micro_pullback(max_pause_bars,
  require_light_volume)`; the defaults reproduce the frozen 2026-09-05 rule.
* `src/momentum_trader/discipline.py` — the four account-level rules.
* `src/momentum_trader/scanner.py` — `warrior_strict`, warm-up wiring, the
  guardrail hooks.
* `research/backtests/bt17_momentum_pool.py` — `--warrior-strict` (descriptive).
* Tests: `tests/momentum_trader/test_warrior_checklist.py`,
  `test_warrior_strict_arm.py`, `test_discipline.py`.

The guardrails are scanner-only: a pool backtest walks one symbol at a time and
has no coherent day-level P&L to apply "3 consecutive losses" to.
