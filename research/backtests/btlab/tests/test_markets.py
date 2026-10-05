"""The US switch: ticks, clock, costs, and that NSE numbers did not move."""

from __future__ import annotations

import pytest

from btlab import base, markets, runner, sim
from btlab.tests.test_runner import cand
from btlab.tests.test_sim import FLAT, mk
import pandas as pd

US = base.BaseRule.for_market("US")


def test_nse_rule_key_is_unchanged_by_the_market_field():
    plain = base.BaseRule()
    assert plain.market == "NSE" and base.BaseRule.for_market("NSE").key() == plain.key()
    assert US.key() != plain.key()


def test_us_defaults_come_from_the_market():
    assert (US.day_chg_min, US.day_chg_max) == (10.0, 50.0) and US.entry_cutoff_min == 15 * 60
    assert base.BaseRule.for_market("US", day_chg_min=5.0).day_chg_min == 5.0


def test_ticks_follow_the_market():
    assert base.tick_up(5.003, 0.01) == 5.01 and base.tick_up(5.003, 0.05) == 5.05
    assert base.tick_down(5.049, 0.01) == 5.04 and base.tick_down(5.049, 0.05) == 5.0


def test_resistance_target_uses_the_market_tick_not_the_nse_grid():
    class L:
        def __init__(self, p): self.price, self.kind, self.touches, self.strength, self.age = p, "round", 1, 1, 0
    lv = [L(5.00)]
    # 0.15% under $5.00 is 4.9925: the US penny grid keeps 4.99, the NSE 5-paise grid would say 4.95
    assert base.resist_target(lv, 4.5, 0.15, 0.01)[1] == 4.99
    assert base.resist_target(lv, 4.5, 0.15, 0.05)[1] == 4.95


def test_last_bar_of_the_session_differs():
    assert markets.NSE.eod_min == 914 and markets.US.eod_min == 959


def test_us_costs_are_the_engine_us_model_and_nse_costs_the_mis_model():
    from src.momentum_trader import us_costs
    assert markets.US.costs(5.0, 5.2, 1000) == pytest.approx(us_costs.calc_costs(5.0, 1000).total)
    assert markets.NSE.costs(100.0, 101.0, 100) != markets.US.costs(100.0, 101.0, 100)


def test_cost_breakeven_covers_us_costs():
    be = sim.cost_breakeven(5.0, 1000, "US")
    assert be > 5.0 and (be - 5.0) * 1000 > markets.US.costs(5.0, be, 1000)


def test_a_us_day_runs_to_its_own_close_and_stamps_usd():
    # 390 flat minutes from 09:30: nothing stops or targets, so the exit is the last bar, 15:59
    arr = mk([FLAT] * 390, start=9 * 60 + 30)
    c = cand(decision_min=9 * 60 + 40, fill_min=9 * 60 + 42, fill_k=12)
    cfg = runner.RunConfig(years=[2026], rule=US, patterns=["hammer"], risk_inr=50.0, max_notional_inr=5000.0)
    tr = runner.run(cfg, pd.DataFrame([c]), {"AAA|2026-03-02": arr})
    assert tr.iloc[0]["currency"] == "USD" and tr.iloc[0]["exit_reason"] == "eod"
    assert tr.iloc[0]["exit_time"] == "16:00"      # the last bar starts 15:59 and closes at 16:00
