# NSE paper-scanner repair plan

**Status:** planning only. This document authorizes neither implementation nor deployment, schedule changes, order placement, new profitability tests, strategy changes, or holdout access.

## Objective and boundaries

Make the scanner's paper decisions, fills, risk limits, lifecycle records, and operational evidence trustworthy before interpreting output. Preserve paper-only operation and the current no-fixed-target `catalyst_first_pullback` playbook. A repaired engine is a new data-generating version: do not pool its records with prior forward records without a dated protocol decision.

Any changed early-entry definition, setup/filter, positive-news criterion, exit, or threshold is a separately registered strategy change, not an engineering repair. Thirty trades per group is an initial screen, never approval for live capital.

## Evidence review and corrections

The scanner source under review is untracked in the original checkout. `HEAD` therefore does not reproduce it. Before any implementation, create an approved source snapshot, lockfile digest, config/schema version, and—later—sanitized image digest. Do not reset or modify the original checkout.

| Area | Verified evidence | Conclusion |
| --- | --- | --- |
| Scheduling | `infrastructure/ecs-scanner.yml` defines one EventBridge Fargate launch at 09:05 IST weekdays; the process self-ends at 15:35 IST. | Preserve it; do not add a duplicate schedule. The manifest does **not** prove the deployed task revision/image tag. Verify that later using sanitized metadata. |
| Paper-only | The manifest sets `TRADING_MODE=paper`; `scanner.py` has no broker order call. | Keep paper-only. |
| Forward playbook | `scanner.py` selects MA9 only, ordinal 1, `trend_full`, no fixed target. | Do not change rules during correctness work. |
| Fill timing | `scanner.py:_process` sends completed bars to `engine.step`; `step` fills pending orders at the current bar's `open`. | A decision can be known after its recorded fill: proven chronology defect. |
| Runtime state | State, pending orders and risk are in memory; `ledger.py` uses insert then non-upserting close update. | Restart, duplicate worker, and same-step lifecycle safety are not established. |
| Security incident | The handoff reports a token in prior request logging. This review did not fetch logs or secrets. | Treat as an owner-led incident; never reproduce the value. |

## Prioritized repair backlog

### P0 — prevent new paper entries until fixed or explicitly quarantined

1. **Fill chronology.** A scanner call after a minute closes can fill at that minute's historical open. Persist exchange-event, receipt, decision, and simulated-fill times. A live-paper fill must be an executable observation after decision; an OHLC replay estimate must be visibly separate.

2. **Intra-bar exit ordering.** `engine.step` updates high/breakeven before testing low and checks `false_break` before hard stop. OHLC cannot tell whether the high or low occurred first, so a trail may bind too early or a stop can be hidden. Process live ticks in event order; define a conservative/bounded outcome for OHLC conflicts. Do not claim exact stop execution.

3. **Position-cap bypass.** Pending entries fill before `Scanner._open_count()` applies `mt_max_positions`. Reserve risk, capital, and slots before admission/fill; include reservations in all limits and release them on cancellation, expiry, close, or recovery.

4. **Lost lifecycle records.** A trade opening and closing in one `step` is absent from the post-step `opened` path. `ledger.closed` does not upsert. Use immutable lifecycle events, stable `trade_id`, idempotency keys, retry/outbox behavior, and reconciliation; notifications are not the ledger.

5. **Repeated stale bars.** `closed_bars` returns all completed bars and `_process` has no per-symbol processed-bar cursor. On a quiet/stale feed, the last bar can be passed to `step` repeatedly, potentially filling pending work again. Store a monotonic cursor and reject stale, duplicate, and out-of-session events.

6. **EOD depends on a quote.** Live EOD exit happens only when a bar reaches 15:14; a halted symbol can reach 15:35 without a close. (Backtest instead closes at its final price.) Drive EOD from wall clock, expire pending entries, and persist `unpriced/unresolved`, not an invented close.

### P1 — required before using output as protocol data

7. **Recovery and duplicate worker safety.** Add a leased session owner, fencing token, checkpoint/replay with deduplication, and recovery mode that manages existing state but refuses new entries until reconciliation passes.

8. **Allowed setup masking.** `step` chooses `found[0]` before applying `allowed_setups`, so a disallowed setup can hide an allowed MA9 setup. Filter eligibility before documented priority; record all detected setups and rejection reasons.

