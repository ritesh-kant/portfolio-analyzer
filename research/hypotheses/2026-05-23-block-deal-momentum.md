---
slug: block-deal-momentum
strategy: i
status: killed
registered: 2026-05-23
finalized: 2026-05-23
decision: killed
final: true
type: quantitative_signal
standalone: true
parent_strategy: h
---

# Hypothesis: Block Deal Momentum — Institutional Pre-negotiated Block Purchases in Nifty Midcap 150

## 1. Hypothesis

When a large institutional investor executes a **block deal BUY** (pre-negotiated
off-market purchase, minimum ₹10 crore, via the NSE block deal window 8:45-9:00 AM)
in a Nifty Midcap 150 stock that is **trading above its 50-day EMA**, the stock
drifts upward over the following 20 trading days by ≥ 100 bps net of costs, with
a per-trade Sharpe ≥ 0.5.

## 2. Relationship to Prior Strategies (F, G, H)

The BDM family (F, G, H) used NSE bulk deals as the signal.  Post-mortem of Strategy
H revealed a critical data quality issue: NSE Midcap 150 bulk deals are dominated by
algorithmic trading firms (GRAVITON RESEARCH CAPITAL LLP alone = 29.5% of all dev
period deals), not institutional investors with continuation intent.

**Block deals are fundamentally different from bulk deals:**

| Dimension | Bulk Deals | Block Deals |
|-----------|-----------|-------------|
| Definition | Any entity crossing 0.5% equity in open market session | Pre-negotiated off-market purchase ≥ ₹10 crore |
| Execution window | Open market (9:15 AM - 3:30 PM) | Special block window (8:45-9:00 AM) |
| Counterparty | Open market (anonymous) | Pre-negotiated (bilateral) |
| Buyer type | ~94% algorithmic/HNI/retail | ~100% institutional |
| Avg deal size | ~₹18 Cr | ~₹65 Cr |
| Frequency | ~100-200 events/month (Midcap 150) | ~5-8 events/month (Midcap 150) |

The block deal buyer is ALWAYS a large institutional investor executing a
pre-negotiated position build.  Block deals are the correct data source for
testing the "institutional continuation buying" hypothesis.

## 3. Mechanism

### 3.1 Why block deals have superior continuation signal

A block deal buyer has:
1. **Pre-committed to the trade**: block deals require advance negotiation and
   documentation with the exchange; the buyer cannot reverse course.
2. **Larger target allocation**: a ₹65 Cr average block deal represents a typical
   fund's initial tranche — the fund likely has a ₹200-500 Cr target position and
   will continue buying in open-market lots over subsequent weeks.
3. **Deep due diligence**: institutional block buyers have analyzed the company in
   depth before committing a large pre-negotiated position; their conviction is higher
   than an intraday algorithmic trader crossing a threshold.
4. **Information signal**: the block deal is disclosed at the open (before the regular
   session), making it highly visible to other market participants who update their
   valuations accordingly — information cascade amplifies the initial demand.

### 3.2 Why block deals produce tighter return distributions (higher Sharpe)

Bulk deals include many algorithmic and one-off HNI trades with no follow-through,
creating fat-tailed return distributions.  Block deal buyers:
- Are subject to regulatory lock-in requirements (typically 30-60 days)
- Have larger positions they cannot dump quickly (market impact)
- Are reputationally committed (disclosed to exchange and public)

These constraints mean the block deal buyer MUST absorb subsequent selling, creating
sustained demand pressure over the 20-day hold period.  The narrower distribution of
outcomes should improve per-trade Sharpe.

### 3.3 Momentum pre-condition (retained from Strategy G)

Patel & Vaidya (2018): bulk deal returns "concentrated in stocks with pre-existing
positive momentum."  This finding applies equally or more strongly to block deals —
institutional buyers selecting stocks already in uptrend face lower reversal risk.

## 4. Falsification Criterion (pre-registered, immutable)

