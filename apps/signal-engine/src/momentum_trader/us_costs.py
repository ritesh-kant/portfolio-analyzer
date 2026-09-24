"""US equity cost model — IBKR Pro, for the US arm of the momentum trader.

Parallel to `news_trader.trailing_sl.calc_costs` (the NSE MIS model), and
deliberately NOT a generalisation of it. The two markets bill on different
axes and mixing them into one function would hide the fact that matters:

    India bills a PERCENTAGE of turnover  (STT, stamp, GST, exchange)
    the US bills PER SHARE                (commission, clearing, venue, TAF)

So the same strategy costs a completely different amount in the two markets
depending on share price alone. BT39 measured the crossover at roughly
$12-15/share: below it the US is dearer, above it cheaper.
See `research/backtests/bt39_us_cost_repricing.py`.

Key shapes to hold on to
------------------------
* Per-share fees are a FIXED number of cents whatever the price, so as a
  percentage they explode as price falls. At $2 a share the fee stack alone is
  ~0.7%; at $50 it is ~0.03%.
* The 1-cent minimum tick does the same thing to the spread. At $2 one cent is
  50 bps; at $50 it is 2 bps. Crossing a one-cent spread on both legs costs
  0.5% of a $2 stock and 0.04% of a $50 stock.
* The guide's own price band is $1-$20 (`us_universe.PRICE_MAX_USD`), which is
  the expensive end of both effects. That is not an argument against the band,
  but it does mean the stop distance has to be large enough to pay for it —
  which is what `us_risk.plan_trade` enforces.

Impact assumption, and why it matches India's
---------------------------------------------
The Indian model bundles a 5 bps/side market-impact estimate into its total.
5 bps per side is half of a 10 bps round-trip spread, so the equivalent US
assumption is a 10 bps spread FLOORED AT ONE CENT. Using a smaller floor would
quietly make the US look cheaper than India for reasons of bookkeeping rather
than market structure.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── IBKR Pro, US equities ───────────────────────────────────────────────────
FIXED_PER_SHARE = 0.0050      # USD/share, all-in (venue fees absorbed)
FIXED_MIN_ORDER = 1.00        # USD per order

TIERED_PER_SHARE = 0.0035     # USD/share, first tier (<= 300k shares/month)
TIERED_MIN_ORDER = 0.35
TIERED_CLEARING = 0.00020     # USD/share
TIERED_EXCHANGE = 0.00300     # USD/share — liquidity-REMOVING.
#   A resting buy-stop becomes marketable the instant it triggers, so this arm
#   pays the take fee and never earns the add rebate. Modelling the rebate here
#   would be assuming an execution style the strategy does not use.

MAX_COMMISSION_FRAC = 0.01    # both plans cap commission at 1% of trade value

# ── regulatory, sell side only ──────────────────────────────────────────────
SEC_FEE_RATE = 0.0000278      # on sale proceeds
TAF_PER_SHARE = 0.000166      # FINRA trading activity fee
TAF_MAX = 8.30                # per trade

# ── market impact ───────────────────────────────────────────────────────────
MIN_TICK = 0.01               # US equities >= $1.00 quote in pennies
SPREAD_BPS = 10.0             # assumed spread when wider than one tick;
                              # equals the Indian model's 5 bps/side
DEFAULT_PLAN = "tiered"


@dataclass(frozen=True)
class USCosts:
    commission: float         # IBKR + clearing + venue, both legs
    sec: float                # SEC fee, sell leg
    taf: float                # FINRA TAF, sell leg
    slippage: float           # modelled spread crossing, both legs
    total: float              # everything above
    notional: float

    @property
    def pct(self) -> float:
        """Round-trip cost as a percentage of position value."""
        return 100.0 * self.total / self.notional if self.notional else float("nan")

    @property
    def fees_pct(self) -> float:
        """Fees only, impact excluded — the like-for-like number against
        India's `calc_costs(...)["total"] - calc_costs(...)["slippage"]`."""
        fees = self.total - self.slippage
        return 100.0 * fees / self.notional if self.notional else float("nan")

    def as_dict(self) -> dict[str, float]:
        """Same key shape as the Indian `calc_costs`, so the ledger can store
        either without branching."""
        return {
            "commission": round(self.commission, 4),
            "sec": round(self.sec, 4),
            "taf": round(self.taf, 4),
            "slippage": round(self.slippage, 4),
            "total": round(self.total, 4),
        }


def spread_usd(price: float, spread_bps: float = SPREAD_BPS) -> float:
    """Assumed bid-ask spread in dollars, floored at one tick.

    The floor is the part that bites: under about $10 the penny tick is wider
    than a 10 bps spread, so the cost of crossing stops scaling with price and
    becomes a fixed cent — which is why cheap stocks are expensive to trade.
    """
    return max(MIN_TICK, price * spread_bps / 1e4)


def impact_usd(price: float, shares: int, spread_bps: float = SPREAD_BPS) -> float:
    """Round-trip impact: half the spread on entry, half again on exit."""
    return spread_usd(price, spread_bps) * shares


def calc_costs(
    price: float,
    shares: int,
    *,
    plan: str = DEFAULT_PLAN,
    spread_bps: float = SPREAD_BPS,
    include_impact: bool = True,
) -> USCosts:
    """Round-trip USD cost of `shares` at `price`, entry and exit.

    `price` is used for both legs. That is the same simplification the Indian
    model makes when it prices a round trip at a single level, and it keeps the
    fee figure independent of the trade's outcome — a cost model that depends
    on the exit price cannot be used to size the trade before it is taken.
    """
    if price <= 0 or shares <= 0:
        return USCosts(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    value = price * shares

    if plan == "fixed":
        per_side = min(
            max(FIXED_MIN_ORDER, FIXED_PER_SHARE * shares),
            MAX_COMMISSION_FRAC * value,
        )
        commission = per_side * 2
    elif plan == "tiered":
        ibkr = min(
            max(TIERED_MIN_ORDER, TIERED_PER_SHARE * shares),
            MAX_COMMISSION_FRAC * value,
        )
        venue = (TIERED_CLEARING + TIERED_EXCHANGE) * shares
        commission = (ibkr + venue) * 2
    else:
        raise ValueError(f"unknown IBKR plan: {plan!r} (expected 'tiered' or 'fixed')")

    sec = value * SEC_FEE_RATE
    taf = min(TAF_MAX, TAF_PER_SHARE * shares)
    slippage = impact_usd(price, shares, spread_bps) if include_impact else 0.0

    return USCosts(
        commission=commission,
        sec=sec,
        taf=taf,
        slippage=slippage,
        total=commission + sec + taf + slippage,
        notional=value,
    )


def cheaper_plan(price: float, shares: int) -> str:
    """Which IBKR plan costs less for this order. Fixed wins on very small
    share counts (its $1 minimum beats tiered's per-share venue fees only when
    size is tiny); tiered wins as soon as the order is of any real size."""
    fixed = calc_costs(price, shares, plan="fixed", include_impact=False).total
    tiered = calc_costs(price, shares, plan="tiered", include_impact=False).total
    return "fixed" if fixed < tiered else "tiered"
