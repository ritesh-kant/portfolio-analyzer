"""Live paper-trading scanner — one long-lived process per session (ECS Fargate
task, or `make mt-scan` locally).

    09:05  start: token → instruments → universe → prev closes, turnover, RVOL
           profiles (REST, cached) → subscribe FULL feed for every name
    09:15… every minute, ~2s after the bar closes: for each name with a new
           closed 1-min bar, run engine.step(); log candidates, open/close paper
           positions in Mongo `mt_*` + the forward CSV; Telegram on entry/exit
    15:35  EOD summary, disconnect, exit 0

The engine is the same code bt17 replays historically, so the forward log and
the backtest are directly comparable. Paper-only: there is no order module in
this file by design (hypothesis v2 §3, no-relax rules). Long by default; with
MT_ENABLE_SHORTS each name also gets a `short_side.ShortBook` - the same engine
on the reflected tape - sharing the position cap and the day's guardrails, and
a symbol never holds a long and a short at the same time.
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
from .alerts import NSE_TAG, attention_message, entry_message, eod_message, exit_message
from .bars import IST, SESSION_OPEN, BarBuilder
from .catalyst import FEED_ALIVE_WITHIN, feed_last_signal, hard_catalyst
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
    build_session_levels,
    fill_pending_quote,
    force_close,
    resample_5m,
    step,
)
from .exits import MODE_FIXED, MODE_TREND_FULL, MODE_TREND_RESISTANCE_STATE
from .levels import SESSION_LEVEL_SESSIONS, TARGET_BUFFER_PCT
from .ledger import PaperLedger
from .news_context import recent_news
from .short_side import ShortBook, ShortEvents, next_round_level
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
            macd_open_tolerance=settings.mt_macd_open_tolerance,
            allowed_pullback_ordinals=GUIDE_PULLBACK_ORDINALS,
            peak_hours_only=True,
            peak_hours_end=_parse_hhmm(settings.mt_peak_hours_end),
            warm_context=True,
            vol_baseline_min_bars=VOL_BASELINE_MIN_BARS,
            use_fixed_target=True,
            # Operator decision 2026-09-25 after GODREJIND topped ₹1 under the
            # Sep 17 high: cap the target just below earlier sessions' highs
            # too. research/hypotheses/2026-09-25-session-resistance-target.md
            session_level_sessions=SESSION_LEVEL_SESSIONS,
            target_buffer_pct=TARGET_BUFFER_PCT,
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
        self.shorts: dict[str, ShortBook] = {}       # same keys; empty unless shorts on
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
            DisciplineConfig(enabled=settings.mt_discipline,
                             giveback_halt=settings.mt_giveback_halt)
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
        telegram._send(self.s.telegram_bot_token, self.s.telegram_chat_id, f"{NSE_TAG} {text}")

    # ── startup ───────────────────────────────────────────────────────────────

    def _connect_db(self) -> None:
        try:
            from pymongo import MongoClient
            client: Any = MongoClient(self.s.mongodb_uri, serverSelectionTimeoutMS=5000)
            self._db = client[self.s.mongodb_db_name]
            self._db.list_collection_names()
            self._signals = self._db["nt_signals"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Mongo unavailable (%s) — CSV-only ledger, catalyst unknown for all", exc)
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
            print("FAIL: Mongo/news signals unavailable; catalyst labels would all be unknown")
            return 5
        last_signal = feed_last_signal(self._signals)
        feed_stale = last_signal is None or last_signal < datetime.utcnow() - FEED_ALIVE_WITHIN

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
        if feed_stale:
            print(f"  WARN: catalyst feed stale (last nt_signals write {last_signal or 'never'} UTC) "
                  "— every catalyst label will be recorded as unknown")
        else:
            print(f"  catalyst feed=live (last write {last_signal} UTC)")
        print(f"  paper log={log_path}")
        return 0

    def _catalyst(self, symbol: str, at: pd.Timestamp) -> tuple[int | None, str]:
        if self._signals is None:
            return None, ""
        try:
            return hard_catalyst(self._signals, symbol, at)
        except Exception:  # noqa: BLE001
            logger.exception("catalyst lookup failed")
            return None, ""

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
            if self.s.mt_enable_shorts:
                prev_loser = (len(closes) >= 2
                              and float(closes.iloc[-1] / closes.iloc[-2] - 1) <= -0.04)
                self.shorts[inst.key] = ShortBook(
                    sym, prev_close, profile, warmup_1m=warmup_1m, prev_day_loser=prev_loser,
                    # The long arm reads yesterday's anchors only under the
                    # resistance-state rules; the mirror reads them on the same
                    # condition so the two sides run the same rules.
                    prev_day={"high": float(daily["high"].iloc[-1]),
                              "low": float(daily["low"].iloc[-1]), "close": prev_close}
                    if self.cfg.require_resistance_breakout else None,
                )
            self.states[inst.key] = DayState(
                symbol=sym, prev_close=prev_close, cum_vol_profile=profile,
                prev_day_gainer=prev_gainer, prev_day=prev_day,
                # hist_1m ends yesterday, so these are all pre-open facts.
                session_levels=build_session_levels(
                    hist_1m, self.cfg.session_level_sessions),
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
        logger.info("universe ready: %d names tradeable, skipped=%s, shorts=%s",
                    len(keys), skipped, "on" if self.shorts else "off")
        sides = " · long + short" if self.shorts else ""
        self._tg(f"🔔 ready {today}: {len(keys)} names in scan{sides}, skipped {skipped}")
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
            if self.cfg.fill_mode != FILL_FUTURE_TRIGGER:
                return
            book = self.shorts.get(key)
            if book is not None and book.has_pending:
                self._tick_short(key, book, ts_ms, ltp)
            st = self.states[key]
            if st.pending is None:
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

    def _tick_short(self, key: str, book: ShortBook, ts_ms: int, ltp: float) -> None:
        """Quote-thread fill for an armed short. Caller holds the state lock."""
        when = pd.Timestamp(ts_ms, unit="ms", tz="UTC").tz_convert(IST)
        blocked = self._short_block_reason(key)
        if blocked is not None:
            rej = book.cancel_pending(when, blocked, ltp)
            if rej is not None:
                self._tick_rejections.append(rej)
            return
        ev = book.fill_quote(when, ltp, self.cfg, self.builder.closed_bars(key, when))
        if ev.opened is not None:
            self._tick_entries.append(ev.opened)
        self._tick_rejections.extend(ev.rejections)

    def _short_block_reason(self, key: str) -> str | None:
        """Why an armed short may not proceed right now, or None."""
        allowed, reason = self.discipline.can_trade()
        if not allowed:
            return f"halted:{reason}"
        if self._open_count() >= self.s.mt_max_positions:
            return "max_positions"
        st = self.states.get(key)
        if st is not None and (st.position is not None or st.pending is not None):
            return "opposite_side_open"
        return None

    def _open_count(self) -> int:
        longs = sum(1 for st in self.states.values() if st.position is not None)
        return longs + sum(1 for b in self.shorts.values() if b.has_position)

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
                book = self.shorts.get(key)
                if st.pending is not None and book is not None and (
                    book.has_position or book.has_pending
                ):
                    pending = st.pending
                    st.rejections.append(Rejection(
                        symbol=st.symbol, time=now, reason="opposite_side_open",
                        setup=pending.cand.setup.name, trigger=pending.cand.setup.trigger,
                    ))
                    st.pending = None
                attention_events.extend(st.attention_events[n_a:])
                candidates.extend(st.candidates[n_c:])
                rejections.extend(st.rejections[n_r:])
                if st.position is not None and had_pos is None:
                    entries.append(st.position)
                closed.extend(st.closed[n_x:])

        if self.shorts:
            short_ev, short_opened = self._process_shorts(now, snapshots)
            attention_events.extend(short_ev.attention)
            candidates.extend(short_ev.candidates)
            rejections.extend(short_ev.rejections)
            closed.extend(short_ev.closed)
            entries.extend(short_opened)

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
            self._tg(attention_message(a))
        for c in candidates:
            self._ledger.candidate(c)
            logger.info(
                "candidate %s %s %s trig=%.2f stop=%.2f chg=%.1f%% rvol=%.1f cat=%s",
                c.symbol, c.side, c.setup.name, c.setup.trigger, c.setup.stop,
                c.day_chg_pct, c.rvol, c.catalyst,
            )
        for rejection in rejections:
            self._ledger.rejected(rejection)
            logger.info(
                "rejected %s %s %s reason=%s trigger=%.2f observed=%s",
                rejection.symbol, rejection.side, rejection.setup, rejection.reason,
                rejection.trigger,
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
            self._ledger.closed(t, next_round_level(t.entry, t.side), chart_bars)
            self._tg(exit_message(t))

    def _process_shorts(
        self, now: pd.Timestamp, snapshots: dict[str, pd.DataFrame]
    ) -> tuple[ShortEvents, list[Position]]:
        """Step every name's short book on this bar; the short twin of the
        long loop in `_process`. Returns the bar's events in real prices and
        the positions opened on it."""
        out = ShortEvents()
        opened: list[Position] = []
        for key, bars in snapshots.items():
            book = self.shorts.get(key)
            if book is None or bars.empty:
                continue
            with self._state_lock:
                close = float(bars["close"].iloc[-1])
                if self.cfg.fill_mode == FILL_FUTURE_TRIGGER and book.pending_expired(now):
                    self._absorb(out, opened, book.fill_quote(now, close, self.cfg, bars))
                had_pending = book.has_pending
                ev = book.step(bars, self.cfg, lambda _symbol, _at: (0, ""),
                               allow_replay_fill=self.cfg.fill_mode != FILL_FUTURE_TRIGGER)
                self._absorb(out, opened, ev)
                if not had_pending and book.has_pending:
                    book.stamp_decision(now, self.cfg.attention_pending_minutes)
                if book.has_pending:
                    blocked = self._short_block_reason(key)
                    if blocked is not None:
                        rej = book.cancel_pending(now, blocked, close)
                        if rej is not None:
                            out.rejections.append(rej)
        return out, opened

    @staticmethod
    def _absorb(out: ShortEvents, opened: list[Position], ev: ShortEvents) -> None:
        out.attention.extend(ev.attention)
        out.candidates.extend(ev.candidates)
        out.rejections.extend(ev.rejections)
        out.closed.extend(ev.closed)
        if ev.opened is not None:
            opened.append(ev.opened)

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
            for key, book in self.shorts.items():
                if not book.has_position:
                    continue
                px = self.builder.latest_close(key)
                if px is None:
                    bars = snapshots.get(key, pd.DataFrame())
                    px = float(bars["close"].iloc[-1]) if not bars.empty else None
                if px is None:
                    logger.error("eod_sweep: SHORT %s still open and no price to mark it "
                                 "at — reconcile by hand", book.symbol)
                    continue
                short_trade = book.force_close(now, float(px), self.cfg)
                if short_trade is not None:
                    logger.warning("eod_sweep: covered short %s at last-seen ₹%.2f",
                                   book.symbol, px)
                    swept.append(short_trade)
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
        self._tg(entry_message(p, fixed_exit=self.cfg.exit_mode == MODE_FIXED))

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
        closed += [t for b in self.shorts.values() for t in b.closed]
        cands = sum(len(st.candidates) for st in self.states.values())
        cands += sum(len(b.candidates) for b in self.shorts.values())
        attention = sum(len(st.attention_events) for st in self.states.values())
        attention += sum(len(b.attention) for b in self.shorts.values())
        reasons = [r.reason for st in self.states.values() for r in st.rejections]
        reasons += [r.reason for b in self.shorts.values() for r in b.rejections]
        extra: list[str] = []
        if self.discipline.cfg.enabled:
            extra.append(f"🛡️ Guardrails: {self.discipline.summary()}")
        # How much of the session the engine saw as exchange candles rather than
        # snapshot-built bars. Anything well under 100% means the feed stopped
        # sending `I1` and the live arm drifted back to the approximate bars.
        now = _now()
        official = total = 0
        for key in self.states:
            from_exchange, minutes = self.builder.candle_coverage(key, now)
            official, total = official + from_exchange, total + minutes
        bars = (f"{100.0 * official / total:.1f}% exchange candles "
                f"({official:,} of {total:,} min)" if total else "no bars")
        extra.append(f"📊 Bars: {bars} · feed {self._feed_status}")
        self._tg(eod_message(day=now.date(), trades=closed, attention=attention,
                             candidates=cands, rejection_reasons=reasons, extra=extra))

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
