---
slug: one-minute-agreement
strategy: momentum_trader
status: killed
registered_at: '2026-09-12'
finalized_at: '2026-09-13'
decided_at: '2026-09-13'
hypothesis_hash: ''
parent: momentum-catalyst-upstox-v2
---

# Hypothesis: A five-minute setup whose own one-minute chart is not in favour at the moment of the decision earns a lower mean gross return than one whose one-minute chart agrees, because the five-minute bar hides whether the move is being bought or merely printed on a thin book.

> ⚠️ **Origin disclosed.** Proposed by the operator on 2026-09-12 after reading a
> single losing chart (KEI 2022-01-19, `bt17_trades_vs_b.csv`): a `ma9_pullback`
> triggered on a ₹11 one-minute spike carrying only **1.28×** its recent minute
> volume, two minutes after a **17×**-volume red minute (58,354 shares closing
> near its low), with the one-minute EMA9 already **below** the EMA20. It was
> filled at the next minute's open, ₹6.10 (0.50%) above the trigger, and stopped
> four minutes later. This file exists so that origin is on the record.
>
> 🔴 **Multiple-testing disclosure — read before interpreting any result.** This
> is at least the **seventh** entry-side test in this line of work, and **no
> unused historical window remains**: 2022–23 (3 looks), 2024 (4 looks) and 2026
> (entry-location) are all spent; 2025 was read at pool level by BT17 and is no
> longer a clean hold-out. Upstox one-minute history starts in 2022, so there is
> nothing else to hold back. **A pass on any of these windows is therefore
> exploratory, not confirmatory, and may not by itself authorize deployment.**
> The only confirmatory evidence available is forward paper trading.
>
> ⚠️ **Prior on the mechanism is poor.** [quality-selectivity](2026-09-06-quality-selectivity.md)
> (anti p = 0.979) and [max-move-checklist](2026-09-07-max-move-checklist.md)
> (all 6 checks failed) both tried to keep only "good-looking" entries and both
> did **worse than random**. [entry-location](2026-09-06-entry-location.md)
> found one component (near-support) pointing the *opposite* way to intuition.
> The distinguishing feature here is that the rule reads a different timeframe
> rather than re-scoring the same one — that is the part that has not been tested.
>
> ⚠️ **One-sided-sample warning.** The features below were read off a loser. The
> 2026-09-12 review already rejected one rule generated that way ("the 2R target
> is further than the stock can travel", false when winners were included). The
> anti-strategy test in §4 exists precisely to catch this and may not be waived.

## 1. The rule (frozen, zero new tunable numbers)

`engine._one_minute_gate`, behind `EngineConfig.require_1m_agreement` (default
**False**, so every prior arm reproduces byte-for-byte).

Evaluated on the last **closed** one-minute bar at the instant a five-minute
setup is detected — the same instant the candidate is created, so it cannot see
past the decision, and it reads identically in every fill mode (the
[fill-latency](2026-09-05-entry-fill-latency.md) comparison requires both arms
to take the same trades).

The one-minute chart is "in favour" when **all four** hold:

| # | Test | Refusal reason | Provenance |
|---|---|---|---|
| 1 | 1-min EMA9 > EMA20 | `1m_trend_down` | the trend test `_attention_context` already applies on 5m |
| 2 | trigger minute is green | `1m_red_or_flat` | `_attention_confirmation` |
| 3 | closes in the top 40% of its range | `1m_weak_close` | `_attention_confirmation` (`ONE_MIN_CLOSE_POSITION_MIN = 0.60`) |
| 4 | volume ≥ 2.5× its own 20-bar average | `1m_low_volume` | `ATTENTION_CONFIRM_VOL_RATIO`, unchanged |

Every threshold is **reused** from a rule frozen before this file existed. No
number was chosen by looking at outcomes. Refusals are recorded in the
candidates CSV's `quality_reason`, so the refused set is auditable.

Setups in `ONE_MINUTE_SETUPS` (`micro_pullback`, the two attention setups) pass
untouched: their own confirmation **is** this test, and re-applying it would
make the live attention arm a different strategy rather than a gated one.

Verified on the originating chart: at the 11:34 decision the gate returns
`(False, '1m_trend_down')`; at the genuine 11:07 thrust it returns `(True, 'ok')`.
That is a sanity check of the wiring, **not evidence**.

## 2. Dev runs

⚠️ **Default history:** the operator made this rule ON by default on 2026-09-12
(part of the strategy, not an overlay). The KILL in §5 returned it to opt-in, so
no prior backtest's reproducibility is affected in the end.

The measurement below is **two runs of the same strategy**, not two deployed
strategies — nothing new is scheduled, no second ECS task exists. The `off` run
exists only to produce the number this file's criteria are written against.
`--first-candidate-only` is mandatory in both: without it, `one_trade_per_day`
lets a later setup replace a refused one, the gated set stops being an exact
subset, and the §3 anti-test becomes invalid (this defect cost 30/250 trades in
quality-selectivity).

The two runs were executed on 2026-09-13 while the default was ON, so their
flags read `--tag 1ma_on` (default) and `--no-1m-agreement`. After the KILL the
default returned to False and the flag returned to opt-in, so the commands that
reproduce this result today are:

```bash
uv run research/backtests/bt17_momentum_pool.py --start 2022-01-01 --end 2023-12-31 \
  --first-candidate-only --require-1m-agreement --tag 1ma_on
uv run research/backtests/bt17_momentum_pool.py --start 2022-01-01 --end 2023-12-31 \
  --first-candidate-only --tag 1ma_off
uv run research/backtests/bt30_one_minute_agreement.py
```

Report at **real MIS costs (0.21%)** as well as the 0.80% stress, per the
correction recorded in quality-selectivity.

## 3. Pre-registered criteria (locked before the first run)

All four must pass, on the dev window, or this is a KILL.

- **G1 — lift.** mean gross with the rule on minus mean gross with it off ≥ **+0.60 pp**. (The selectivity bar, not the paired +0.30 pp bar: this rule removes trades.)
- **G2 — level.** rule-on mean gross ≥ **+0.35%/trade** — the viability bar re-derived from the measured 0.206% real round-trip cost in [volatility-scaled-entry](2026-09-06-volatility-scaled-entry.md). A positive lift that still loses money is a KILL.
- **G3 — anti-strategy.** 1,000 random subsets of the rule-off run, each the same size as the rule-on set: the rule-on set's mean gross must beat **≥ 95%** of them (p < 0.05). A rule that a coin flip reproduces is not a rule.
- **G4 — feasibility.** ≥ **100** trades survive the rule. Below that the test is not decided, it is unpowered — and no threshold may be relaxed to reach the count.

**Locked in advance:** no component may be dropped, no threshold retuned, and no
second variant may be run on this data if it fails. One shot, as with BT12/BT14.

## 4. What a pass would and would not mean

A pass on a spent window is a reason to run the rule **forward**, nothing more.
Deployment evidence would be a forward arm meeting the standing gate (≥30 trades
/ ≥20 sessions, mean actual-cost net > 0, 95% interval clearing +0.35% gross).

## 5. Result — **KILL** (3 of 4 criteria failed), 2026-09-13

Dev window 2022-01-01..2023-12-31, NIFTY 500 (504 symbols), both runs with
`--first-candidate-only`. The gated set is an exact subset of the control (the
subset check in `bt30_one_minute_agreement.py` reported no extras), so G3 is valid.

| | rule OFF | rule ON |
|---|---|---|
| trades | 1,814 | **1,030** (56.8% survive) |
| gross %/trade | +0.0516 | **+0.0513** |
| net at real 0.21% | −0.1584 | −0.1587 |
| net at 0.80% stress | −0.9546 | −0.9549 |
| win % | 35.2 | 34.9 |

| | measured | required | |
|---|---|---|---|
| G1 lift | **−0.0003 pp** | ≥ +0.60 pp | FAIL |
| G2 level | **+0.0513%** | ≥ +0.35% | FAIL |
| G3 anti-strategy | **p = 0.526** (beat 47.4% of 1,000 same-size random subsets) | p < 0.05 | FAIL |
| G4 feasibility | 1,030 | ≥ 100 | PASS |

**The rule removed 784 trades and changed the mean gross by three ten-thousandths
of a percentage point.** The removed trades averaged **+0.0520%** gross (win
35.7%); the kept trades averaged **+0.0513%** (win 34.9%) — the filter cannot tell
them apart. p = 0.526 says it plainly: deleting a random 43% of the trades would
have done the same thing about half the time. This is the same verdict as
[quality-selectivity](2026-09-06-quality-selectivity.md) (p = 0.979) and
[max-move-checklist](2026-09-07-max-move-checklist.md): reading a chart harder at
the entry does not separate winners from losers in this pool, and reading a
*different timeframe* — the one genuinely new thing here — does not either.

Refusal reasons (candidates, rule ON): `1m_red_or_flat` 503 · `1m_low_volume` 324
· `1m_weak_close` 121 · `1m_warmup` 82 · `1m_trend_down` 61.

**Observations, explicitly NOT rules** (each would be post-hoc slicing of a
window that is already spent; recorded only so nobody re-derives them as a
discovery): the removed set held proportionally more `target` exits (17.9% vs
31.2% — the rule cut winners as readily as losers, and the *kept* targets were
smaller, +1.48% vs +2.09%); the rule's sign flipped by year (2022 +0.012 pp,
2023 −0.012 pp), which is what a coin looks like.

