---
slug: add-to-winner-at-1r
strategy: momentum_trader
status: registered
registered_at: '2026-09-19'
finalized_at: ''
decided_at: ''
hypothesis_hash: ''
---

# Hypothesis: once a trade has paid for itself, buying a second equal-risk tranche earns more than it costs — and starting at half size makes the fast failures cheaper

The buy-side companion to
[cost-aware-breakeven-stop](2026-09-19-cost-aware-breakeven-stop.md). Registered
before any run. Plain language on purpose.

## 0. How size is decided today, and what changes

Today every trade is sized by **risk, not by rupees invested**. The engine
picks how much it is willing to lose if the stop hits, `risk_inr = ₹500`, and
divides that by the distance from the buy price to the stop:

```
qty = min( ₹500 / (entry − stop) ,  ₹50,000 / entry )
```

A ₹392 stock with a stop ₹4.20 below gets 119 shares, about ₹46,700 invested,
losing ₹500 if stopped. The second term is a sanity cap so a very tight stop
cannot balloon the position; it binds on a large minority of trades, which
matters below.

The position is **one tranche, fixed from entry to exit**. Nothing is ever
added. Multi-entry exists but it is sequential re-entry after a close, never
adding to a live position.

This test adds a second tranche. On the bar that lifts the stop to the
cost-aware breakeven (the bar after the trade first reaches 1R), it buys again,
filled at that bar's open, risking the same rupees against the stop both
tranches now share. Two equal-risk tranches, no new numbers: the trigger is the
already-frozen `BREAKEVEN_AT_R = 1.0`, the size rule is the existing one, and
the stop is the one the other hypothesis is already testing.

## 1. Mechanism

Two different bets, tested as two arms.

**Arm P — add on strength (more capital).** A stock that has travelled 1R in
the trader's favour without stopping out has, weakly, shown the move is real.
Warrior's own guidance is to press winners. Since the first tranche is now
protected at cost breakeven, the add-on's rupee risk is the *only* live risk,
so the position's risk is unchanged at about ₹500 while its size roughly
doubles. If the signal has any continuation at all, size arrives where it is
deserved.

**Arm H — half now, half once it proves itself (same capital).** Same add-on,
but the first tranche starts at half size, so the position ends at roughly
today's full size rather than twice it. This one has a concrete mechanism
behind it, taken from the measured loss decomposition: **about 39% of trades
stop out within five minutes, and those fast stop-outs are where the loss
sits.** Arm H puts half the money at risk during exactly that window and only
commits the rest to trades that survived it. It does not need continuation to
work; it only needs the early failures to be cheaper than the late successes
are expensive.

Arm P is the operator's question. Arm H is the one with the better prior.

## 2. Expected effect size and my honest prior

**Poor for Arm P, cautiously neutral-to-positive for Arm H.**

The add-on buys at about entry + 1R and its stop is about 1R below that, so it
risks roughly 1R to make whatever the trail eventually gives. On a signal
measured at essentially **zero drift** (BT17 pool gross ≈ 0.00%/trade across
five years, reconfirmed out of sample in 2026), there is no reason to expect
price to continue after 1R, so the add-on's expected gross is near zero while
it pays a second full round trip of about 0.21%. The base rate of "add to the
winner" ideas in this repo is bad: multi-entry was killed with a 2.41x trade
increase and 2.5x the loss, and trade #1 of the day measured *better* than
trades #2–#5.

Arm H is different in kind. It does not add exposure; it *delays* half of it
past the most expensive minutes. If the fast stop-outs really are the loss
centre, halving them is a mechanical saving that does not require any drift.

- **Direction**: Arm P, add-on tranche net > 0. Arm H, total net per trade
  better than the cost-stop control.
- **Magnitude**: Arm P, ±₹20 per add-on. Arm H, +₹20 to +₹60 per trade if the
  fast-stop mechanism is real, 0 if it is not.
- **Units**: ₹ per trade at **real** costs (0.21% round trip), paired on
  identical entries.

## 3. Falsification criterion (LOCKED before experimentation)

Control for both arms is the **cost-aware-stop arm** (`cost`), not the plain
base, so what is measured here is the add-on alone and not the stop change
already under test. Windows: full-year 2023 and 2024, attention arm. Pairing is
on shared `(date, symbol, entry_time)`.

**Arm P — add on strength**

- **P1 (primary)**: mean `add_net_inr` per trade that actually added, at real
  costs, **> 0 with p < 0.05**. This is the clean question — does the second
  tranche pay for its own round trip? — and it cannot be flattered by simply
  deploying more capital. Fails → KILL Arm P.
