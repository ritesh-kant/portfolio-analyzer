# BT36 — where the entry fill leak comes from, and what a resting buy-stop recovers

**Run 2026-09-15.** Descriptive execution measurement, not signal mining: the trade
population is held fixed and only the fill model changes. Window 2024, 210 cached
symbols, 984 trades, legacy bt17 path, real MIS costs.

## 1. Diagnosis — the leak is decision latency, not slippage

Engine sets a pending entry at a **minute's close**, fills at the **next minute's open**.
Model validated against recorded fills: **100% match, mean |error| 0.0001%** (n=5,587
clean trades).

Decomposing the mean +0.2175% gap:

    trigger minute's own run past the level   +0.2159%   (99%)
    minute-to-minute open jump                +0.0015%   ( 1%)

No gap risk, no spread, no impact. The level is crossed ~a third of the way into the
signal minute (median breach position 0.32 of its range), price runs for the rest of it,
and you buy where that minute closed. Mean trigger-minute range is 0.479% — you pay for
about half of it. Worse on bigger minutes (0.128% → 0.332% across range quartiles) and
in the opening hour (09:xx minutes average 0.644% range).

Understated: `CHASE_MAX_EXT_PCT = 1.0` truncates the distribution at 0.999%; worse cases
became rejections, not trades.

Split by where price was when the signal minute opened:

| | share | gap | share of leak |
|---|---|---|---|
| level crossed **during** the signal minute | 66.5% | +0.177% | 54% |
| price **already above** the level | 33.5% | **+0.298%** | 46% |

The second group is pure waiting cost — the break happened earlier and the engine was
still confirming.

## 2. What a resting buy-stop actually recovers

New engine fill modes (`FILL_RESTING`, `FILL_RESTING_SIZED`); 439 tests pass, existing
modes untouched.

| model | n | gross | net @ real | total |
|---|---|---|---|---|
| `next_open` — today | 984 | −0.0309% | −0.2369% | **−₹102,777** |
| `trigger` — upper bound, uses future info | 984 | +0.2000% | −0.0062% | −₹1,298 |
| `resting` — buy-stop, entry gate held fixed | 984 | +0.2006% | −0.0057% | −₹1,038 |
| `resting_sized` — buy-stop, **realistic** sizing | 964 | +0.1192% | −0.0869% | **−₹41,365** |

    latency prize                 ₹101,478
    captured, gate held fixed     ₹101,739  (100%)  <- not physically realisable
    captured, realistic sizing     ₹61,411  ( 61%)
    cost of realistic sizing       -₹40,328

## 3. Where the 39% goes — and a wrong answer I had to retract

First read: "the chase guard is filtering signals." **Wrong.** Removing it changes only
**4 trades** (worth ₹12). The guard is doing essentially nothing.

The real cause is `plan_trade`'s `gate_entry`. Its docstring (`risk.py:67-71`) says the
parameter exists so a fill-price change "cannot silently admit or drop a trade." A live
resting order *cannot* see the next open, so it must judge the stop-sanity band on the
trigger — the price it actually pays. That admits and drops different candidates, and
`one_trade_per_day` then cascades the divergence across the session: **240 different
trades taken, 260 dropped**, net −₹40,328.

I also briefly mislabelled those 240 as chase-rejects. They are not: a chase-reject must
open >1% above the trigger, and these average +0.03%. Only 4 qualify.

**This give-back is not intrinsic.** `stop_is_sane`'s band was calibrated against
next-open fills; feeding it trigger fills shifts every stop distance without the band
moving. Recalibrating it for resting fills is bounded, concrete work — not a signal hunt.
Deliberately NOT tuned here: 2024 is spent, and fitting the band to it would be exactly
the BT33-v2 mistake.

## 4. What this does and does not mean

- The leak is real, mechanical, and the largest single number in the repo: **1.04× the
  entire round-trip cost.**
- Fully closed, the 2024 pool goes from −₹102,777 to about break-even (−₹1,298).
  **Break-even, not profitable.** Execution alone does not make this system make money.
- Realistically today it recovers 61%: −₹102,777 → −₹41,365, i.e. −0.237% → −0.087%
  per trade. Still the biggest available improvement by a wide margin.
- Live resting fills currently read +0.03% over trigger (n=11) vs the pool's +0.218%,
  consistent with this — but n=11, and a paper fill is an observed quote, not a book.

## 5. Next, in order

1. **Raise the live resting-fill sample** to n≥100. Free, forward, spends no window.
2. **Recalibrate `stop_is_sane` for trigger-priced fills** — on a window that is not
   2024, pre-registered, since it is worth up to ₹40,328 of the ₹101,478.
3. Only then ask whether the system clears costs.

