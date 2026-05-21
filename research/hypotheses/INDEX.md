# Hypothesis Index

| Slug | Strategy | Status | Registered | Finalized | Decision |
|------|----------|--------|------------|-----------|----------|
| [pead-midcap](2026-05-19-pead-midcap.md) | pead_midcap | killed | 2026-05-19 | 2026-05-20 | KILL — Sharpe 0.077, DSR 0.392 (TTM EPS proxy too noisy) |
| [pead-midcap-v2](2026-05-20-pead-midcap-v2.md) | pead_midcap | killed | 2026-05-20 | 2026-05-21 | KILL — LightGBM oof_brier≈random; 65% TTM EPS in training; 3 trades, Sharpe −0.336 |
| [index-recon-arb](2026-05-21-index-recon-arb.md) | index_recon | registered | 2026-05-21 | — | — |

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
