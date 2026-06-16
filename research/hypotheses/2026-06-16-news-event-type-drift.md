---
slug: news-event-type-drift
strategy: news_trader_event_type
status: registered
registered_at: '2026-06-16'
finalized_at: ''
decided_at: ''
hypothesis_hash: ''
---

# Hypothesis: News that carries a *value-changing event* (M&A, earnings surprise, order win, regulatory action, capital action) drifts ≥ +0.30%/trade in the entry window on real bars, while generic-PR / analyst-rehash news does not — and the current classifier dilutes the former with the latter, which is why BT9 measured the blended cohort at gross ≈ 0.

> ⚠️ This hypothesis CANNOT be tested on the 79 existing closed trades. The
> `event_type` field does not exist in `nt_signals` yet (verified: classifier
> emits only signal/magnitude/confidence/sector/stocks/reasoning). It requires
> a classifier change to ship FIRST, then fresh forward data. The falsification
> criterion below is locked now, before any event_type data exists — this is the
> purest pre-registration in the run (zero contamination possible).

## 1. Mechanism

Four pre-registered backtests (BT7 shorts, BT8 large-cap fade, BT9 bullish-long,
BT11 options) and live days D8–D11 agree: the deployed signal — "bullish/bearish
+ magnitude + confidence + sector" — has **no monetizable drift** on real 5-min
bars (BT9 cohort gross −0.251%/trade; D10 12/12 time_stop; D11 9/11 time_stop).
The downstream levers are exhausted: exits were re-fit at D7 (time-stop now
clamps the flat cohort to ≈ flat), costs are at the intraday floor (~₹20/trade =
0.21%), and limit-entry (BT10) saves only ~₹7. **A gross ≈ 0 signal cannot be
made profitable downstream.** The only remaining lever is the signal's gross edge.

The economic claim: **not all "news" is information.** A value-changing event —
M&A / open offer, an earnings surprise, a large order win, a regulatory approval
or penalty, a buyback or capital raise — changes fundamental value and is
incorporated *slowly* in mid/small-caps (low analyst coverage, retail-dominated
order flow). This is post-earnings-announcement drift (PEAD) and its
event-study cousins — among the most replicated anomalies in the literature.
By contrast, product-launch PR, MOU/partnership announcements, broker
target-price notes, and "stock in focus" listicles carry no new fundamental
information and should show **no drift**.

The current classifier collapses both classes into the same actionable bucket.
If Group A (information events) drifts and Group B (noise) does not, the blended
cohort BT9 measured is exactly the +drift-of-A averaged against the 0-drift-of-B
— i.e. **gross ≈ 0 is the predicted artifact of mixing them.** Splitting on
`event_type` is the test of whether a tradable subset is hiding inside the noise.

**Pre-registered event_type taxonomy (locked):**

| Group | event_type values | hypothesis |
|---|---|---|
| **A — information** | `m_and_a`, `earnings`, `order_win`, `regulatory`, `capital_action` | drift > 0 |
| **B — noise (control)** | `rating_analyst`, `generic_pr`, `macro_sector`, `management`, `other` | drift ≈ 0 |

The primary test is the **A-vs-B spread** (controls for market regime — both
groups see the same tape). Group membership is fixed above; no event_type will
be reassigned between groups after seeing results.

## 2. Expected effect size

- **Direction**: long for bullish events, short for bearish events; intraday
  primary, multi-day secondary (see §3)
- **Magnitude**: Group A gross +0.3–0.6%/trade in the entry window; Group B
  gross ≈ 0 (±0.1%); spread (A − B) ≥ +0.30%/trade
- **Units**: % gross per trade; ₹ at ₹10,000/position
- **Sample mechanism estimate**: PEAD literature documents 0.5–2% drift over
  1–5 days post-surprise in low-coverage names; we conservatively claim the
  entry-window (intraday, deployed exits) captures the front ~0.3–0.6% of that.

## 3. Falsification criterion (LOCKED before experimentation)

Replay all Group-A and Group-B actionable signals (high-conf, moderate/major,
NIFTY500 minus NIFTY50) over the forward window. Deployed exit policy
(TP +1%, SL 3%, time-stop 90 min, EOD 15:15), entry = first 5-min bar ≥
signal + 15 min, 09:30–14:30 IST entries, entity-merge dedupe. Direction =
long for bullish, short for bearish. Real Yahoo 5-min bars, production cost
model. **Single shot per horizon — no sweeps.**

**Minimum sample before ANY result is read: n(Group A) ≥ 30.** Below that the
window is too thin (per the n≥30 discipline that killed sector/publisher cuts).
At ~5–8 information events/week this implies ~4–6 forward weeks of collection.

**Primary (intraday, deployed exits):**
- **PASS if** Group-A gross ≥ **+0.30%/trade** AND spread (A − B) ≥ **+0.30%/trade**
  AND anti-strategy spread ≈ 0 (see below)
- **KILL if** Group-A gross < **+0.15%/trade** OR spread (A − B) < **+0.15%/trade**
- **Between +0.15% and +0.30%**: NOT VIABLE at current costs but mechanism real
  — decision is "attack costs (limit-entry) and/or tighten event_type taxonomy",
  not "ship as-is"