- **P2**: pooled total net per trade **≥ control**, i.e. the add-on does not
  make the whole strategy worse.
- **P3**: `add_net_inr` > 0 in 2023 **and** 2024 separately.
- **P4**: ≥ 300 trades with an add in each year, else VOID not pass.

**Arm H — half now, half on proof**

- **H1 (primary)**: pooled paired total net per trade, at real costs, **beats
  the control by ≥ +₹10 with p < 0.05**. Fails → KILL Arm H.
- **H2**: better in 2023 **and** 2024 separately.
- **H3**: ≥ 500 shared trades per year.
- **H4 (mechanism check, must also hold)**: the improvement must come from the
  losing trades being smaller, not from the winners being bigger — mean net on
  trades that never added must improve, and by more than half of the total
  improvement. If Arm H wins only through its winners, the stated mechanism is
  wrong and the result is a size accident. → KILL.

**Capital honesty, declared in advance.** Arm P deploys up to 2x the capital of
the control, so its total-rupee figures are not comparable to anything and are
reported as descriptives only. Arm H is capital-comparable by construction.
Neither arm's rupee totals may be quoted against any other hypothesis in this
repo.

**No threshold may be relaxed after seeing results.** Both windows are already
spent for other momentum work, so a pass here is **"not killed"**, not "ship":
it earns a forward paper check, nothing more. Neither arm changes the
strategy's standing viability verdict, which is negative.

Pre-declared descriptives (no verdict weight): share of trades that reach the
add trigger; add-on win rate; distribution of `add_entry / entry`; exit-reason
mix; how often the notional cap rather than risk binds each tranche.

## 4. Data needed

- Existing Upstox 1-minute cache, 2023 and 2024, attention universe.
- No new feeds. Fee model `trailing_sl.calc_costs`, charged **per tranche**,
  because a second tranche is a second real round trip.

## 5. Train / dev / hold-out split

- 2023 and 2024, both already spent for other momentum work.
- **2025 stays sealed and is not touched.**
- Forward: live paper log, only if an arm survives.

## 6. Code reference

- `EngineConfig.pyramid_add_at_1r`, `EngineConfig.initial_risk_fraction`,
  `engine._add_to_winner`, add-on accounting in `engine._exit`.
- `ClosedTrade.add_qty / add_entry / add_time / base_net_inr / add_net_inr`,
  and `avg_entry` / `total_qty` so percentages are on capital actually used.
- Runner: `bt17 --pyramid-add-1r [--initial-risk-fraction 0.5]`.
- Analysis: `research/backtests/bt44_add_to_winner.py`.
- Tests: `tests/momentum_trader/test_engine.py` (5 add-on tests).

## 7. Result (BT44, run 2026-09-20)

Control is the cost-aware-stop arm. Full-year 2023 + 2024, attention arm,
3,139 shared trades, paired trade-for-trade. Runs tagged
`cs{23,24}_{cost,pyr,half}`.

**Trades reaching the add trigger: 533 of 3,139 (17.0%)** — 353 in 2023
(18.7%), 180 in 2024 (14.4%). The add fills a mean **+0.81%/+0.87% above
entry** and is the same size as the first tranche (1.00x), as designed.

### Arm P — add on strength: the add-on loses money, decisively

| Window | Adds | Add-on net/add | Add-on win rate | t | p |
|---|---:|---:|---:|---:|---:|
| 2023 | 353 | **−₹74.57** | 22.66% | −4.42 | <0.0001 |
| 2024 | 180 | **−₹117.84** | 19.44% | −6.06 | <0.0001 |
| Pooled | 533 | **−₹89.18** | — | −6.87 | <0.0001 |

95% CI [−₹114.64, −₹63.72]. Total net per trade falls −₹15.14 (p<0.0001).

⭐ **The first tranche on those very same trades made +₹284.53 (2023) and
+₹259.34 (2024).** Reaching 1R genuinely marks a better-than-average trade —
and buying *more* of it at +0.8% above entry still loses. That is what zero
drift means in practice: the move that already happened does not predict the
next one, and the add-on risks about 1R to find out while paying a second full
round trip.

| Criterion | Result |
|---|---|
| P1 add-on net > 0, p<0.05 | **FAIL** (−₹89.18, p<0.0001) |
| P2 total net not worse | **FAIL** (−₹15.14) |
| P3 add-on net > 0 in both years | **FAIL** (−74.57 / −117.84) |
| P4 ≥300 adds per year | **VOID** (2023: 353, 2024: **180**) |

