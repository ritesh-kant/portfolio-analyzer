# Descriptive review of the 20 trades printed in the live-rule PDF

Reviewed 2026-10-05. This is an audit of existing trades, not a new strategy test. No strategy code, configuration, original CSV, or registry entry was changed. Suggestions below are hypotheses, not demonstrated improvements.

Source PDF: `/Users/ritesh/Downloads/Live rule — warrior_strict live rule · 3y_20261004.pdf` (31 pages). Source run: `bt17_trades_live_3y_20261004.csv`.

[Open the existing dashboard, grouped by exit reason](http://localhost:8898/run/bt17_trades_live_3y_20261004?group=exit_reason). Select `checkpoint_stop` and match symbol/date in the trade table.

## Scope and method

The PDF headline covers 464 trades, 2023-01-04 through 2026-09-23. Despite the name `3y`, these dates span four calendar years. The PDF actually prints only the first 20 of the run's 39 checkpoint-stop exits, from 2023-03-09 through 2024-06-13. It does not contain all 464 charts, or even all checkpoint exits. This is a sample selected by an outcome known only after entry.

Reviewed every printed chart and its available level table, matched all 20 to unique CSV rows, and inspected their cached one-minute bars. Every one of these 20 has cached bars. Five trades elsewhere in the full run have no matching cache file under the current symbol names; they remain in the summary statistics.

Real-cost numbers use the dashboard's `summary_fields` and the existing itemised MIS `calc_costs`, including its estimated 5 bps/side slippage. They are modelled costs, not historical contract-note reconciliations. Stressed numbers are the CSV's original net values, including the additional 40 bps/side stress. No flat cost deduction was substituted.

For the descriptive post-exit path, examine minutes strictly AFTER the exit minute through 15:15 IST. Record the first bar whose high reaches the ORIGINAL target and whose low reaches the ORIGINAL hard stop. This does not simulate continued holding: it ignores intervening EMA/MACD/support/trailing exits, opportunity substitution, execution latency, and capital constraints. It is not the return of a proposed rule. The exit minute is separately checked for stop/target ambiguity.

All these windows have already been examined or tuned on. The live paper record is the only untuned data. The replay does not reproduce the live three-strike guardrail or size ladder.

## Results in context

| Population | Trades | Net at real costs | Mean net/trade | Median net/trade | Net win rate | Mean after dropping top 5 net winners |
|---|---:|---:|---:|---:|---:|---:|
| Printed PDF trades | 20 | +₹3,891.34 | +₹194.57 | +₹201.98 | 75.0% | +₹112.97 |
| All checkpoint-stop exits | 39 | +₹7,077.98 | +₹181.49 | +₹206.91 | 74.4% | +₹132.62 |
| Complete original run | 464 | −₹37,029.11 | −₹79.80 | −₹143.86 | 31.5% | −₹90.36 |

The printed 20 have mean gross +0.620%, mean real-cost net +0.413%, and stressed net −₹3,896.29 (mean −₹194.81, median −₹196.31). The complete run has mean gross +0.0459%, mean real-cost net −0.1602%, and stressed net −₹218,021.92 (mean −₹469.87, median −₹536.83). Full-run gross profit is approximately ₹9,580 against ₹46,609 of modelled costs. Its real-cost mean is negative in every calendar-year slice: 2023 −₹79.25, 2024 −₹71.39, 2025 −₹110.31, 2026 −₹57.30.

The largest loss bucket is the original stop: 133 trades, net −₹49,660, mean −₹373.39, median −₹343.60. Targets contribute +₹29,633 on 61 trades. These outcome groups describe where P&L came from; their labels cannot be used as entry filters.

## Trade-by-trade assessment

R means entry minus original hard stop. Net amounts below are rounded to the nearest rupee. Later touches are observations of the subsequent tape, never information available at entry. A recommendation to investigate an exit does not mean it was wrong under the rule that was running.

| # | Trade, PDF page | Entry → exit | Real net | Observed path and assessment | Improvement to investigate |
|---|---|---|---:|---|---|
| 1 | CHENNPETRO, 2023-03-09, p2 | 10:25 ₹248.45 → 10:28 ₹248.95 | −₹2 | Only ₹100.50 gross against ₹102.99 costs. Original ₹250.65 target touched at 10:36; original stop later at 14:00. This was a cost-losing scratch followed by continuation. | Make net checkpoint economics visible before entry; investigate whether the stop lifts too early on a resistance approach. Do not interpret a positive sale price as a protected net profit. |
| 2 | STARHEALTH, 2023-03-16, p4 | 13:49 ₹544.85 → 14:37 ₹548.25 | +₹207 | Slow advance toward ₹549.95/550, then loss of momentum. No later ₹552.05 target touch; original ₹541.25 stop touched at 15:15. | Retain protection in the candidate design. This is a counterexample to extending every checkpoint trade. |
| 3 | IIFL, 2023-06-02, p6 | 10:50 ₹455.85 → 10:57 ₹458.60 | +₹197 | A brief pullback exited the position; ₹460.65 target touched at 11:02, five minutes later, without a later hard-stop touch by 15:15. | Candidate for a test that distinguishes an ordinary pullback from a failed breakout; preserve the original risk limit. |
| 4 | GPIL, 2023-06-19, p8 | 11:02 ₹98.40 → 11:05 ₹98.55 | −₹27 | Checkpoint stop offered only +0.152% gross. Target was never subsequently reached; original ₹98 stop touched at 11:18. The chart shows a congested range. | Address poor net reward available near resistance at entry; widening the exit alone would have exposed this trade to further loss. |
| 5 | HINDCOPPER, 2023-06-28, p10 | 09:43 ₹118.20 → 09:47 ₹118.85 | +₹172 | The opening push failed near the session high. No later ₹119.60 target touch; hard stop ₹117.50 touched at 10:03. | Keep prompt protection of a failed opening push. Do not impose a blanket minimum holding period. |
| 6 | SYRMA, 2023-07-12, p12 | 10:28 ₹475.45 → 10:34 ₹478.50 | +₹217 | The exit minute ranged ₹478.20–481.65 and closed at its high. Target ₹482.35 touched at 10:36. The checkpoint cushion was only ₹0.75. | Strong example motivating a volatility/structure-aware checkpoint test, or a test requiring a completed close beyond resistance before arming it. These are alternatives, not a combined rule. |
| 7 | GICRE, 2023-07-21, p14 | 11:13 ₹193.45 → 11:14 ₹194.05 | +₹52 | Exited after one minute for only 0.67R gross and about ₹52 net. Target ₹195.25 touched at 11:41; no later original-stop touch. | Investigate premature stop activation and very small net payoff relative to initial risk. A one-minute exit is not automatically a malfunction. |
| 8 | GMDCLTD, 2023-08-22, p16 | 11:34 ₹187.80 → 11:37 ₹189.40 | +₹322 | A three-minute trade; the later advance reached ₹190.20 at 11:50. The ₹0.30 cushion was roughly 0.32 times the preceding 14 one-minute true ranges' mean. | A useful continuation example for the checkpoint test. Do not use the printed completed entry candle's 12.2× volume retrospectively as an entry signal. |
| 9 | NH, 2023-09-14, p18 | 09:57 ₹1080.10 → 10:07 ₹1096.70 | +₹574 | Captured 1.33R gross. Target ₹1105 was not touched until 10:58, 51 minutes after exit; the intervening chart is choppy. | Treat this as a respectable capture. A later high alone does not justify more time or risk. |
| 10 | RAMCOCEM, 2023-09-26, p20 | 10:09 ₹925.55 → 10:17 ₹927.20 | −₹14 | ₹89.10 gross versus ₹103.06 costs. Target ₹931.55 touched only at 11:30; original stop touched at 11:51. | Prioritise net headroom over waiting longer. The checkpoint cushion was already about one preceding-minute ATR, so a uniformly wider buffer is not a convincing explanation here. |
| 11 | TRITURBINE, 2023-09-27, p22 | 10:57 ₹441.75 → 11:09 ₹442.95 | +₹33 | Session-high resistance ₹444.35 sits only 1R above entry. Exit captures 0.46R gross; target ₹446.95 touched at 11:29. | Compare prospective net checkpoint reward with initial risk before entry. Candidate for an entry-economics review and the checkpoint timing test. |
| 12 | DEEPAKFERT, 2023-10-16, p23 | 10:41 ₹667.40 → 10:46 ₹667.90 | −₹65 | Gross profit ₹37 against ₹101.78 costs. Resistance ₹670 and the two buffers leave a stop only 0.075% above entry. Target ₹671.90 touched at 11:10. | Clear example of a planned checkpoint exit that cannot cover costs. Assess whether to admit such trades or arm protection later; do not simply lift the stop to fee breakeven. |
| 13 | TEJASNET, 2023-10-25, p24 | 09:55 ₹863.55 → 09:56 ₹867.30 | +₹112 | The 09:56 minute has O ₹868.00, H ₹870.70, L ₹867.00, C ₹867.50: both the ₹869.45 target and ₹867.30 checkpoint stop were crossed. Stop-first is the engine's conservative convention. The next minute also touches target; hard stop touched at 10:06. | Audit execution ambiguity using finer data or disclose bounds. Do not change the fill ordering to target-first because the chart looks favourable. |
| 14 | RKFORGE, 2023-11-09, p25 | 10:51 ₹712.10 → 11:29 ₹717.80 | +₹296 | Failed near ₹720. Target ₹724.20 was not subsequently touched; hard stop ₹706.05 touched at 12:27 and 15:15 close was ₹686. | Preserve a way to exit a failed resistance test. Useful adverse example against which any more patient exit must be evaluated. |
| 15 | HSCL, 2023-12-05, p26 | 13:05 ₹285.50 → 13:26 ₹289.10 | +₹526 | Captured 1.29R gross. Neither target ₹291.10 nor original stop ₹282.70 was subsequently touched through 15:15; close ₹289.00. | No clear need to change this trade. Letting it run longer did not guarantee extra reward. |
| 16 | EIHOTEL, 2024-01-02, p27 | 11:26 ₹259.05 → 11:27 ₹259.20 | −₹74 | Only ₹28.95 gross against ₹103.04 costs. Original target ₹260.95 touched at 11:49; no later original-stop touch. | Strongest example of the two buffers consuming nearly all available headroom. Examine the cost feasibility of the checkpoint plan before entry. |
| 17 | JUBLINGREA, 2024-04-04, p28 | 12:08 ₹484.20 → 12:23 ₹488.30 | +₹270 | Intended checkpoint stop was ₹488.50; exit minute opened below it at ₹488.30. No later ₹495.60 target touch; original stop ₹478.50 touched at 13:04. | Keep the realistic gap-through-stop fill. This is a good protective exit, not evidence that every high-volume impulse deserves more room. |
| 18 | BLUESTARCO, 2024-04-08, p29 | 10:51 ₹1430.65 → 11:07 ₹1445.60 | +₹408 | Target ₹1454.25 touched three minutes later at 11:10, but original stop ₹1418.85 touched at 11:50. | Study a slightly more patient checkpoint while retaining the existing target. This supports neither unlimited holding nor removal of all protection. |
| 19 | JUBLINGREA, 2024-04-10, p30 | 11:58 ₹513.40 → 12:06 ₹518.40 | +₹342 | Sustained continuation: target ₹524.80 touched at 12:15; no later hard-stop touch; 15:15 close ₹541.50. | One of the clearest candidates for a more patient checkpoint. Contrast it with the same symbol on April 4; ticker identity and volume alone do not distinguish them. |
| 20 | DCMSHRIRAM, 2024-06-13, p31 | 12:07 ₹1036.60 → 12:18 ₹1046.80 | +₹346 | Target ₹1059.80 was not reached until 15:02, 164 minutes after exit; original stop was not subsequently touched. | Respect the captured gain and holding-time trade-off. A late target touch is weak evidence for relaxing an intraday momentum exit. |

## Mechanisms worth separating

### 1. A checkpoint profit can be a net loss by construction

For these trades the checkpoint is approximately resistance less 0.15%, and the checkpoint stop is another 0.15% below the checkpoint, with downward tick rounding at both steps. The two buffers consume about 0.30 percentage points of the resistance headroom before the roughly 0.21% modelled round-trip cost. This is accounting, not a newly fitted 0.51% entry threshold.

EIHOTEL is the clearest arithmetic: ₹260 resistance → ₹259.60 checkpoint → ₹259.20 stop, versus entry ₹259.05. Gross ₹28.95 on 193 shares cannot pay ₹103.04 costs. CHENNPETRO, GPIL, RAMCOCEM and DEEPAKFERT have the same problem to varying degrees. These five total −₹182.20 despite positive gross returns.

A useful reporting improvement is to show, before entry, the modelled net proceeds at checkpoint stop and at 2R, alongside original risk and deployed capital. If this becomes an entry gate, its effect must be tested on every eligible setup: excluding these five known losers after seeing their exits would be circular. Many trades with the same checkpoint geometry could instead reach target.

### 2. The fixed cushion is often smaller than ordinary minute movement

Across the printed sample, checkpoint-to-stop distance is a median 0.52 times the mean true range of the 14 completed one-minute bars immediately preceding entry. This is a descriptive diagnostic, not the engine's ATR and not a threshold to optimise. It explains why routine pullbacks can touch the stop, but says nothing alone about which stop maximises net returns.

Fourteen of 20 subsequently touch their original target; 11 do so within 30 minutes. TEJASNET additionally has an unresolved target/stop ordering within its exit minute. Five others reach the original stop without a later target touch; HSCL reaches neither. The sample was chosen by checkpoint exit, so these proportions are not a forecast for all entries or even all 39 checkpoint exits.

One coherent candidate for a future registered test is **changing only checkpoint arming from a high touch below resistance to a completed one-minute close above the recorded resistance**. Keep the original hard stop, target, trend exits, position size, and next-bar activation unchanged. This could avoid arming during an approach to resistance, but can surrender the protective profits in STARHEALTH, GPIL, HINDCOPPER, RKFORGE and JUBLINGREA April 4. It is a plausible mechanism, not a recommendation to deploy it. A volatility-scaled cushion is a different hypothesis; do not combine or sweep both.

### 3. Keep execution and chart timing honest

TEJASNET cannot be resolved by the five-minute chart, or even by the minute OHLC alone. JUBLINGREA April 4 demonstrates that a stop price is not a guaranteed fill price. NSE describes impact cost as dependent on order size and available order-book liquidity: [NSE impact-cost explanation](https://www.nseindia.com/static/products-services/indices-impact-cost). Retain the modelled costs and reconcile them with paper/live fills before calling them realistic for every symbol.

The chart's printed entry bucket may contain minutes AFTER entry. For example, the 11:55 candle for JUBLINGREA April 10 closes at 12:00, after its 11:58 entry. Its final volume and indicators cannot be entry-time predictors. Current `build_detail` also derives supplemental levels from the full five-minute bucket containing entry; use the CSV-recorded key levels for this audit. That is a display provenance concern; this review has not established look-ahead in the engine's recorded entry decisions. Missing early chart MACD/EMA values likewise do not establish that the engine lacked its own warmup history.

## Existing evidence limits the obvious changes

- [BT60: reverting to resistance-capped targets](../hypotheses/2026-10-05-revert-to-resistance-cap.md), 284 matched entries across 2024, 2025 and 2026 YTD: checkpoint −₹80.2/trade versus cap −₹84.5, median −₹135.3 versus −₹126.8, drop-top-5 −₹97.0 versus −₹99.8. Cap was worse on mean in every year and rejected by its criteria; paired p=0.26 is not proof of checkpoint superiority.
- [BT59: removing EMA9 exits](../hypotheses/2026-10-04-no-ema9-exit.md), 94 matched 2026 entries: real net mean −₹57.3 → −₹60.4, median −₹102.3 → −₹102.7, win rate 34.0% → 36.2%, drop-top-5 −₹101.0 → −₹104.4. Seven actual exits changed. More winners did not mean more money.
- [BT43: cost-aware breakeven](../hypotheses/2026-09-19-cost-aware-breakeven-stop.md) was already measured as approximately neutral, −₹0.09/trade, p=0.893. That experiment is not identical to a checkpoint entry gate, but it rules out claiming that moving a stop to cover fees automatically creates an edge.

The full system loses about ₹80/trade; the tested checkpoint-versus-cap difference is only about ₹4/trade on BT60's window. The larger research priority is whether entry opportunities have sufficient prospective net reward and directional follow-through. A few attractive continuation charts cannot establish this.

## What a subsequent test must establish

No candidate was run here. Before any altered-rule replay, allocate the next free BT number and pre-register one mechanism and falsification criteria. Compare the same window and eligible opportunity set against the frozen control; report replacement entries as well as paired trades. Require improvement in real-cost net per rupee deployed, mean and median, resilience after removing top winners, year-level consistency, and maintained downside control. Re-run the complete strategy with all its other exits, rather than replacing a sold trade with its later high. Historical results remain descriptive evidence on spent data; forward paper observations are needed before claiming a reliable improvement.

## Verification

The existing dashboard root and exact run URL both returned HTTP 200. Recomputed full-run figures agree with the PDF headline after rounding. All 20 identities match the first 20 checkpoint exits in CSV order, and their real-cost amounts agree with the PDF.

`pnpm bt:check` reports `0 unlabelled run(s)` but currently exits 1 because four pre-existing BT56 US CSVs are not listed (empty/unparseable): `bt56_trades_us_20260924_1002_{before,both,resistance_only,vwap_only}.csv`. No files in that unrelated work were modified. This review creates no new trade CSV or dashboard registration.