**Secondary (multi-day hold, Group A only — reported but NOT decision-gating on
its own):**
- 3-trading-day hold, close on day-3 open, overnight gap + carry costs modeled
  (no MIS leverage; short-borrow proxy for bearish). Read only if primary PASSes
  or lands in the NOT-VIABLE band. Mechanism: PEAD is a multi-day phenomenon;
  the intraday box (D7 EOD-close, correct for the no-edge signal) may be the
  wrong horizon for a real information signal. A multi-day PASS requires its own
  gross ≥ +0.50%/trade after carry costs AND its own anti-strategy.

**Anti-strategy control (locked):** randomly permute the event_type labels across
the same signals (preserving the A/B group sizes) and re-run the spread. If the
permuted spread is ≥ the real spread, the split is noise → KILL regardless of the
headline number. This is the BT7/cross-sectional-momentum discipline: a real
edge must beat its own shuffled label.

**Contamination control:** event_type is captured OBSERVATIONALLY only. The live
`trade_decision` gate is NOT changed to filter on event_type until this
hypothesis passes a hold-out. Forward paper trades continue under the current
(unfiltered) policy, so the replay cohort is never tuned on event_type.

These thresholds cannot be relaxed after seeing results.

## 4. Data needed

- **New field `event_type` on `nt_signals`** — does not exist yet. Requires the
  classifier change scoped in §6 to deploy first. Populated forward from the
  deploy date only; historical signals will have `event_type` absent and are
  excluded from the cohort.
- Yahoo Finance 5-min bars (via `research/backtests/replay_lib.py`), cached.
- Production cost model (long + short directions) from `trailing_sl.py`.
- For the multi-day secondary: daily bars + an overnight-carry / borrow-cost
  addendum to the cost model (to be specified before that read, not now).
- PIT discipline: `event_type` is frozen on the signal at classification time
  (same `created_at` as signal/magnitude); the replay reads it as-of signal time.

## 5. Train / dev / hold-out split

Forward-only data (the field is new), so the split is temporal and defined at
collection time:

- **Dev**: first window reaching n(Group A) ≥ 30 after classifier deploy
  (~first 4–6 forward weeks). Replay + anti-strategy run ONCE here.
- **Hold-out**: the subsequent equal-or-longer window. NEVER touched until
  status=final. A dev PASS is promoted to final; the hold-out is the single
  confirmatory shot.
- No re-running the dev window with adjusted taxonomy — that requires a NEW
  hypothesis file with an explicit delta.

## 6. Code reference

**Classifier change (must ship before data collection — scoped, not yet built):**
- `apps/signal-engine/src/news_trader/classifier.py`
  - Add `event_type` enum + one-line definitions to `_SYSTEM` prompt
  - Add `"event_type"` to the `required` key set (line ~115) and to
    `_partial_parse` scalar salvage (line ~74) — it is a scalar, recoverable
  - Normalize: `result["event_type"] = str(result.get("event_type") or "other").lower()`;
    coerce any value outside the taxonomy to `"other"`
  - Bump `_DEFAULT_PROMPT_VERSION` 1.0.0 → 1.1.0 and
    `Settings.nt_classifier_prompt_version` (prompt change = semver bump)
  - Update the module docstring output-schema block
- `apps/signal-engine/handlers/news_classifier.py`
  - Add `"event_type": result["event_type"]` to `signal_doc` (line ~340)
  - Add event_type to the `[CLASSIFIER] LLM result` log line (line ~242)
- `apps/signal-engine/handlers/trade_decision.py` (optional, cheap, consistent)
  - Freeze `entry_event_type` on the position doc alongside
    `entry_publisher_count` — lets the replay tag traded positions without a
    join. NOT used to gate entry.
- Tests: add `event_type` to classifier fixtures + a parse/normalize test; assert
  `signal_doc` carries it; assert out-of-taxonomy → `"other"`.

**Replay harness (built after ~4–6 forward weeks):**
- `research/backtests/bt12_event_type_drift.py` (+ shared `replay_lib.py`),
  trade CSVs alongside, bars cached in `.cache/`. Mirror BT9's structure.

## 7. Result (filled after experiment — single dev run, no re-runs)

- n(Group A) / n(Group B): <fill>
- Group-A gross %/trade (intraday): <fill>
- Group-B gross %/trade (intraday): <fill>
- Spread (A − B) %/trade: <fill>
- Anti-strategy (label-permuted) spread: <fill — must be ≈ 0>
- Per-event_type gross (reported, not gated): m_and_a / earnings / order_win /
  regulatory / capital_action / rating_analyst / generic_pr / ...
- Multi-day secondary (if read): Group-A gross after carry costs: <fill>
- Net %/trade after production costs; cost-stress net: <fill>

## 8. Decision

- [ ] **PASS** — Group-A gross ≥ +0.30% AND spread ≥ +0.30% AND anti ≈ 0:
      build the live event_type filter in `trade_decision` (Group A only),
      then confirm on hold-out before any capital/paper-policy change
- [ ] **NOT VIABLE AT CURRENT COSTS** (+0.15–0.30%) — mechanism real but thin;
      attack costs (limit-entry) and/or tighten taxonomy in a NEW hypothesis
- [ ] **KILL** (< +0.15% or spread < +0.15% or anti ≥ real) — **the final word.**
      If even value-changing events show no entry-window drift on real bars, then
      retail-latency news trading on NSE is not viable. Declare the news-trader a
      documented negative result and STOP — do not mine a fifth variation on this
      signal. (This is the committed stop condition agreed 2026-06-16.)
