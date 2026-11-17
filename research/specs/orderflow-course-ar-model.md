# Orderflow course ("AR Model") — extracted rules

Source: *Complete Orderflow Trading Course (AR Model)*, Christopher Creamer ("Thraxx Trades"), YouTube
`PkzTQ2_0vpw`, 4 h 25 min, ~4 months old as of 2026-11-12.

**How this was extracted (full pass, 2026-11-12):** the whole auto-generated transcript (2,024 timestamped lines)
was read, except the closing psychology section (3:58–4:25), which was keyword-scanned for concrete risk numbers.
About 55 frames were captured at the moments he points at a chart or slide, with zoomed reads where numbers mattered.
Timestamps `[h:mm]` point into the video.

Each rule is tagged by **source**: `[S]` said out loud, `[V]` only visible on screen, `[S+V]` both. It is also tagged
by **testability**: `[M]` mechanical as stated, `[P]` partly (needs a definition he doesn't give), `[D]`
discretionary.

---

## What "AR" is

**AR = "Auction Resolution Model"** `[V 0:06 title slide]`. A four-step framework, in this order:

**1 Environment & Context → 2 Location → 3 Path → 4 Confirmation**, then execute. A "no" at any step = pass.
`[S+V 0:06–0:16, 0:59, 1:09]`

| | |
|---|---|
| Instruments | **NQ/MNQ** (Nasdaq-100 futures) and **GC/MGC** (gold futures) `[S+V 0:06]` |
| Session | **Only the New York AM session: the first ~90 minutes after the 09:30 ET open** `[S 0:14]` |
| Platforms | ATAS and DeepCharts (footprint, execution), TradingView (charting), Tanuki Trade (gamma) `[S+V 0:06]` |
| Timeframes | Structure on 4 h / 1 h / 15 m / 5 m; ranges on 5 m–1 h; execution on **5 m footprint candles** `[S 0:07, 3:19]` |

---

## Step 1 — Environment & context

### 1a. Market structure and bias `[S 0:07, 1:20–1:21, 1:35]` **[P]**
- Plain structure on 4 h, 1 h, 15 m and 5 m: higher highs/higher lows = bullish, lower highs/lower lows = bearish,
  or sideways.
- **Bias = the higher-timeframe direction.** Example `[S+V 1:35]`: 1 h "lower high, lower high, lower high" → short
  bias → the target ("draw") is the prior lows.
- **Never trade against the bias** at any level (fib zone, Asia-range level, gamma level). `[S 1:21, 1:57, 2:04]`

### 1b. Three environments `[S+V 0:59–1:02]` **[P]**

| Environment | What it looks like |
|---|---|
| **Initiative expansion** (trend) | Higher-timeframe alignment, breaks hold and extend, pullbacks resolve, acceptance outside the prior range, follow-through after imbalance |
| **Balance rotation** (range) | Repeated rejection at extremes, mean reversion, failed breakouts, overlapping structure, both sides active |
| **Low participation / compression** (chop) | Tight overlapping candles, repeated failed micro-breakouts, imbalances with no continuation, slow tape, thin liquidity |

### 1c. Model ↔ environment fit `[S+V 1:03–1:08, slide at 1:03:30]` **[M once 1b is defined]**

| Model | Expansion | Rotation | Compression |
|---|---|---|---|
| Continuation | **Strong** | Weak (breakouts fail) | Poor ("frequent small stop-outs") |
| Reversal | Selective: only at higher-TF extremes, after an extended move, with clear acceptance failure | **Strong at range extremes** | Weak |
| Scalp / micro | Moderate | Moderate (capped by range width) | Worst ("high frequency + low displacement = death by friction") |

### 1d. No-trade rule `[S+V 1:09, slide at 1:09:20]` **[P]**
Pass when **2–3** of these hold: several failed breakouts in a row; overlapping structure with no expansion; repeated
return to value; no higher-timeframe alignment; no clean location; can't define a continuation path.

### 1e. Gamma regime `[S+V 2:08–2:45]` **[M if GEX data exists]**
- **Positive gamma** (price above the gamma flip / "HVL"): dealers hedge against the move → expect compression,
  pinning, mean reversion. **Don't take breakouts; fade failed breakouts; take profit quickly and trail tight.**
  `[S 2:37–2:41]`
- **Negative gamma** (below the flip): hedging adds to moves → expect expansion. **Hold winners toward the targets.**
  `[S 2:41–2:43]` (Apr 7 example: ~200 points in 15 minutes.)
- Levels: **call wall** (C1), **put wall** (P1), **gamma flip / HVL**. "Stacked" levels (several option metrics at
  one strike) → slower, two-sided "decision-style" trade. None of them is automatic support/resistance.
  `[S+V 2:21–2:26]`
- **If gamma and order flow disagree, order flow wins.** `[S+V 2:28, slide at 2:28:30]`

## Step 2 — Location

### 2a. Range + value area + Fibonacci zone `[S+V 0:08–0:10, 1:14–1:43]`
1. **Range** = the most significant swing low and swing high on 5 m–1 h. **[P]** He never defines
   "significant". `[V]`: in every example it's the extreme of the latest clear leg.
2. **Fixed-range volume profile** from that swing low to swing high → value area high (VAH), POC, value area low
   (VAL). **Above VAH = premium, below VAL = discount.** **[M]** `[S+V]`
3. **Fibonacci levels:** 0.5, 0.62, **0.705, 0.788, 0.886**, 1.1. **[M]** `[S+V slide 1:20]`
4. **Zone = 0.705–0.886, and it has to sit outside the value area:** below VAL for longs, above VAH for shorts.
   `[S 0:09–0:10, V 0:09 chart]`
5. **Draw one side only, in the bias direction.** Bullish → swing low→high, buy the lower zone. Bearish → swing
   high→low, sell the upper zone. `[S 1:23]` **[D bias]**
6. **Invalidation: a candle close beyond 0.886** on the working timeframe (5 m / 15 m / 1 h) cancels the reversal
   idea **and flips the expectation to continuation toward 1.1.** `[S+V slide 1:20]` **[M]**
7. **What the chart examples show `[V 1:35, 1:37, 1:40, 1:42, 3:43]`:** in the replay days he picks (Nov 20,
   Oct 14, and others), price **reacted at the near edge of the zone, around 0.62–0.705**, often without reaching
   0.788. He says the same at 1:37 ("came into premium in the 62 … our zone at the first level and we dropped").
   So in practice the trigger area is the zone's *first* touch.
8. **Target = the opposite liquidity:** the swing low/high the range was drawn from, labelled **"DOL"** (draw on
   liquidity) on his charts. `[V 1:36, 2:43 plan drawing]` **[M]**
9. A private indicator ("darkpool decoder") narrows the zone to three internal levels. A close through those
   levels invalidates them. Not reproducible. `[S+V 1:24–1:27]` Details below.

### 2a′. The "Dark Pool Decoder" — what the frames show `[S+V 1:24:00–1:27:30, V 1:34:40, 1:42]` (read 2026-11-12)

His slide, "The Dark Pool Decoder Tool and Zone Logic", says:
- *Purpose:* find a focus zone **inside the 0.705–0.886 region** and narrow attention to **three internal levels**.
- *How he uses it:* draw it from swing high to swing low for a premium zone, or from swing low to swing high for a
  discount zone. The decoder levels are reaction zones. A close through them invalidates them.
- *Interpretation:* if the zone holds, keep expecting a reversal. A close through it means expect continuation.
- At the bottom of the slide: "The tool does not predict. It helps structure expectations."

Out loud he adds: he uses it because on a large range the 0.705–0.886 band can be 100–200 NQ points wide. He calls
it optional ("you don't need this, but I love it"). He uses it only in the bias direction. It is better when the zone
sits beyond a previous swing high (for shorts) or swing low (for longs).

**What the indicator draws.** A scan of the whole video (every 20 s, then every 4 s near hits, looking for its
magenta colour) found it at **1:25–1:28** (two worked examples on a slide) and **1:34:40** and **1:41:56–1:42:48**
(used live on NQ charts). Each time he drags it from one swing to the other, like a Fib tool. It draws a box that
starts at the second swing and runs to the right. A **darker band** sits on the near side and a **brighter band**
(≈ 27–33 % of the box) sits on the far side.

The **three levels** are the near edge, the line where the shading changes, and the far edge. At 1:34:40 his drawing
shows a grab handle on each of the three. He says "if you ever see my other charts, you may see boxes on it … this is
where it's coming from."

The levels were measured pixel by pixel on the 1080p frames against the Fib labels on the same chart (±0.005 of
the leg). The fractions are retracements measured from the second swing, the same scale as his Fib tool:

| Where | Side | Leg | Near edge | Shade change | Far edge | Box ÷ leg |
|---|---|---|---|---|---|---|
| 1:25:20 slide | discount | 25,536 → 26,051 (515 pts) | 0.728 (25,676) | 0.832 (25,622) | 0.870 (25,603) | 14 % |
| 1:27:05 slide | premium | 26,051 → ≈ 25,654 (≈ 397, low read off his arrow) | ≈ 0.60 (25,891) | ≈ 0.74 (25,947) | ≈ 0.80 (25,972) | 20 % |
| 1:34:40, NQ 28 Jan → 1 Feb 2026 | premium | 26,349 → 25,181 (1,168) | 0.619 (25,904) | 0.749 (26,055) | 0.812 (26,129) | 19 % |
| 1:42:00, NQ 11 → 16 Sep 2025 | discount | 24,327 → 24,887 (560) | 0.544 (24,582) | 0.707 (24,491) | 0.774 (24,453) | 23 % |
| 1:42:32, NQ 4 → 5 Sep 2025 | discount | 23,899 → 24,391 (492) | 0.625 (24,084) | 0.763 (24,016) | 0.818 (23,988) | 19 % |
| **median of 5** | | | **0.62** | **0.75** | **0.81** | **19 %** |

A sixth use, at 1:42:12 (3 September 2025), has no Fib on screen, so it could not be measured.

**What this shows:**
- **Contrary to his slide,** the box usually does **not** sit inside 0.705–0.886. In 4 of 5 examples its near edge
  is at 0.54–0.63, which is above 0.705 for a short and below it for a long. Its far edge is at 0.77–0.82, short of
  0.886. Only the 1:25 slide example sits inside. This matches §2a item 7: price usually reacts around 0.62–0.705.
- **It is not a fixed Fib ratio.** The near edge ranges 0.54–0.73 and the box is 14–23 % of the leg, and both vary
  far more than the ±0.005 reading error. Some other input moves it (volume and volatility are both guesses; the
  name suggests volume). Five examples cannot pin down which.
- In all three live examples, price touched the box and turned. These are examples he chose, so this says nothing
  about how often it works.

**Watching him draw it (1:34:23–1:35:00, read 2026-11-14).** Frames every 0.2 s through the drag:
- He drags an arrow from the swing high to the swing low. An on-screen widget branded "QUANTLABUSA.COM" shows the
  drag angle (−51.5° at the end). He gives no name or source for it.
- The box is drawn **live while he drags** and **scales with the drag**. With the arrow start at y = 206 px, the
  box's far edge stayed at 0.811–0.814 of the dragged distance at four cursor positions (arrow tip at 756, 774, 794
  and 830 px). Two short trial drags before that (1:34:30–1:34:33) made thin boxes in the same place relative to
  the drag. So on this chart it is a **fixed ratio of the two points he drags**, like a Fib tool: about
  **0.61 / 0.75 / 0.81** measured from the second point.
