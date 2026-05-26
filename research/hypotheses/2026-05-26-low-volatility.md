---
slug: low-volatility
strategy: low_vol
status: registered
registered: 2026-05-26
finalized: ''
decision: ''
final: false
type: portfolio_strategy
standalone: true
---

# Hypothesis: Low-Volatility + Quality Filter on Nifty Midcap 150

## 1. Mechanism

**What it does (plain language):** Every month, measure how "jumpy" each stock's
price has been over the past year — its daily return volatility. Buy the 30 least
jumpy stocks that also have healthy earnings. Rebalance monthly.

**Why this works economically:**

The low-volatility anomaly is one of the most replicated findings in finance
(Black 1972, Haugen & Heins 1975, Baker et al. 2011). It inverts the intuition
that more risk = more return:

1. **Institutional mandate distortion.** Most fund managers are judged against a
   benchmark. To beat the benchmark they systematically overweight high-volatility
   (high-beta) stocks, which inflates their price and depresses their future
   returns. Low-vol stocks are systematically underowned → underpriced → higher
   future risk-adjusted return.

2. **Lottery-ticket bias.** Retail investors overpay for high-volatility stocks
   because they feel like lottery tickets. This pushes high-vol stocks above fair
   value. Low-vol stocks are "boring" → chronically cheap relative to risk.

3. **Defensive in drawdowns.** Low-vol stocks shed less in market corrections
   because they have lower systematic exposure (beta < 1). In the 2024-26 Indian
   midcap correction that killed Strategy R (momentum), low-vol would have
   experienced a fraction of the −36% drawdown.

**SWE analogy:** Think of high-vol stocks as code with high cyclomatic complexity —
more paths, more bugs, more likely to crash under load. Low-vol stocks are the
boring, battle-tested modules that just work, don't spike CPU, and rarely crash.

**Quality filter (carried over from Strategy Q/R):**
Same Screener.in EPS filter — exclude stocks with negative or declining EPS.
This avoids "low-vol because the company is dying slowly" — a classic value trap
where a dying company's stock price barely moves because nobody cares about it.

**Why the dev period will show lower returns (pre-acknowledged):**
The dev gate runs on July 2023 → June 2024, one of the strongest Indian midcap
bull runs on record. In bull markets, high-volatility stocks outperform low-vol
stocks in absolute return. This is expected and documented — the low-vol anomaly
shows up in risk-adjusted returns across full market cycles, not in bull-market
absolute return. The holdout period (July 2024 → present) coincides with the
correction that destroyed momentum — exactly when low-vol should perform best.

**Academic support:**

- Baker, Bradley & Wurgler (2011): Low-vol anomaly robust across 33 countries.
- Blitz & van Vliet (2007): Low-vol replicates in emerging markets specifically.
- NSE-India research: Nifty Low Volatility 50 outperforms Nifty 50 on risk-adjusted
  basis over 2004–2023 (NSE indices factsheet).
- Patel (2019, IIMA): Low-vol anomaly confirmed in Indian midcap universe.

---

## 2. Expected Effect Size

