"""The engine's sizing seam: NSE must be untouched, the US must use us_risk.

`_plan_entry` replaced three direct `plan_trade(...)` calls inside the live
engine. These tests pin that the NSE branch returns exactly what those calls
returned, for every input shape they were given, before anything else is said
about the US branch.
"""

from __future__ import annotations

import itertools

import pandas as pd
import pytest

from src.momentum_trader import engine
from src.momentum_trader.engine import EngineConfig, _plan_entry
from src.momentum_trader.market import NSE, US
from src.momentum_trader.risk import plan_trade
from src.momentum_trader.volume_confirmation import volume_confirmation_evidence

ENTRIES = [60.0, 99.95, 250.0, 1217.35, 1999.0]
STOP_DISTANCES = [0.001, 0.003, 0.0054, 0.0125, 0.03, 0.045]   # inside and outside the band
GATE_OFFSETS = [0.0, -0.002, 0.004]                              # gate below / at / above fill
FRACTIONS = [1.0, 0.5]                                           # discipline starter size


@pytest.mark.parametrize(
    "entry,dist,gate_off,fraction",
    list(itertools.product(ENTRIES, STOP_DISTANCES, GATE_OFFSETS, FRACTIONS)),
)
def test_nse_branch_is_the_old_call_exactly(entry, dist, gate_off, fraction):
    # < 1 is only valid inside the BT44 add-on arm, which EngineConfig
    # enforces: starter size -> pyramid_add_at_1r -> cost_aware_breakeven.
    half = fraction < 1.0
    cfg = EngineConfig(initial_risk_fraction=fraction, pyramid_add_at_1r=half,
                       cost_aware_breakeven=half)
    stop = round(entry * (1 - dist), 2)
    gate = round(entry * (1 + gate_off), 2)
    old = plan_trade(
        entry, stop,
        risk_inr=cfg.risk_inr * cfg.initial_risk_fraction,
        max_notional_inr=cfg.max_notional_inr * cfg.initial_risk_fraction,
        rr=cfg.rr, gate_entry=gate,
    )
    new, refusal = _plan_entry(cfg, entry, stop, gate_entry=gate)
    assert new == old                      # dataclass equality: every field identical
    assert refusal == "stop_not_sane"      # the historical reason, unchanged


def test_default_config_is_nse():
    assert EngineConfig().market is NSE


# ── the US branch ───────────────────────────────────────────────────────────
def us_cfg(**kw) -> EngineConfig:
    return EngineConfig(market=US, risk_inr=50.0, max_notional_inr=5_000.0, **kw)


def test_us_branch_admits_a_stop_the_nse_band_would_refuse():
    """A 5% stop on a +40% low-float runner: outside NSE's 3% band, a normal
    invalidation for the US population."""
    entry, stop = 10.00, 9.50
    assert plan_trade(entry, stop, risk_inr=50.0, max_notional_inr=5_000.0) is None
    plan, refusal = _plan_entry(us_cfg(), entry, stop, gate_entry=entry)
    assert plan is not None and refusal == ""
    assert plan.qty == 100 and plan.risk_inr == pytest.approx(50.0)   # dollars, see EngineConfig


def test_us_branch_keeps_the_real_refusal_reason():
    """$2 stock, 1.5% stop: crossing a one-cent spread twice is 1% of price, so
    the trade cannot pay for itself. The reason must survive to the log."""
    plan, refusal = _plan_entry(us_cfg(), 2.00, 1.97, gate_entry=2.00)
    assert plan is None and refusal == "cost_over_risk"


def test_us_branch_refuses_a_stop_inside_one_tick():
    plan, refusal = _plan_entry(us_cfg(), 2.00, 1.995, gate_entry=2.00)
    assert plan is None and refusal == "stop_inside_tick"


def test_us_gate_price_decides_the_structure_but_the_fill_decides_size():
    """Mirrors NSE: judged on the gate, sized on what was paid."""
    plan, _ = _plan_entry(us_cfg(), 10.10, 9.50, gate_entry=10.00)
    assert plan is not None and plan.entry == pytest.approx(10.10)
    assert plan.qty == int(50.0 // (10.10 - 9.50))


def test_us_fill_under_water_is_refused_even_if_the_gate_passed():
    plan, refusal = _plan_entry(us_cfg(), 9.40, 9.50, gate_entry=10.00)
    assert plan is None and refusal == "degenerate"


def test_us_costs_are_charged_in_dollars_per_share():
    """The cost seam: the same cfg.market routes the round trip."""
    assert engine.EngineConfig(market=US).market.round_trip_cost(5.0, 5.0, 1000) == pytest.approx(
        US.round_trip_cost(5.0, 5.0, 1000))


# ── the evidence module's session date ──────────────────────────────────────
def _us_session_bars() -> pd.DataFrame:
    """09:30-15:59 ET on 2026-09-24. In India that is 19:00 on the 24th to
    01:29 on the 25th — the session crosses midnight IST at 14:30 ET."""
    idx = pd.date_range("2026-09-24 09:30", "2026-09-24 15:59", freq="1min", tz="America/New_York")
    return pd.DataFrame(
        {"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 100.0}, index=idx
    )


def test_us_session_is_one_day_in_its_own_timezone():
    ev = volume_confirmation_evidence(_us_session_bars(), tz=US.timezone)
    assert ev.get("data_error") is None
    assert len(ev["bars"]) > 0


def test_ist_default_still_splits_a_us_session_which_is_why_it_is_a_parameter():
    """Documents the defect the parameter fixes: under IST the afternoon is a
    'new day'. The engine now passes `cfg.market.timezone`, so it never does."""
    bars = _us_session_bars()
    ist_dates = bars.index.tz_convert(NSE.timezone).normalize().unique()
    et_dates = bars.index.tz_convert(US.timezone).normalize().unique()
    assert len(ist_dates) == 2 and len(et_dates) == 1


def test_nse_default_timezone_is_unchanged():
    import inspect

    assert inspect.signature(volume_confirmation_evidence).parameters["tz"].default == NSE.timezone