9. **Eligibility fails open.** Missing fact files/per-symbol facts and several null numeric fields pass static filters; missing turnover passes dynamic filtering. Separate mandatory eligibility (dated series, surveillance, circuit band, coverage/freshness) from experimental float/promoter factors. Required fields must parse strictly and fail closed.

10. **Catalyst unknown becomes control.** Database failures and lookup exceptions return catalyst `0`; only event type is persisted. Model `present`, `absent`, `unknown` with source/event ID plus publication, ingestion, classification, query, and decision times. `unknown` belongs in neither arm. Classifier direction is deliberately ignored; making it a positive-news filter needs a new hypothesis.

11. **Aggregate risk is undefined.** Only individual risk/notional and an open-count default exist. Specify a paper account/budget and centrally enforce aggregate open-plus-pending risk/notional, daily loss including costs/open losses, and deliberate sector/event concentration limits. Executable spread/depth treatment is a new policy decision, not a demonstrated code defect.

12. **Reports omit or misstate facts.** `ledger.py` writes a calculated 2R target even in trend/no-target mode; EOD is Telegram-only. Persist config/source/schema version, decision/rejection reason, data age, fill/cost assumptions, exit mode, `target=null` where unused, unresolved count, and durable session summary.

### P2 — parity, security, and research hardening

13. **Live/backtest context differs.** `run_day` supplies warmup/previous-day context while `Scanner.prepare` does not. MA9 requires 25 current-session five-minute bars (roughly 11:20 earliest). Restoring permitted warmup parity is a repair; an earlier session-aware MA9 definition is a new strategy, and prior-session price action must not be concatenated into today's pattern.

14. **Security and health controls.** Redact URLs/query/header values before log emission; add synthetic-secret tests; narrow broad SSM access; restrict logs; rotate the reported credential through its owner. Add application-level missing-start, stale-feed, unexpected-stop, database-failure, and missing-EOD detection. ECS RUNNING is not health.

15. **Protocol/screen audit.** The forward screen annualizes per-trade Sharpe and compares intraday holdings with full-day benchmark returns. Before a new confirmatory sample, append a dated correction or register a replacement covering daily equity including no-trade days, holding-window benchmark, clustered uncertainty, drawdown, multiplicity, and DSR. Keep locked gates explicit; do not rewrite them quietly.

## Implementation phases

### Phase 0 — reproducible evidence and fixtures

**Depends on:** nothing. **Deliver:** approved source/image/config inventory, event-state vocabulary, and deterministic synthetic/recorded-event fixtures. No backtest, profitability work, or holdout access.

**Acceptance:**

- Snapshot identifies scanner files, base commit, lockfile, configuration and schema without printing secrets.
- Fixtures reproduce retrospective fill; high/low and false-break/stop conflicts; 4 open + 3 pending against cap 5; same-step entry/exit; duplicate bar; feed halt before EOD; restart; duplicate worker.
- Fixtures label facts versus design choices and use no new profitability sample.

**Exit:** reviewers agree on fixture semantics and event schema. No deploy.

### Phase 1 — deterministic execution and admission control

**Depends on:** Phase 0. **Scope:** event-time/fill model, chronological exits, cursor, expiry, reservations/limits, clock-driven EOD state machine.

**Acceptance:**

- No simulated fill predates decision; replay-only estimates are explicitly labeled.
- Tick sequences follow event time; OHLC conflicts yield the pre-specified conservative/bounded result and reason.
- Cap and aggregate risk/notional hold with simultaneous symbols, cancellation, rejection, recovery, and one processing pass.
- Replayed input is idempotent; halted feeds expire entries and end in explicit unresolved state.

**Rollback:** feature-flag the new engine. If migration fails, stop new entries, preserve evidence, and manage remaining paper positions as safe/unresolved. Retain old behavior only for forensic replay, not new entries once P0 is confirmed.

### Phase 2 — durable lifecycle, recovery, and reporting

**Depends on:** Phase 1 IDs/transitions. **Scope:** immutable event/outbox, idempotent projections, session lease/fencing, checkpoint recovery, CSV/Mongo reconciliation, structured EOD summary.

**Acceptance:**

