---
slug: multi-entry-same-stock
strategy: momentum_trader
status: killed
registered_at: '2026-09-06'
finalized_at: '2026-09-06'
decided_at: '2026-09-06'
hypothesis_hash: ''
parent: momentum-catalyst-upstox-v2
---

# Hypothesis: Staying with a momentum stock and trading every setup it gives all day — buying each dip, selling each push — earns more money per stock-day than taking only the first setup and walking away, because the entry we currently take is the weakest one the stock offers.

> ⚠️ **Fresh dev window, on purpose.** `pullback-ordinal` (2026-09-06) recorded
> 2024 as **spent for entry-side mining** after four examinations. This is an
> entry-side change, so running it on 2024 would make that record meaningless
> one day after writing it. Dev here is **2022–2023**, which no hypothesis in
> this repo has ever read. 2024 is skipped entirely. 2025 remains the sealed
> hold-out.
>
> ⚠️ **This is the most cost-exposed change proposed so far.** Every round trip
> costs roughly 1% once stressed, and this change multiplies round trips. If the
> extra trades are merely average, the result is a multiple of the same loss.
> Gate A exists to make that the deciding question rather than a footnote.

## 0. What changes

`EngineConfig.one_trade_per_day` already exists and already gates re-entry;
nothing in the decision logic needs rewriting. The two arms:

| Arm | Rule |
|---|---|
| `single` (baseline, = every prior backtest) | first setup of the day per symbol wins; the stock is then done |
| `multi` (treatment) | every setup that fires is taken, all day, no cap |

**No cap, by operator's choice.** A cap of 3 (or any number) would be a new
tunable constant invented for this test, and inventing constants is how a rule
gets quietly fitted. Unlimited introduces none. The realised distribution of
trades per symbol-day is reported as a secondary.

Unchanged and not up for negotiation in this test: the entry patterns, the
1% chase guard, the stop and 2:1 target, the 14:30 entry cutoff, the 15:15
close, and `fill_mode = next_open`. Only re-entry differs. A position is never
opened while one is open, and never on the same bar one closed.

## 1. Mechanism

`pullback-ordinal` (2026-09-06) measured, on 2024, the mean gross return of each
entry by which pullback of the day's move it sat on:

| ordinal | 1 | 2 | 3 | 5 |
|---|---|---|---|---|
| gross %/trade | −0.083 | −0.039 | −0.034 | +0.215 |

The trade the `single` arm takes is overwhelmingly ordinal 1 — **the worst of
them.** Later entries in the same stock were better. If that ordering is real
rather than noise, then walking away after the first setup is systematically
keeping the weakest entry and discarding the better ones.

That is the case *for* this change, and it is data-derived rather than
theoretical. It is also weak evidence: `pullback-ordinal` was KILLED, its
anti-strategy p was 0.469, and the ordinal-5 bucket held 37 trades. A hint from
a killed test is a reason to look, not a reason to believe. Testing it on a
window that test never touched is the point.

**Why it might fail.** Cost. Also: a stock that gives four setups in a day may
be a stock that is chopping rather than trending, so the later setups could be
selecting for exactly the wrong condition. And re-entering immediately after a
stop-out is the classic way to convert one bad read into four.

## 2. Expected effect size

- Trades roughly **1.5× to 2.5×** the `single` arm.
- Mean gross %/trade of re-entries minus first entries: **+0.10 to +0.40** pp.
- Stressed net ₹ per symbol-day: genuinely uncertain in sign. This is the gate.

## 3. Falsification criterion (LOCKED before any code is written)

Dev window **2022-01-01 → 2023-12-31**. **All four must pass; any one failing
is a KILL.**

- **GATE A — money:** stressed net ₹ **per symbol-day**, `multi` − `single`,
  **> 0**. Per symbol-day, not per trade: the whole point is taking more trades,
  so a per-trade comparison would flatter an arm that trades less. If holding
  the stock all day makes less money than trading it once, the answer is no,
  whatever the per-trade numbers say.
- **GATE B — quality:** mean gross %/trade of **re-entry** trades (the 2nd and
  later in a symbol-day) minus **first-entry** trades ≥ **+0.30** pp, positive
  sign. This is the §1 mechanism stated as a number. If the extra trades are not
  better than the one we already take, there is no reason to take them.
