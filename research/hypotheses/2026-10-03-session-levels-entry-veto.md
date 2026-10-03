---
slug: session-levels-entry-veto
strategy: momentum_warrior_strict
status: killed
registered_at: '2026-10-03'
finalized_at: ''
decided_at: '2026-10-03'
hypothesis_hash: ''
---

# Hypothesis (BT55): refusing entries with < 1R of room to a PRIOR-session high improves net per trade

## 1. Mechanism

The 1R headroom veto reads only today's levels plus yesterday's high/low. Highs of the
last 10 sessions (`build_session_levels`) feed the target cap / checkpoint only. ASAHIINDIA
2026-09-24 bought 993.10 with the 15-Sep high 995.00 0.19% above (stop 4.65 away): the
level became a checkpoint, hit one minute later, and the trade closed −0.11%. Supply left
by earlier sessions should cap a long just as today's does.

## 2. Expected effect size

- Direction: long. Fewer trades, each with ≥1R of known room.
- Magnitude: unknown; prior record says level-based filters on this zero-drift signal are
  ~null (BT37/38, BT50). Expect ≈0.

## 3. Falsification criterion (LOCKED before running)

Control = live rule (`--warrior-strict --multi-entry`), arm = control + `--session-levels-veto`,
2026-01-01..2026-09-29, same cache. Real-cost (itemised MIS) net, paired by symbol-date where
both traded. Window is SPENT: it can kill the idea, never bless it.

KILL if ANY:
1. Arm − control net per trade (all trades, real costs) ≤ +₹0 .
2. Median net per trade of arm ≤ control median.
3. Paired-trade delta p ≥ 0.10 (two-sided).
4. Positive delta disappears after dropping the top 5 trades of the arm.
5. Arm removes < 5% of control trades (nothing tested) or > 50% (different strategy).

Replacement entries are expected (a refusal frees the slot), so the anti-test is NOT a
random-subset one; the paired/unpaired comparison above is the test.

## 4. Data / hold-out

2026 cache only. 2025 hold-out stays sealed.

## 5. Results

Run 2026-10-03, 2026-01-01..09-29, real-cost net (itemised MIS):

| | trades | net/trade | median | win % | net ₹ |
|---|---|---|---|---|---|
| control (live rule) | 133 | −₹66.4 | −₹103.0 | 31.6 | −8,834 |
| arm (+veto) | 94 | −₹57.3 | −₹102.3 | 34.0 | −5,386 |

The arm took a strict SUBSET: 94 identical trades, 39 removed (29.3%), **0 replacement entries**, so paired delta on common trades is exactly 0. The 39 removed averaged −₹88.4 (sum −₹3,447) vs −₹57.3 for the kept.
Criteria: (1) +₹9.1/trade PASS; (2) median −102.3 vs −103.0 PASS by ₹0.7; (3) paired p undefined (identical trades) - removed vs kept Welch test below; (4) drop-top-5 arm −₹101.0 vs control −₹98.9 **FAIL**; (5) 29% removed PASS.
**KILLED on criterion 4**: the mean gain is entirely the removed trades being a bit worse than average, and it vanishes when the top 5 winners are dropped. Still loses ₹57/trade; no edge. Code kept, flag OFF.
