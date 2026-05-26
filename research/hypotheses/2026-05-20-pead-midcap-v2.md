---
slug: pead-midcap-v2
strategy: pead_midcap
status: killed
registered: 2026-05-20
finalized: 2026-05-21
decision: killed — dev gate failure (6 of 7 criteria failed)
supercedes: pead-midcap (2026-05-19)
---

# Hypothesis: Post-Earnings Announcement Drift v2 — Nifty Midcap 150

## 1. Hypothesis

After a positive **actual quarterly EPS surprise** exceeding 1 robust standard
deviation of the cross-sectional distribution (median + 1.4826 × MAD), with the
day-of price reaction capturing ≤ 50% of the historically observed median PEAD
response magnitude, Nifty Midcap 150 stocks drift upward 80–120 basis points
over the next 5 trading days, net of realistic transaction costs — **excluding
events occurring within 45 calendar days of a Lok Sabha general election date**.

## 2. Mechanism

The PEAD mechanism is unchanged from v1 (Bernard & Thomas 1989; structural
under-coverage, institutional friction, retail anchoring — see v1 §2).

**Why v2 succeeds where v1 failed:**

1. **Quarterly EPS, not TTM.** TTM EPS is the trailing four-quarter sum. When
   Q3 beats by 20%, TTM moves by only ~5% because the other three quarters dilute
   the signal. Actual quarterly EPS (Q3-FY25 vs Q3-FY24) gives the full,
   undiluted surprise. v1's Sharpe of 0.077 reflects noise, not a dead mechanism
   — mean drift of 53.4 bps confirmed the economic signal is real.

2. **Election regime filter.** v1 fold 3 (Feb–May 2024, pre-Lok Sabha election)
   showed mean drift of −138 bps — a 191 bps swing vs the other folds. General
   elections create broad uncertainty that suppresses all mean-reversion and drift
   strategies. Filtering events ±45 days around election dates removes one
   documented regime break without requiring the model to learn it.

## 3. Expected Effect Size

