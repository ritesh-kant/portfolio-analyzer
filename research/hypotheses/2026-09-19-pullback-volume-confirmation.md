# Breakout-versus-pullback volume confirmation

Registered 2026-09-19 before implementation or outcome measurement. Exploratory;
no new deployed strategy arm and no change to the enabled one-minute slope gate.

## Mechanism and fixed rule

A four-bar volume slope can be negative across a strong push, quiet pause and
renewed breakout. Test whether recognising the phases improves entry selection.
Reuse the existing micro-pullback detector: two green push candles, one or two
red/doji pause candles, then a break above the pause. For a recognised pattern,
replace the four-bar price/volume slope check with all of:

- Mean pause volume <= 0.80 times mean push volume (existing FLAG_VOL_RATIO).
- Breakout volume strictly greater than mean pause volume.
- Breakout closes strictly above the pause high.

Use the detector's existing shortest-pause-first choice. Do not search a longer
pause after a volume failure. Retain the existing strong green close and
above-average one-minute volume requirements, entry trigger/stop, resistance,
five-minute EMA/VWAP context, fills, sizing and exits. If there is no recognised
pullback, keep the current four-bar slope check. All inputs are completed,
consecutive one-minute candles from the current NSE session. No five-minute
volume check. No thresholds will be retuned after this run.

## Data, timing and scope

- Recorded September 18 trades: diagnostic only, already spent and selected by
  the old strategy. Keep their recorded costs and disclose replacement bias.
- Complete cached symbol-sessions, August 24–September 4, 2026, all available
  2026 cache symbols with prior history. September 4 is the latest cache date.
  This window is already research-exposed, NOT an unseen holdout.
- Replay the production engine minute by minute, including replacement entries;
  compare both existing strategy configurations independently. Per-symbol
  simulation does not reproduce shared position caps, scanner risk halts or
  tick ordering. Report this as a symbol-session replay, not a portfolio test.
- Prior sessions only for volume profiles and indicator warmup. Discard missing
  or incomplete evaluation sessions and report coverage. No network candle
  downloads, broker actions or Mongo writes.

## Evaluation fixed before results

No established effect-size estimate. Minimum worthwhile observed improvement:
positive net INR per evaluated symbol-session and positive improvement after
an additional 5 bps slippage per side, using identical sizing/costs in both
comparisons. Require at least 30 differing trade decisions across at least 5
session dates before claiming even provisional support. A negative net or
stressed-net improvement rejects this version on the exploratory sample;
insufficient changes is inconclusive. Either outcome leaves active entry rules
unchanged. Historical success alone does not authorize claiming a durable edge.

Record rejected decision evidence and offline 5/15/30-minute close returns;
these are opportunity diagnostics, never simulated trade P&L. Freeze this rule
for any subsequent forward evaluation; do not schedule a new trading arm.

## Results

Ran the fixed rule once after correcting a data-loading defect: parquet uses
microsecond timestamps, whereas pandas `date_range` uses nanoseconds;
`DatetimeIndex.equals` incorrectly labelled all 462 sessions incomplete. The
check now compares timestamp values, with a regression test. This correction
changed coverage only; it did not change any trading rule or threshold.

219 cached symbols inspected; 74 have data within the window, covering **462
complete symbol-sessions across ten session dates**. No partial available
sessions were admitted. Symbols without cached data in this period are absent,
so this is not a full historical NSE universe. Existing BT17 eligibility gates
(including prior history, price, turnover and a reachable gain threshold) run
inside those sessions; not all 462 sessions generate an entry opportunity.

| Strategy / rule | Trades | Gross INR | Costs INR | Net INR | Net with extra 5 bps/side |
|---|---:|---:|---:|---:|---:|
| attention_1m_merged / current slope | 33 | +2,423.45 | 3,096.85 | −673.40 | −2,176.56 |
| attention_1m_merged / pullback alternative | 33 | +1,901.25 | 3,076.07 | −1,174.82 | −2,667.93 |
| warrior_strict / current slope | 2 | −39.75 | 200.58 | −240.33 | −337.69 |
| warrior_strict / pullback alternative | 4 | +495.15 | 396.13 | +99.02 | −93.25 |

Main-strategy net delta **−₹501.42**, or −₹1.0853 per cached complete
symbol-session; stressed delta −₹491.37. Eleven otherwise-valid confirmation
decisions differed across eight dates (nine refused, two rescued). The two
current-only filled entries earned +₹144.44 net; the two alternative-only
entries lost −₹356.98. Replacement entries therefore matter: the count stayed
at 33 but the trades changed. **Reject this version for the main strategy.**

Strict-strategy net delta +₹339.35; stressed delta +₹244.44. Only two
confirmation decisions on two dates changed, below the registered 30 / 5
minimum. **Inconclusive for strict; do not enable.**

On the separately recorded September 18 ledger, the current slope retains 22
trades (net −₹826.46) and the alternative retains 20 (net −₹683.10). The apparent
₹143.36 improvement is entirely CHOICEIN (−₹108.10) and MEDANTA (−₹35.26), both
rejected for heavy-volume pauses. No missing stored charts. This diagnostic
does not model replacements and cannot outweigh the broader negative result.

## Implementation and reproducibility

- Research runner: `research/backtests/bt40_pullback_volume.py`; invoke with
  `apps/signal-engine/.venv/bin/python research/backtests/bt40_pullback_volume.py --jobs 4`.
- Raw trades, per-decision evidence and offline forward returns:
  `research/backtests/bt40_pullback_volume_results.json`.
- Fixed risk ₹500/trade, notional cap ₹50,000, existing per-strategy defaults,
  production cost function. `future_trigger` OHLC replay fills on a later
  completed bar's qualifying close, not a hypothetical touch of its high;
  tick chronology is unavailable. The configured 0.03% resting-fill parameter
  does not apply to that future-trigger close-price fill path.
- Uses the current checked-out engine, including pre-existing stop/target
  priority and false-break changes. These are held equal in both comparisons;
  this is not a reproduction of a deployed historical engine snapshot.
- New `volume_confirmation.py` supplies descriptive evidence. Volume-related
  rejections (including reclaim entries) now persist both slopes, the push /
  pause / breakout measurements, and up to five input candles in the existing
  rejection ledger. No new strategy, order, database collection or scheduled
  job is created. This diagnostic change still requires normal deployment to
  start appearing in runtime logs.
- The alternative only exists in the research runner's scoped adapter. Both
  configured strategies retain the existing one-minute slope gate. Five-minute
  volume filtering remains absent. No deployment was performed.

## Decision

Do not replace the active rule. Keep the diagnostic instrumentation and the
fixed research implementation for audit. All historical windows used here were
already exposed; none of these numbers is an unseen holdout result. No
threshold retuning or historical rescue variant follows this result.