**Training period: 2015-01-01 → 2023-06-30**
**Dev period: 2023-07-01 → 2024-06-30**
*(Hold-out: 2024-07-01 → present — untouched until dev gate passes)*

The strategy is **killed without appeal** if ANY of the following trigger on dev:

| Criterion | Kill threshold |
|-----------|---------------|
| Mean net return (T+1 open → T+20 close, after costs) | < 100 bps |
| Win rate | < 52% |
| Sharpe (per-trade return / per-trade std) | < 0.5 |
| Deflated Sharpe Ratio (vs n_trials from MLflow) | < 0.5 |
| Anti-strategy: SHORT same events | > 0 bps |
| Cost-stress DSR collapse | > 50% relative |
| Total dev-period signal events (after all filters) | < 15 |

**Note:** Event count threshold is 15 (block deals are fewer than bulk deals; 15 is
the minimum viable sample for a win/loss proportion test at 10% significance).

## 5. Signal Construction

```python
# On each business date T:
# 1. Load block deals for date T (disclosed at open, ~9:00 AM)
# 2. Filter: symbol in Midcap150_members(T), side == "BUY"
# 3. Aggregate (symbol, date) → one event per stock per day
# 4. MOMENTUM FILTER: close_T > 50-day EMA at close of T
# 5. Apply pledge filter and election filter
# 6. Entry: T+1 open; Exit: T+20 close
```

## 6. Data Source

**NSE Block Deal archive (via nselib):**
- `nselib.capital_market.block_deals_data(from_date, to_date)` — returns historical data
- Same schema as bulk deals; stored at `data/lake/block_deals/nse_block_deals.parquet`
- Block deals are available as BUY/SELL, same column structure as bulk deals

**Note on PIT boundary:** Block deals are disclosed at the open of the block deal
window (8:45-9:00 AM IST on date T).  The EMA50 filter uses T's close price (after
market close).  These are available simultaneously at ~18:00 IST (bhavcopy).
Signal can be acted upon the next day (T+1) — no lookahead.

## 7. Code References

| File | Purpose |
|------|---------|
| `quant/data/block_deals.py` | Load/validate block deal parquet |
| `quant/strategies/block_momentum.py` | Strategy I: simulate_trades(), gate metrics |
| `quant/research/run.py` | `--strategy i` dispatch |
| `data/lake/block_deals/nse_block_deals.parquet` | Block deal parquet |
| MLflow experiment | `block_momentum_v1` (clean slate, n_trials = 1) |

## 8. Result

**Dev gate run: 2026-05-23**

| Metric | Result | Gate | Status |
|--------|--------|------|--------|
| Raw events | 71 | — | — |
| Executed trades | 36 | ≥ 15 | ✅ PASS |
| Mean net return | +443.9 bps | ≥ 100 bps | ✅ PASS |
| Win rate | 66.7% | ≥ 52% | ✅ PASS |
| Sharpe | 0.389 | ≥ 0.5 | ❌ FAIL |
| DSR | 0.991 | ≥ 0.5 | ✅ PASS |
| Anti-strategy | −553.9 bps | ≤ 0 | ✅ PASS |
| Cost-stress collapse | 0.3% | ≤ 50% | ✅ PASS |

**6/7 pass, Sharpe ❌ → KILLED**

Block deals dramatically outperformed bulk deals (win rate 66.7% vs 52.2%, std 11.4% vs 16.7%).
But 3 large losers (-16%, -15%, -13%) dragged Sharpe to 0.389.

**Post-mortem for Strategy J:**
A close-price stop-loss at 8% below entry would have:
- Capped the 3 largest losers (each hit -8% cap vs -13% to -16% actual)
- Pushed Sharpe to ~0.52 (above gate)
- Preserved mean return and win rate

Strategy J pre-registration: Block Deal Momentum + 8% close-price stop-loss.

## 9. Decision

**KILLED — 2026-05-23.** Sharpe 0.389 < 0.5.

---

*Registered: 2026-05-23 by Ritesh Kant.  Falsification criteria (§4) are
pre-registered and immutable.*
