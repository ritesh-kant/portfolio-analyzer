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
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import FrameType
from typing import Any

import pandas as pd

from src.config import Settings
from src.news_trader import telegram
from src.news_trader.market_calendar import is_trading_day

from . import universe
from .bars import IST, SESSION_OPEN, BarBuilder
from .catalyst import hard_catalyst
from .discipline import DayDiscipline, DisciplineConfig
from .engine import (
    FILL_FUTURE_TRIGGER,
    GUIDE_PULLBACK_ORDINALS,
    VOL_BASELINE_MIN_BARS,
    ClosedTrade,
    DayState,
    EngineConfig,
    Position,
    Rejection,
    build_cum_volume_profile,
    fill_pending_quote,
    force_close,
    resample_5m,
    step,
)
from .exits import MODE_FIXED, MODE_TREND_FULL, MODE_TREND_RESISTANCE_STATE
from .indicators import round_levels_above
from .ledger import PaperLedger
from .news_context import recent_news
from .upstox import (
    Instrument,
    UpstoxAuthError,
    UpstoxClient,
    read_parquet_cache,
    write_parquet_cache,
)
from .upstox_auth import read_token_ssm

logger = logging.getLogger("mt.scanner")

SESSION_START = SESSION_OPEN
SESSION_END = (15, 35)
# Wall-clock backstop for the 15:15 close. `step()` only exits on a bar that
# ARRIVES, and reads the clock off that bar's own timestamp, so a position in a
# name that stops printing before 15:15 would otherwise be carried overnight —
# which this strategy must never do. One minute of grace lets the ordinary
# bar-driven `eod_close` fire first at its real price; whatever is still open at
# 15:16 is closed here at the last price we saw, tagged `eod_sweep` so the
# forward log can tell a stale-mark close apart from a real one.
EOD_SWEEP = (15, 16)
# Data-driven "the market is shut" backstop. If not one symbol in the universe
# has printed a bar by this time, the exchange is closed whatever
# `market_calendar` says, and the task exits instead of burning a Fargate day.
# 30 minutes past the open is far beyond any feed-connection delay and far
# inside the first print of every liquid NSE name.
NO_DATA_GRACE = (9, 45)
PROFILE_DAYS = 20
# Prior sessions of 1-minute bars kept per symbol to warm the indicators. The
# 5-minute 200 EMA needs 200 bars = 1,000 minutes ≈ 2.7 sessions, so 5 is the
# smallest round number that produces a value from the opening bell; the exit
# rules' own warm-up (`exits.WARMUP_BARS`) slices whatever it needs from this.
# Until 2026-09-15 the scanner passed NO warm-up at all, so every live exit
# indicator ran cold and the 5-minute trend context could not exist before
# 10:55 — see `engine._attention_context`.
WARMUP_SESSIONS = 5
STRATEGY_BASELINE = "baseline"
STRATEGY_CATALYST_FIRST_PULLBACK = "catalyst_first_pullback"
STRATEGY_ATTENTION_1M = "attention_1m"
STRATEGY_ATTENTION_1M_RESISTANCE_STATE = "attention_1m_resistance_state"
# The single forward arm (2026-09-12). It combines resting buy-stop fills,
# resistance-state exits, the resistance-breakout entry requirement, and the
# structural support/resistance position-management rules.
STRATEGY_ATTENTION_1M_MERGED = "attention_1m_merged"
# The Warrior transcript's own checklist, all of it, switched on together
# (2026-09-15, operator request "match all these"). It is the merged arm plus
# every entry rule the guide states that nothing enforced: a real micro
# pullback with light volume in it, a positive-and-open 1-minute MACD, first or
# second pullback only, entries confined to the morning volume peak, a 2:1
# target alongside the trend exits, and the account-level guardrails in
# `discipline.py`. Prior-session warm-up is part of the arm, not an extra: the
# peak-hours rule is unsatisfiable without it.
#
# ⚠ Two things this arm is NOT. It is not an attribution experiment — eleven
# rules move at once, so a result cannot be assigned to any one of them. And
# three of its components have already been measured and killed on this
# repo's own data: the pullback ordinal (BT17, anti p=0.469), the
# quality/selectivity bundle (p=0.979) and the 1-minute agreement gate (BT30,
# anti p=0.526) all failed to beat random deletion of the same number of
# trades. It supersedes `attention_1m_merged` at n=12 of its registered 30.
# research/hypotheses/2026-09-15-warrior-guide-strict.md
STRATEGY_WARRIOR_STRICT = "warrior_strict"


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
    if settings.mt_strategy == STRATEGY_ATTENTION_1M_MERGED:
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
    if settings.mt_strategy == STRATEGY_WARRIOR_STRICT:
        return EngineConfig(
            risk_inr=settings.mt_risk_inr,
            max_notional_inr=settings.mt_max_notional_inr,
            exit_mode=MODE_TREND_RESISTANCE_STATE,
            fill_mode=FILL_FUTURE_TRIGGER,
            use_attention_entries=True,
            attention_day_chg_min=settings.mt_attention_day_chg_min,
            attention_rvol_min=settings.mt_attention_rvol_min,
            require_resistance_breakout=True,
            # ── the guide's checklist ────────────────────────────────────────
            require_micro_pullback=True,
            require_light_pullback_volume=True,
            require_macd_positive_open=True,
            allowed_pullback_ordinals=GUIDE_PULLBACK_ORDINALS,
            peak_hours_only=True,
            peak_hours_end=_parse_hhmm(settings.mt_peak_hours_end),
            warm_context=True,
            vol_baseline_min_bars=VOL_BASELINE_MIN_BARS,
            use_fixed_target=True,
        )
    raise ValueError(
        f"unknown MT_STRATEGY {settings.mt_strategy!r}; expected "
        f"{STRATEGY_BASELINE!r}, {STRATEGY_CATALYST_FIRST_PULLBACK!r}, "
        f"{STRATEGY_ATTENTION_1M!r}, {STRATEGY_ATTENTION_1M_RESISTANCE_STATE!r}, "
        f"{STRATEGY_ATTENTION_1M_MERGED!r}, or {STRATEGY_WARRIOR_STRICT!r}"
    )


