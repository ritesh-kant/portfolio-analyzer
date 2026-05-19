"""L4 strategy ensemble — one module per live strategy.

Each strategy is a thin shell around (model + entry filter + regime gate +
exit rules). Strategies do NOT do their own data fetching or feature
engineering; they consume from L2/L3.

Planned strategies (per plan §5):
    pead_midcap.py    — Strategy A: Post-Earnings Drift on Nifty Midcap 150
                        (PRIMARY; targets first live deployment ~Month 5)
    index_recon.py    — Strategy B: index reconstitution arbitrage (~Month 6)
    promoter_pledge.py — Strategy C: defensive universe filter (no positions)
    fno_vrp.py        — Strategy D: F&O volatility risk premium (Month 7+)
"""
