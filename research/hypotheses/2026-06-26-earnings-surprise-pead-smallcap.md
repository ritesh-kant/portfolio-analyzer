---
slug: earnings-surprise-pead-smallcap
strategy: earnings_surprise_pead
status: killed
registered_at: '2026-06-27'
finalized_at: '2026-08-24'
decided_at: '2026-08-24'
hypothesis_hash: 'sha256:1810c8ebf133cb667ff51cb2f4154b1b202736d591089093e619093a0c9dd061'
---

# Hypothesis: Smaller-cap NSE stocks with a large *earnings surprise vs. consensus* (SUE) drift in the direction of the surprise over the following ~5 trading days; a cross-sectional, market-neutral long-(high-SUE)/short-(low-SUE) book captures this drift net of costs.

> ⚠️ This is NOT a variation on the killed RSS-news-reaction system (BT9/BT12,
> committed stop). It is a different architecture: an **event study** driven by
> the exchange earnings calendar + actual-vs-consensus EPS, on a **multi-day**
> horizon, in **smaller-cap** names. It is the version of "news trading" that the
> convergent evidence (below) says can actually work — built specifically to fix
> the three things our pipeline was missing.
>
> ⚠️ It CANNOT be run yet. It requires **point-in-time consensus EPS estimates**,
> which we do not have. That is the single acquirable "missing piece." The
> falsification criteria below are LOCKED now, before any SUE data exists.

## 0. Why this, and why now (the three missing pieces)

Our 186-trade live run + four pre-registered kills (BT7/8/9/11) + four independent
expert sources (Skiena 2010; Foucault-Hombert-Roşu, JF 2016; Brunnermeier 2000;
and the 2026 practitioner/PEAD web survey) all converge: a **slow, headline-driven,
liquid-name, intraday** news reactor cannot profit. The same sources name what the
profitable participant has instead — and this hypothesis is built to supply all three:

1. **Trade the surprise, not the headline.** Our classifier emits direction from a
   headline with no expectations baseline → it buys good news that was already priced.
   Fix: rank on **SUE = (actual − consensus) / dispersion**, not sentiment.
2. **Avoid the speed race.** News-reaction profit accrues to whoever is fastest (FHR:
   "news trading arises in equilibrium *only when the speculator is fast*"). Fix:
   **multi-day hold** (T+1 → T+5), where being seconds-late is irrelevant.
3. **Go where the drift still lives.** PEAD "decayed post-2016 … only ultra-low-latency
   configs remain significant; drift appeared to occur only in microcap stocks"
   (UCLA Anderson / ScienceDirect 2024). Fix: **smaller-cap universe**, not the
   efficient large/mid liquid names our old system traded.

**Relation to prior PEAD kills:** `pead-midcap` (killed) *confirmed the drift is real*
(53 bps mean) but failed Sharpe (0.077) — root cause logged as "the TTM EPS series was
the wrong proxy." `pead-yoy`, `pead-ml`, `pead-n` (all killed) each tried to engineer a
surprise from **free data** and each was too noisy. This hypothesis fixes the one named,
never-addressed root cause: a **real consensus-based SUE**. See §3 no-relax rule.

## 1. Mechanism

Post-earnings-announcement drift (PEAD): markets under-react to earnings surprises;
cumulative abnormal returns drift in the surprise direction for days-to-weeks. It is
among the most-replicated anomalies in finance (Ball & Brown 1968; Bernard & Thomas
1989). The behavioral driver is **slow information diffusion** in low-analyst-coverage,
retail-dominated names — precisely smaller-caps. Large/liquid names price the surprise
within hours (efficient → no drift, our BT8/BT9 large-cap kills); smaller-caps reprice
over days, and a multi-day horizon does not require winning the latency race. A
cross-sectional long-short book on SUE is market-neutral, so it is robust to tape
direction (the failure mode that killed our directional longs on green-tape days).

## 2. Expected effect size

- **Direction**: spread — long top-quintile SUE, short bottom-quintile SUE (market-neutral)
- **Magnitude**: long-short **+60–150 bps per event over 5 trading days** (literature:
  0.5–2% over 1–60 days in low-coverage names; we claim the front of that, smaller-cap)
- **Units**: bps of gross/net return per event; book-level monthly Sharpe
- **Sample mechanism estimate**: Bernard-Thomas SUE deciles ~ +0.5–1%/quarter top-minus-
  bottom in US; Indian smaller-caps are less efficient → conservatively the same order,
  partly eroded post-2016 per the decay literature. **Threshold set below the literature
  midpoint to bias toward KILL.**

## 3. Falsification criterion (LOCKED before experimentation)