---

# PART 2 — 2026-09-15, same day: is there a matching SELL leak, and what is the fix?

## 6. Sell side: no leak. Measured, not assumed.

Exits split into two kinds:

- **Price-based** — `stop` and `target`, 72% of exits. `exits.check_stop` fills at
  `min(stop, bar.open)` and `check_target` at `max(target, bar.open)`. Those are already
  **resting-order models**: you get the level, or the gap price when it is worse (stop) /
  better (target). Nothing to fix.
- **Signal-based** — `false_break`, `eod_close` and the trend exits, 28% of exits. These
  fill at the close of the bar that produced the signal, which a real system could only
  act on one bar later.

Measured that one-bar delay on all 274 signal exits in the 2024 pool:

    mean slip  +0.0089%   median +0.0000%   worse than modelled in only 21.9% of cases
    false_break (n=201)  +0.0099%      eod_close (n=73)  +0.0062%
    total across the pool  +₹755   =  +0.0025%/trade spread over all 984 trades

**Selling a minute later is very slightly BETTER, not worse.** There is no sell-side
leak, so nothing was changed on the exit path.

Why the asymmetry with entries: an entry is a *breakout* — you buy into a move already in
progress, so a minute's delay costs you the rest of that minute. An exit fires on
*failure/reversal*, which carries no comparable immediate continuation.

## 7. The real bug: the backtest was not modelling production

The live scanner runs `FILL_FUTURE_TRIGGER` for **all five** strategy configs
(`scanner.py:143-187`) and fills through `fill_pending_quote`, which takes the first quote
at or above the trigger and gates on `gate_entry=price` — the price actually paid.

**So production already rests its buy-stops. It never had this leak.** What had the leak
was `bt17`'s default `--fill-mode next_open`, which is where the pool numbers
(−0.18%/trade, gross ≈0.00%, the BT29 population) all came from. Those figures described
a system nobody runs, ~0.22%/trade worse than the deployed one.

`bt17 --attention` already mirrored live (`future_trigger`); the **legacy path did not**.

## 8. What was changed

- `EngineConfig.entry_slip_pct` (default **0.0**, so every prior run reproduces). Applied
  to resting fills only, and clamped never to pay worse than the next open — at that
  price the same prints would have filled the order anyway.
- `bt17 --live-fill` = `--fill-mode resting_sized --entry-slip-pct 0.03`, the production
  model in one flag. `LIVE_ENTRY_SLIP_PCT = 0.03` is the live-measured figure (n=11,
  paper) and is flagged in-code as an estimate, not a constant.
- `bt17` now prints a loud warning whenever `fill=next_open` is in use, saying it is not
  how production fills and pointing at `--live-fill`.
- **No production code changed** — live was already correct.
- Tests: +9 in `test_fill_mode.py` (20 in that file, **574 across the suite**), covering
  the new modes, the chase-guard difference, gate-on-price-paid, the slip clamp, and that
  slip cannot touch the legacy modes.

## 9. Corrected baseline — legacy pool, 2024, 210 symbols, real costs

| model | n | fill gap | gross | net | total |
|---|---|---|---|---|---|
| `next_open` (legacy) | 984 | +0.2040% | −0.0309% | −0.2369% | −₹102,777 |
| `--live-fill` (production) | 977 | +0.0202% | **+0.0881%** | **−0.1180%** | **−₹54,092** |

**+0.119 pp/trade, +₹48,685.** Gross turns positive; net is still negative, so the honest
conclusion from Part 1 stands: **closing the leak halves the loss, it does not make the
system profitable.**

`next_open` re-run after the change is **byte-identical** to the pre-change CSV (984 rows).

---

# PART 3 — a bug in Part 2's own fix, found by checking the fills against the bars

## 10. The deployed arm's replay has the SAME leak

Replaying the deployed arm (`bt17 --attention`, which mirrors live's
`FILL_FUTURE_TRIGGER`) on 2024/210 symbols:

    n = 1,288   fill gap +0.1794%   gross -0.0957%   net@real -0.3016%   -₹180,538

**+0.179% is nearly as bad as `next_open`'s +0.204%.** The replay of `future_trigger`
fills at *a later bar's close at-or-above the trigger* ("Replay has no quote chronology…
a high-only touch is not enough", `engine.py`), whereas live fills at the **first quote**
at or above it. So the deployed arm's historical numbers are pessimistic too — the same
class of error as the legacy path, in a different place.

## 11. Sell side re-checked where it actually matters

The legacy arm is only 28% signal-based exits; **the deployed arm is 84%**
(`false_break` 605, `ema9_break` 210, `volume_climax` 130, `resistance_reject` 71,
`macd_fade` 54 of 1,288). Re-measured there:

    mean slip +0.0082%  (favourable)   worse than modelled in 20.0% of cases
    total +₹4,161 over 1,078 signal exits  =  +0.0069%/trade