def _recent_sessions(hist_1m: pd.DataFrame, sessions: int) -> pd.DataFrame | None:
    """The last `sessions` calendar days of 1-minute bars, or None if empty.

    Bounded on purpose: `hist_1m` is 35 days for ~145 symbols, and the whole of
    it would be carried in memory for the entire session to feed indicators that
    look back at most `exits.WARMUP_BARS` bars.
    """
    if hist_1m is None or hist_1m.empty:
        return None
    days = sorted(set(hist_1m.index.normalize()))[-sessions:]
    recent = hist_1m[hist_1m.index.normalize().isin(days)]
    return None if recent.empty else recent


def _parse_hhmm(value: str) -> time:
    """"10:45" → time(10, 45). Raises rather than silently trading all day."""
    try:
        hh, mm = (int(part) for part in str(value).strip().split(":", 1))
        return time(hh, mm)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected HH:MM, got {value!r}") from exc


def _apply_env_overrides(cfg: EngineConfig, settings: Settings) -> EngineConfig:
    """Apply per-deployment switches on top of the strategy's own config.

    Applied here rather than inside each `_strategy_config` branch so every
    strategy honours the same env switches and no branch can silently miss one.
    """
    cfg.require_1m_agreement = settings.mt_require_1m_agreement
    cfg.require_rising_price_volume = settings.mt_require_rising_price_volume
    cfg.one_trade_per_day = settings.mt_one_trade_per_day
    return cfg


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
        _apply_env_overrides(self.cfg, settings)
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
        # Account-level guardrails (3 strikes / 50% give-back / size ladder).
        # They act on the DAY, across every symbol, so they live on the scanner
        # rather than in the per-symbol engine.
        self.discipline = DayDiscipline(
            DisciplineConfig(enabled=settings.mt_discipline)
        )
        self._full_risk_inr = settings.mt_risk_inr
        self._sync_risk()

    # ── daily guardrails ──────────────────────────────────────────────────────

    def _sync_risk(self) -> None:
        """Push the size ladder's current fraction into the engine config.

        Sizing happens inside `plan_trade`, which reads `cfg.risk_inr`, so the
        ladder is applied by changing that one number rather than by threading a
        multiplier through every call. Kept in sync immediately after each close
        so a fill arriving on the quote thread cannot use a stale size.
        """
        self.cfg.risk_inr = self.discipline.risk_inr(self._full_risk_inr)

    def _guard_pending(self, st: DayState, when: pd.Timestamp,
                       observed: float | None = None) -> bool:
        """Cancel an armed entry if the day has been halted. True = cancelled.

        Checked at both arming points (bar loop and quote thread) because a
        buy-stop armed before the third strike must not fill after it.
        """
        if st.pending is None:
            return False
        allowed, reason = self.discipline.can_trade()
        if allowed:
            return False
        pending = st.pending
        rejection = Rejection(
            symbol=st.symbol, time=when, reason=f"halted:{reason}",
            setup=pending.cand.setup.name, trigger=pending.cand.setup.trigger,
            observed_price=observed,
        )
        st.pending = None
        st.rejections.append(rejection)
        return True

    def _record_close(self, trade: ClosedTrade) -> None:
        """Fold a closed trade into the day's guardrails and re-size."""
        if not self.discipline.cfg.enabled:
            return
        was_halted = self.discipline.halted
        self.discipline.record(trade.net_inr)
        self._sync_risk()
        if self.discipline.halted and not was_halted:
            logger.warning("discipline: trading halted — %s (%s)",
                           self.discipline.halted_reason, self.discipline.summary())
            self._tg(f"🛑 HALTED <b>{self.discipline.halted_reason}</b> — "
                     f"{self.discipline.summary()}")

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
        self._ledger = PaperLedger(
            self._db, _repo_path(self.s.mt_log_csv), bool(facts), self.s.mt_strategy
        )

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
            warmup_1m = _recent_sessions(hist_1m, WARMUP_SESSIONS)
            self.states[inst.key] = DayState(
                symbol=sym, prev_close=prev_close, cum_vol_profile=profile,
                prev_day_gainer=prev_gainer, prev_day=prev_day,
                # Prior-session bars for the indicators. Held for every strategy
                # so exits are warm live as they already are in bt17; the
                # attention CONTEXT only consults them when `warm_context` is on,
                # so no existing arm changes behaviour by gaining them.
                warmup_1m=warmup_1m,
                warmup_5m=resample_5m(warmup_1m) if warmup_1m is not None else None,
            )
            self.inst_by_key[inst.key] = inst
            self.turnover[sym] = turnover_cr
            keys.append(inst.key)
        logger.info("universe ready: %d names tradeable, skipped=%s", len(keys), skipped)
        self._tg(f"ready {today}: {len(keys)} names in scan, skipped {skipped}")
        return keys

    def _cached_daily(self, inst: Instrument, start: date, end: date) -> pd.DataFrame:
        """Daily bars, from the EFS cache when it is readable.

        This exact line killed the 2026-09-14 session: `pd.read_parquet` on a
        zero-byte file raised out of `prepare()` before the feed ever opened.
        A cache is an optimisation — an unreadable entry must mean "fetch it",
        never "end the day". Writes are atomic so a concurrent reader can never
        see a partial file again.
        """
        f = self.cache / "daily" / f"{inst.symbol.replace('&', '_')}_{end.isoformat()}.parquet"
        cached = read_parquet_cache(f)
        if cached is not None and not cached.empty:
            return cached
        df = self.client.daily(inst.key, start, end)
        f.parent.mkdir(parents=True, exist_ok=True)
        write_parquet_cache(f, df)
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
            if self._guard_pending(st, when, ltp):
                self._tick_rejections.append(st.rejections[-1])
                return
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

    def _on_candle(self, key: str, ts_ms: int, open_: float, high: float,
                   low: float, close: float, volume: float) -> None:
        if key not in self.states:
            return
        with self._state_lock:
            self.builder.on_candle(key, ts_ms, open_, high, low, close, volume)

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
                self._guard_pending(st, now, float(bars["close"].iloc[-1]))
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

        closed.extend(self._eod_sweep(now, snapshots))

        # Fold results into the day's guardrails BEFORE any I/O below, so the
        # size ladder and the halts are current for the next bar even if a
        # Mongo or Telegram write is slow. Chronological so "3 consecutive
        # losses" means what it says when several positions close together.
        for t in sorted(closed, key=lambda x: x.exit_time):
            self._record_close(t)

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
            snapshot_key = next(
                (k for k, st in self.states.items() if st.symbol == t.cand.symbol), None
            )
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

    def _market_looks_closed(self, now: pd.Timestamp) -> bool:
        """True once the grace time has passed with not one bar built anywhere.

        `market_calendar` is a hand-maintained list and is known to be missing
        entries — 2026-09-14 (Ganesh Chaturthi) was absent, so the scanner ran a
        full Fargate day against a closed exchange. This is the data-driven
        backstop: on any real session, 140-odd liquid NSE names have all printed
        by 09:45, so zero bars across the entire universe means the market is
        shut, whatever the calendar claims.

        Deliberately "zero across every symbol", never a per-symbol or partial
        test: one quiet name is normal, and a threshold would eventually be
        tuned. It cannot fire before the grace time, so a slow feed connection
        does not trip it.
        """
        if self.s.mt_bypass_market_hours or not self.states:
            return False
        if now.time() < datetime(2000, 1, 1, *NO_DATA_GRACE).time():
            return False
        with self._state_lock:
            return not any(
                not self.builder.closed_bars(key, now).empty for key in self.states
            )

    def _eod_sweep(
        self, now: pd.Timestamp, snapshots: dict[str, pd.DataFrame]
    ) -> list[ClosedTrade]:
        """Force-close anything still open at 15:16 (see EOD_SWEEP).

        Normally a no-op: the bar-driven `eod_close` inside `step()` has already
        closed every position that was still printing at 15:15. This only
        catches a position whose symbol went quiet before then, which the
        bar-driven path cannot see at all.
        """
        if now.time() < datetime(2000, 1, 1, *EOD_SWEEP).time():
            return []
        swept: list[ClosedTrade] = []
        with self._state_lock:
            for key, st in self.states.items():
                if st.position is None:
                    continue
                px = self.builder.latest_close(key)
                if px is None:
                    bars = snapshots.get(key, pd.DataFrame())
                    px = float(bars["close"].iloc[-1]) if not bars.empty else None
                if px is None:
                    logger.error(
                        "eod_sweep: %s still open and no price to mark it at — "
                        "position left open, reconcile by hand", st.symbol
                    )
                    continue
                trade = force_close(st, now, float(px), self.cfg)
                if trade is not None:
                    logger.warning(
                        "eod_sweep: closed %s at last-seen ₹%.2f (last bar %s) — "
                        "the symbol stopped printing before 15:15",
                        st.symbol, px,
                        "none" if snapshots.get(key, pd.DataFrame()).empty
                        else str(snapshots[key].index[-1]),
                    )
                    swept.append(trade)
        return swept

    def _record_open(self, p: Position) -> None:
        assert self._ledger is not None
        doc_id = self._ledger.opened(p)
        if doc_id is not None:
            threading.Thread(
                target=self._attach_news_context,
                args=(doc_id, p.cand.symbol, p.entry_time),
                daemon=True,
                name=f"news-context-{p.cand.symbol}",
            ).start()
        exit_note = (f"target ₹{p.plan.target:.2f}"
                     if self.cfg.exit_mode == MODE_FIXED else "negative-signal exit")
        self._tg(f"📝 ENTER <b>{p.cand.symbol}</b> {p.cand.setup.name} "
                 f"@₹{p.plan.entry:.2f} ×{p.plan.qty} stop ₹{p.plan.stop:.2f} "
                 f"{exit_note} cat={p.cand.catalyst}")

    def _attach_news_context(self, doc_id: str, symbol: str, at: pd.Timestamp) -> None:
        """Best-effort background annotation for review — never on the live
        decision path, never allowed to raise into the scan loop."""
        if self._db is None:
            return
        try:
            from bson import ObjectId

            items = recent_news(symbol, at.to_pydatetime())
            self._db["mt_positions"].update_one(
                {"_id": ObjectId(doc_id)}, {"$set": {"news_context": items}}
            )
        except Exception:  # noqa: BLE001
            logger.exception("news_context attach failed for %s", symbol)

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
        if self.discipline.cfg.enabled:
            self._tg(f"guardrails: {self.discipline.summary()}")
        # How much of the session the engine saw as exchange candles rather than
        # snapshot-built bars. Anything well under 100% means the feed stopped
        # sending `I1` and the live arm drifted back to the approximate bars.
        now = _now()
        official = total = 0
        for key in self.states:
            o, t = self.builder.candle_coverage(key, now)
            official, total = official + o, total + t
        if total:
            self._tg(f"bar source: {official}/{total} minutes "
                     f"({100.0 * official / total:.1f}%) from exchange 1m candles")

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

        streamer = self.client.stream(keys, self._on_tick, mode="full", on_status=_status,
                                      on_candle=self._on_candle)

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
                if self._market_looks_closed(now):
                    self._tg(f"🟡 no bars by {now.strftime('%H:%M')} — "
                             f"market appears closed, exiting")
                    logger.warning(
                        "no symbol printed a bar by the %02d:%02d grace time across "
                        "%d names — treating %s as a non-trading day and exiting. "
                        "If it WAS a trading day, the feed is broken, not the market.",
                        *NO_DATA_GRACE, len(self.states), now.date(),
                    )
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
