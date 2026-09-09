"""Live paper-trading scanner — one long-lived process per session (ECS Fargate
task, or `make mt-scan` locally).

    09:05  start: token → instruments → universe → prev closes, turnover, RVOL
           profiles (REST, cached) → subscribe FULL feed for every name
    09:15… every minute, ~2s after the bar closes: for each name with a new
           closed 1-min bar, run engine.step(); log candidates, open/close paper
           positions in Mongo `mt_*` + the forward CSV; Telegram on entry/exit
    15:35  EOD summary, disconnect, exit 0

The engine is the same code bt17 replays historically, so the forward log and
the backtest are directly comparable. Long-only, paper-only: there is no order
module in this file by design (hypothesis v2 §3, no-relax rules).
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Any

import pandas as pd

from src.config import Settings
from src.news_trader import telegram
from src.news_trader.market_calendar import is_trading_day
from src.news_trader.trailing_sl import calc_costs

from . import universe
from .bars import IST, BarBuilder
from .catalyst import hard_catalyst_detail
from .engine import (
    ENGINE_VERSION,
    FILL_OBSERVED_QUOTE,
    CatalystResult,
    DayState,
    EngineConfig,
    build_cum_volume_profile,
    fill_pending_from_quote,
    manage_position_from_quote,
    quote_is_fresh,
    resample_5m,
    step,
)
from .exits import MODE_FIXED, MODE_TREND_FULL
from .indicators import round_levels_above
from .ledger import PaperLedger
from .risk import TradePlan, plan_trade
from .upstox import Instrument, UpstoxAuthError, UpstoxClient
from .upstox_auth import read_token_ssm

logger = logging.getLogger("mt.scanner")

SESSION_START = (9, 15)
SESSION_END = (15, 35)
PROFILE_DAYS = 20
STRATEGY_BASELINE = "baseline"
STRATEGY_CATALYST_FIRST_PULLBACK = "catalyst_first_pullback"


def _strategy_config(settings: Settings) -> EngineConfig:
    """Return the paper-only engine configuration selected by `MT_STRATEGY`."""
    if settings.mt_strategy == STRATEGY_BASELINE:
        return EngineConfig(risk_inr=settings.mt_risk_inr,
                            max_notional_inr=settings.mt_max_notional_inr,
                            fill_mode=FILL_OBSERVED_QUOTE)
    if settings.mt_strategy == STRATEGY_CATALYST_FIRST_PULLBACK:
        return EngineConfig(
            risk_inr=settings.mt_risk_inr,
            max_notional_inr=settings.mt_max_notional_inr,
            exit_mode=MODE_TREND_FULL,
            allowed_setups=("ma9_pullback",),
            allowed_pullback_ordinals=(1,),
            fill_mode=FILL_OBSERVED_QUOTE,
        )
    raise ValueError(
        f"unknown MT_STRATEGY {settings.mt_strategy!r}; expected "
        f"{STRATEGY_BASELINE!r} or {STRATEGY_CATALYST_FIRST_PULLBACK!r}"
    )


def _market_data_token(settings: Settings, ssm_token: str | None = None) -> str:
    """Prefer the year-long read-only token over the daily OAuth credential."""
    return settings.upstox_analytics_token or settings.upstox_access_token or ssm_token or ""


def _repo_path(p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    root = Path(__file__).resolve().parents[4]
    return (root / path) if root.exists() else Path.cwd() / path


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz=IST)


class Scanner:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.cfg = _strategy_config(settings)
        self.cache = _repo_path(settings.mt_cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        configured = settings.upstox_analytics_token or settings.upstox_access_token
        token = _market_data_token(
            settings, None if configured else read_token_ssm(os.getenv("STAGE", "dev"))
        )
        self.client = UpstoxClient(token, cache_dir=self.cache)
        self.builder = BarBuilder()
        self.states: dict[str, DayState] = {}        # instrument key → state
        self.inst_by_key: dict[str, Instrument] = {}
        self.turnover: dict[str, float] = {}
        self._stop = threading.Event()
        self._db: Any = None
        self._signals: Any = None
        self._ledger: PaperLedger | None = None
        self._feed_status = "init"
        self._session_id = ""
        self._lease_owner = str(uuid.uuid4())
        self._state_lock = threading.RLock()
        self._fatal_error = False

    # ── notifications ─────────────────────────────────────────────────────────

    def _tg(self, text: str) -> None:
        telegram._send(self.s.telegram_bot_token, self.s.telegram_chat_id, f"🧭 <b>MT</b> {text}")

    # ── startup ───────────────────────────────────────────────────────────────

    def _connect_db(self) -> None:
        try:
            from pymongo import MongoClient
            client: Any = MongoClient(self.s.mongodb_uri, serverSelectionTimeoutMS=5000)
            self._db = client[self.s.mongodb_db_name]
            self._db.list_collection_names()
            self._signals = self._db["nt_signals"]
        except Exception as exc:  # noqa: BLE001
            # Exception strings may include credential-bearing connection URLs.
            logger.warning("Mongo unavailable type=%s", type(exc).__name__)
            self._db = None
            self._signals = None

    def dry_run(self) -> int:
        """Verify live-paper dependencies without recording a candidate or trade."""
        if not self.client.token:
            print("FAIL: no UPSTOX_ANALYTICS_TOKEN or UPSTOX_ACCESS_TOKEN configured")
            return 3
        try:
            insts = self.client.nse_equities()
            inst = insts.get("RELIANCE")
            if inst is None:
                print("FAIL: RELIANCE is absent from the Upstox NSE instrument list")
                return 4
            bars = self.client.intraday_1m(inst.key)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: Upstox REST market-data check failed: {exc}")
            return 4

        status: list[str] = []
        ready = threading.Event()

        def _status(value: str) -> None:
            status.append(value)
            ready.set()

        streamer: object | None = None
        try:
            streamer = self.client.stream([inst.key], lambda *_args: None, on_status=_status)
            ready.wait(10)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL: Upstox WebSocket check failed: {exc}")
            return 4
        finally:
            if streamer is not None:
                try:
                    streamer.disconnect()  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass
        if "open" not in status:
            print(f"FAIL: Upstox WebSocket did not open (status={status or ['no response']})")
            return 4

        self._connect_db()
        if self._signals is None:
            print("FAIL: Mongo/news signals unavailable; catalyst labels would all be zero")
            return 5

        log_path = _repo_path(self.s.mt_log_csv)
        probe = log_path.parent / f".{log_path.name}.dry-run"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok\n")
            probe.unlink()
        except OSError as exc:
            print(f"FAIL: paper-log path is not writable ({log_path}): {exc}")
            return 6

        print("PASS: no-order dry run")
        print(f"  strategy={self.s.mt_strategy}")
        print(f"  REST={inst.symbol} intraday bars={len(bars)}")
        print("  WebSocket=open")
        print("  catalyst database=connected")
        print(f"  paper log={log_path}")
        return 0

    def _catalyst(self, symbol: str, at: pd.Timestamp) -> CatalystResult:
        if self._signals is None:
            return CatalystResult(status="unknown")
        try:
            return hard_catalyst_detail(self._signals, symbol, at)
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalyst lookup failed type=%s", type(exc).__name__)
            return CatalystResult(status="unknown")

    def prepare(self, today: date) -> list[str]:
        """Resolve the universe and pull the per-name context. Returns instrument keys.

        Historical/daily candles are public on Upstox, so this works without a
        token; the token is required by run() for the live feed."""
        insts = self.client.nse_equities()
        facts_path = _repo_path(self.s.mt_universe_csv)
        facts = universe.load_facts(facts_path)
        extra = [_repo_path(p.strip())
                 for p in self.s.mt_extra_universe_files.split(",") if p.strip()]
        symbols = universe.base_symbols(extra)
        self._connect_db()
        self._ledger = PaperLedger(self._db, _repo_path(self.s.mt_log_csv), bool(facts))
        self._session_id = f"{today.isoformat()}:{self.s.mt_strategy}"

        keys: list[str] = []
        start_hist = today - timedelta(days=45)
        skipped: dict[str, int] = {}
        for sym in symbols:
            inst = insts.get(sym)
            if inst is None:
                skipped["no_instrument"] = skipped.get("no_instrument", 0) + 1
                continue
            ok, why = universe.passes_static(facts.get(sym), require_facts=True)
            if not ok:
                skipped[why.split(":")[0]] = skipped.get(why.split(":")[0], 0) + 1
                continue
            daily = self._cached_daily(inst, start_hist, today - timedelta(days=1))
            if daily.empty or len(daily) < 5:
                skipped["no_daily"] = skipped.get("no_daily", 0) + 1
                continue
            prev_close = float(daily["close"].iloc[-1])
            turnover_cr = float((daily["close"] * daily["volume"]).iloc[-20:].mean() / 1e7)
            ok, why = universe.passes_dynamic(prev_close, turnover_cr, require_turnover=True)
            if not ok:
                skipped[why] = skipped.get(why, 0) + 1
                continue
            hist_1m = self.client.cached_1m(
                inst, today - timedelta(days=35), today - timedelta(days=1)
            )
            profile = build_cum_volume_profile(hist_1m, PROFILE_DAYS) if not hist_1m.empty else None
            closes = daily["close"]
            prev_gainer = len(closes) >= 2 and float(closes.iloc[-1] / closes.iloc[-2] - 1) >= 0.04
            self.states[inst.key] = DayState(symbol=sym, prev_close=prev_close,
                cum_vol_profile=profile, prev_day_gainer=prev_gainer,
                warmup_1m=hist_1m, warmup_5m=resample_5m(hist_1m),
                prev_day={name: float(daily[name].iloc[-1]) for name in ("high", "low", "close")})
            self.inst_by_key[inst.key] = inst
            self.turnover[sym] = turnover_cr
            keys.append(inst.key)
        logger.info("universe ready: %d names tradeable, skipped=%s", len(keys), skipped)
        self._tg(f"ready {today}: {len(keys)} names in scan, skipped {skipped}")
        return keys

    def _cached_daily(self, inst: Instrument, start: date, end: date) -> pd.DataFrame:
        f = self.cache / "daily" / f"{inst.symbol.replace('&', '_')}_{end.isoformat()}.parquet"
        if f.exists():
            return pd.read_parquet(f)
        df = self.client.daily(inst.key, start, end)
        f.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(f)
        return df

    # ── main loop ─────────────────────────────────────────────────────────────

    def _on_tick(self, key: str, ts_ms: int, ltp: float, vtt: float | None) -> None:
        if key not in self.states or self._stop.is_set():
            return
        exchange_time = pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_convert(IST)
        receipt_time = _now()
        if not quote_is_fresh(exchange_time, receipt_time, ltp):
            return
        with self._state_lock:
            if self._stop.is_set() or not quote_is_fresh(exchange_time, _now(), ltp):
                return
            try:
                self._handle_tick(key, ts_ms, ltp, vtt, exchange_time, receipt_time)
            except Exception as exc:  # noqa: BLE001
                logger.error("paper tick failed type=%s; stopping", type(exc).__name__)
                self._fatal_error = True
                self._stop.set()

    def _handle_tick(self, key: str, ts_ms: int, ltp: float, vtt: float | None,
                     exchange_time: pd.Timestamp, receipt_time: pd.Timestamp) -> None:
        assert self._ledger is not None
        self.builder.on_tick(key, ts_ms, ltp, vtt)
        st = self.states[key]
        n_closed = len(st.closed)
        manage_position_from_quote(st, exchange_time=exchange_time,
                                   receipt_time=receipt_time, price=ltp, cfg=self.cfg)
        for trade in st.closed[n_closed:]:
            self._record_close(trade)
        if st.pending is None:
            return
        admitted, reason = self._admit_pending(st, price=ltp)
        if not admitted:
            logger.info("dropping pending %s reason=%s", st.symbol, reason)
            st.pending = None
            return
        position = fill_pending_from_quote(
            st, exchange_time=exchange_time, receipt_time=receipt_time,
            price=ltp, cfg=self.cfg, bars_1m=self.builder.closed_bars(key, receipt_time),
        )
        if position is not None:
            self._record_open(position)

    def _record_close(self, trade: Any) -> None:
        assert self._ledger is not None
        self._ledger.closed(trade, round_levels_above(trade.entry)[0])
        mark = "✅" if trade.net_inr > 0 else "❌"
        self._tg(f"{mark} EXIT <b>{trade.cand.symbol}</b> {trade.exit_reason} "
                 f"@₹{trade.exit:.2f} net ₹{trade.net_inr:,.0f}")

    def _record_open(self, position: Any) -> None:
        assert self._ledger is not None
        self._ledger.opened(position)
        exit_note = (f"target ₹{position.plan.target:.2f}"
                     if self.cfg.exit_mode == MODE_FIXED else "negative-signal exit")
        self._tg(f"📝 ENTER <b>{position.cand.symbol}</b> {position.cand.setup.name} "
                 f"@₹{position.plan.entry:.2f} ×{position.plan.qty} stop ₹{position.plan.stop:.2f} "
                 f"{exit_note} cat={position.cand.catalyst}")

    def _open_count(self) -> int:
        return sum(1 for st in self.states.values() if st.position is not None)

    def _pending_plan(self, st: DayState, price: float | None = None) -> TradePlan | None:
        if st.pending is None:
            return None
        cand = st.pending.cand
        return plan_trade(
            cand.setup.trigger if price is None else price, cand.setup.stop,
            risk_inr=self.cfg.risk_inr,
            max_notional_inr=self.cfg.max_notional_inr, rr=self.cfg.rr,
        )

    def _risk_totals(self, filling: DayState | None = None,
                     price: float | None = None) -> tuple[float, float, float]:
        """Return notional, hard-stop risk, and realised losses with reservations."""
        notional = risk = realised_loss = 0.0

        def loss_at_stop(plan: TradePlan) -> float:
            return (plan.risk_inr
                    + calc_costs(plan.entry, plan.stop, plan.qty, direction="long")["total"]
                    + (plan.entry + plan.stop) * plan.qty * self.cfg.stress_slip)

        for state in self.states.values():
            if state.position is not None:
                notional += state.position.plan.notional_inr
                risk += loss_at_stop(state.position.plan)
            pending = self._pending_plan(state, price if state is filling else None)
            if pending is not None:
                notional += pending.notional_inr
                risk += loss_at_stop(pending)
            realised_loss += sum(min(0.0, trade.net_inr) for trade in state.closed)
        return notional, risk, realised_loss

    def _admit_pending(self, st: DayState, price: float | None = None) -> tuple[bool, str]:
        if self._open_count() >= self.s.mt_max_positions:
            return False, "max_positions"
        if self._pending_plan(st, price) is None:
            return False, "invalid_pending_plan"
        notional, risk, realised_loss = self._risk_totals(st, price)
        if notional > self.s.mt_total_capital_inr:
            return False, "capital_limit"
        # Conservative until durable mark-to-market is introduced: reserve each
        # active hard-stop loss against the daily loss budget.
        if -realised_loss + risk > self.s.mt_daily_loss_limit_inr:
            return False, "daily_loss_limit"
        return True, "ok"

    def _process(self, now: pd.Timestamp) -> None:
        with self._state_lock:
            if not self._stop.is_set():
                self._process_locked(now)

    def _process_locked(self, now: pd.Timestamp) -> None:
        assert self._ledger is not None
        if not self._ledger.acquire_session(self._session_id, self._lease_owner):
            raise RuntimeError("lost single-writer session lease")
        for key, st in self.states.items():
            bars = self.builder.closed_bars(key, now)
            session_start = datetime.combine(now.date(), datetime.min.time()).replace(
                hour=SESSION_START[0], minute=SESSION_START[1]
            )
            session_start = pd.Timestamp(session_start, tz=IST)
            bars = bars.loc[bars.index >= session_start]
            if bars.empty:
                continue
            unseen = bars if st.last_processed_bar is None else bars.loc[
                bars.index > st.last_processed_bar
            ]
            for bar_time in unseen.index:
                # Pass the complete history up to this event, but never process
                # a prior event twice. This also handles delayed loops without
                # silently skipping intermediate closed bars.
                history = bars.loc[:bar_time]
                n_c, n_x = len(st.candidates), len(st.closed)
                had_pos = st.position

                # Drop unavailable slots early. Live aggregate admission is
                # checked again at the exact quote price under the same lock.
                if st.pending is not None and self._open_count() >= self.s.mt_max_positions:
                    logger.info("max positions reached — dropping pending %s", st.symbol)
                    st.pending = None

                previous_exit = None if had_pos is None else had_pos.pending_exit
                step(st, history, self.cfg, self._catalyst, decision_time=now)
                # DB lookups can take time; no tick observed before completion
                # may be used as the subsequent entry or signal-exit price.
                completed_at = _now()
                if (st.position is not None and st.position.pending_exit is not None
                        and previous_exit is None):
                    reason, decided_at = st.position.pending_exit
                    st.position.pending_exit = (reason, max(decided_at, completed_at))
                for c in st.candidates[n_c:]:
                    c.decision_time = completed_at
                    self._ledger.candidate(c)
                    logger.info("candidate %s %s trig=%.2f stop=%.2f chg=%.1f%% rvol=%.1f cat=%d",
                                c.symbol, c.setup.name, c.setup.trigger, c.setup.stop,
                                c.day_chg_pct, c.rvol, c.catalyst)
                if st.position is not None and had_pos is None:
                    self._record_open(st.position)
                for t in st.closed[n_x:]:
                    self._record_close(t)
                st.last_processed_bar = bar_time

    def _eod_summary(self) -> None:
        closed = [t for st in self.states.values() for t in st.closed]
        cands = sum(len(st.candidates) for st in self.states.values())
        net = sum(t.net_inr for t in closed)
        wins = sum(1 for t in closed if t.net_inr > 0)
        by_setup: dict[str, int] = {}
        for t in closed:
            by_setup[t.cand.setup.name] = by_setup.get(t.cand.setup.name, 0) + 1
        unresolved = [st for st in self.states.values() if st.position is not None]
        for st in unresolved:
            st.unresolved_reason = "eod_without_fresh_quote"
        for st in self.states.values():
            if st.pending is not None:
                logger.info("expiring pending %s at end of day", st.symbol)
                st.pending = None
        if self._ledger is not None:
            self._ledger.session_summary({
                "session_id": self._session_id,
                "ended_at": _now().to_pydatetime(),
                "strategy": self.s.mt_strategy,
                "engine_version": ENGINE_VERSION,
                "feed_status": self._feed_status,
                "candidate_count": cands,
                "closed_trade_count": len(closed),
                "net_inr": net,
                "unresolved_position_count": len(unresolved),
                "unresolved_symbols": [st.symbol for st in unresolved],
                "paper": True,
                "status": "failed" if self._fatal_error or unresolved else "completed",
            })
        self._tg(f"EOD: {cands} candidates, {len(closed)} trades, {wins}W/{len(closed) - wins}L, "
                 f"net ₹{net:,.0f}, unresolved={len(unresolved)}, setups={by_setup}, "
                 f"feed={self._feed_status}")

    def run(self) -> int:
        today = _now().date()
        if not self.s.mt_bypass_market_hours and not is_trading_day(today):
            logger.info("%s is not a trading day — exiting", today)
            return 0
        if not self.client.token:
            self._tg("❌ no Upstox market-data token — exiting")
            logger.error("auth: missing UPSTOX_ANALYTICS_TOKEN or UPSTOX_ACCESS_TOKEN")
            return 3
        try:
            keys = self.prepare(today)
        except UpstoxAuthError:
            logger.error("market-data authentication failed")
            return 3
        except Exception as exc:  # noqa: BLE001
            logger.error("paper startup failed type=%s", type(exc).__name__)
            return 7
        if not keys:
            self._tg("❌ empty universe — exiting")
            return 4
        assert self._ledger is not None
        if not self._ledger.acquire_session(self._session_id, self._lease_owner):
            logger.error("single-writer session lease unavailable")
            return 5
        if self._ledger.has_open_positions_for_day(datetime.combine(today, datetime.min.time())):
            logger.error("unresolved paper positions; reconciliation required")
            return 7

        def _status(s: str) -> None:
            self._feed_status = "error" if s.startswith("error") else s
            logger.info("feed %s", self._feed_status)
            if s.startswith("error"):
                self._tg("⚠️ feed error")

        def _sig(_signum: int, _frame: FrameType | None) -> None:
            self._stop.set()
        signal.signal(signal.SIGTERM, _sig)
        signal.signal(signal.SIGINT, _sig)

        end_t = datetime(2000, 1, 1, *SESSION_END).time()
        exit_code = 0
        streamer: Any = None
        try:
            # Seed supplies context only; the engine cannot fill an expired
            # historical signal. The non-expiring guard covers a slow seed.
            if _now().time() > datetime(2000, 1, 1, *SESSION_START).time():
                for key in keys:
                    self.builder.seed(key, self.client.intraday_1m(key))
            streamer = self.client.stream(keys, self._on_tick, mode="full", on_status=_status)
            while not self._stop.is_set():
                now = _now()
                if now.time() >= end_t and not self.s.mt_bypass_market_hours:
                    break
                # wake ~2s after each minute boundary so the previous bar is closed
                sleep_s = 62 - now.second if now.second < 2 else 62 - now.second
                self._stop.wait(min(sleep_s, 60))
                if self._stop.is_set():
                    break
                try:
                    self._process(_now())
                except Exception:  # noqa: BLE001
                    logger.error("paper process loop failed; reconciliation required")
                    exit_code = 7
                    break
            if self._stop.is_set():
                exit_code = 7
        except Exception as exc:  # noqa: BLE001
            logger.error("paper session failed type=%s", type(exc).__name__)
            exit_code = 7
        finally:
            self._stop.set()
            if self._feed_status != "open":
                exit_code = 7
            if streamer is not None:
                try:
                    streamer.disconnect()
                except Exception:  # noqa: BLE001
                    exit_code = 7
            # Wait for any callback already holding the lock before finalizing.
            with self._state_lock:
                if self._fatal_error or any(st.position is not None for st in self.states.values()):
                    exit_code = 7
                self._fatal_error = exit_code != 0
                try:
                    self._eod_summary()
                    if exit_code == 0:
                        self._ledger.release_session(self._session_id, self._lease_owner)
                except Exception as exc:  # noqa: BLE001
                    logger.error("paper finalization failed type=%s", type(exc).__name__)
                    exit_code = 7
        return exit_code


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    args = sys.argv[1:] if argv is None else argv
    scanner = Scanner(settings)
    if "--dry-run" in args:
        return scanner.dry_run()
    if "--prepare-only" in args:
        # startup path check (no token needed): universe, filters, profiles
        keys = scanner.prepare(_now().date())
        sample = [scanner.inst_by_key[k].symbol for k in keys[:15]]
        print(f"{len(keys)} names ready; first: {sample}")
        return 0
    return scanner.run()


if __name__ == "__main__":
    sys.exit(main())