**No sell leak on the arm where signal exits dominate.** Conclusion from Part 2 holds.

## 12. 🔴 Part 2's `resting_sized` was fabricating fills

Checking every attention fill against its own bar: **583 of 1,894 entries (30.8%) were
priced ABOVE the entry bar's high** — fills at a price the market never traded.

Cause: the resting branch filled unconditionally on the bar after the signal. That is
defensible for the legacy setups, which fire on `bar.high > trigger` (a buy-stop at the
level was hit inside that same signal bar), but **wrong for the attention path**, whose
trigger sits *above* price when the order is armed — that order genuinely waits, and may
never fill. Hence the mode name `future_trigger`.

### Fix

`Pending.trigger_reached` now records, at arming time, whether price had already traded
through the level:

- **`trigger_reached=True`** (legacy) → filled at the trigger inside the signal bar.
- **`trigger_reached=False`** (attention) → the order waits for `bar.high >= trigger`;
  a bar that gaps open above the level fills at that **open**, which is worse — the exact
  mirror of `exits.check_stop`'s `min(stop, bar.open)`.

Both branches now keep the **chase cap** and judge the stop-sanity band on the price
actually paid, matching `fill_pending_quote` line for line. (Part 2 had removed the chase
cap for resting on the argument that a filled order has nothing to decline — wrong: live
cancels on a runaway quote, and so must the replay.)

Also refactored the shared position-opening into `_open_position`.

### Tests

`test_fill_mode.py` rewritten for the corrected semantics: **23 tests in the file, 577
across the suite.** The regression that matters is
`test_a_future_trigger_waits_and_does_not_invent_a_fill`, which pins the 30.8% bug.

## 13. Two more rounds of the same bug, and the final fix

The `trigger_reached` fix alone did **not** work — impossible fills went 30.8% → 33.5%.
Cause: these triggers sit on round numbers that the bar often only *just* touches
(`high == trigger` exactly), so `trigger × (1 + 0.03%)` alone priced the fill above
anything that traded. Then the same defect showed on the legacy side at 3.79%
(worst +0.028%).

Final rule — **a resting fill can never exceed what its own bar traded**:

- waiting (attention): `fill = min(max(trigger×(1+slip), bar.open), bar.high)`
- already hit (legacy): `fill = min(trigger×(1+slip), pending.armed_high)`, where
  `armed_high` is recorded at arming time from the setup's own timeframe.

Verified with a per-mode validator (each mode fills on a different bar, so each needs
its own bound):

    OK  next_open      0 / 984    OK  live-fill (legacy)     0 / 975
    OK  live-fill (att) 0 / 1633  OK  future_trigger         0 / 1288

**579 tests pass.** `next_open` is byte-identical to the pre-change CSV.

## 14. Final numbers — 2024, 210 symbols, real costs

**Legacy pool**

| model | n | fill gap | gross | net | total |
|---|---|---|---|---|---|
| `next_open` | 984 | +0.2040% | −0.0309% | −0.2369% | −₹102,777 |
| `--live-fill` | 975 | +0.0293% | **+0.0916%** | **−0.1145%** | **−₹52,749** |

**+0.1224 pp/trade, +₹50,028.**

**Deployed (attention) arm**

| model | n | fill gap | gross | net | total |
|---|---|---|---|---|---|
| `future_trigger` | 1,288 | +0.1794% | −0.0957% | −0.3016% | −₹180,538 |
| `--live-fill` | 1,633 | +0.0247% | −0.0573% | −0.2633% | −₹199,828 |

**+0.0383 pp/trade** — but the **total gets worse** (−₹180,538 → −₹199,828) because the
resting model fills 345 more orders: the conservative `future_trigger` replay demands a
*close* above the level and so misses touches that only wick through. More trades at
negative expectancy loses more money. Judge per-trade, not totals.

## 15. Bottom line

- The entry leak is real, mechanical, and now correctly modelled. **No production code
  changed — live was already resting its buy-stops.**
- Every historical legacy-path figure in this repo understated the system by
  **~0.12 pp/trade**; the deployed arm's replay understated it by ~0.04 pp.
- Corrected, the 2024 legacy pool is **−0.1145%/trade** instead of −0.2369%. Gross turns
  positive (+0.0916%). **Net is still negative** — real costs are 0.21% and the corrected
  gross does not cover them.
- **There is no sell-side leak.** Selling one bar later is +0.008%, i.e. marginally
  favourable, on both arms.

Closing the leak roughly **halves the loss. It does not make the system profitable.**