**Gate 0 — COST-STRESS, evaluated FIRST (the lesson from every prior result).**
Apply a *stressed* smaller-cap cost model: round-trip = 2× modelled (wider small-cap
spreads + delivery brokerage + STT + a short-borrow proxy on the short leg + no MIS
leverage). **If the dev long-short net return ≤ 0 under cost-stress → KILL immediately**,
before reading any other metric. Costs are where this strategy most plausibly dies.

If Gate 0 passes, the standard battery (dev), all must hold:
- mean 5-day long-short drift ≥ **+50 bps/event**
- per-event Sharpe ≥ **0.5**
- Deflated Sharpe Ratio (purged k-fold, n_trials counted honestly) ≥ **0.5**
- anti-strategy DSR (SUE labels permuted across events, group sizes preserved) ≤ **0**
- capacity-adjusted DSR @ ₹50L ≥ **0.3**
- median events/fold ≥ **30**

**Hold-out (touched once, only if all dev gates pass):** DSR ≥ **0.4** AND net alpha vs
NIFTY Smallcap index > 0. Else KILL.

**🔒 No-relax rules (locked):**
- Thresholds cannot be loosened after seeing results.
- **No free-data proxy substitution.** If real consensus EPS is unavailable, the
  hypothesis stays BLOCKED — we do NOT fall back to TTM/YoY/ML proxies. Those are
  already killed (`pead-yoy`/`pead-ml`/`pead-n`); rerunning them is a process violation.
- Single shot per horizon. Primary horizon = 5 days; 3-day and 10-day are reported
  secondaries, not additional shots at PASS.

## 4. Data needed

1. **Consensus EPS estimates, point-in-time** — mean/median estimate + # of estimates +
   dispersion, frozen as-of T−1 before each announcement. **PREREQUISITE, NOT YET HELD.**
   The acquirable missing piece (see estimates-feed cost analysis: I/B/E/S via WRDS-academic
   ≈ near-free if affiliated, else $15k–50k+/yr commercial; India mid-tier ₹50k–3L/yr but
   PIT-timestamp quality must be verified). Economics only justify the spend at scale.
2. **Actual reported EPS** (standalone + consolidated) — exchange filings / financial-data feed.
3. **Earnings announcement dates** — NSE corporate-results calendar (the event clock; our
   RSS pipeline surfaces only ~3.8 actionable earnings/week — wrong source. The calendar
   gives ~1,800 smaller-cap events/yr, ample for n≥30/fold).
4. **Smaller-cap universe file** — primary = NIFTY 500 minus NIFTY 100 (mid+small, liquid).
   We currently have only `nifty50.py` / `nifty500.py`; **NIFTY 100 list must be added**
   (extend `refresh_nifty500.py`). Secondary/exploratory: extend toward NSE microcap —
   where the 2024 research says drift is *strongest* but tradability is *worst* (flag the
   liquidity/impact tradeoff explicitly; do not promote microcap to primary without a
   capacity-stressed pass).
5. **Daily bars** — have (Yahoo via `replay_lib.fetch_bars`; extend interval to `1d`).
6. **Small-cap cost/borrow model** — new: wider spread assumption than the intraday large-cap
   model, delivery (not MIS) brokerage/STT, short-borrow proxy. Needed for Gate 0.

PIT discipline: SUE frozen at announcement time; entry uses T+1 open (no same-bar look-ahead);
universe membership as-of the event date (avoid survivorship via the dated index files).

## 5. Train / dev / hold-out split

If historical consensus is purchased (back to ~2015): **Train** 2015-01-01→2022-12-31 ·
**Dev** 2023-01-01→2024-06-30 · **Hold-out** 2024-07-01→present (NEVER touched until final).
If only *forward* consensus is acquirable: forward-only temporal split — dev = first window
reaching ≥30 events/side, hold-out = the subsequent equal-or-longer window (as in the
event_type hypothesis). Split is fixed at data-acquisition time, before any run.

## 6. Code reference

**Data source RESOLVED (2026-06-26): Trendlyne StratQ (₹5,900/yr).** Verified in-browser:
consensus estimates ARE point-in-time (the "Surprises" tab gives pre-result estimate vs
actual) and smaller-caps are covered (Sapphire 20-21 analysts). BUT forecaster fields are
**display-only** (not bulk-exportable) and estimate history is only **~3 quarters** deep —
too shallow + not extractable for a historical backtest. → We run the **forward-only path**
(§5): log each result's consensus + actual as earnings come in, accumulate to n>=30, then
Gate 0. ToS-clean (read live estimates you paid for; no archive scrape).

**BUILT + validated 2026-06-26:**
- `research/backtests/bt13_forward.py` — forward logger + Gate-0 screen. Reads the event
  log, fetches Yahoo daily bars, computes SUE (dispersion-standardized when >=3 analysts +
  a real range; else price-scaled fallback — handles the near-zero-EPS blow-up), the
  T+1->T+5 market-adjusted drift (benchmark NIFTY 500 `^CRSLDX`), and at n>=30 runs the
  LOCKED cost-stress Gate 0 (long top-SUE tercile vs short bottom; stressed delivery cost
  0.75%/leg round trip; KILL if net spread <= 0). Spearman(SUE, drift) reported as support.
  Compute path validated end-to-end (prices/drift/SUE/Spearman all correct).
