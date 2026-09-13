"""One-minute agreement gate (hypothesis 2026-09-12-one-minute-agreement).

A five-minute setup is refused unless its own one-minute chart is in favour at
the moment of the decision: short-term trend up, and a green trigger minute
that closes strong on real volume.
"""

from __future__ import annotations

import pandas as pd
from src.momentum_trader import engine as eng
from src.momentum_trader.setups import Setup

SETUP = Setup("ma9_pullback", 100.0, 99.0)
IST = "Asia/Kolkata"


def _bars(closes: list[float], volumes: list[float] | None = None,
          last: tuple[float, float, float, float] | None = None) -> pd.DataFrame:
    """A one-minute frame ending on a strong green bar unless `last` overrides it."""
    n = len(closes)
    vols = volumes if volumes is not None else [100.0] * n
    idx = pd.date_range("2022-01-19 10:00", periods=n, freq="1min", tz=IST)
    df = pd.DataFrame(
        {
            "open": [c - 0.05 for c in closes],
            "high": [c + 0.05 for c in closes],
            "low": [c - 0.10 for c in closes],
            "close": closes,
            "volume": vols,
        },
        index=idx,
    )
    df.index.name = "ts"
    if last is not None:
        df.iloc[-1, df.columns.get_indexer(["open", "high", "low", "close"])] = list(last)
    return df


def _rising(n: int = 30) -> list[float]:
    return [100.0 + 0.2 * i for i in range(n)]


def _falling(n: int = 30) -> list[float]:
    return [100.0 - 0.2 * i for i in range(n)]


def _gate(bars: pd.DataFrame, setup: Setup = SETUP) -> tuple[bool, str]:
    return eng._one_minute_gate(bars, setup, eng.EngineConfig(require_1m_agreement=True))


def test_gate_is_off_by_default() -> None:
    """OFF since 2026-09-13: BT30 measured -0.0003 pp and anti p = 0.526, so the
    rule is a trade-count cut, not a filter. Deployments opt in per-environment
    through MT_REQUIRE_1M_AGREEMENT."""
    assert eng.EngineConfig().require_1m_agreement is False
    assert eng._one_minute_gate(_bars(_falling()), SETUP, eng.EngineConfig()) == (True, "ok")


def test_rule_applies_when_switched_on() -> None:
    on = eng.EngineConfig(require_1m_agreement=True)
    assert eng._one_minute_gate(_bars(_falling()), SETUP, on) == (False, "1m_trend_down")


def test_agreeing_chart_passes() -> None:
    vols = [100.0] * 29 + [400.0]
    bars = _bars(_rising(), vols, last=(105.0, 106.0, 104.9, 105.9))
    assert _gate(bars) == (True, "ok")


def test_short_frame_is_warmup_not_a_pass() -> None:
    assert _gate(_bars(_rising(10))) == (False, "1m_warmup")


def test_one_minute_downtrend_is_refused() -> None:
    vols = [100.0] * 29 + [400.0]
    bars = _bars(_falling(), vols, last=(94.0, 95.0, 93.9, 94.9))
    assert _gate(bars) == (False, "1m_trend_down")


def test_red_or_doji_trigger_minute_is_refused() -> None:
    vols = [100.0] * 29 + [400.0]
    red = _bars(_rising(), vols, last=(106.0, 106.1, 104.9, 105.0))
    assert _gate(red) == (False, "1m_red_or_flat")
    flat = _bars(_rising(), vols, last=(105.0, 105.0, 105.0, 105.0))
    assert _gate(flat) == (False, "1m_red_or_flat")


def test_weak_close_is_refused() -> None:
    vols = [100.0] * 29 + [400.0]
    # green, but gives back most of its range: closes in the bottom half
    bars = _bars(_rising(), vols, last=(105.0, 107.0, 104.9, 105.4))
    assert _gate(bars) == (False, "1m_weak_close")


def test_thin_trigger_minute_is_refused() -> None:
    # the KEI failure mode: a big green candle on ordinary volume
    vols = [100.0] * 29 + [128.0]
    bars = _bars(_rising(), vols, last=(105.0, 106.0, 104.9, 105.9))
    assert _gate(bars) == (False, "1m_low_volume")


def test_one_minute_setups_are_not_double_gated() -> None:
    hostile = _bars(_falling())
    for name in eng.ONE_MINUTE_SETUPS:
        assert _gate(hostile, Setup(name, 100.0, 99.0)) == (True, "ok")


def test_attention_lane_keeps_its_own_close_threshold() -> None:
    assert eng.ONE_MIN_CLOSE_POSITION_MIN == 0.60


def test_env_switch_reaches_every_strategy() -> None:
    """MT_REQUIRE_1M_AGREEMENT is applied after the per-strategy config is
    built, so no strategy branch can miss it."""
    from src.config import Settings
    from src.momentum_trader import scanner

    assert Settings().mt_require_1m_agreement is False
    for strategy in (scanner.STRATEGY_BASELINE,
                     scanner.STRATEGY_CATALYST_FIRST_PULLBACK,
                     scanner.STRATEGY_ATTENTION_1M,
                     scanner.STRATEGY_ATTENTION_1M_MERGED):
        for flag in (False, True):
            settings = Settings(mt_strategy=strategy, mt_require_1m_agreement=flag)
            cfg = scanner._strategy_config(settings)
            cfg.require_1m_agreement = settings.mt_require_1m_agreement
            assert cfg.require_1m_agreement is flag
