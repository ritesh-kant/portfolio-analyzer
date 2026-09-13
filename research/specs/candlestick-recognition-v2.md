# Candlestick recognition v2/v3: research, executable rules, and audit

Research/access date: 11 September 2026 (v2), 12 September 2026 (v3).
Rule version now in code: `candles-v3-20260912`. The v3 revision is described
at the end of this document; everything above it still applies unless the v3
section says otherwise.

## Findings and scope

The supplied Warrior Trading *Candlestick Pattern Reference Chart* contains 21
named patterns. Its pictures are a vocabulary, not an executable specification:
they do not define lookbacks, numerical tolerances, timestamps, missing-data
handling, or entry confirmation. The implementation now covers every name on
that sheet on completed 1-minute and 5-minute candles, with one shared detector
for attention tags, persisted formation evidence, and chart annotations.

The important correction is **shape + preceding context**, not merely a red,
small, green sequence. A directional reversal requires the appropriate preceding
trend. A formed pattern is not a confirmed trade, and neither establishes a
profitable strategy. StockCharts explicitly separates prior trend, formation,
and subsequent confirmation in its [bullish reversal guidance](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-bullish-reversal-patterns).

This work changes local recognition, evidence capture, and chart alignment. It
does not place orders, rewrite historical ledger records, deploy the service,
change risk limits, or claim to eliminate false breakouts. The existing
two-stage attention strategy still needs its independent mover, volume, trend,
trigger, and risk checks. Recognition improvements are not a retrospective
explanation that every losing trade was invalid.

## Research method and source selection

Read the supplied PDF as text **and visually** so image-only labels were not
missed. Compared official TradingView pattern documentation for all 21 names
with TA-Lib's executable-recognition documentation, StockCharts' charting
education, IG's pattern explanations, and Zerodha Varsity's Indian-market
examples. These are primary documentation/education from the respective
implementers and publishers, not third-party pattern-list SEO summaries.

Source disagreements are preserved below. Their numerical choices are not
silently combined into a claim that there is one universally accepted detector.
The rule constants below are explicit **engineering policy**, not statistically
validated optima. They were not selected by optimizing today's losses.

## Shared quantitative contract

For candle `i`, define `B = abs(C - O)`, `R = H - L`,
`U = H - max(O, C)`, and `D = min(O, C) - L`.
Green means `C > O`; red means `C < O`. Equality is neither colour.

For a candidate of N candles, use the **ten candles strictly before its first
candle** as the body/range baseline. Freeze that baseline for the entire
formation; no candidate or future candle enters it. Let `MB` be mean body and
`MR` mean high–low range over those ten candles.

| Term | Implemented predicate |
| --- | --- |
| Long body | `B > MB` and `B / R >= 0.55` |
| Short body | `B < MB`; additional shape-specific ratios apply below |
| Doji | `B <= 0.10 * R` **and** `B <= 0.10 * MR` |
| Small non-doji shape | Positive body, generally `B / R <= 0.30`; actual doji classification takes priority |
| Long hammer shadow | Relevant shadow `>= 2 * B` |
| Very short opposing shadow | Relevant shadow `<= 0.10 * R` |
| Approximately equal tweezer extremum | Difference `<= 0.05 * mean(R)` over the last five baseline candles |

