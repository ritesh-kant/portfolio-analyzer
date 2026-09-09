# Momentum scanner repair plan

Prepared 2026-09-08. Planning document, not authorization to change trading rules.

## Purpose and scope

Make the paper scanner's decisions, fills, risk limits and records trustworthy
before evaluating profitability. A working ECS process and passing unit tests
are not sufficient evidence of correct trading or low risk.

The user requested this plan in a separate task. That task should verify and
refine the plan, with file references and acceptance tests. It should not deploy,
rotate credentials, place orders, rewrite past results or start new backtests.

The source checkout is /Users/ritesh/codebase/ritesh-codebase/portfolio-analyzer.
Much of the scanner and research is untracked or modified. A new Git worktree may
not contain these files. Read the original checkout for evidence; never reset it,
commit its unrelated changes or copy its secrets into a worktree. Record how a
later implementation will capture the relevant source and lockfile reproducibly.

## Current operation

- AWS region ap-south-1; cluster portfolio-analyzer-mt-prod; stack mt-scanner-prod.
- CloudWatch group /ecs/portfolio-analyzer/mt-scanner-prod.
- Task definition last verified: mt-scanner-prod:2; image tag 2026-09-07-2.
- Read-only Upstox Analytics token; no broker order integration. Paper only.
- Weekday start 09:05 IST, process exit 15:35 IST; holiday check in application.
- Strategy catalyst_first_pullback: MA9 setup only, ordinal 1, day change 4-8%,
  relative volume >=3, trend_full exit, one position per symbol-day.
- Non-catalyst paper trades are the intentional control group, not a gate bug.
- First session completed with zero paper positions. This does not prove the
  scanner rejected every signal correctly.
- Existing 16:00 Codex follow-up is a thread automation. Do not assume it is an
  AWS-resident health monitor independent of the user's computer; verify its
  execution dependency before describing monitoring coverage.

## Findings to reproduce

All source paths below are relative to apps/signal-engine/src/momentum_trader
unless explicitly qualified. Separate demonstrated code paths from hypotheses.

1. **Retrospective fills:** scanner._process handles completed bars; engine.step
   fills pending trades at that bar's earlier open. Capture exchange time,
   receipt time, signal-decision time and simulated-fill time. Use an executable
   quote arriving after the decision; label replay-only OHLC estimates separately.

2. **Intrabar stop ordering:** engine.step calls exits.update_high before checking
   the same bar's low. A new breakeven stop can bind against a low that occurred
   before the high. false_break also executes before a hard stop and can hide a
   stop breach behind a later close. Resolve live events chronologically; for
   historical OHLC, declare ambiguity and use conservative or bounded outcomes.
   Do not claim a stop guarantees its exact price.

3. **Position cap bypass:** scanner._process drops pending entries only after
   step has filled them. Several pending names can exceed mt_max_positions.
   Reserve risk/capital before filling, account for pending reservations, and
   release reservations on rejection, expiration or close. Test 4 open plus
   3 pending with a cap of 5, including multiple symbols in one processing pass.

4. **Incomplete trade persistence:** a trade can open and close inside one step.
   scanner only calls ledger.opened if a position remains afterward; ledger.closed
   updates an existing Mongo record without upsert. The CSV can contain a trade
   absent from Mongo and the entry notification. Produce ordered lifecycle events
   with durable trade IDs, idempotent writes and reconciliation. Test same-step
   entry/stop, retries, database outage and crash between writes.

5. **Repeated/stale candles and end of day:** no last-processed timestamp gate
   exists in scanner._process. A stale bar can be processed again and fill a
   pending entry on the trigger bar. Engine close time uses the last bar's time,
   so a halted symbol may never reach its close deadline. Separate wall-clock
   deadlines from quote times, block stale entries, expire pending orders, and
   report unpriced/unresolved positions instead of inventing an end-of-day fill.

6. **Recovery and duplicate workers:** state is in memory and not restored;
   restarts seed candles but not positions, daily risk or pending orders. Add
   checkpoints, replay with deduplication and one active worker/session lease.
   Verify exit behavior after feed loss, restart and duplicate scheduled launch.

7. **Early-entry blind spot:** setups.ma_pullback requires 25 current-session
   five-minute bars, so no entry before about 11:20 IST. Scanner.prepare also
   leaves DayState warmup_1m, warmup_5m and prev_day empty despite loading history.
   Restore live/backtest parity for existing inputs. Redesigning early entries
   with prior-session averages is a separately registered strategy change:
   simply concatenating sessions must not turn yesterday's price pattern into
   today's pullback. Define session boundaries and actual signal-availability time.

8. **Allowed setup hidden:** engine picks found[0] before applying allowed_setups.
   A disallowed bull flag/flat top can hide an allowed MA9 setup on the same bar.
   Select allowed setups before priority resolution; reproduce simultaneous setups.

9. **Missing eligibility data:** universe.load_facts/passes_static permit missing
   data. Circuit band, surveillance, series, float and promoter checks are skipped.
   Separate mandatory trading eligibility from experimental float criteria; use
   dated authoritative sources and fail closed when required data is absent or
   stale. Validate partial rows, NaN strings and per-symbol coverage. bool(facts)
   is not proof that every traded name had its eligibility checked.