- **anti:** shuffle the first-entry / re-entry labels across the `multi` arm's
  trades 5,000 times; p(shuffled Gate-B spread ≥ observed) < **0.10**.
- **n:** ≥ **100** re-entry trades, so Gate B is not decided by a handful.

**Hold-out (2025), touched once, only if all four dev criteria pass:** Gate A
must hold (net ₹/symbol-day improvement > 0) and Gate B ≥ **+0.15** pp,
correctly signed. Otherwise KILL.

🔒 **No-relax rules:**
- Two arms only. A cap on re-entries, a "stop the stock after a loss" rule, a
  cool-down between trades, or a minimum gap since the last exit are each a
  **new hypothesis file**. None may be added here to rescue a failing result.
- 2024 is not to be used for this or any further entry-side test.
- Reporting a subset that worked — a setup, an hour, a re-entry number — is an
  observation, not a filter.
- Passing licenses re-entry in the forward capture. It does not license live
  money, and it does not make the pool viable: the 4-symbol 2022–23 probe run
  on 2026-09-06 showed stressed net −1.008%/trade, so the underlying fade is
  present in this window too.

## 4. Data & split

- **Dev:** 2022-01-01 → 2023-12-31. Never previously read by any hypothesis.
- **Skipped:** 2024, declared spent by `pullback-ordinal` §8.
- **Hold-out:** 2025. Sealed.
- Upstox 1-minute history covers 2022; confirmed by a 4-symbol probe on
  2026-09-06 (57 trades). The rest of the window needs a fresh cache fill.
- Costs: production intraday MIS plus +40 bps/side, identical in both arms.

## 5. Code

- `engine.py` — `EngineConfig.one_trade_per_day`, already present, already
  gating. No decision logic changes.
- Runner: `bt17_momentum_pool.py --multi-entry`.
- Analysis: `bt21_multi_entry.py` applies the four criteria.
- Tests: `tests/momentum_trader/test_multi_entry.py`.

## 6. Result — 2022–2023 dev, run 2026-09-06

274 symbols processed (223 skipped on the daily pre-filter), **2,408 traded
symbol-days**.

### Design validation

The `multi` arm's first-entry rows are **identical** to the whole `single` arm —
2,408 trades, gross −0.046%/trade, net ₹−1,144,845.67 in both. Re-entry added
trades without disturbing the ones we already took, exactly as §0 required and
as `test_multi_arm_is_a_superset_starting_with_the_same_trade` asserts. The
comparison is clean.

### The arms

| | single | multi |
|---|---|---|
| trades | 2,408 | **5,797** (2.41×) |
| trades per symbol-day | 1.00 | 2.41 |
| gross %/trade | −0.046 | −0.078 |
| stressed net %/trade | −1.051 | −1.084 |
| win % | 13.12 | **10.90** |
| **stressed net ₹ per symbol-day** | **−475.43** | **−1,202.31** |

Trades per symbol-day in the `multi` arm: 938 days took 1, then 557, 390, 247,
149, 74, 31, 17 and 5 days took 2 through 9.

### All three testable criteria FAILED

| criterion | required | observed | verdict |
|---|---|---|---|
| GATE A — money | > 0 | **−₹726.87** /symbol-day | FAIL |
| GATE B — quality | ≥ +0.30 pp | **−0.0553 pp** | FAIL |
| anti (shuffle p) | < 0.10 | **0.9898** | FAIL |
| n(re-entries) | ≥ 100 | 3,389 | PASS |

Gate A is unambiguous: working the stock all day **lost 2.5× as much money** per
opportunity as taking one trade and leaving. Gate B is wrong-signed — the extra
trades were worse than the one we already take, not better. The anti-strategy
p of 0.9898 says the observed spread is more adverse than 99% of random
relabellings; this is not a null result, it is a confidently negative one.

### Costs are not the explanation

The pre-registered secondary — Gate A recomputed with **all costs removed** —
came out at **−₹64.48 per symbol-day**, still negative. So this is not a good
idea that trading costs erase. The additional trades are worse *before* any
cost is applied. Cost then multiplies the damage, but it did not cause it.

