# Four-bar price/volume quadrant gate — ON/OFF replay with a matched anti-test

Registered 2026-09-19 **before** any ON/OFF run was executed. This file fixes
the arms, the windows, the anti-test and the verdict rule. Nothing below the
"Results" heading existed when the runs were launched.

## What is being tested

`EngineConfig.require_rising_price_volume`: at the one-minute confirmation, a
long is allowed only when both the close slope and the total-volume slope over
the last four completed bars of the current session are strictly positive
(`up_price_up_volume`). Implemented in `engine.price_volume_slopes` /
`_attention_confirmation`, frozen at four bars.

It was enabled live on 2026-09-18 on a post-hoc same-day observation of three
refused trades (`2026-09-18-price-volume-quadrant-overlay.md`). That is not
evidence. On 2026-09-19 it was moved behind `Settings.mt_require_rising_price_volume`,
default **OFF**, and given a bt17 opt-in (`--rising-price-volume`) so the two
arms can be replayed at all — previously `--attention` forced it ON, so no
ON/OFF comparison was possible.

Only one of the source graphic's four quadrants is expressible. OHLCV has no
aggressor side, so "sell volume rising/falling" cannot be computed from this
feed; the two reversal cases in the chart are not implemented and are not
under test.

## Arms

Identical in every other respect, differing only in the gate:

```
bt17_momentum_pool.py --attention --first-candidate-only \
    [--rising-price-volume]  --start … --end … --tag …
```

`--first-candidate-only` is used in BOTH arms, per the requirement recorded in
BT30. bt17 defaults otherwise (one trade per symbol per day, `future_trigger`
fills, `trend_resistance_state` exits, 0.80% stress).

⚠ The live arm runs multi-entry (`MT_ONE_TRADE_PER_DAY=false`, operator
override). These runs therefore measure the gate's selectivity, not the exact
live trade sequence.

## Windows — both already spent

- **W1** 2026-08-24 → 2026-09-04, cached 2026 symbols (the BT40 window).
- **W2** 2024-01-01 → 2024-12-31, cached 2024 symbols.

Both windows have already been used for entry-side mining. Per the standing
rule that **a spent window can KILL but never BLESS**, this test can only
remove the gate, never authorize it. A passing result leaves the gate OFF and
awaiting a forward or unseen-window test.

## Anti-test (the primary criterion)

The gate is **not** a subset filter. `state.candidate_seen` is set only after a
successful confirmation, so a refusal leaves the day open and a later
confirmation can produce a trade that does not exist in the OFF arm. Drawing
random subsets of the OFF trades — the naive anti-test — is therefore invalid
here; that is exactly the error that produced BT38's false PASS (p=0.011) on
volume shelves.

The matched null instead **refuses at random with the same replacement
dynamics**: patch the confirmation point so that each otherwise-valid
confirmation is refused independently with probability `p`, where `p` is the
refusal rate the real gate exhibited on the same window, and replay the engine
so refusals create the same opportunity for later entries. 200 seeds.

`p_anti` = fraction of seeds whose mean gross%/trade is at least the gate's.
Runner: `research/backtests/bt41_price_volume_gate_ab.py`.

## Criteria — fixed before the runs

| # | Criterion | Threshold |
|---|---|---|
| G1 | anti-test | `p_anti < 0.05` on W1 **and** W2 |
| G2 | gross lift, ON − OFF | ≥ **+0.10 pp** per trade, pooled |
| G3 | sign consistency | lift > 0 in **both** windows |
| G4 | surviving sample | `n_on` ≥ 30 on W2 |

Gross, not net, is the lift measure: costs are ~0.21% real and a filter that
only cuts trade count reduces total cost without improving selection (the
frequency-dial lesson from BT30).

**Verdict rule.** Any criterion failing ⇒ **KILL**: the gate stays OFF, is not
re-enabled live, and no threshold or window variant follows on this data. All
criteria passing ⇒ **not killed**, gate still OFF, re-test forward before any
live enable. No thresholds may be retuned after seeing the numbers.

## Results

Filled in after the single pre-registered run. See below.
