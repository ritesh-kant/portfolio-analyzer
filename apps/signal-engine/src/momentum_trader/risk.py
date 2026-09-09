"""Risk rules from the Warrior "Stock Selection" doc, adapted for NSE MIS.

The doc's one hard rule: manage every trade to a **2:1 profit-to-loss ratio**
(target distance = 2 × stop distance). Its table shows why: at 2:1 you break
even at a 33% win rate, at 1:1 you need 50%.

Position sizing here is *risk-based*, the way the doc trades it: choose how
many rupees you are willing to lose if the stop hits, and derive quantity from
the stop distance. The old news-trader sized by fixed notional (₹50k) and used a
% stop, which makes the rupee risk vary with volatility. Both are capped so a
tight stop cannot balloon into an oversized position.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_RR = 2.0          # target = entry + RR × (entry − stop)
MIN_STOP_PCT = 0.003      # stop ≥ 0.3% below entry — below that, NSE tick noise stops you out
MAX_STOP_PCT = 0.03       # stop ≤ 3% below entry — beyond that, the setup is not "low risk"


@dataclass(frozen=True)
class TradePlan:
    entry: float
    stop: float
    target: float
    qty: int
    risk_inr: float          # rupees lost if the stop fills exactly
    reward_inr: float        # rupees gained if the target fills exactly
    notional_inr: float

    @property
    def rr(self) -> float:
        return self.reward_inr / self.risk_inr if self.risk_inr > 0 else 0.0


def target_for(entry: float, stop: float, rr: float = DEFAULT_RR) -> float:
    """Target price implied by the stop and the reward:risk multiple."""
    if entry <= stop:
        raise ValueError("long trade requires entry > stop")
    return entry + rr * (entry - stop)


def stop_is_sane(entry: float, stop: float) -> bool:
    """Reject stops that are too tight (noise) or too wide (not a low-risk entry)."""
    if entry <= 0 or stop <= 0 or stop >= entry:
        return False
    dist = (entry - stop) / entry
    return MIN_STOP_PCT <= dist <= MAX_STOP_PCT


def plan_trade(
    entry: float,
    stop: float,
    *,
    risk_inr: float,
    max_notional_inr: float,
    rr: float = DEFAULT_RR,
    gate_entry: float | None = None,
) -> TradePlan | None:
    """Size a long so a stop-out loses ≈ `risk_inr`, capped at `max_notional_inr`.

    Returns None when the stop is outside the sane band or the size rounds to 0.
    Costs are not netted here; the paper ledger applies calc_costs at exit.

    `gate_entry` is the price the stop-sanity band is judged against; it defaults
    to `entry`. The fill-latency comparison passes the *baseline* arm's fill in
    both arms, so a change of fill price cannot silently admit or drop a trade
    and the two arms stay trade-for-trade comparable
    (research/hypotheses/2026-09-05-entry-fill-latency.md §2).
    """
    if not stop_is_sane(entry if gate_entry is None else gate_entry, stop):
        return None
    if entry <= stop:          # gate price passed the band but this fill does not
        return None
    per_share_risk = entry - stop
    qty_by_risk = int(risk_inr // per_share_risk)
    qty_by_notional = int(max_notional_inr // entry)
    qty = max(0, min(qty_by_risk, qty_by_notional))
    if qty == 0:
        return None
    tgt = target_for(entry, stop, rr)
    return TradePlan(
        entry=entry,
        stop=stop,
        target=tgt,
        qty=qty,
        risk_inr=per_share_risk * qty,
        reward_inr=(tgt - entry) * qty,
        notional_inr=entry * qty,
    )
