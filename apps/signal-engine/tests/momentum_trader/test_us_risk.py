"""Sizing, and the cost-over-risk gate that replaces NSE's percentage stop floor."""

from __future__ import annotations

import pytest

from src.momentum_trader import us_risk
from src.momentum_trader.us_risk import Rejection, USTradePlan


def test_two_to_one_target_is_unchanged_from_the_guide():
    assert us_risk.target_for(10.0, 9.0) == pytest.approx(12.0)


def test_stop_inside_one_tick_is_not_a_price():
    """The NSE 0.3% floor would ADMIT this: 0.3% of $2 is 0.6 cents, under one
    tick. A percentage floor cannot express tick noise in a penny market."""
    rejected = us_risk.stop_is_sane(2.00, 1.994)
    assert isinstance(rejected, Rejection) and rejected.reason == "stop_inside_tick"


def test_wide_stop_allowed_well_past_the_nse_three_percent_bound():
    """Warrior US names are up 10%+ on 5x volume; a 3% cap would reject the
    population the screen exists to find."""
    assert us_risk.stop_is_sane(10.00, 9.50) is None


def test_stop_beyond_the_chosen_bound_rejected_with_a_reason():
    rejected = us_risk.stop_is_sane(10.00, 8.50)
    assert isinstance(rejected, Rejection) and rejected.reason == "stop_too_wide"


def test_cheap_stock_with_a_tight_stop_cannot_pay_for_itself():
    """$2 stock, 1.5% stop. Crossing a penny spread twice alone is 1% of price,
    so cost approaches the amount at risk. This is BT39's finding as a gate."""
    result = us_risk.plan_trade(2.00, 1.97, risk_usd=100.0, max_notional_usd=10_000.0)
    assert isinstance(result, Rejection) and result.reason == "cost_over_risk"
    assert "break even" in result.detail


def test_same_cheap_stock_passes_once_the_stop_is_wide_enough():
    """Not a filter to tune away: the same name qualifies on a setup whose
    invalidation is further off."""
    result = us_risk.plan_trade(2.00, 1.86, risk_usd=100.0, max_notional_usd=10_000.0)
    assert isinstance(result, USTradePlan)


def test_breakeven_win_rate_is_reported_and_above_the_guides_number():
    plan = us_risk.plan_trade(10.00, 9.60, risk_usd=200.0, max_notional_usd=20_000.0)
    assert isinstance(plan, USTradePlan)
    assert plan.rr == pytest.approx(2.0)
    assert 1 / 3 < plan.breakeven_win_rate <= (1 + us_risk.MAX_COST_OVER_RISK) / 3


def test_sizing_honours_both_the_risk_budget_and_the_notional_cap():
    plan = us_risk.plan_trade(10.00, 9.50, risk_usd=1_000.0, max_notional_usd=5_000.0)
    assert isinstance(plan, USTradePlan)
    assert plan.qty == 500                      # notional cap binds, not risk
    assert plan.notional_usd == pytest.approx(5_000.0)


def test_risk_budget_binds_when_it_is_the_smaller_constraint():
    plan = us_risk.plan_trade(10.00, 9.50, risk_usd=100.0, max_notional_usd=50_000.0)
    assert isinstance(plan, USTradePlan)
    assert plan.qty == 200
    assert plan.risk_usd == pytest.approx(100.0)


def test_rejections_carry_a_reason_rather_than_returning_none():
    """The NSE arm returns None, so 'found nothing' and 'could not afford it'
    look identical in the log. They are different facts."""
    result = us_risk.plan_trade(10.00, 9.99, risk_usd=100.0, max_notional_usd=10_000.0)
    assert isinstance(result, Rejection)
    assert result.reason and result.detail


def test_degenerate_stop_rejected():
    result = us_risk.plan_trade(10.0, 10.0, risk_usd=100.0, max_notional_usd=10_000.0)
    assert isinstance(result, Rejection) and result.reason == "degenerate"


def test_size_that_rounds_to_zero_is_reported_as_such():
    result = us_risk.plan_trade(20.0, 19.0, risk_usd=0.5, max_notional_usd=10_000.0)
    assert isinstance(result, Rejection) and result.reason == "size_zero"
