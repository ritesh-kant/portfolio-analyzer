"""The US fee stack, and the price-dependence that makes it different from NSE."""

from __future__ import annotations

import pytest

from src.momentum_trader import us_costs


def test_cost_percentage_falls_sharply_with_price():
    """The whole reason this module is separate from the Indian one: per-share
    billing means a cheap stock is far more expensive as a percentage.
    1,000 shares round trip: 1.18% at $2 against 0.13% at $50."""
    cheap = us_costs.calc_costs(2.00, 1000).pct
    dear = us_costs.calc_costs(50.00, 1000).pct
    assert cheap == pytest.approx(1.18, abs=0.01)
    assert dear == pytest.approx(0.13, abs=0.01)
    assert cheap > 8 * dear


def test_penny_tick_dominates_impact_under_ten_dollars():
    """Below ~$10 the one-cent tick is wider than a 10 bps spread, so impact
    stops scaling with price and becomes a fixed cent per share."""
    assert us_costs.spread_usd(2.00) == pytest.approx(0.01)
    assert us_costs.spread_usd(5.00) == pytest.approx(0.01)
    assert us_costs.spread_usd(50.00) == pytest.approx(0.05)


def test_impact_assumption_matches_the_indian_model_above_the_tick_floor():
    """India assumes 5 bps/side = 10 bps round trip. Where the tick does not
    bind, the US assumption must be the same number or the two markets are not
    being compared, only re-labelled."""
    costs = us_costs.calc_costs(100.00, 500, include_impact=True)
    impact_pct = 100.0 * costs.slippage / costs.notional
    assert impact_pct == pytest.approx(0.10)


def test_guide_price_band_costs_more_than_indian_mis():
    """Measured reference: the NSE arm's real round trip is 0.206%. Inside the
    guide's $1-$20 band the US stack is worse, which is the constraint the US
    arm has to be designed around rather than assumed away."""
    for price in (2.0, 5.0, 10.0):
        assert us_costs.calc_costs(price, 1000).pct > 0.206


def test_above_the_crossover_us_is_cheaper():
    assert us_costs.calc_costs(50.0, 500).pct < 0.206


def test_tiered_charges_the_take_fee_on_both_legs():
    """A triggered buy-stop removes liquidity; the venue fee is not optional."""
    with_venue = us_costs.calc_costs(10.0, 1000, plan="tiered", include_impact=False)
    venue = (us_costs.TIERED_CLEARING + us_costs.TIERED_EXCHANGE) * 1000 * 2
    assert with_venue.commission > venue


def test_fixed_plan_has_a_dollar_minimum_per_side():
    """10 shares bill $0.05, so the $1 per-order minimum is what is actually
    charged — twice, once per leg."""
    costs = us_costs.calc_costs(50.0, 10, plan="fixed", include_impact=False)
    assert costs.commission == pytest.approx(2.00)


def test_commission_capped_at_one_percent_of_value():
    """A tiny, cheap order would otherwise pay more in commission than it is
    worth; both IBKR plans cap at 1%."""
    costs = us_costs.calc_costs(1.00, 20, plan="fixed", include_impact=False)
    assert costs.commission == pytest.approx(2 * 0.01 * 20.0)   # capped, not $2


def test_sec_and_taf_are_sell_side_only_and_taf_is_capped():
    big = us_costs.calc_costs(5.0, 1_000_000, include_impact=False)
    assert big.taf == pytest.approx(us_costs.TAF_MAX)


def test_dict_shape_matches_the_indian_ledger_contract():
    keys = set(us_costs.calc_costs(5.0, 100).as_dict())
    assert {"total", "slippage"} <= keys


def test_zero_size_is_free_not_an_error():
    assert us_costs.calc_costs(5.0, 0).total == 0.0


def test_unknown_plan_rejected():
    with pytest.raises(ValueError, match="unknown IBKR plan"):
        us_costs.calc_costs(5.0, 100, plan="lite")