- After the drag, it becomes a static rectangle. It starts at the second point's candle (Sun 1 Feb 20:00) and
  extends right. When selected, it has handles at its corners and edge midpoints, like any rectangle drawing. The
  inner line (0.75) is labelled **"DLP"**. The bright band is 0.75–0.81 and the darker band is 0.61–0.75. When not
  selected, it shows only its outline.
- His words over it: take "the swing high to the swing low and we now create a zone … this is essentially where in
  premium I'm looking for us to potentially turn around if my bias is that we're going to go lower". His bias
  "would have flipped if we got above this area".
- **The 1:42 drags, checked against his own arrow ends (read 2026-11-14).** Pixel rows on the 1080p frames; the
  fractions are measured from the second point:

  | Drag | Arrow start → end (px) | Fib anchors (px) | Near · split · far | When the box appears |
  |---|---|---|---|---|
  | 1:34:36 short, 1 h chart | 206 → 756 | 205 → 759 | 0.613 · 0.751 · 0.811 | live, scales during the drag |
  | 1:42:18 long, 15 m | 632 → 25 | 631 → 24 | 0.618 · 0.755 · 0.817 | live, scales during the drag |
  | 1:41:50 long, 15 m | 741 → 31–38 | 741 → 31 | 0.546–0.550 · 0.702–0.705 · 0.775–0.777 | ~2.6 s after he lets go |

  - **Anchoring does not explain the spread.** In all three drags, his arrow ends sit within about 1 % of the leg
    from his Fib's anchors, so the Fib-based readings in the table above were right.
  - Two of the three drags give the **same ratios, about 0.62 / 0.75 / 0.81**. The 1:27 slide (≈ 0.60 / 0.74 / 0.80)
    agrees with them. Within each of those two drags, the box scaled with the cursor at a fixed ratio.
  - **The 1:41:50 drag is a real exception, at 0.55 / 0.70 / 0.78.** That box did not follow the drag: it appeared
    2.6 s after he let go, with no other drag before it. The 1:25 slide (0.73 / 0.83 / 0.87) is the other outlier.
  - **The inside split lands at about 0.68–0.73 of the box height** in every example, so the box keeps its shape
    when it moves.
  - **Best reading:** a fixed-ratio tool at about 0.62 / 0.75 / 0.81 (the values our stand-in uses), with a second
    mode or setting we cannot see. Five frames cannot say what that mode is.