10. **Catalyst labels lack failure and provenance states:** scanner maps database
    failure to catalyst=0. A connection alone does not prove news ingestion is
    recent. catalyst.py tests event category and ignores direction; an earnings
    event is not necessarily positive news. Store source ID/URL, publication,
    ingestion and classification times, event linkage and data-health status.
    Use present/absent/unknown; unknown cannot enter the control group. Confirm
    that classifier fields match the stored schema without exposing documents
    containing personal information. Any new positive-news selection requires a
    new hypothesis. Define publication versus bar-open versus decision cutoff.

11. **Risk controls incomplete:** defaults are INR 500 per trade, INR 50,000
    notional per trade and 5 positions. Add account capital, combined open and
    pending risk, daily loss (including open losses/costs), and sector/event
    concentration limits. Select and document paper assumptions; no user-approved
    capital allocation exists for real trading. Volume and volatility are not
    substitutes for spread, depth and executable liquidity checks.

12. **Credential exposure:** prior AWS logs visibly included a Telegram bot token
    in an HTTP request URL. Do not repeat or fetch that value for this plan.
    Plan redaction before log emission, a synthetic-secret regression test,
    restricted log access and bot-token rotation/redeployment through the proper
    credential owner. Deleting logs alone does not revoke a credential. Review
    broad SSM reads and remove unrelated provider-secret loading from this task.

13. **Misleading reports and incomplete observability:** CSV/position target fields
    still contain a 2R target even in no-target mode. Logs don't clearly separate
    rejected candidates from eligible signals; end-of-day summary is sent through
    Telegram but not recorded as a structured durable summary. Add strategy/config
    version, decision and rejection reason, data freshness, observed fill/cost
    assumptions, actual configured exit mode and unresolved-position count.
    Report null for unused targets. Add AWS-side missing-start, stale-feed,
    unexpected-stop, database failure and missing-end-of-day detection, without
    treating a task's RUNNING status as application health. Verify closure before
    returning success; preserve meaningful failure exit codes.

14. **Research protocol needs an audit:** current registration requires 30 trades
    per group and annualised Sharpe. Thirty is an initial screen, not live-money
    approval. Document daily equity aggregation including no-trade days, actual
    holding-window benchmark versus full-day benchmark, day/event clustering,
    multiple tests and DSR (Deflated Sharpe Ratio), drawdowns and uncertainty.
    Do not silently change the locked gates. Append a dated correction or register
    a new protocol before collecting a new confirmatory sample. Do not combine
    records produced by materially different engine versions.

## Delivery order and acceptance

### Phase 0: Evidence and reproducible baseline

Inventory dirty/untracked source, lockfile and deployed image. Preserve prior
paper records with their version and defects. Produce tiny deterministic
reproductions for findings 1-8. Identify further suspected issues (including
seeded volume, feed gaps, incomplete five-minute candles and pivot-confirmation
delay) without declaring them proven before reproduction. No profitability
optimization is needed to demonstrate engineering faults.

### Phase 1: Execution, risk and persistence correctness

Address event chronology, allowed-setup selection, reservations, lifecycle
persistence, stale-data checks, clock-driven shutdown and recovery. Acceptance:
no fill predates its decision; no stale/repeated event creates a new trade;
limits hold across concurrent pending entries; every entry and exit is persisted
once; same-step closes reconcile; ambiguous prices remain explicitly labelled.

### Phase 2: Input integrity and operations

Address eligibility, catalyst unknown states, indicator-history parity, logging
redaction, credential remediation and AWS monitoring. Test feed/database failures,
worker death, duplicate startup, market holiday, late start and market close
without fresh ticks. Verify both data freshness and account state.

### Phase 3: Separate strategy revision

If early first-pullback entry is desired, pre-register its exact session-aware
definition and new numerical rejection criteria before implementing that rule.
Do not add indicator, news-direction or support/resistance filters by tuning old
outcomes. Retain no fixed profit target and paper-only operation. Audit the
claimed inventory of used/untouched data; do not open any holdout without explicit
authorization. Existing outcomes are diagnostic, not fresh confirmation.

### Phase 4: Reviewed paper rollout

Propose separate small changes and acceptance tests; validate the container with
recorded event replays and AWS no-order dry run. Define deploy, rollback and data
version boundaries. Never run two strategy workers writing the same session.
Watch complete sessions including a trade lifecycle and outage drill before
calling paper operation reliable. Reliability checks and profit validation are
separate gates. This planning task ends with the reviewable plan, not deployment.

## Suggested model allocation

GPT-5.6 Terra with high reasoning is a reasonable implementation candidate for
bounded fixes with explicit fixtures and acceptance criteria. That is an engineering
judgment, not a measured guarantee on this repository. Use an independent stronger
review for fill chronology, concurrent risk, recovery and research validity.
The user asked for advice about Terra, not to force the new task's model setting.