⚠️ The originating trade (KEI 2022-01-19) appears in **neither** run:
`--first-candidate-only` — required for G3's validity — changes which setup that
day's first candidate is, so the chart that started this was not in the tested
population. The rule was judged on 1,814 trades, not on it.

**Decision on the hypothesis as stated: KILL.** As a *filter* — a rule that
separates trades worth taking from trades not worth taking — this is dead. No
second variant on this data: no threshold relaxation, no dropping a component,
no "1m trend only" retry. The committed stop applies to that question.

## 6. Disposition: off by default, switchable per deployment (2026-09-13)

After reading §5 the operator's first call was to keep the rule enabled, on the
reasoning that it takes 43% fewer trades at the same win rate and so pays less in
round-trip cost; the final call was to **ship it disabled and controlled by an
environment variable** — `MT_REQUIRE_1M_AGREEMENT` (default `false`), applied in
`scanner.MomentumScanner.__init__` after the per-strategy config is built so no
strategy branch can miss it. In backtests: `bt17 --require-1m-agreement`, also
off by default. Both the reasoning and the counter-evidence are kept below,
because the switch will be read again by whoever decides whether to flip it.

**That reasoning is arithmetically correct and is recorded as the reason.** At
real MIS costs on 2022–23:

| | trades | total net | per trade |
|---|---|---|---|
| rule off | 1,814 | −₹138,626 | −₹76 |
| rule on | 1,030 | **−₹83,068** | **−₹81** |
| random 43% cut | 1,030 | **−₹78,712** | −₹76 |

**Counter-evidence, recorded so it is not lost:** the ₹55k saving is the
*frequency* effect, not selection. A random 43% cut loses **less** (−₹78.7k vs
−₹83.1k), and per trade the rule is **worse** (−₹81 vs −₹76) — it kept slightly
the worse half. Costs are charged per trade, so cutting count cannot improve
per-trade economics; it scales the whole line down. The same logic taken to its
end says trade nothing, which loses ₹0.

**Two conditions on ever enabling it, written down in advance:**

1. **If pool expectancy ever turns positive** (gross > real cost), this rule
   removes ~43% of the profit and must be revisited immediately. Its benefit
   exists only while expectancy is negative.
2. **Forward evidence accrues 43% slower.** Any forward gate counted in trades
   (e.g. the merged arm's ≥30) now takes proportionally longer to decide.

**No live effect either way.** The deployed arm (`attention_1m_merged`) uses
attention entries, and `ONE_MINUTE_SETUPS` are exempt by construction — their own
one-minute confirmation *is* this test. The switch reaches backtests and any
legacy five-minute strategy only.
