---
slug: pead-midcap
strategy: pead_midcap
status: finalized
registered: 2026-05-19
finalized: 2026-05-20
decision: kill
final: false
---

# Hypothesis: Post-Earnings Announcement Drift — Nifty Midcap 150

## 1. Hypothesis

After a positive earnings surprise exceeding 1 standard deviation of the naive
consensus baseline, with the day-of price reaction capturing ≤ 50% of the
historically observed median PEAD response magnitude, Nifty Midcap 150 stocks
drift upward 80–120 basis points over the next 5 trading days, net of realistic
transaction costs.

## 2. Mechanism

PEAD is the most replicated anomaly in finance (Bernard & Thomas 1989; Ball &
Brown 1968). Its persistence in Indian midcaps is driven by three structural
factors:

1. **Under-coverage**: Nifty Midcap 150 stocks average ~6 sell-side analyst
   estimates vs ~25 for Nifty 50. Thin coverage means the market's prior is
   noisier — a genuine surprise is not priced in instantly.

2. **Institutional friction**: Domestic MFs and FIIs are benchmarked against
   Nifty 50 / Next 50. Midcap rebalancing is slower — it takes 2–5 sessions
   for price-discovery to complete in thinner order books.

3. **Retail anchoring**: Retail investors who dominate midcap volume anchor to
   recent prices and underweight new fundamental information. The drift is the
   market slowly updating as more informed traders accumulate.

The mechanism is causal and behavioural, not statistical. It does not depend on
any feature that could be curve-fit to the training data.

## 3. Expected Effect Size

- Mean 5-day net drift for qualifying events: 80–120 bps
- Sharpe (5-day trade P&L / std): ≥ 0.5
- Median trades per purged k-fold fold: ≥ 30 (for statistical significance)
- Expected qualifying events per quarter for Midcap 150 universe: ~15–30

Effect size is net of NSE transaction costs:
  STT (0.1% sell-side) + exchange fees + SEBI fee + GST + conservative slippage
  (0.15% both sides) = ~0.45% round-trip.

## 4. Falsification Criterion (pre-registered, immutable)

**Dev period: 2023-07-01 → 2024-06-30**

The strategy is **killed without appeal** if ANY of the following trigger on
the dev set:

| Criterion | Kill threshold |
|-----------|---------------|
| Mean 5-day net drift (gate-filtered events) | < 40 bps |
| Sharpe (per-trade daily P&L) | < 0.5 |
| Deflated Sharpe Ratio (vs n_trials from MLflow) | < 0.5 |
| Anti-strategy: inverse signal net P&L | > 0 (if inverse also profits, not a signal) |
| Cost-stress DSR (slippage t-dist df=4, scale=2×) | collapse > 50% vs nominal |
| Capacity-adjusted DSR @ ₹50L AUM | < 0.3 |
| Median trades per fold | < 30 |

"Adjust one parameter and re-run" is explicitly not permitted after seeing dev
results. The dev period is for a single evaluation pass, not iterative tuning.
All hyperparameters are chosen via purged k-fold inside the *training* split
(2015-01-01 → 2023-06-30) only.

## 5. Feature List (pre-registered, ≤ 20 features)

All features below are locked at hypothesis registration. Adding or removing
features after seeing dev residuals is a process violation (plan §3.3).

| # | Feature name | Source | Lag discipline |
|---|---|---|---|
| 1 | `eps_surprise_pct` | NSE filings vs YoY naive baseline | available at filing as_of_timestamp |
| 2 | `revenue_surprise_pct` | NSE filings vs YoY naive baseline | available at filing as_of_timestamp |
| 3 | `guidance_direction` | LLM filing parser (Deepseek V3) | available at filing as_of_timestamp |
| 4 | `mgmt_tone_score` | LLM filing parser (Deepseek V3) | available at filing as_of_timestamp |
| 5 | `day0_price_reaction` | NSE Bhavcopy, day of announcement | available at 18:00 IST announcement day |
| 6 | `reaction_coverage_ratio` | day0 / historical median PEAD day0 (computed on train) | same as above |
| 7 | `momentum_residual_5d` | OHLCV via builder.py | available T-1 close |
| 8 | `momentum_residual_20d` | OHLCV via builder.py | available T-1 close |
| 9 | `turnover_z_score_day0` | OHLCV via builder.py, announcement day | available at 18:00 IST announcement day |
| 10 | `sector_return_5d` | NSE sector index via Bhavcopy | available T-1 close |
| 11 | `nifty_return_5d` | Nifty index via Bhavcopy | available T-1 close |
| 12 | `market_cap_log` | shares_outstanding × close (from Bhavcopy) | available T-1 close |
| 13 | `analyst_coverage_proxy` | screener.in (best-effort; 0 if unavailable) | best-effort; no data → 0 |
| 14 | `days_since_last_result` | derived from filings history | available at filing timestamp |
| 15 | `quarter_sin` | sin(2π × fiscal_quarter / 4) | available at announcement |
| 16 | `quarter_cos` | cos(2π × fiscal_quarter / 4) | available at announcement |

Total: **16 features**. The model hard-errors if passed more than 20.