TA-Lib supplies a useful precedent for adaptive ten-candle body/range scales,
two-body long-shadow comparison, and five-candle equality tolerance. The
additional own-range ratios and frozen pre-formation baseline are our policy,
not TA-Lib parity. See [TA-Lib candle settings](https://ta-lib.org/api/candle-settings/).

**Local trend policy:** use the last five baseline closes, not candles within
the pattern. Up requires at least three of four close-to-close changes positive
and total advance greater than half those five candles' mean range. Down is
the exact negative mirror. Otherwise classify sideways. This is a transparent,
short-horizon implementation choice; it is not equivalent to TradingView's
SMA50 or SMA50/SMA200 choices. There is no published consensus that five closes
and this range threshold are optimal for NSE intraday bars.

## Complete 21-pattern specification

All directional rows require the stated prior trend in addition to the shape.
“Mirror” means reverse price inequalities, colours, and upper/lower shadows.
Long, short, doji, and tolerance refer to the quantitative contract above.

| PDF name / code identifier | Bars; context | Exact formation requirements and primary definition |
| --- | --- | --- |
| Hammer / `hammer` | 1; down | Short positive body, body/range at most .30, lower shadow at least 2 bodies, upper shadow at most .10 range. Either body colour. [TradingView](https://www.tradingview.com/support/solutions/43000583775-hammer-bullish/) |
| Inverted Hammer / `inverted_hammer` | 1; down | Short positive body at most .30 range; upper shadow at least 2 bodies, lower at most .10 range. Either colour. [TradingView](https://www.tradingview.com/support/solutions/43000583780-inverted-hammer-bullish/) |
| Dragonfly Doji / `dragonfly_doji` | 1; down | Doji, lower shadow at least .60 range, upper at most .10 range. Without downtrend, retain only neutral doji classification. [TradingView](https://www.tradingview.com/support/solutions/43000583768-dragonfly-doji-bullish/) |
| Bullish Spinning Top / `bullish_spinning_top` | 1; any | Green short body with `.10 < B/R <= .30`; both shadows longer than body. **Neutral indecision**, not bullish direction. [TradingView white spinning top](https://www.tradingview.com/support/solutions/43000583790-spinning-top-white/) |
| Bullish Engulfing / `bullish_engulfing` | 2; down | Short red then long green; second real body contains first and is strictly larger. Wick engulfment is unnecessary. Endpoint equality is allowed, equal-sized bodies are not. [TradingView](https://www.tradingview.com/support/solutions/43000583771-engulfing-bullish/) |
| Tweezer Bottom / `tweezer_bottom` | 2; down | Long red then green; lows equal within adaptive tolerance. [TradingView](https://www.tradingview.com/support/solutions/43000592709-tweezer-bottom-bullish/) |
| Morning Doji Star / `morning_doji_star` | 3; down | Morning Star rules below with middle candle meeting both doji thresholds. Emit specific doji-star name instead of duplicate generic star. [TradingView](https://www.tradingview.com/support/solutions/43000592704-morning-doji-star-bullish/) |
| Three White Soldiers / `three_white_soldiers` | 3; down | Three long green candles; strictly rising closes; each later open above prior open and no higher than prior close; upper shadows at most .10 range. [TradingView](https://www.tradingview.com/support/solutions/43000583793-three-white-soldiers-bullish/) |
| Morning Star / `morning_star` | 3; down | Long red, short middle with body at most .30 range and smaller than both neighbours, long green. Middle body entirely below first close; third open above middle body's top; third close strictly above first body's midpoint. [TradingView](https://www.tradingview.com/support/solutions/43000583787-morning-star-bullish/) |
| Rising Three / `rising_three` | 5; up | Long green, three short red candles with successively lower closes and full ranges contained inside first range, long green. Final open above previous close; final close above first close. Middle bodies smaller than both long bodies. [TradingView](https://www.tradingview.com/support/solutions/43000592711-rising-three-methods-bullish/) |
| Doji / `doji` | 1; any | Both doji thresholds. Neutral indecision. Zero-range or zero-volume candles are not evidence. Specialized dragonfly/gravestone names take priority only with matching context. [TradingView](https://www.tradingview.com/support/solutions/43000583767-doji/) |
| Hanging Man / `hanging_man` | 1; up | Hammer geometry, but after an advance; bearish reversal classification. [TradingView](https://www.tradingview.com/support/solutions/43000583776-hanging-man-bearish/) |
| Shooting Star / `shooting_star` | 1; up | Inverted-hammer geometry after an advance; bearish reversal classification. [TradingView](https://www.tradingview.com/support/solutions/43000583789-shooting-star-bearish/) |
| Gravestone Doji / `gravestone_doji` | 1; up | Doji, upper shadow at least .60 range, lower at most .10 range. Without uptrend, neutral doji only. [TradingView](https://www.tradingview.com/support/solutions/43000583773-gravestone-doji-bearish/) |
| Bearish Spinning Top / `bearish_spinning_top` | 1; any | Red mirror of green spinning top. **Neutral indecision** despite PDF colour label. [TradingView black spinning top](https://www.tradingview.com/support/solutions/43000583791-spinning-top-black/) |
| Bearish Engulfing / `bearish_engulfing` | 2; up | Short green then long red; second body fully contains first and is strictly larger. [TradingView](https://www.tradingview.com/support/solutions/43000583769-engulfing-bearish/) |
| Tweezer Top / `tweezer_top` | 2; up | Long green then red; highs equal within adaptive tolerance. [TradingView](https://www.tradingview.com/support/solutions/43000592710-tweezer-top-bearish/) |
| Evening Doji Star / `evening_doji_star` | 3; up | Evening Star rules with middle meeting both doji thresholds; specific name takes priority. [TradingView](https://www.tradingview.com/support/solutions/43000592705-evening-doji-star-bearish/) |
| Three Black Crows / `three_black_crows` | 3; up | Three long red candles; strictly falling closes; later opens below prior opens and no lower than prior closes; lower shadows at most .10 range. [TradingView](https://www.tradingview.com/support/solutions/43000583792-three-black-crows-bearish/) |
| Evening Star / `evening_star` | 3; up | Mirror of Morning Star: middle body entirely above first close, third open below middle body, final close strictly below first body's midpoint. [TradingView](https://www.tradingview.com/support/solutions/43000583772-evening-star-bearish/) |
| Falling Three / `falling_three` | 5; down | Mirror of Rising Three: three contained short green candles with rising closes, final long red opens below previous close and closes below first close. [TradingView](https://www.tradingview.com/support/solutions/43000592712-falling-three-methods-bearish/) |

## Disagreements resolved explicitly

1. **Stars and gaps.** Classical stars separate the middle body from its
   neighbours. Some intraday/24-hour interpretations relax gaps; IG explicitly
   describes this variation. This version does **not** silently use it. Both
   real-body gaps remain mandatory; high–low ranges need not gap. A relaxed
   variant would need a separately named/versioned rule and evaluation.
   [IG](https://www.ig.com/uk/ig-academy/the-basics-of-technical-analysis/candlestick-patterns)
2. **Star penetration.** TA-Lib defaults to 30% penetration of the first body;
   this implementation requires more than 50%. Zerodha's teaching example is
   stronger still, with the last close above the first open. These are distinct
   policies; the implemented midpoint rule follows the TradingView definition,
   not a claim of universal agreement.
   [TA-Lib Morning Star](https://ta-lib.org/functions/cdlmorningstar.html),
   [TA-Lib Evening Star](https://ta-lib.org/functions/cdleveningstar.html),
   [Zerodha Varsity](https://zerodha.com/varsity/chapter/multiple-candlestick-patterns-part-3/)
3. **Trend is a separate obligation.** Shape-oriented library functions are not
   a substitute for trend validation. Our frame API enforces it; raw `is_*`
   helpers deliberately remain geometry-only for compatibility. Do not use
   those helpers directly to authorize attention.
   [TA-Lib Hammer](https://ta-lib.org/functions/cdlhammer.html)
4. **Three Methods means five candles.** Despite the PDF's grouped heading,
   the canonical formation is one impulse, three retracement candles, and one
   resumption. This implementation requires full-range containment, stricter
   than variants permitting partial overlap or body-only containment. It does
   not require the final close beyond the first high/low, only beyond its close.
   [TA-Lib Rising/Falling Three Methods](https://ta-lib.org/functions/cdlrisefall3methods.html)
5. **Soldiers/crows variations.** Use three formation candles and separately
   measured prior trend. This is not TA-Lib's extra preceding-white-candle
   variant for crows. Open placement and wick ratios are deliberately strict.
   [TA-Lib Three Black Crows](https://ta-lib.org/functions/cdl3blackcrows.html),
   [TA-Lib Three White Soldiers](https://ta-lib.org/functions/cdl3whitesoldiers.html)
6. **Indecision is not bullishness.** Preserve the PDF's two spinning-top names
   for recognizability, but store `direction=neutral`, `kind=indecision` for both.
   The current attention strategy may watch neutral formations; this does not
   make them bullish entry signals.
   [StockCharts introduction](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/introduction-to-candlesticks)

## Time, data quality, and evidence contract

- Candle timestamps are **start times**, not formation/decision times. A 13:10
  five-minute candle becomes complete at 13:15. Persist `formed_at` separately.
- Require ten valid baseline candles plus the full formation, all consecutive,
  unique, ascending, clock-aligned, and within the same calendar session date.
  Production supplies exchange-session frames; this detector does not provide
  a holiday calendar or authorize out-of-session trading.
- Missing minutes are not forward-filled into pretend candles. A 5m aggregate
  requires all five distinct constituent minutes in its clock bucket. Missing
  buckets also break the detector's baseline/formation continuity.
- Reject non-finite, non-positive, zero-range, zero-volume, or inconsistent
  OHLCV in the recognition window. Insufficient or invalid evidence means no
  label, not a guessed label.
- Only closed bars may reach recognition. The optional `closed_through` cutoff
  additionally filters full historical frames. Without it, callers promise
  already-closed data. Tests cover future-data exclusion and prefix invariance.
- Save exact pattern OHLCV, timeframe, first/last starts, formation completion,
  preceding closes/trend, adaptive scales, tolerance, direction, kind, status,
  and rule version. Pattern `confirmation` is the formation high for bullish
  patterns or low for bearish ones: a reference level, not evidence of a later
  fill. The existing attention strategy can use a different 1m trigger.
- Save promotion-time mover/RVOL and thresholds separately from candidate-time
  5m EMA/VWAP and 1m volume/close-position evidence. Never explain an earlier
  promotion using a later RVOL measurement.
- Frontend five-minute bars use the same clock buckets. Pattern overlays require
  exact matching timestamps and timeframe. Fill markers use the containing
  candle, never the nearest future candle.
- Historical records without snapshots remain explicitly incomplete. No blanket
  “all gates passed” assertion or retroactive evidence fabrication.

## SAREGAMA: what the stored evidence actually showed

Read-only inspection of 11 September 2026 paper records found attention labelled
11:42 IST (the bar start; completion at 11:43) with legacy `morning_star`
context, day change approximately 1.6035%
and RVOL approximately 1.5255. The eventual trade was a separate
`attention_1m_confirmation` signal, not a Morning Star entry at 13:14.

The old five-minute aggregation/loose tag path could label this sequence:

| Bar start IST | Open | High | Low | Close | Volume |
| --- | ---: | ---: | ---: | ---: | ---: |
| 11:25 | 512.85 | 513.25 | 512.05 | 512.10 | 5,148 |
| 11:30 | 512.25 | 512.80 | 512.20 | 512.35 | 3,701 |
| 11:35 | 512.55 | 514.15 | 512.25 | 514.15 | 15,806 |

This is **not a v2 Morning Star**. The middle body starts above 512.10, so it
does not gap below the first body's bottom. The preceding closes were rising
(507.95 → 508.70 → 512.55 for the immediately preceding three bars), not a
downtrend. Independently, the 11:25 legacy bucket is missing the 11:26 source
minute and is excluded by the new completeness check. The historical sequence
is retained as a negative regression, not used to fit profitability thresholds.

The 13:13 1m confirmation candle was O514.35/H515.75/L514.35/C515.75,
volume 7,609, with recorded volume ratio approximately 9.094. The later paper
fill was 515.75 at 13:14:06 IST; exit was 514.40 on the 13:18 bar, labelled
`false_break`. The defended 1m level was 515.00, while the displayed 5m
resistance was approximately 515.875. These are different levels. Correcting
pattern names does not, by itself, establish whether that separate breakout
rule has an edge or whether all six trades would disappear in a fresh replay.

## Implementation and reproducible verification

- `apps/signal-engine/src/momentum_trader/candles.py`: shared 21-pattern contract.
- `engine.py` / `ledger.py`: attention/candidate evidence snapshots; bearish
  patterns cannot authorize a long through a bullish shape alias.
- `apps/web/lib/momentum-bars.ts`: complete clock-bucket aggregation and
  containing-candle marker placement.
- `apps/web/app/momentum/page.tsx`: measured versus unavailable historical
  evidence; expandable exact formation candle tables.
- `research/backtests/bt26_candlestick_pattern_replay.py`: retains its original
  three-bullish-pattern experiment; newly recognized bearish/neutral patterns
  must not accidentally become long entries. Stop-gap fills are conservative.

Run from `apps/signal-engine`:

```sh
uv run pytest tests/momentum_trader/ -q
uv run ruff check src/momentum_trader/candles.py tests/momentum_trader/test_candlestick_recognition.py
```

Run from repository root:

```sh
node --test apps/web/lib/momentum-bars.test.mjs
pnpm --filter web typecheck
```

Tests include positive fixtures for all 21 patterns on both timeframes,
wrong/sideways trend rejection, insufficient history, price-scale invariance,
adversarial geometry, invalid/missing data, exact evidence, no-lookahead,
SAREGAMA rejection, scanner integration, persistence, and chart alignment.
Synthetic positive fixtures establish agreement with the declared rules, not
precision/recall against an independently labelled market dataset.

Local verification on 11 September: 319 momentum-trader tests passed, five
frontend clock/marker tests passed, frontend TypeScript check passed, and Ruff
checks passed for the detector and new recognition/replay tests.

The broader signal-engine test collection is separately blocked by eleven
unrelated modules importing the unavailable `quant` package; the passing count
above is the complete momentum-trader suite, not the whole repository suite.

## Limitations and next validation boundary

This is a strict recognition implementation, not proof of investment returns.
Daily-candle educational definitions have been translated to 1m/5m bars;
intraday transfer is an unvalidated hypothesis. Strict gaps and uninterrupted
history will intentionally reduce matches, especially in sparse data. Zero
volume/range rejection also reduces coverage. Report that coverage loss rather
than relaxing rules invisibly.

Before relying on results for real capital: independently label market examples
and near-misses, measure false positives and missed patterns by timeframe,
compare rule variants on data not used for selection, then evaluate the complete
strategy out of sample with costs, slippage, gap handling, and drawdowns. Keep
paper forward evidence versioned. Neither this research nor passing software
tests justifies a win-rate claim, production-performance promise, or automatic
live-trading rollout.

The corrected aggregation and recognition intentionally change fresh replay
results. Reproducing a previously published experiment requires its original
code revision and data, not relabelling its historical results as v2 results.


---

## v3 revision (12 September 2026): source-by-source re-check

A second research pass compared every rule in `candles.py` against the
**executable** TA-Lib C source (`ta_CDL*.c`, not the summary pages), Bulkowski's
identification guidelines on thepatternsite.com, TradingView's indicator help,
StockCharts ChartSchool, and Zerodha Varsity. Six rules changed. None of the
constants was chosen by looking at outcomes.

| Rule | What the sources say | v2 | v3 |
| --- | --- | --- | --- |
| Hammer / hanging man position | TA-Lib CDLHAMMER: `min(o,c) <= prev.low + Near`; CDLHANGINGMAN: `min(o,c) >= prev.high - Near`. Text sources: "at the low/high of the move". | no position test | **added, TA-Lib exact** |
| Inverted hammer / shooting star position | TA-Lib requires a *real-body gap* below/above the prior body (`max(o,c) < min(prev.o,prev.c)` / mirror). Bulkowski's one-line shooting star needs no gap; Warrior describes "a push to new highs, rejected". | none | **added: TA-Lib gap relaxed by TA-Lib's own Near tolerance** (`body top <= prior body bottom + Near` / mirror). Intraday 5m bars rarely gap; the relaxation is stated, not silent. |
| Three Methods containment | TA-Lib: `min/max(o,c)` of candles 2-4 inside candle 1's **high-low**. Bulkowski: "close within the high-low range of the first candle". TradingView: "bodies inside the range of the first candle". | full high-low of candles 2-4 inside | **bodies inside; wicks may poke out** (v2 was stricter than every source) |
| Soldiers / crows opens | TA-Lib: `open > prev.open` and `open <= prev.close + Near`. Bulkowski: "opening within the prior candle's body". | `open <= prev.close`, no slack | **Near slack added** |
| Soldiers / crows vs Advance Block | TA-Lib Far test: each body `> prev body - Far`, to separate the pattern from Advance Block. | none | **added** (`>=`, Far = .60 x mean range(5)) |
| Spinning top body cap | TA-Lib / Bulkowski / TradingView: small body, both shadows longer than the body. Both shadows > body already implies B/R < 1/3. | extra own cap B/R <= .30 | **cap removed** |

Deliberate differences kept, with the reason:

* **"Very short" shadow is measured against the candle's own range** (<= .10 R),
  not TA-Lib's .10 x mean range(10). Nison-derived text ("closes at or near the
  high") describes the shadow relative to the candle; the adaptive average is a
  TA-Lib implementation convenience.
* **Long shadow = 2 x body** (Bulkowski, StockCharts, Zerodha). TA-Lib's default
  ShadowLong is only 1 x body.
* **Star penetration = first-body midpoint** (TradingView, Bulkowski "at least
  midway"); TA-Lib defaults to 30%.
* **Third star candle must be long** (TradingView, Bulkowski "tall"); TA-Lib only
  requires it not to be short.
* **Three black crows need no explicit white candle before them** (TA-Lib does);
  the separately measured prior up trend carries that role.
* **Tweezers keep TradingView's colours** (red then green / green then red);
  Bulkowski accepts any colours, which removes the directional reading.
* **Trend policy unchanged** (five closes). TA-Lib tests no trend; TradingView
  uses SMA50; Bulkowski judges visually. Ours is explicit and short-horizon.

Near and Far are TA-Lib's settings verbatim: `.20` and `.60` of the mean
high-low range over the last five baseline candles. Equal stays `.05`.

### Sources consulted for v3 (all read 12 September 2026)

* TA-Lib C source: `ta_CDLHAMMER.c`, `ta_CDLHANGINGMAN.c`, `ta_CDLINVERTEDHAMMER.c`,
  `ta_CDLSHOOTINGSTAR.c`, `ta_CDLSPINNINGTOP.c`, `ta_CDLDRAGONFLYDOJI.c`,
  `ta_CDLENGULFING.c`, `ta_CDLMORNINGSTAR.c`, `ta_CDLMORNINGDOJISTAR.c`,
  `ta_CDLEVENINGSTAR.c`, `ta_CDL3WHITESOLDIERS.c`, `ta_CDL3BLACKCROWS.c`,
  `ta_CDLRISEFALL3METHODS.c`, `ta_CDLMATCHINGLOW.c`; and
  [candle settings](https://ta-lib.org/api/candle-settings/).
* Bulkowski, thepatternsite.com: Hammer, HammerInv, HangingMan, ShootingStar,
  Dragonfly, Gravestone, SpinTopWhite, SpinTopBlack, BullEngulfing,
  BearEngulfing, TweezersBottom, TweezersTop, MorningStar, MorningDojiStar,
  EveningStar, EveningDojiStar, ThreeWhiteSoldiers, ThreeBlackCrows,
  Rising3Methods, Falling3Methods.
* TradingView help: Hammer, Morning Star, Tweezer Bottom, Spinning Top White,
  Engulfing Bullish, Rising Three Methods.
* StockCharts, *Introduction to Candlesticks*; Zerodha Varsity, single
  candlestick patterns part 3 (shooting star: "at least twice the length of the
  real body").
* Warrior Trading, *How to Trade the Shooting Star Pattern* (their sheet is the
  one being implemented).

### What Bulkowski's statistics say about these names

Independent of our own census, Bulkowski's daily-bar study (4.7 million candle
lines) reports that many names in the sheet do not behave as their theory says:
hanging man acts as a bullish continuation 59% of the time, inverted hammer as a
bearish continuation 65%, tweezers ~random (52% / 56% the wrong way), dragonfly
and gravestone doji 50-51%, shooting star 59%. The three-candle stars and
soldiers/crows are his best performers (72-82%) - and are exactly the ones that
almost never form on 5-minute bars. Our BT28 census reached the same shape of
conclusion on NSE intraday data.

### Effect of v3 on detections

Same 30 audit sessions as the v2 report (5 symbols x 6 days): 488 -> 470 labels.
hammer 10 -> 5, hanging man 10 -> 3, inverted hammer 12 -> 7, shooting star
7 -> 2 (position rule); spinning tops +4 (cap removed); everything else equal.
Near misses 507 -> 529. Tests: 319 -> 323 passing, new cases for every rule
that changed.

## Strength — a size number alongside the name (13 Sep 2026)

`PatternMatch.strength` = range of the formation's **confirming (last) candle**
divided by the mean range of the ten candles before the formation. One
definition works across all 21 patterns because the last candle is the
confirming candle in every one of them. `candles.STRENGTH_WEAK_BELOW = 0.75`
is the suggested display threshold; `evidence["signal_candle_range"]` carries
the numerator so the value is auditable.

**Why it exists.** Every published definition of a doji, hammer or spinning top
is a ratio test against the candle's *own* range. A candle spanning ₹0.70 on a
₹615 stock passes "body ≤ 10% of range" exactly as well as one spanning ₹7.00.
The name is right; the ratio simply cannot say whether the candle was big enough
to matter. Measured over the 5,045 formations found at real trade entries in
BT29: 74.7% are below 1.0× and 12.9% span less than one 0.21% round trip.

**What it deliberately does not do.** It gates nothing. No detection is
suppressed, no name changes, and `PATTERN_RULES_VERSION` stays
`candles-v3-20260912` — re-running BT28 or BT29 reproduces the identical set of
matches (verified on 300 trade entries: zero differences). TA-Lib has no size
floor either; inventing one inside the detector would silently redefine the
patterns. Consumers decide: the momentum trade chart and both audit reports draw
sub-threshold formations faint with a dashed box and print the multiple in the
label.

Splitting BT29's clean pool at 1× gives +0.026 pp in favour of the larger
formations (t = +0.50). Real in direction, far too small to trade. This field
improves the annotation; it is not an edge and must not be sold as one.