- Entry, exit, same-step close, retry, database outage, and crash-between-writes create exactly one logical lifecycle with stable ID.
- Restart restores state without duplicate entry; second worker is fenced; recovery admits nothing until reconciliation passes.
- Event store, position projection, CSV, and notification status reconcile; notification failure cannot erase a trade.
- Trend mode persists `target=null`, actual exit mode, versions, data age, and unresolved count.

**Rollback:** additive, dual-readable schema only. On projection failure, disable the new writer, retain immutable events, and reconcile read-only; never rewrite prior forward records.

### Phase 3 — data integrity, protocol boundary, security, and monitoring

**Depends on:** Phase 2 run/session IDs. **Scope:** fail-closed facts, catalyst provenance/unknown, warmup parity, redaction/SSM, health alerts, dated protocol amendment.

**Acceptance:**

- Partial/NaN/stale/missing/malformed fact rows have named rejections and cannot trade.
- Catalyst tests distinguish present/absent/unknown; DB outage/stale ingestion yields unknown, never control `0`.
- Live uses the same permitted warmup/previous-day inputs as replay without moving pattern availability across session boundaries.
- Synthetic secret never appears in logs; named minimum SSM permissions and all five application-health alerts are tested.
- Dated protocol document states metrics and engine-version boundaries without relaxed gates or holdout access.

**Rollback:** quarantine, do not fail open, when eligibility/catalyst freshness is unavailable. Archive prior validated inputs only as evidence.

### Phase 4 — reviewed paper rollout

**Depends on:** all prior acceptance checks, independent review, and owner confirmation for any credential remediation.

1. Build a uniquely versioned image; test isolated no-order dry run with synthetic/recorded replays.
2. Verify sanitized deployed image/config/schema metadata against review; drill holiday, late start, feed gap, DB outage, and EOD.
3. Enable one leased paper session and observe full lifecycle plus outage recovery before calling it reliable.
4. Stamp every record with a session/version boundary. Do not merge it with earlier records without the protocol decision.

**Go:** every test passes, one lease holder, durable health/EOD evidence, no silent unresolved state, metadata matches approved artifact.

**Abort/rollback:** duplicate lease/write, stale mandatory data, unreconciled lifecycle, missing EOD evidence, or P0 chronology breach. Disable new entries through the existing operational control, preserve evidence, and roll back only to the last reviewed paper artifact. Do not alter schedules, send ad-hoc messages, or recreate historical records.

## Review controls and model choice

- A zero-trade session or a successful ECS run demonstrates neither correct filtering nor profitability.
- Non-catalyst rows are the intended control only when their label is known; unknown is not control.
- GPT-5.6 Terra at high reasoning is suitable for bounded, fixture-led implementation phases. Use an independent stronger review for chronology, concurrent risk, recovery, security, and research validity.

## Source pointers

### 2026-09-09 necessary-fixes boundary

The current local safety patch is not completion of all phases above. It keeps
live paper marks separate from historical OHLC fills, rejects stale/expired
observations, checks quote-price exposure including estimated stop-exit costs,
and fails closed on persistence errors. LTP marks do not prove executable liquidity.

Automatic restart/takeover is intentionally disabled: `mt_scanner_guard` holds a
non-expiring global paper-writer claim and `mt_scanner_runs` prevents a second run
on the same date (including another strategy). Interrupted, unresolved, or failed
sessions retain the guard. Before any manual release, stop and verify termination
of the previous task, preserve its records, and reconcile events, positions, CSV,
and session status. Do not delete daily claims to reset intraday risk. No automatic
state restoration or retry outbox is implemented. Deploying this protocol requires
stopping older scanner versions, which do not honor the new guard.

Unknown catalyst coverage is not a verified non-catalyst control. Mandatory fact
fields are validated, but fact freshness, complete linked-news provenance, and
sector-aware admission remain outside this bounded patch. No deployment or
profitability validation was performed.

Reviewed read-only in `/Users/ritesh/codebase/ritesh-codebase/portfolio-analyzer`:

- `apps/signal-engine/src/momentum_trader/{scanner,engine,exits,bars,ledger,universe,catalyst}.py`
- `infrastructure/ecs-scanner.yml`
- `research/hypotheses/2026-09-05-momentum-catalyst-upstox-v2.md`
- `research/hypotheses/2026-09-07-catalyst-first-pullback-forward.md`
- `research/backtests/bt18_momentum_forward_screen.py`