- **Direction:** long-only (no shorting — cash delivery at Zerodha)
- **Selected positions:** bottom 20% of eligible universe by realized vol ≈ 20–25 stocks
- **Expected gross Sharpe over full cycle:** 0.8–1.4
- **Dev-period Sharpe (bull market, expected lower):** 0.3–0.7
- **Holdout-period Sharpe (correction, expected higher):** 0.6–1.2
- **MaxDD:** targeted ≤ 15% (key differentiator vs momentum's −36%)

Cost reference: same as Q/R — ~45 bps round-trip (Zerodha delivery 2026).
Lower turnover than momentum (low-vol stocks are "sticky" — they change slowly).
Estimated portfolio turnover: ~25–35% monthly vs ~50% for momentum.

---

## 3. Pre-Registered Signal Construction (IMMUTABLE after this commit)

```python
# STEP 1: Annual EPS quality filter (identical to Strategy Q/R)
#   Source: data/lake/earnings/screener_annual.parquet
#   Updated each May using March year-end results
#   Eligible: eps[fy] > 0 AND eps_growth_yoy >= 0.0

EPS_GROWTH_FLOOR = 0.0  # no declining EPS

# STEP 2: Compute realized volatility for each eligible symbol
#   On last trading day of each month (signal_date):
#
#   lookback_prices = close[signal_date - 252 days : signal_date]   # ~252 prices
#   daily_returns   = lookback_prices.pct_change().dropna()          # ~251 returns
#   realized_vol    = std(daily_returns, ddof=1) * sqrt(252)         # annualised
#
#   Only symbols with >= 200 daily return observations are scored (gap tolerance).
#   A stock missing > 52 days of price data in the lookback is excluded.

VOL_LOOKBACK = 252  # trading days (~12 months)
MIN_OBSERVATIONS = 200  # minimum daily returns required to score

# STEP 3: Rank by realized vol ascending (lowest = most eligible)
#   Long bottom 20% of eligible quality universe (LOWEST volatility)
#   Buffer: stay in portfolio until rank rises above 30% (avoid churn)

TOP_PCT    = 0.20   # long bottom 20% by vol (inverse ranking vs momentum)
BUFFER_PCT = 0.30   # exit when rank > 30% (wider buffer than momentum)

# STEP 4: Entry / exit timing
#   Signal computed at market close on last trading day of month
#   Entry: open of first trading day of next month (identical to Q/R)
#   No vol-target overlay — low-vol inherently targets lower portfolio risk

# STEP 5: Anti-portfolio (HIGH volatility, for gate comparison)
#   Top 20% by vol (most volatile eligible stocks)
#   Same quality filter applied — apples-to-apples Sharpe comparison
```

**What is NOT allowed to change after registration:**

- Volatility lookback (252 days)
- Minimum observations required (200)
- Selection percentile (bottom 20%)
- Buffer percentile (30%)
- EPS growth floor (0.0)
- Entry timing (next-day open)
- Anti-portfolio construction (top 20% by vol, same quality filter)

---

## 4. Falsification Criteria (LOCKED)

**Dev period gate (2023-07-01 → 2024-06-30). ALL must pass:**

Note: this period is a bull market, which is structurally unfavorable for low-vol.
Gates are calibrated accordingly — lower absolute-return thresholds, strict
drawdown threshold, and Sharpe-comparison anti-strategy gate.

| Criterion                     | Kill if... | Rationale                                                              |
| ----------------------------- | ---------- | ---------------------------------------------------------------------- |
| Dev DSR                       | < 0.35     | 19 prior experiments; lower bar for bull-market period                 |
| Dev Sharpe (net costs)        | < 0.35     | Bull-market period; low-vol underperforms in absolute return by design |
| Strategy Sharpe ≥ Anti Sharpe | False      | Core anomaly test: low-vol must be better risk-adjusted than high-vol  |
| Max drawdown                  | > 15%      | Stricter than momentum — controlled drawdown is the key promise        |
| Monthly rebalances            | < 12       | Statistical floor                                                      |
| Avg positions/month           | < 5        | Filter not too aggressive                                              |

**Hold-out gate (2024-07-01 → present). Single shot:**

Note: correction regime — where low-vol should demonstrate its value.

| Criterion                     | Kill if... |
| ----------------------------- | ---------- |
| DSR                           | < 0.35     |
| Sharpe (net costs)            | < 0.35     |
| Max drawdown                  | > 20%      |
| Strategy Sharpe ≥ Anti Sharpe | False      |

---

## 5. Data Needed

| Source                  | Path                                         | Use                              |
| ----------------------- | -------------------------------------------- | -------------------------------- |
| NSE Bhavcopy OHLCV      | `data/lake/nse_bhavcopy/*.parquet`           | Daily closes for vol computation |
| Screener.in annual P&L  | `data/lake/earnings/screener_annual.parquet` | EPS quality filter               |
| Midcap 150 constituents | `data/lake/midcap150_constituents.csv`       | Universe                         |

All data already on disk. No new acquisition.

---

## 6. Train / Dev / Hold-out Split

- **Train:** 2015-05-01 → 2023-06-30
- **Dev:** 2023-07-01 → 2024-06-30 (12 monthly rebalances)
- **Hold-out:** 2024-07-01 → present (single shot)

---

## 7. Code References

| File                          | Purpose                          |
| ----------------------------- | -------------------------------- |
| `quant/strategies/low_vol.py` | Signal, simulation, gate metrics |
| `quant/research/run.py`       | `--strategy low_vol` dispatch    |
| MLflow experiment             | `low_vol_v1`                     |

---

## 8. Result (filled after dev gate run)

| Criterion            | Result | Gate   | Status |
| -------------------- | ------ | ------ | ------ |
| DSR                  | —      | ≥ 0.35 | —      |
| Sharpe               | —      | ≥ 0.35 | —      |
| Sharpe ≥ Anti Sharpe | —      | True   | —      |
| Max drawdown         | —      | ≤ 15%  | —      |
| Monthly rebalances   | —      | ≥ 12   | —      |
| Avg positions        | —      | ≥ 5    | —      |

---

## 9. Decision

- [ ] **SHIP** — all gates passed on dev; hold-out passed
- [ ] **KILL** — gate triggered
- [ ] **ITERATE** — train only; new hypothesis required

---

_Registered: 2026-05-26._
_Strategy S — 19 prior experiments. Gates calibrated for bull-market dev period._
_Cross-sectional gate is Sharpe comparison (risk-adjusted), not absolute-return spread._