- `research/backtests/bt13_forward_log.csv` — the accumulating event log (seeded with real
  SAPPHIRE data). Run: `uv run bt13_forward.py`.
- **Bulk capture via the Forecaster screener** (found 2026-06-26 — avoids per-stock visits):
  `…/forecaster/eps-quarter-surprise-above-0/` (beat) and `…-below-0/` (missed) list every
  stock's "Qtr EPS Surprise %" + result quarter in one page, filterable to a watchlist/index.
  Log `surprise_pct` directly (the engine accepts it; method=screener_pct). ⚠️ This % distorts
  on near-zero-EPS names — for the final tercile pull raw eps_avg/actual for a clean SUE.
- `research/backtests/bt13_watchlist.csv` — 44 smaller-cap names to track (filter the screener to these).

**Still TODO (only once Gate 0 is passed — do NOT pre-build):** `nifty100.py` for the exact
`smallcap_universe = NIFTY_500 − NIFTY_100`; full DSR/purged-k-fold/anti-strategy battery;
short-leg feasibility (multi-day smaller-cap shorts need F&O/SLB — may force long-only).

## 7. Result (filled after experiment — single dev run, no re-runs)

Single dev run executed 2026-08-24 at first touch of n≥30 (per §5, dev/hold-out split
boundary fixed at that moment). Forward-captured Q1FY27 (Apr–Jun 2026) results season,
30 events across the smaller-cap watchlist (`bt13_watchlist.csv`), via
`bt13_forward.py` against the LOCKED §3 screen (long top-SUE tercile vs short bottom
tercile, T+1→T+5 NIFTY500-adjusted drift, stressed cost 0.75%/leg round trip).

- **Gate 0 cost-stress net (long-short, stressed costs): −2.97%/trade — FAILED (must be > 0).**
- n events: 30 total priced (10 per tercile leg, n=30 threshold met exactly)
- Long leg (top-SUE tercile) mkt-adj 5-day drift: **−1.32%**
- Short leg (bottom-SUE tercile) mkt-adj 5-day drift: **+0.15%**
- Gross long-short spread: **−1.47%** (wrong-signed: low-surprise names drifted *better*
  than high-surprise names)
- Stressed round-trip cost (2 legs): 1.50%
- SUE~drift Spearman rho (support metric, want > 0): **−0.116** (weak, wrong-signed)
- Dev per-event Sharpe / DSR: not computed — Gate 0 (evaluated FIRST, per §3) failed,
  so the battery is moot per the locked no-relax rules.
- Anti-strategy / capacity-adjusted DSR / per-horizon breakdown / hold-out: not run —
  Gate 0 failure means the hold-out window is never touched, per §3/§5.
- 30 of 44 watchlist names captured this season (14 uncaptured: NIACL/GICRE/JBCHEPHARM/
  HBLENGINE lack sufficient Trendlyne analyst coverage or hadn't reported Q1FY27 yet as
  of 2026-08-24; the remaining ~10 were simply not reached before n=30 triggered).
  ⚠️ Not a random sample of the full universe — see note below.
- Where EPS wasn't tracked quarterly on Trendlyne (CRAFTSMAN, CAMS, MANAPPURAM,
  GRANULES, GLENMARK, JUBLFOOD), Net Income surprise was used as the SUE proxy
  (same-direction fractional surprise; documented per-row in `bt13_forward_log.csv`).
  CAMS additionally required a split-adjustment workaround (10-Oct-2025 5:1 split
  corrupted the raw EPS avg/actual comparison).

## 8. Decision

- [ ] **SHIP** — Gate 0 passed AND all dev gates passed AND hold-out passed
- [x] **KILL** — Gate 0 failed (cost-stress ≤ 0: net −2.97%/trade, and wrong-signed
      spread −1.47%). No second look; no proxy fallback; no fifth PEAD variation.
- [ ] **ITERATE** — train-set only; requires a NEW hypothesis file with an explicit delta.

**The SUE consensus-based PEAD hypothesis is a documented non-viable result on NSE
smaller-caps, Q1FY27 forward sample.** Real, paid (Trendlyne StratQ), point-in-time
consensus data — the one ingredient every prior PEAD attempt (`pead-midcap`,
`pead-yoy`, `pead-ml`, `pead-n`) lacked — still produced a wrong-signed, cost-negative
result. High-SUE names *underperformed* low-SUE names over T+1→T+5, the opposite of
the PEAD literature's predicted drift. Per the locked no-relax rules, this line of
research is closed: no further PEAD variations on this signal family.
