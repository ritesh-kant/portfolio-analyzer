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
from datetime import date, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Any

import pandas as pd

from src.config import Settings
from src.news_trader import telegram
from src.news_trader.market_calendar import is_trading_day

from . import universe
from .bars import IST, BarBuilder
from .catalyst import hard_catalyst
from .engine import (
    FILL_FUTURE_TRIGGER,
    DayState,
    EngineConfig,
    Position,
    Rejection,
    build_cum_volume_profile,
    fill_pending_quote,
    step,
)
from .exits import MODE_FIXED, MODE_TREND_FULL, MODE_TREND_RESISTANCE_STATE
from .indicators import round_levels_above
from .ledger import PaperLedger
from .upstox import Instrument, UpstoxAuthError, UpstoxClient
from .upstox_auth import read_token_ssm

logger = logging.getLogger("mt.scanner")

SESSION_START = (9, 15)
SESSION_END = (15, 35)
PROFILE_DAYS = 20
STRATEGY_BASELINE = "baseline"
STRATEGY_CATALYST_FIRST_PULLBACK = "catalyst_first_pullback"
STRATEGY_ATTENTION_1M = "attention_1m"
STRATEGY_ATTENTION_1M_RESISTANCE_STATE = "attention_1m_resistance_state"


def _strategy_config(settings: Settings) -> EngineConfig:
    """Return the paper-only engine configuration selected by `MT_STRATEGY`."""
    if settings.mt_strategy == STRATEGY_BASELINE:
        return EngineConfig(risk_inr=settings.mt_risk_inr,
                            max_notional_inr=settings.mt_max_notional_inr)
    if settings.mt_strategy == STRATEGY_CATALYST_FIRST_PULLBACK:
        return EngineConfig(
            risk_inr=settings.mt_risk_inr,
            max_notional_inr=settings.mt_max_notional_inr,
            exit_mode=MODE_TREND_FULL,
            allowed_setups=("ma9_pullback",),
            allowed_pullback_ordinals=(1,),
        )
    if settings.mt_strategy == STRATEGY_ATTENTION_1M:
        return EngineConfig(
            risk_inr=settings.mt_risk_inr,
            max_notional_inr=settings.mt_max_notional_inr,
            exit_mode=MODE_TREND_FULL,
            fill_mode=FILL_FUTURE_TRIGGER,
            use_attention_entries=True,
            attention_day_chg_min=settings.mt_attention_day_chg_min,
            attention_rvol_min=settings.mt_attention_rvol_min,
        )
    if settings.mt_strategy == STRATEGY_ATTENTION_1M_RESISTANCE_STATE:
        return EngineConfig(
            risk_inr=settings.mt_risk_inr,
            max_notional_inr=settings.mt_max_notional_inr,
            exit_mode=MODE_TREND_RESISTANCE_STATE,
            fill_mode=FILL_FUTURE_TRIGGER,
            use_attention_entries=True,
            attention_day_chg_min=settings.mt_attention_day_chg_min,
            attention_rvol_min=settings.mt_attention_rvol_min,
            require_resistance_breakout=True,
        )
    raise ValueError(
        f"unknown MT_STRATEGY {settings.mt_strategy!r}; expected "
        f"{STRATEGY_BASELINE!r}, {STRATEGY_CATALYST_FIRST_PULLBACK!r}, "
        f"{STRATEGY_ATTENTION_1M!r}, or {STRATEGY_ATTENTION_1M_RESISTANCE_STATE!r}"
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
        token = _market_data_token(
            settings, read_token_ssm(os.getenv("STAGE", "dev"))
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
        self._state_lock = threading.RLock()
        self._tick_entries: list[Position] = []
        self._tick_rejections: list[Rejection] = []

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
            logger.warning("Mongo unavailable (%s) — CSV-only ledger, catalyst=0 for all", exc)
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

    def _catalyst(self, symbol: str, at: pd.Timestamp) -> tuple[int, str]:
        if self._signals is None:
            return 0, ""
        try:
            return hard_catalyst(self._signals, symbol, at)
        except Exception:  # noqa: BLE001
            logger.exception("catalyst lookup failed")
            return 0, ""

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

        keys: list[str] = []
        start_hist = today - timedelta(days=45)
        skipped: dict[str, int] = {}
        for sym in symbols:
            inst = insts.get(sym)
            if inst is None:
                skipped["no_instrument"] = skipped.get("no_instrument", 0) + 1
                continue
            ok, why = universe.passes_static(facts.get(sym))
            if not ok:
                skipped[why.split(":")[0]] = skipped.get(why.split(":")[0], 0) + 1
                continue
            daily = self._cached_daily(inst, start_hist, today - timedelta(days=1))
            if daily.empty or len(daily) < 5:
                skipped["no_daily"] = skipped.get("no_daily", 0) + 1
                continue
            prev_close = float(daily["close"].iloc[-1])
            turnover_cr = float((daily["close"] * daily["volume"]).iloc[-20:].mean() / 1e7)
            ok, why = universe.passes_dynamic(prev_close, turnover_cr)
            if not ok:
                skipped[why] = skipped.get(why, 0) + 1
                continue
            hist_1m = self.client.cached_1m(
                inst, today - timedelta(days=35), today - timedelta(days=1)
            )
            profile = build_cum_volume_profile(hist_1m, PROFILE_DAYS) if not hist_1m.empty else None
            closes = daily["close"]
            prev_gainer = len(closes) >= 2 and float(closes.iloc[-1] / closes.iloc[-2] - 1) >= 0.04
            # The resistance-state arm needs yesterday's anchors to determine
            # whether a confirmation is breaking a structural ceiling.  Keep
            # the frozen control's inputs unchanged.
            prev_day = None
            if self.cfg.require_resistance_breakout:
                prev_day = {
                    "high": float(daily["high"].iloc[-1]),
                    "low": float(daily["low"].iloc[-1]),
                    "close": prev_close,
                }
            self.states[inst.key] = DayState(
                symbol=sym, prev_close=prev_close, cum_vol_profile=profile,
                prev_day_gainer=prev_gainer, prev_day=prev_day,
            )
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
        if key not in self.states:
            return
        with self._state_lock:
            self.builder.on_tick(key, ts_ms, ltp, vtt)
            st = self.states[key]
            if self.cfg.fill_mode != FILL_FUTURE_TRIGGER or st.pending is None:
                return
            when = pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_convert(IST)
            if self._open_count() >= self.s.mt_max_positions:
                pending = st.pending
                rejection = Rejection(
                    symbol=st.symbol,
                    time=when,
                    reason="max_positions",
                    setup=pending.cand.setup.name,
                    trigger=pending.cand.setup.trigger,
                    observed_price=ltp,
                )
                st.pending = None
                st.rejections.append(rejection)
                self._tick_rejections.append(rejection)
                return
            bars = self.builder.closed_bars(key, when)
            n_r = len(st.rejections)
            pos = fill_pending_quote(st, when, ltp, self.cfg, bars)
            if pos is not None:
                self._tick_entries.append(pos)
            self._tick_rejections.extend(st.rejections[n_r:])

    def _open_count(self) -> int:
        return sum(1 for st in self.states.values() if st.position is not None)

    def _process(self, now: pd.Timestamp) -> None:
        assert self._ledger is not None
        entries: list[Position] = []
        attention_events: list[Any] = []
        candidates: list[Any] = []
        rejections: list[Rejection] = []
        closed: list[Any] = []

        # Snapshot completed bars and drain quote-thread events quickly. Mongo
        # and Telegram I/O are deliberately performed after releasing this
        # lock so a slow network write cannot make the live feed miss quotes.
        with self._state_lock:
            entries.extend(self._tick_entries)
            self._tick_entries.clear()
            rejections.extend(self._tick_rejections)
            self._tick_rejections.clear()
            snapshots = {
                key: self.builder.closed_bars(key, now)
                for key in self.states
            }

        for key, bars in snapshots.items():
            if bars.empty:
                continue
            with self._state_lock:
                st = self.states[key]
                n_c, n_x = len(st.candidates), len(st.closed)
                n_a, n_r = len(st.attention_events), len(st.rejections)
                had_pos = st.position
                if (
                    self.cfg.fill_mode == FILL_FUTURE_TRIGGER
                    and st.pending is not None
                    and st.pending.expires_at is not None
                    and now > st.pending.expires_at
                ):
                    fill_pending_quote(
                        st, now, float(bars["close"].iloc[-1]), self.cfg, bars
                    )
                had_pending = st.pending
                step(
                    # Catalyst is recorded metadata, not an entry gate. Resolve
                    # it below without holding the quote-thread lock.
                    st, bars, self.cfg, lambda _symbol, _at: (0, ""),
                    allow_replay_fill=self.cfg.fill_mode != FILL_FUTURE_TRIGGER,
                )
                if had_pending is None and st.pending is not None:
                    # Replace the bar-end approximation with the actual live decision time.
                    st.pending.decision_time = now
                    st.pending.expires_at = now + pd.Timedelta(
                        minutes=self.cfg.attention_pending_minutes
                    )
                if st.pending is not None and self._open_count() >= self.s.mt_max_positions:
                    pending = st.pending
                    st.rejections.append(Rejection(
                        symbol=st.symbol,
                        time=now,
                        reason="max_positions",
                        setup=pending.cand.setup.name,
                        trigger=pending.cand.setup.trigger,
                    ))
                    st.pending = None
                attention_events.extend(st.attention_events[n_a:])
                candidates.extend(st.candidates[n_c:])
                rejections.extend(st.rejections[n_r:])
                if st.position is not None and had_pos is None:
                    entries.append(st.position)
                closed.extend(st.closed[n_x:])

        for c in candidates:
            c.catalyst, c.event_type = self._catalyst(c.symbol, c.time)
        for a in attention_events:
            self._ledger.attention(a)
            logger.info(
                "attention %s reason=%s chg=%.1f%% rvol=%.1f tags=%s",
                a.symbol, a.reason, a.day_chg_pct, a.rvol, ",".join(a.candle_tags) or "-",
            )
            self._tg(
                f"👀 ATTENTION <b>{a.symbol}</b> {a.reason} "
                f"chg {a.day_chg_pct:.1f}% RVOL {a.rvol:.1f}x "
                f"tags={','.join(a.candle_tags) or '-'}"
            )
        for c in candidates:
            self._ledger.candidate(c)
            logger.info(
                "candidate %s %s trig=%.2f stop=%.2f chg=%.1f%% rvol=%.1f cat=%d",
                c.symbol, c.setup.name, c.setup.trigger, c.setup.stop,
                c.day_chg_pct, c.rvol, c.catalyst,
            )
        for rejection in rejections:
            self._ledger.rejected(rejection)
            logger.info(
                "rejected %s %s reason=%s trigger=%.2f observed=%s",
                rejection.symbol, rejection.setup, rejection.reason, rejection.trigger,
                "-" if rejection.observed_price is None else f"{rejection.observed_price:.2f}",
            )
        for p in entries:
            self._record_open(p)
        for t in closed:
            # `snapshots` holds the complete session through the bar which
            # produced the exit. Persist the raw data with the trade so its
            # eventual review chart is reproducible without calling Upstox.
            snapshot_key = next((k for k, st in self.states.items() if st.symbol == t.cand.symbol), None)
            chart_bars = (
                snapshots.get(snapshot_key, pd.DataFrame())
                if snapshot_key is not None
                else pd.DataFrame()
            )
            self._ledger.closed(t, round_levels_above(t.entry)[0], chart_bars)
            mark = "✅" if t.net_inr > 0 else "❌"
            self._tg(
                f"{mark} EXIT <b>{t.cand.symbol}</b> {t.exit_reason} "
                f"@₹{t.exit:.2f} net ₹{t.net_inr:,.0f}"
            )

    def _record_open(self, p: Position) -> None:
        assert self._ledger is not None
        self._ledger.opened(p)
        exit_note = (f"target ₹{p.plan.target:.2f}"
                     if self.cfg.exit_mode == MODE_FIXED else "negative-signal exit")
        self._tg(f"📝 ENTER <b>{p.cand.symbol}</b> {p.cand.setup.name} "
                 f"@₹{p.plan.entry:.2f} ×{p.plan.qty} stop ₹{p.plan.stop:.2f} "
                 f"{exit_note} cat={p.cand.catalyst}")

    def _eod_summary(self) -> None:
        closed = [t for st in self.states.values() for t in st.closed]
        cands = sum(len(st.candidates) for st in self.states.values())
        attention = sum(len(st.attention_events) for st in self.states.values())
        rejections = sum(len(st.rejections) for st in self.states.values())
        net = sum(t.net_inr for t in closed)
        wins = sum(1 for t in closed if t.net_inr > 0)
        by_setup: dict[str, int] = {}
        for t in closed:
            by_setup[t.cand.setup.name] = by_setup.get(t.cand.setup.name, 0) + 1
        self._tg(
            f"EOD: {attention} attention, {cands} candidates, {rejections} rejections, "
            f"{len(closed)} trades, {wins}W/{len(closed) - wins}L, net ₹{net:,.0f}, "
            f"setups={by_setup}, feed={self._feed_status}"
        )

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
        except UpstoxAuthError as exc:
            logger.error("auth: %s", exc)
            return 3
        if not keys:
            self._tg("❌ empty universe — exiting")
            return 4

        # late start / restart: seed today's bars over REST
        now = _now()
        if now.time() > datetime(2000, 1, 1, *SESSION_START).time():
            for key in keys:
                try:
                    self.builder.seed(key, self.client.intraday_1m(key))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("seed %s failed: %s", key, exc)

        def _status(s: str) -> None:
            self._feed_status = s
            logger.info("feed %s", s)
            if s.startswith("error"):
                self._tg(f"⚠️ feed {s}")

        streamer = self.client.stream(keys, self._on_tick, mode="full", on_status=_status)

        def _sig(_signum: int, _frame: FrameType | None) -> None:
            self._stop.set()
        signal.signal(signal.SIGTERM, _sig)
        signal.signal(signal.SIGINT, _sig)

        end_t = datetime(2000, 1, 1, *SESSION_END).time()
        try:
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
                    logger.exception("process loop error")
        finally:
            try:
                streamer.disconnect()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass
            self._eod_summary()
        return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if os.getenv("AWS_SECRETS_ENABLED", "").lower() == "true":
        from src.secrets import bootstrap_secrets
        bootstrap_secrets(stage=os.getenv("STAGE", "dev"))
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