**If we build a stand-in,** the honest version is a **fixed-ratio box at the median readings: 0.62 / 0.75 / 0.81 of
the leg, brighter band from 0.75 to 0.81**. It is labelled "fitted to 5 screen readings, not his formula" (as we did
for AVR) and tested as an optional filter against the plain 0.705–0.886 zone. **Built 2026-11-12:** Range Fib Zone draws it (setting "Show the
decoder box", on by default), and AR Strategy's "Zone" setting / `pnpm bt:ar --set zone=decoder` uses it. First look
on NQ 5 m (all-day window, exploratory): worse than his zone, −$13,023 vs −$7,194 over ~59 trades, all of the
difference in the 17–18 trades only one of them took (`research/backtests/README-ar-backtest.md`). Frames saved during the read:
1:25:10, 1:27:05, 1:34:44, 1:42:00, 1:42:32.

**Own indicator, 2026-11-14:** "Dark Pool Decoder (fitted)" (AR Model group, `ardpd`; `decoderZones` in
`apps/web/lib/tv/ar.ts`). It draws a box on each of the last N swing legs (default 3; swing size 2 × ATR 14, editable).
The near edge, "DLP" line and far edge are editable (default 0.62 / 0.75 / 0.81). Legs can be every leg, only the
1-hour trend's direction (his rule), longs only or shorts only. Each box is followed candle by candle:
- **waiting:** price has not reached it yet.
- **in box:** price has reached it and nothing is decided.
- **held:** price went back to the leg's end.
- **broken:** a candle closed past the far edge.
- **extended:** price ran past the leg's end first.

Decided boxes end at that candle and are drawn faded. Range Fib Zone's own decoder box is now off by default
(`arDecoder` absent = off) so the two do not overlap.

### 2b. Other location references `[S 0:10–0:12, 0:29–0:30]`
Gamma levels (above); resting limit-order clusters on the DOM/heatmap (round numbers, prior highs/lows); prior
session highs/lows; higher-timeframe levels; session volume-profile POC/VAH/VAL/LVNs. Any of these can be
"location". He says a level without participation is not a trade.

## Step 3 — Path: Asia-range volatility levels ("AVR") `[S+V 1:43–2:08]`

NQ-only indicator built from his own 8–15 years of NQ daily statistics.

1. Mark the **Asia session** high, low, midpoint (EQ) and quarters. They are fixed when Asia closes and do not
   repaint. **[M]**
2. Session model `[S+V slide 1:50]`: **Asia defines the range → London often makes the daily wick or a fake-out away
   from Asia → New York makes the real move or continues.** Price often returns to the Asia range later in the day.
3. Projections are sized from the **20-day daily ATR** (the average daily range). **Numbers read off the indicator's
   on-screen table `[V]`:**

   | Day | ATR(20) | Up projection | Down projection | Up / ATR | Down / up |
   |---|---|---|---|---|---|
   | ~1:57 example | 278.74 | 41.81 | 44.6 | 15.0 % | 1.067 |
   | 3:30 live day (May 18) | 485.69 | 63.0 | 67.2 | 13.0 % | 1.067 |

   The **down projection is consistently 1.067× the up projection** (his "asymmetric" levels). The projection as a
   share of ATR **shrinks as ATR rises**, which matches his "volatility normalization / change clamping".
4. **Level offsets measured from the price axis on one day `[V 2:04, ±5 pts]`** (ATR 278.74):

   | Level | Above Asia high | ≈ × ATR | Below Asia low | ≈ × ATR |
   |---|---|---|---|---|
   | Level 1 (band) | +10 to +31 | 0.04–0.11 | −12 to −40 | 0.04–0.14 |
   | AVR− (dotted) | +52 | 0.19 | −60 | 0.22 |
   | AVR (band, "average expansion") | +94 to +115 | 0.34–0.41 | −105 to −125 | 0.38–0.45 |
   | AVR+ (band, "extended") | ~+188 | 0.67 | ~−215 | 0.77 |
   | Max ("outlier days only") | ~+267 | 0.96 | — | — |

   These are approximate pixel readings, good enough to rebuild something similar, not his exact formula.
5. Use `[S 1:56–2:05]`: **context only, never an entry.** Trade only in the bias direction. In the bias direction,
   these levels are where to **take profit** or watch for exhaustion. With a short bias, never buy a zone. Non-trending
   days tend to stay inside AVR− on both sides. `[S 2:02]`

## Step 4 — Confirmation and entry (footprint)

### 4a. What absorption is `[S+V 2:58–3:14]`
"High effort, low result": heavy aggressive volume into a level while price fails to extend. On the footprint
`[S 3:05]`: heavy volume at the level, aggressive imbalances into it, **candle POC stuck at the rejection end / in
the wick**, strong delta, repeated pushes that fail, and **the next candle fails to continue**. Exhaustion (low
effort, low result, participation drying up) is a different thing. `[S+V 3:03–3:04]`

**The one real example, read from the screen `[V 3:12, delta row zoomed]`:** the breakout candles had delta +649
(5K contracts), +14 (6K), +814 (10K). The **absorption candle had delta +15 on 12K contracts**, the highest volume
of the sequence (2–4× its 3–6K neighbours). It closed **bearish** with its POC at the top, above the level. The next
candles printed **−337 and −273** delta: the pressure flip. **[P]** (needs bid/ask volume)

### 4b. Confirmation checklist `[V 3:09–3:10, on-screen list]`
After absorption is suspected, wait for: **delta flips direction · next candle fails to continue · candle closes away
from the absorbed level · price accepts back inside the prior range · order-flow pressure starts to shift ·
opposite-side liquidity becomes the target.**
Decision tree `[S+V 3:09–3:10]`: meaningful location? → no: ignore. Aggression failed to produce a result? → no:
no trade. Pressure flipped? → no: **wait**; yes: trade idea valid.

### 4c. Delta flip rule `[S 2:52–2:54]`
A delta flip counts **only** at a structural inflection point, **after** failed initiative pressure, **and** with a
measurable change in price behaviour after it. Inside balance it's noise ("on a one-minute you'll see delta flips
all over the place").

### 4d. The entry trigger: two candles `[S+V 3:21–3:22, slide at 3:22]` **[P]**
1. **Confirmation candle** (5 m): shows the absorption plus the pressure flip, and closes.
2. **Entry candle:** opens and **immediately pulls back into the confirmation candle's low-volume node (LVN)**, or
   into the previous candle's POC `[S 3:29]`. **Stacked imbalances at that spot make it better.**
The LVN is not the signal, only where he refines the fill.

### 4e. Stop `[S 3:14, 3:34, 3:43, 3:46–3:47; V 3:47]`
Beyond the far side of the absorption / the swing extreme ("stop at the lows", "stop right above these highs").

## Trade management (live trades, 3:30–3:50)

- **Targets:** the next structural or gamma level: the put/call wall, P1/P2, the opposite end of the range, a
  prior-session high/low, an unfilled gap. `[S+V]`
- **One measured trade `[V 3:48, position tool]`:** **stop 40.75 pts (0.151%), target 71 pts (0.262%),
  reward:risk 1.74**, hit for +71 pts.
- **Breakeven** after roughly +25 pts, or once the move clears a large resting order. `[S 3:37, 3:47]`
- **Trail** just past the last 5 m candle, the POC of the last 15 m candle, or a large resting order on the DOM.
  Tighter in positive gamma and when buyers/sellers have already defended an area once. `[S 3:33–3:35, 3:47–3:48]`
- **Re-entry:** he re-takes the same idea after a stop-out (three shorts toward the same P1 on one day). `[S 3:38]`
- **Stop for the day** once the idea has played out, when the market starts compressing, or when the 5 m ATR is very
  large for the account (an 81-pt 5 m ATR made him cut size and quit). `[S 3:38–3:39]`
- **Risk rules** `[S 4:00–4:22, keyword scan]`: a hard daily loss limit, trade limits, cut size after emotional
  escalation. No numbers given.

## Not rules

- `[0:17–0:52]`: why patterns fail, auction theory (initiative vs. responsive traders), depth of market, footprint
  anatomy, delta arithmetic. Background only.
- `[0:16]`: sponsor read (a prop-firm comparison site). `[2:44]`: affiliate code for the gamma platform.
- `[3:50–4:25]`: psychology and a pitch for his paid mentorship.

---

## What carries over to this repo, and what doesn't

Checked against what this project has already built and tested (memory and `research/hypotheses/INDEX.md`).

| Course idea | Can we test it here? | Already tested? |
|---|---|---|
| **Absorption → confirmation → two-candle LVN entry** (his core trigger) | **Codified 2026-11-12** in `apps/web/lib/tv/ar-orderflow.ts` (AR Strategy `confirm: 'orderflow'`, `entry: 'lvn'`), but **not with real data** for NQ. The 1-minute cache has no bid/ask split, and the per-candle LVN needs volume-at-price inside a 5 m candle. The Lab's `delta_divergence` / `cvd_divergence` (`btlab/orderflow.py`) **sign each minute's volume by its candle body**, which only approximates delta. A real test needs tick or bid/ask data, which `apps/signal-engine/scripts/capture_ticks.py` (untracked, in progress) may start collecting. | Approximate versions: **CVD divergence lost at real costs** in the Lab (2026: −₹125/−₹113/−₹75 per trade). **BT35 absorption base**: unresolved (net +0.021%/trade, n=111, not significant). |
| **Fib 0.705–0.886 zone outside value area, close-beyond-0.886 invalidation, target = opposite swing** | **Yes.** Mechanical once "significant swing" is defined (e.g. the Lab's structural swing points). One Lab `Plugin` for the entry zone, one for the invalidation exit. The visuals suggest also testing a "first touch at 0.62–0.705" variant. | **Not tested.** |
| Value-area premium/discount (VAH/VAL/POC of a fixed-range profile) | Yes (1-minute bars give volume at approximate prices). | **BT37/38 volume shelves KILLED**, but that was a different construct; the project's VP has no POC/value area yet. **Untested.** |
| Regime filter / model-fit / no-trade rule | Partly. Needs our own definitions (overlap ratio, failed-breakout count, range vs. ATR). | Related levers killed on the momentum strategy (volatility, ATR band; 9 entry-side levers killed). |
| Only the first 90 minutes after the open | Yes. | The live rule already ran an 11:00 cap (BT53 tested removing it: KILLED 1/4; operator moved it to 14:00 anyway). |
| Asia-range AVR levels | **Reconstructable** (≈13–15 % of ATR(20) per unit, down = 1.067× up, offsets above), but **doesn't transfer to NSE stocks**: NSE cash trades only 09:15–15:30 IST, with no Asia/London/New York cycle within one stock's day. An NSE analogue would be an opening-range ± ATR projection: a new hypothesis, not his indicator. | Not tested. |
| Gamma regime (positive → fade, take profit fast; negative → hold) | Only going forward. The GEX indicator exists (`3e9c073`), but there is no **historical** GEX data to backtest with. NSE single stocks have thin options books, so this mainly applies to the index. Recording NQ (QQQ-proxy) snapshots began 2026-11-14 (`pnpm bt:gex`). The strategy labels every trade's regime, and `gamma: 'targets'` takes profit at the first gamma level in positive gamma. | Not tested: no history yet. |
| Breakeven at ~+25 pts, tight trail behind the last 5 m candle | Yes. | **BT46 breakeven KILLED**; **removing the trailing stop changed nothing** (2026-09-20). Stop tweaks have been P&L-neutral on this signal. |
| Prior session high/low as target | Yes. | **BT50** session-high target cap: null (+₹4.32/trade, p=0.66). |

**Bottom line:** the core of AR is discretionary (bias + location + live footprint read). Two mechanical pieces are
new to this repo and testable on our data: **(1) the Fibonacci 0.705–0.886 zone outside the value area with the
close-beyond-0.886 invalidation and a target at the opposite swing**, and **(2) value-area premium/discount**. The
piece he leans on most, **real footprint absorption with the two-candle LVN entry**, can't be tested honestly until
we have bid/ask tick data. Lab runs are exploratory; anything worth believing still needs a pre-registered BT number.
