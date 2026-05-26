---
slug: <kebab-case-id>
strategy: <strategy_module_name> # e.g. pead_midcap
status: draft # draft | registered | final | shipped | killed
registered_at: ''
finalized_at: ''
decided_at: ''
hypothesis_hash: '' # SHA of this file at finalization; used by holdout_lock
---

# Hypothesis: <one sentence stating the directional bet>

> Copy this file to `YYYY-MM-DD-<slug>.md` and fill every section before
> writing any model code. An empty section means the experiment is not
> ready to run.

## 1. Mechanism

Why does this work economically? What is the behavioral or structural
inefficiency? If you cannot name a mechanism, you have a curve fit, not
a hypothesis.

## 2. Expected effect size

- **Direction**: long / short / spread
- **Magnitude**: e.g. 80–120 bps over 5 trading days
- **Units**: bps, R-multiples, % CAGR — be specific
- **Sample mechanism estimate**: tie this number to academic literature
  or a documented economic chain, not to past backtest runs

## 3. Falsification criterion (LOCKED before experimentation)

Pre-register the failure condition. Numeric only.

- **On dev set**: e.g. realized mean drift < 40 bps OR Sharpe < 0.5 → KILL
- **On hold-out**: e.g. Deflated Sharpe < 0.4 OR alpha vs Nifty < 0 → KILL

These thresholds **cannot be relaxed after seeing results**. If you find
yourself wanting to relax them, the strategy is already dead — you're
just delaying the funeral.

## 4. Data needed

- Source feeds: e.g. NSE Bhavcopy, NSE corporate filings, consensus from screener.in
- Lookback required: e.g. 90 calendar days before each inference date
- PIT discipline notes: where does as_of_timestamp come from per feed?

## 5. Train / dev / hold-out split

- **Train**: 2015-01-01 → 2023-06-30
- **Dev**: 2023-07-01 → 2024-06-30
- **Hold-out**: 2024-07-01 → present (NEVER touched until status=final)

## 6. Code reference

- Strategy module: `apps/signal-engine/quant/strategies/<module>.py`
- Feature builders used: `quant/features/...`
- Validator: `quant/research/purged_kfold.py` with embargo = max(holding_period × 2, 10)

## 7. Result (filled after experiment)

- Dev DSR: <number>
- Dev Sharpe (post cost-stress): <number>
- Capacity-adjusted DSR @ ₹50L: <number>
- Anti-strategy DSR (should be ≤ 0): <number>
- Trades per fold (median): <number>
- Alpha vs Nifty: <number>
- Strategy correlation with existing ensemble: <number>

(All 8 gate criteria from plan §3.2 must be filled.)

## 8. Decision

- [ ] **SHIP** — all gate criteria passed on dev; final hold-out passed
- [ ] **KILL** — falsification criterion triggered; no second look
- [ ] **ITERATE** — only allowed on train; requires opening a new
      hypothesis file with a clear delta from this one
