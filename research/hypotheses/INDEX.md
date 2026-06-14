# Hypothesis Index

| Slug                                                                 | Strategy      | Status | Registered | Finalized  | Decision                                                                                                               |
| -------------------------------------------------------------------- | ------------- | ------ | ---------- | ---------- | ---------------------------------------------------------------------------------------------------------------------- |
| [pead-midcap](2026-05-19-pead-midcap.md)                             | pead_midcap   | killed | 2026-05-19 | 2026-05-20 | KILL — Sharpe 0.077, DSR 0.392 (TTM EPS proxy too noisy)                                                               |
| [pead-midcap-v2](2026-05-20-pead-midcap-v2.md)                       | pead_midcap   | killed | 2026-05-20 | 2026-05-21 | KILL — LightGBM oof_brier≈random; 65% TTM EPS in training; 3 trades, Sharpe −0.336                                     |
| [index-recon-arb](2026-05-21-index-recon-arb.md)                     | index_recon   | killed | 2026-05-21 | 2026-05-22 | KILL (holdout) — Mean 23.5 bps < 100 bps; only 5 Nifty-50 events (data incomplete)                                     |
| [cross-sectional-momentum](2026-05-25-cross-sectional-momentum.md)   | cs_momentum   | killed | 2026-05-25 | 2026-05-25 | KILL — Anti-strategy DSR 0.913 > 0; dev bull-run contamination                                                         |
| [quality-filtered-momentum](2026-05-26-quality-filtered-momentum.md) | qf_momentum   | killed | 2026-05-26 | 2026-05-26 | KILL — Anti-strategy DSR 0.777 > 0; gate design error (absolute-return criterion wrong for long-only factor)           |
| [qf-momentum-spread](2026-05-26-qf-momentum-spread.md)               | qf_momentum_r | killed | 2026-05-26 | 2026-05-26 | KILL (holdout) — Sharpe −0.325, MaxDD −36.4%; cross-sectional spread survived (DSR 0.598) but absolute return inverted |

| [low-volatility](2026-05-26-low-volatility.md) | low_vol | killed | 2026-05-26 | 2026-05-26 | KILL — Strategy Sharpe 2.175 < Anti Sharpe 3.201; high-vol beats low-vol in bull-market dev period by design |
| [news-shorts-replay](2026-06-11-news-shorts-replay.md) | news_trader_shorts | killed | 2026-06-11 | 2026-06-11 | KILL — gross +0.045%/trade < +0.15% marginality floor with anti-strategy also gross-negative (= noise, no bearish drift); cost-stress −₹24/trade also breaches −₹10 viability floor |
| [largecap-fade](2026-06-11-largecap-fade.md) | news_trader_largecap_fade | killed | 2026-06-11 | 2026-06-11 | KILL — fade gross −0.026%/trade ≤ 0; momentum control gross-POSITIVE (+0.019%) voids overreaction story; large-caps are simply efficient both directions |
| [bullish-signal-replay](2026-06-11-bullish-signal-replay.md) | news_trader_long | killed | 2026-06-11 | 2026-06-11 | KILL — cohort gross −0.251%/trade (kill line +0.10%); never-traded subset −0.296%; majors WORSE than moderates; core selectivity has no drift on real bars — prior positive readings were censored/delayed-quote artifacts |
| [limit-entry](2026-06-11-limit-entry.md) | news_trader_limit_entry | shipped | 2026-06-11 | 2026-06-11 | SHIP — limit net −₹1,930 vs market −₹2,273 (+₹343), 96% fill, missed trades were losers; adopt for live Kite order design (execution-layer only; cannot rescue a negative-gross signal) |
| [news-options-long](2026-06-13-news-options-long.md) | news_trader_options_long | killed | — | 2026-06-13 | KILL (synthetic screen) — all 8 strike×expiry×stop cells net-negative; baseline −₹2.0k to −₹3.1k/trade, vol-crush −₹9.7k to −₹13.8k, gross −3.4% to −5.2%/trade premium; options amplify BT9's negative underlying drift ~30–45×. No underlying edge → no option wrapper helps |

## Status legend

- **draft** — being written; experiments not allowed yet
- **registered** — falsification criterion locked; train + dev experiments allowed
- **final** — hold-out can be touched (once)
- **shipped** — passed hold-out; live or paper trading
- **killed** — falsification criterion triggered on dev or hold-out; never re-opened

## Discipline

The first hypothesis to land here will be for **Strategy A: Post-Earnings Drift
on Nifty Midcap 150** (~Month 3 of the roadmap). Until then, this index is empty.

Adding a row to this index without a corresponding file in this directory
is a process violation. The hypothesis file is the artifact; this index
is just a navigation aid.