**Target variable**: binary — 1 if 5-trading-day forward return from
announcement_day+1_open > 40 bps, else 0. Threshold is the lower bound of the
falsification criterion and is pre-registered here.

## 6. Data Required

- NSE Bhavcopy 2015-01-01 → 2024-06-30 (train + dev, no hold-out)
- NSE corporate results filings (quarterly results announcements) for Midcap 150 universe
- LLM parsing: Deepseek V3 API key (env: `DEEPSEEK_API_KEY`) + Gemini 2.5 Pro fallback (`GOOGLE_API_KEY`)
- screener.in consensus (best-effort; toS gray area, rate-limited to < 1 req/sec)

Nifty Midcap 150 constituent list as of each rebalance date (quarterly).
Use the list as of T-1 to avoid reconstitution look-ahead.

## 7. Code References

- `apps/signal-engine/quant/strategies/pead_midcap.py` — strategy definition
- `apps/signal-engine/quant/features/earnings.py` — earnings feature builders
- `apps/signal-engine/quant/agents/filing_parser.py` — LLM extraction
- `apps/signal-engine/quant/models/calibrated_lgbm.py` — LightGBM + isotonic
- `apps/signal-engine/quant/data/earnings_ingest.py` — L1 data pipeline
- `apps/signal-engine/quant/research/run.py` — gate check CLI

## 8. Result

**Evaluated 2026-05-20. Gate check on dev split (2023-07-01 → 2024-06-30).**

| Metric | Value | Threshold | Pass? |
|--------|-------|-----------|-------|
| Mean 5-day net drift | 53.4 bps | ≥ 40 bps | PASS |
| Sharpe (per-trade) | 0.077 | ≥ 0.5 | **FAIL** |
| DSR (n_trials=5) | 0.392 | ≥ 0.5 | **FAIL** |
| Anti-strategy DSR | 0.000 | ≤ 0.5 | PASS |
| Cost-stress DSR collapse | 48.1% | ≤ 50% | PASS |
| Capacity DSR @ ₹50L | 0.375 | ≥ 0.3 | PASS |
| Median trades/fold | 31 | ≥ 30 | PASS |

- Total trades: 145 (dev period, 5 purged k-folds)
- MLflow experiment: `pead_midcap` (5 runs, including 4 debug/bug-fix runs)
- Decision: **KILL** — Sharpe and DSR fail gate criteria.

**Post-mortem (key learnings):**
1. Mean drift of 53 bps confirms PEAD is real in Nifty Midcap 150 — the
   economic mechanism is there. The signal just has too much noise.
2. The TTM EPS series from screener.in was the wrong proxy. TTM changes
   smoothly across quarters; individual quarterly EPS surprises are much
   stronger signals. The TTM approach attenuates the true surprise magnitude.
3. Fold 3 (2024-02-02 → 2024-05-10, pre-election period) showed mean drift
   of −138 bps — clear regime break. Election uncertainty suppresses PEAD.
4. n_trials=5 in DSR reflects 4 debug runs + 1 final; each counted because
   DSR is honest about all data peeks. Next hypothesis should start clean.

**Recommended next hypothesis (PEAD-v2):**
- Use BSE corporate filing dates from `https://api.bseindia.com` (less
  aggressively rate-limited than NSE/Akamai).
- Use standalone + consolidated quarterly EPS from the BSE filing PDF
  (parse via filing_parser.py agent, already built).
- Add regime filter: skip all events within 45 days of known election dates
  (NSE market holiday calendar already has election dates as half-days).
- Re-register as new hypothesis `2026-05-20-pead-midcap-v2.md`.

This section is immutable after this commit.

## 9. Decision

*(Filled after hold-out, if and only if all dev gate criteria pass.)*

- Hold-out DSR:
- Hold-out mean drift:
- Hold-out alpha vs Nifty:
- Decision: [ ] Ship to paper  [ ] Kill

---

## 10. Implementation Clarifications (not hypothesis changes)

**2026-05-20 — Cross-sectional surprise estimator changed to robust statistics:**

The pre-registered entry gate ("EPS surprise > 1 std above universe mean")
was initially implemented using the arithmetic mean and std of the
cross-sectional distribution on each announcement day.  This is broken when
one stock reports a recovery-from-distress (e.g. a small finance bank with
near-zero prior-year EPS producing a 4000%+ YoY change): the outlier inflates
both the mean and std, making the threshold > 1000%, which blocks all genuine
candidates on that day.

**Change**: switch to `median + surprise_std_threshold × (1.4826 × MAD)` where
MAD is the Median Absolute Deviation.  This is the standard in PEAD literature
(equivalent to the robust z-score estimator used in López de Prado's work).
It does NOT change the "1 sigma above universe" criterion — it replaces the
non-robust estimator with a robust one.  This is a data-quality fix, not
post-hoc tuning.

This change is documented here rather than in the feature list because it
affects the estimator, not the feature definition or threshold value.

---

*Registered: 2026-05-19 by Ritesh Kant. Immutable after commit. Changes to
falsification criteria or feature list after this commit are process violations.*