### The §1 mechanism is refuted, not merely unsupported

| trade # within the day | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|
| n | 2,408 | 1,470 | 913 | 523 | 276 | 127 | 53 |
| gross %/trade | **−0.046** | −0.114 | −0.063 | −0.127 | −0.184 | −0.008 | +0.033 |
| win % | 13.1 | 9.7 | 9.7 | 8.2 | 5.8 | 12.6 | 13.2 |

**The first trade of the day is the best one**, and quality degrades through the
fifth. Win rate falls monotonically from 13.1% to 5.8% across trades 1–5. Trades
6–8 tick back up but hold 202 trades between them against 5,590 in trades 1–5,
and no cut exists that captures them prospectively.

This is the **direct opposite** of the hint from `pullback-ordinal` that
motivated the test, where on 2024 the first pullback looked worst and later ones
looked better. That hint carried anti-strategy p = 0.469 — i.e. it was already
labelled as noise — and a fresh window has now falsified it outright on 3,389
re-entry trades. This is precisely what the fresh-window rule exists to catch,
and it is the strongest argument in this repo so far for keeping it.

## 7. Decision

- [x] **KILL** — three of four locked criteria failed: Gate A −₹726.87 per
      symbol-day against > 0 required, Gate B −0.0553 pp against +0.30 required,
      anti-strategy p = 0.9898 against < 0.10 required.

**`one_trade_per_day` stays `True`** in the forward capture and everywhere else.
It is not an arbitrary limitation — it is now measured, on a window nothing else
has touched, as the better rule by a factor of 2.5 in money per opportunity.

**No rescue rules.** §3 pre-committed that a cap on re-entries, a stop-the-stock-
after-a-loss rule, a cool-down, or a minimum gap are each a new hypothesis file
and none may be added here. The per-trade-number table invites exactly that
(trades 6–8 look fine); the anti-strategy p-value and the monotone 1→5 decline
say any such split would be fitting to noise.

**2022–2023 is now a used dev window** for entry-side work, like 2024. 2025
remains sealed. Anything further on the entry side needs data outside all three,
or a genuinely different question.

**Scoreboard.** On `momentum-catalyst-upstox-v2`: entries measured twice, exits
once, execution once, entry timing once, re-entry once — six independent
measurements, none of which found an edge, four of them wrong-signed. The
**catalyst gate remains the only untested component**, and it is the only
remaining test that does not consume an already-used window, because it requires
new data (results dates) by construction.

## Post-hoc disclosure (2026-09-06): breakeven lock leaked into `fixed_2r`

While decomposing losses on 2026-09-06 it was found that `exits.update_high`
applied the trend-mode breakeven-at-1R lock in **every** mode, including
`fixed_2r`, from the moment `exits.py` was introduced (2026-09-05). Spec §4 lists
exactly four fixed-mode exits (false_break, stop, target, 15:15); the original
BT17 run had zero `trail_stop` exits. With the leak, ~24% of `fixed_2r` trades
exited at breakeven and target hits fell from ~25% to ~15%.

Measured on 2022–2023 (2,408 identical entries): gross **−0.046% → +0.025%**,
win rate 21.6% → 33.9%, realised R:R 3.18 → 2.06 (i.e. the planned 2:1), target
exits 348 → 594, `trail_stop` 575 → 0. Fixed in `ExitConfig.for_mode(MODE_FIXED)`
(`arm_at_r = breakeven_at_r = inf`); regression test
`tests/momentum_trader/test_fixed_mode_no_lock.py`.

**Effect on this file:** both arms carried the leak. Gate A (net ₹/symbol-day,
−₹726.87) and Gate B (re-entry − first, −0.055 pp) are between-arm differences
with identical exit logic and stand. The `single` arm's absolute figures
(gross −0.046%, net −1.051%) are the leaked numbers; the corrected `single` arm
on the same 2,408 entries is gross +0.025%, net −0.981%. The "first trade is
best, later trades worse" ordering is a within-arm comparison and is unaffected
in kind. Verdict stands.