**Formally VOID by the letter of P4** — 2024 fell short of the pre-registered
floor. **Substantively KILL**: the direction is identical in both years, the
pooled sample is 533 with t=−6.87, and low power cannot manufacture a negative
of this size. Recorded as killed. No re-run, no relaxation of P4.

### Arm H — half now, half on proof: passes its criteria for a trivial reason

All four locked criteria passed (+₹51.90/trade, t=+21.71, better in both
years, H4 mechanism check passed). **The pass is an artifact and the arm is
killed anyway.** Here is why, in the arm's own numbers:

```
capital deployed  arm H / control = 0.582
delta predicted by SIZE ALONE (control loss x (1 - ratio)) : +49.35 INR/trade
delta observed                                             : +51.90 INR/trade
attributable to anything other than size                   :  +2.55 INR/trade
```

§2 asserted "Arm H is capital-comparable by construction". **That premise is
false**, and the data falsified it: only the 17% of trades that reach 1R are
ever topped up, so the other 83% stay at half size for their whole life and
the arm deploys **58% of the control's capital**. H1 counts rupees per trade,
so it rewarded trading smaller. You could collect the same +₹49 by halving
`risk_inr` and adding nothing at all.

The size-neutral measure, net per rupee of capital deployed, settles it:

| Set | n | Control | Arm H | Delta |
|---|---:|---:|---:|---:|
| never added | 2,606 | −0.4259% | −0.4259% | **+0.0000 pp** |
| added | 533 | +0.5846% | +0.1971% | **−0.3875 pp** |
| all | 3,139 | −0.2526% | −0.2432% | +0.0094 pp |

- **Never-added trades: exactly zero.** Halving both the rupee risk and the
  notional cap scales quantity proportionally, and at these sizes brokerage is
  in its 0.03% percentage regime rather than its ₹20 flat regime, so cost per
  rupee is unchanged. **H4 was mathematically vacuous** — it could only ever
  pass, because "cheaper losers" is what smaller positions are by definition.
- **Added trades: −0.39 pp.** The add-on destroys return per rupee on exactly
  the trades it touches, agreeing with Arm P.
- The +0.0094 pp on "all" is a *composition* effect, not skill: arm H happens
  to put ₹46,982 into added trades and only ₹23,157 into never-added ones,
  weighting capital toward the better trades. Worth +0.40 pp gross — and the
  add-on gives back −0.39 pp of it.

### ⚠ Criterion-design failure, recorded against myself

H1 and H4 were pre-registered and both passed, and both were **the wrong
measures**. A rupees-per-trade criterion only tests skill when capital is held
comparable, and I asserted comparability instead of measuring it. This is the
same trap as `MT_REQUIRE_1M_AGREEMENT`, which "improved" totals by cutting 43%
of trades while per-trade expectancy got worse.

The verdict is overridden in the **conservative** direction — a pass is being
refused, not a failure rescued — and the premise check is now **code**, not
prose: `bt44` computes the capital ratio, prints the size-alone prediction and
the per-rupee decomposition, and returns KILL whenever the ratio is below 0.90.
**Rule for next time: any arm that changes position size must be judged per
rupee of capital deployed, and the capital ratio must be printed before the
criteria.**

### 🟢 One genuine observation, deliberately not mined

Capital allocation across trades is worth about **+0.40 pp gross** here:
putting less money into trades that fail early and more into those that
survive. This study cannot claim it — the number is post-hoc, it is entangled
with the add-on that cancels it, and 2023/2024 are spent. It needs its own
pre-registered hypothesis on an unspent window, sized at entry rather than by
adding later. Prior: unknown, and the mechanism would have to survive the fact
that "which trades fail early" is not knowable at entry.

## 8. Decision

- [x] **Arm P KILL** — add-on net −₹89.18/add, p<0.0001, both years negative.
      Formally VOID on P4 (180 adds in 2024 vs a 300 floor); substantively
      killed on direction and consistency. `pyramid_add_at_1r` stays False and
      is set nowhere.
- [x] **Arm H KILL** — all four locked criteria passed, and all four were
      measuring position size. Per rupee of capital the arm is flat on the
      trades it leaves alone and −0.39 pp on the trades it adds to.
      `initial_risk_fraction` stays 1.0 and is set nowhere.

No variant of either arm on this data. Both windows are now spent for the
add-to-winner question.
