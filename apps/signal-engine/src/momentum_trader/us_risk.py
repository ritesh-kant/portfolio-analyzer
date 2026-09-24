"""Risk sizing for the US arm — the guide's 2:1 rule, with a cost-aware stop gate.

`risk.py` (NSE) is untouched. This module exists because one of its constants
does not survive the crossing, and silently reusing it would have been a bug:

    MIN_STOP_PCT = 0.003   # "below that, NSE tick noise stops you out"

That 0.3% floor is a statement about NSE's tick size relative to NSE prices. In
the US the tick is a flat one cent, so 0.3% means something different at every
price: on a $2 stock 0.3% is 0.6 cents — *less than one tick*, so the floor
admits stops that cannot exist. On a $50 stock it is 2.5 cents. A percentage
floor cannot express "wider than the noise" in a penny-quoted market.

The replacement is derived rather than chosen
---------------------------------------------
Take the guide's own 2:1 rule: stop distance R, target 2R, round-trip cost C.

    net win  = 2R - C
    net loss =  R + C

Break-even win rate p solves p(2R - C) = (1 - p)(R + C):

    p = 1/3 + C/(3R)

So C/R is exactly the thing that inflates the win rate you need. The guide's
advertised 33% break-even assumes C is negligible, which is true for the
account sizes it was written for and false for a $2 stock, where crossing a
one-cent spread twice is 1% of the price.

`MAX_COST_OVER_RISK` caps C/R, and the cap states its own consequence:

    C/R = 0.00  ->  break even at 33.3% wins   (the guide's number)
    C/R = 0.25  ->  break even at 41.7% wins   (this module's default)
    C/R = 0.50  ->  break even at 50.0% wins   (2:1 has bought you nothing)

This is the rule BT39 was pointing at. A $2 stock with a 1% round-trip cost
needs at least a 4% stop to clear the default cap — which is a real constraint
on which setups are tradeable, not a filter to be tuned away.

⚠️ `MAX_STOP_PCT` below is a CHOSEN bound, not a measured one. NSE used 3%;
Warrior's US names (up 10%+ on 5x volume, under 10M float) routinely pull back
further than that, so 3% would reject most of them. 10% is a placeholder wide
enough not to be the binding constraint while the forward log is collected. It
must be revisited from data before anything is concluded about this arm, and
`plan_trade` records the reason on every rejection so that is possible.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import us_costs

DEFAULT_RR = 2.0            # the guide's rule, unchanged from the NSE arm
MAX_COST_OVER_RISK = 0.25   # see module docstring — implies a 41.7% break-even
MAX_STOP_PCT = 0.10         # chosen, not measured
MIN_STOP_TICKS = 2          # a stop inside one tick is not a price


@dataclass(frozen=True)
class USTradePlan:
    entry: float
    stop: float
    target: float
    qty: int
    risk_usd: float          # lost if the stop fills exactly, before costs
    reward_usd: float        # gained if the target fills exactly, before costs
    notional_usd: float
    cost_usd: float          # modelled round-trip cost at this size
    cost_over_risk: float    # C/R — the number the gate is built on

    @property
    def rr(self) -> float:
        return self.reward_usd / self.risk_usd if self.risk_usd > 0 else 0.0

    @property
    def breakeven_win_rate(self) -> float:
        """Fraction of trades that must win for this plan to break even, given
        its own costs. Compare against the guide's 0.333."""
        return (1.0 + self.cost_over_risk) / 3.0 if self.rr >= 2.0 else float("nan")


@dataclass(frozen=True)
class Rejection:
    reason: str
    detail: str


def target_for(entry: float, stop: float, rr: float = DEFAULT_RR) -> float:
    if entry <= stop:
        raise ValueError("long trade requires entry > stop")
    return entry + rr * (entry - stop)


def stop_is_sane(entry: float, stop: float) -> Rejection | None:
    """Structural checks that do not depend on position size."""
    if entry <= 0 or stop <= 0 or stop >= entry:
        return Rejection("degenerate", f"entry {entry} must be above stop {stop}")
    distance = entry - stop
    if distance < MIN_STOP_TICKS * us_costs.MIN_TICK:
        return Rejection(
            "stop_inside_tick",
            f"stop is {distance:.4f} away, under {MIN_STOP_TICKS} ticks "
            f"({MIN_STOP_TICKS * us_costs.MIN_TICK:.2f})",
        )
    if distance / entry > MAX_STOP_PCT:
        return Rejection(
            "stop_too_wide",
            f"stop is {100 * distance / entry:.2f}% away, over the chosen "
            f"{100 * MAX_STOP_PCT:.0f}% bound",
        )
    return None


def plan_trade(
    entry: float,
    stop: float,
    *,
    risk_usd: float,
    max_notional_usd: float,
    rr: float = DEFAULT_RR,
    plan: str = us_costs.DEFAULT_PLAN,
    max_cost_over_risk: float = MAX_COST_OVER_RISK,
    gate_entry: float | None = None,
) -> USTradePlan | Rejection:
    """Size a long so a stop-out loses about `risk_usd`, then check the trade
    can pay for itself.

    Returns a `Rejection` rather than None so the forward log records WHY a
    setup was not taken. The NSE arm returns None and the reason is lost, which
    is the difference between "the screen found nothing" and "the screen found
    things it could not afford" — and those need to be told apart before this
    arm is judged.

    `gate_entry` mirrors `risk.plan_trade`: the price the structural checks are
    judged against, so two fill modes take the same trades and differ only in
    what they pay. Size and the cost gate always use the price actually paid.
    """
    structural = stop_is_sane(entry if gate_entry is None else gate_entry, stop)
    if structural is not None:
        return structural
    if entry <= stop:        # the gate price passed, but this fill is under water
        return Rejection("degenerate", f"fill {entry} is not above stop {stop}")

    per_share_risk = entry - stop
    qty_by_risk = int(risk_usd // per_share_risk)
    qty_by_notional = int(max_notional_usd // entry)
    qty = max(0, min(qty_by_risk, qty_by_notional))
    if qty == 0:
        return Rejection(
            "size_zero",
            f"${risk_usd:.2f} of risk over a ${per_share_risk:.2f} stop, capped "
            f"at ${max_notional_usd:.0f} notional, rounds to no shares",
        )

    cost = us_costs.calc_costs(entry, qty, plan=plan).total
    total_risk = per_share_risk * qty
    cost_over_risk = cost / total_risk if total_risk > 0 else float("inf")

    if cost_over_risk > max_cost_over_risk:
        return Rejection(
            "cost_over_risk",
            f"round-trip cost ${cost:.2f} is {cost_over_risk:.2f}x the "
            f"${total_risk:.2f} at risk (cap {max_cost_over_risk:.2f}); this "
            f"plan needs {(1 + cost_over_risk) / 3:.1%} wins to break even",
        )

    tgt = target_for(entry, stop, rr)
    return USTradePlan(
        entry=entry,
        stop=stop,
        target=tgt,
        qty=qty,
        risk_usd=total_risk,
        reward_usd=(tgt - entry) * qty,
        notional_usd=entry * qty,
        cost_usd=cost,
        cost_over_risk=cost_over_risk,
    )
