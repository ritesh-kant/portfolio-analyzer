"""Forward catalyst-first-pullback playbook (hypothesis 2026-09-07)."""

from __future__ import annotations

from pathlib import Path

import pytest
from src.config import Settings
from src.momentum_trader import engine as eng
from src.momentum_trader.scanner import (
    STRATEGY_ATTENTION_1M,
    STRATEGY_CATALYST_FIRST_PULLBACK,
    _market_data_token,
    _repo_path,
    _strategy_config,
)
from src.momentum_trader.setups import Setup


def test_first_pullback_playbook_accepts_only_ma9_ordinal_one() -> None:
    cfg = eng.EngineConfig(allowed_setups=("ma9_pullback",), allowed_pullback_ordinals=(1,))
    assert eng._playbook_gate(Setup("ma9_pullback", 100.0, 99.0), 1, cfg) == (True, "ok")
    assert eng._playbook_gate(Setup("bull_flag", 100.0, 99.0), 1, cfg) == (
        False, "setup_not_allowed"
    )
    assert eng._playbook_gate(Setup("ma9_pullback", 100.0, 99.0), 2, cfg) == (
        False, "pullback_not_allowed"
    )


def test_scanner_selects_registered_forward_playbook() -> None:
    settings = Settings(mt_strategy=STRATEGY_CATALYST_FIRST_PULLBACK)
    cfg = _strategy_config(settings)
    assert cfg.exit_mode == "trend_full"
    assert cfg.allowed_setups == ("ma9_pullback",)
    assert cfg.allowed_pullback_ordinals == (1,)


def test_scanner_selects_attention_watchlist_strategy() -> None:
    settings = Settings(
        mt_strategy=STRATEGY_ATTENTION_1M,
        mt_attention_day_chg_min=1.5,
        mt_attention_rvol_min=1.5,
    )
    cfg = _strategy_config(settings)
    assert cfg.fill_mode == eng.FILL_FUTURE_TRIGGER
    assert cfg.exit_mode == "trend_full"
    assert cfg.use_attention_entries
    assert cfg.attention_day_chg_min == pytest.approx(1.5)
    assert cfg.attention_rvol_min == pytest.approx(1.5)


def test_scanner_rejects_unknown_strategy() -> None:
    with pytest.raises(ValueError, match="unknown MT_STRATEGY"):
        _strategy_config(Settings(mt_strategy="unknown"))


def test_analytics_token_wins_over_daily_and_ssm_tokens() -> None:
    settings = Settings(upstox_analytics_token="analytics", upstox_access_token="daily")
    assert _market_data_token(settings, "ssm") == "analytics"
    daily = Settings(upstox_analytics_token="", upstox_access_token="daily")
    assert _market_data_token(daily, "ssm") == "daily"
    no_env_token = Settings(upstox_analytics_token="", upstox_access_token="")
    assert _market_data_token(no_env_token, "ssm") == "ssm"


def test_repo_relative_paths_resolve_to_the_repository_not_apps_directory() -> None:
    root = Path(__file__).resolve().parents[4]
    assert _repo_path("research/backtests/example.csv") == root / "research/backtests/example.csv"