- Mean 5-day net drift for qualifying events: 80–120 bps
- Sharpe (per-trade 5-day P&L / std): ≥ 0.5
- Median trades per purged k-fold fold: ≥ 25
  (slightly lower than v1's 30 because election-period events are now excluded)
- Expected qualifying events per quarter for Midcap 150: ~12–25

Effect size is net of Indian transaction costs (same round-trip cost model as v1):
STT + exchange fees + SEBI fee + GST + 0.15% slippage both sides ≈ 0.45% total.

## 4. Falsification Criterion (pre-registered, immutable)

**Dev period: 2023-07-01 → 2024-06-30**
(Hold-out: 2024-07-01 → present — untouched until dev gate passes)

The strategy is **killed without appeal** if ANY of the following trigger on dev:

| Criterion                                        | Kill threshold            |
| ------------------------------------------------ | ------------------------- |
| Mean 5-day net drift (gate-filtered events)      | < 40 bps                  |
| Sharpe (per-trade daily P&L)                     | < 0.5                     |
| Deflated Sharpe Ratio (vs n_trials from MLflow)  | < 0.5                     |
| Anti-strategy: inverse signal net P&L            | > 0                       |
| Cost-stress DSR (slippage t-dist df=4, scale=2×) | collapse > 50% vs nominal |
| Capacity-adjusted DSR @ ₹50L AUM                 | < 0.3                     |
| Median trades per fold                           | < 25                      |

n_trials for DSR: **this is a clean MLflow experiment starting from run #1.**
The prior v1 experiment ("pead_midcap") is separate and does not inflate n_trials
for this hypothesis. DSR for v2 will be computed against this experiment's own
run count.

## 5. Feature List (pre-registered, ≤ 20 features)

All features below are locked at registration. The election filter (item 17)
is applied **before** model training — it removes rows from training data, not
as a model feature. This preserves the feature count below 20 and avoids the
model learning an election proxy.

| #   | Feature name              | Source                                                  | Lag discipline                          |
| --- | ------------------------- | ------------------------------------------------------- | --------------------------------------- |
| 1   | `eps_surprise_pct`        | Tickertape quarterly EPS vs same quarter prior year     | available at filing as_of_timestamp     |
| 2   | `revenue_surprise_pct`    | Tickertape quarterly revenue vs same quarter prior year | available at filing as_of_timestamp     |
| 3   | `guidance_direction`      | LLM filing parser (Deepseek V3)                         | available at filing as_of_timestamp     |
| 4   | `mgmt_tone_score`         | LLM filing parser (Deepseek V3)                         | available at filing as_of_timestamp     |
| 5   | `day0_price_reaction`     | NSE Bhavcopy, day of announcement                       | available at 18:00 IST announcement day |
| 6   | `reaction_coverage_ratio` | day0 / historical median PEAD day0 (computed on train)  | same as above                           |
| 7   | `momentum_residual_5d`    | OHLCV via builder.py                                    | available T-1 close                     |
| 8   | `momentum_residual_20d`   | OHLCV via builder.py                                    | available T-1 close                     |
| 9   | `turnover_z_score_day0`   | OHLCV via builder.py, announcement day                  | available at 18:00 IST announcement day |
| 10  | `sector_return_5d`        | NSE sector index via Bhavcopy                           | available T-1 close                     |
| 11  | `nifty_return_5d`         | Nifty index via Bhavcopy                                | available T-1 close                     |
| 12  | `market_cap_log`          | shares_outstanding × close (from Bhavcopy)              | available T-1 close                     |
| 13  | `analyst_coverage_proxy`  | screener.in (best-effort; 0 if unavailable)             | best-effort; no data → 0                |
| 14  | `days_since_last_result`  | derived from filings history                            | available at filing timestamp           |
| 15  | `quarter_sin`             | sin(2π × fiscal_quarter / 4)                            | available at announcement               |
| 16  | `quarter_cos`             | cos(2π × fiscal_quarter / 4)                            | available at announcement               |

Total: **16 features.** Same feature set as v1; only the input to feature #1
changes (quarterly EPS vs TTM). Features #3–4 (LLM) remain in the list but
run in best-effort mode — if Deepseek API is unavailable, features default to 0.

**Election filter (pre-processing, not a feature):**
Remove all events where `business_date` falls within ±45 calendar days of a
Lok Sabha general election date. Known election dates:

- 2019 Lok Sabha: April 11 – May 23, 2019 (results May 23, 2019)
- 2024 Lok Sabha: April 19 – June 1, 2024 (results June 4, 2024)
  Filter window: [election_start − 45 days, election_end + 45 days].

**Target variable:** binary — 1 if 5-trading-day forward return from
announcement_day+1_open > 40 bps, else 0. Same as v1.

## 6. Data Required

- **Tickertape Pro quarterly EPS CSVs** — actual quarterly EPS for Midcap 150
  universe, back to FY2015-16. Ingest via `quant/data/tickertape_earnings.py`.
  Export instructions in that module's docstring.
- NSE Bhavcopy 2015-01-01 → 2024-06-30 (already ingested)
- NSE corporate results filing dates (already in nse_results.parquet)
- LLM: Deepseek V3 API key (`DEEPSEEK_API_KEY`) + Gemini 2.5 Pro fallback (`GOOGLE_API_KEY`)

## 7. Code References

- `apps/signal-engine/quant/data/tickertape_earnings.py` — NEW: Tickertape ingest
- `apps/signal-engine/quant/strategies/pead_midcap.py` — strategy definition
- `apps/signal-engine/quant/features/earnings.py` — earnings feature builders
- `apps/signal-engine/quant/agents/filing_parser.py` — LLM extraction
- `apps/signal-engine/quant/models/calibrated_lgbm.py` — LightGBM + isotonic
- `apps/signal-engine/quant/research/run.py` — gate check CLI

**Election dates constant** to be added to `quant/strategies/pead_midcap.py`:

```python
_ELECTION_WINDOWS = [
    ("2019-02-25", "2019-07-07"),   # 2019 Lok Sabha ±45 days
    ("2024-03-05", "2024-07-16"),   # 2024 Lok Sabha ±45 days
]
```

## 8. Execution Plan

1. **Export Tickertape data** (manual step):
   - Log in to Tickertape Pro
   - For each Midcap 150 symbol: company page → Financials → Quarterly → Export CSV
   - Save all CSVs to a single directory, named `{SYMBOL}.csv`
   - Run `python -m quant.data.tickertape_earnings --probe SYMBOL.csv` on a sample
     to verify format detection

2. **Ingest**:

   ```bash
   cd apps/signal-engine
   python -m quant.data.tickertape_earnings \
     --input-dir ~/Downloads/tt_quarterly/ \
     --dry-run   # verify quality report first
   python -m quant.data.tickertape_earnings \
     --input-dir ~/Downloads/tt_quarterly/  # write parquet
   ```

3. **Verify patch coverage**:

   ```bash
   python -c "
   import pandas as pd
   df = pd.read_parquet('data/lake/earnings/nse_results.parquet')
   print(df['source_url'].str.startswith('tickertape').sum(), 'rows patched')
   print(df['yoy_eps_prev'].notna().mean(), 'yoy_eps fill rate')
   "
   ```

4. **Add election filter** to `pead_midcap.py` and re-run purged k-fold on
   training split to confirm trades-per-fold median stays ≥ 25.

5. **Dev gate run**:

   ```bash
   python -m quant.research.run --strategy pead_midcap --split dev
   ```

6. If all gate criteria pass → single hold-out run with `--split holdout --final`.

## 9. Result

Dev gate run: 2026-05-21. Split: 2023-07-01 → 2024-06-30.
Trade selection: LightGBM (oof_brier=0.2471, n_trials=4).
Data state: 31.5% BSE quarterly EPS in training window, 65.3% TTM remaining.

| Metric                   | Value     | Threshold | Pass? |
| ------------------------ | --------- | --------- | ----- |
| Total trades             | 3         | —         | —     |
| Mean 5-day net drift     | −71.4 bps | ≥ 40 bps  | FAIL  |
| Sharpe (per-trade)       | −0.336    | ≥ 0.5     | FAIL  |
| DSR (n_trials=4)         | 0.000     | ≥ 0.5     | FAIL  |
| Anti-strategy DSR        | 0.000     | ≤ 0.5     | PASS  |
| Cost-stress DSR collapse | 100.0%    | ≤ 50%     | FAIL  |
| Capacity DSR @ ₹50L      | 0.000     | ≥ 0.3     | FAIL  |
| Median trades/fold       | 1         | ≥ 25      | FAIL  |

**Root cause — training data quality failure:**

The LightGBM model trained on a parquet where 65.3% of training events still
had TTM EPS (not quarterly). BSE PDF ingest achieved only 31.5% quarterly
coverage for the 2015–2023 training window. The model's oof_brier (0.2471)
is barely better than random (0.25), confirming it learned no signal from
the mixed-distribution training set. It selected 3 dev events with negative
mean drift (−71.4 bps) — opposite of the predicted direction.

**Infrastructure notes (for next strategy):**

- `merge_with_existing_parquet` has a `drop_duplicates(symbol,fq,fy)` at write
  time that collapsed 4,128 preliminary/revised announcement pairs, losing 2,018
  rows from the parquet. Fix before Strategy B.
- BSE PDF format is inconsistent pre-2018; extraction rate drops below 30%.
  Coverage was insufficient to retrain the model on quarterly features.

## 10. Decision

**KILLED — dev gate triggered on 2026-05-21.**
6 of 7 gate criteria failed. Hold-out not evaluated (protocol prohibits peeking
after a kill).

Per §4 of the registered hypothesis: _"killed without appeal."_
Next: Strategy B — Index Reconstitution Arbitrage (plan §5.2).

---

_Registered: 2026-05-20 by Ritesh Kant. Immutable after this commit.
Changes to falsification criteria or feature list after this commit are
process violations (plan §3.3)._
