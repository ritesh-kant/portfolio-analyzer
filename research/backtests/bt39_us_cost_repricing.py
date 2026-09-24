"""BT39 — what the existing momentum pool costs under a US fee stack.

Descriptive re-pricing, not a new strategy test. The trade population is held
fixed (the BT29 clean pool: one trade per symbol-day, post-breakeven-lock-fix
runs only, multi-entry arm excluded) and ONLY the cost model changes.

The question this answers: India's intraday cost stack is percentage-based
(STT, stamp, GST all scale with turnover) while the US stack is per-SHARE
(commission per share, plus tiny regulatory percentages). Those two shapes
cross over somewhere. This finds where.

Fair-comparison rule
--------------------
The engine's Indian model (`news_trader.trailing_sl.calc_costs`) bundles a
5 bps/side market-impact ESTIMATE into its total. Comparing that against US
commissions alone would be rigging the result, so impact is split out and the
same assumption is applied to both sides. Only the fee stacks differ.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.news_trader.trailing_sl import (  # noqa: E402
    _SLIPPAGE_RATE, calc_costs,
)

# ── US fee stack ────────────────────────────────────────────────────────────
# IBKR Pro, US equities. Two published plans; both modelled because which one
# wins depends entirely on share price, which is the whole point of the study.
FIXED_PER_SHARE = 0.0050      # USD/share, all-in
FIXED_MIN_ORDER = 1.00        # USD, per order
TIERED_PER_SHARE = 0.0035     # USD/share, <=300k shares/month
TIERED_MIN_ORDER = 0.35
TIERED_CLEARING = 0.00020     # USD/share
TIERED_EXCHANGE = 0.00300     # USD/share, liquidity-REMOVING.
#   A resting buy-stop becomes marketable the moment it triggers, so it takes
#   liquidity and pays the take fee rather than earning the add rebate.
MAX_COMMISSION_FRAC = 0.01    # both plans cap at 1% of trade value

SEC_FEE_RATE = 0.0000278      # on SELL proceeds only
TAF_PER_SHARE = 0.000166      # sell side only
TAF_MAX = 8.30


def us_costs(price_usd: float, shares: int, plan: str = "tiered") -> dict[str, float]:
    """Round-trip US fees in USD for `shares` at `price_usd`, entry and exit."""
    value = price_usd * shares
    if plan == "fixed":
        per_side = min(max(FIXED_MIN_ORDER, FIXED_PER_SHARE * shares),
                       MAX_COMMISSION_FRAC * value)
        commission = per_side * 2
    else:
        ibkr = min(max(TIERED_MIN_ORDER, TIERED_PER_SHARE * shares),
                   MAX_COMMISSION_FRAC * value)
        venue = (TIERED_CLEARING + TIERED_EXCHANGE) * shares
        commission = (ibkr + venue) * 2

    sec = value * SEC_FEE_RATE                       # sell leg only
    taf = min(TAF_MAX, TAF_PER_SHARE * shares)       # sell leg only
    total = commission + sec + taf
    return {"commission": commission, "sec": sec, "taf": taf, "total": total,
            "pct": 100.0 * total / value if value else float("nan")}


def india_costs(price_inr: float, qty: int) -> dict[str, float]:
    """Round-trip Indian MIS fees, impact split out so it can be re-applied
    identically to both markets."""
    c = calc_costs(price_inr, price_inr, qty, direction="long")
    value = price_inr * qty
    fees = c["total"] - c["slippage"]
    return {"fees": fees, "impact": c["slippage"], "total": c["total"],
            "fees_pct": 100.0 * fees / value,
            "impact_pct": 100.0 * c["slippage"] / value,
            "pct": 100.0 * c["total"] / value}


IMPACT_PCT = 100.0 * _SLIPPAGE_RATE * 2   # same round-trip assumption, both sides


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=str(ROOT / "research/backtests/bt29_clean_pool.csv"))
    ap.add_argument("--fx", type=float, default=88.0, help="INR per USD")
    ap.add_argument("--bt36-lift", type=float, default=0.1225,
                    help="pp to add to gross for the BT36 live-fill correction "
                         "(measured on the legacy 2024 pool; an APPROXIMATION here)")
    args = ap.parse_args()

    d = pd.read_csv(args.pool)
    d["notional_inr"] = d.entry * d.qty

    # ── 1. the pool as it stands ────────────────────────────────────────────
    real = d.apply(lambda r: india_costs(r.entry, int(r.qty)), axis=1, result_type="expand")
    d = pd.concat([d, real.add_prefix("in_")], axis=1)
    gross = d.gross_pct.mean()
    in_cost = d.in_pct.mean()

    print("=" * 78)
    print("BT39 — US cost re-pricing of the BT29 clean pool")
    print("=" * 78)
    print(f"trades {len(d):,}   years {d.year.min()}–{d.year.max()}   src files {d.src.nunique()}")
    print()
    print("-- the pool at REAL Indian costs -------------------------------------")
    print(f"  gross                       {gross:+.4f}%/trade")
    print(f"  Indian cost                 {in_cost:.4f}%   "
          f"(fees {d.in_fees_pct.mean():.4f} + impact {d.in_impact_pct.mean():.4f})")
    print(f"  net                         {gross - in_cost:+.4f}%/trade")
    print()
    print(f"  median position  Rs {d.notional_inr.median():,.0f}  "
          f"= ${d.notional_inr.median()/args.fx:,.0f}")
    print(f"  median price     Rs {d.entry.median():,.1f}  "
          f"= ${d.entry.median()/args.fx:,.2f}")
    print(f"  median qty          {d['qty'].median():,.0f} shares")
    print()

    # ── 2. transplant the SAME trades, same size, into the US fee stack ─────
    px_usd = d.entry / args.fx
    tier = [us_costs(p, int(q), "tiered")["pct"] for p, q in zip(px_usd, d["qty"])]
    fix = [us_costs(p, int(q), "fixed")["pct"] for p, q in zip(px_usd, d["qty"])]
    d["us_tiered_pct"] = tier
    d["us_fixed_pct"] = fix

    print("-- naive transplant: same share counts, same USD-converted prices ----")
    print(f"  US tiered fees              {d.us_tiered_pct.mean():.4f}%")
    print(f"  US fixed  fees              {d.us_fixed_pct.mean():.4f}%")
    print(f"  India     fees              {d.in_fees_pct.mean():.4f}%")
    print("  (impact excluded from all three — added back identically below)")
    print()

    # ── 3. the grid that actually answers the question ─────────────────────
    print("-- ROUND-TRIP COST as % of position, by share price x position size --")
    print("   US = IBKR tiered fees + the same 0.100% impact assumption")
    print(f"   IN = Indian MIS fees + the same 0.100% impact  (= {IMPACT_PCT:.3f}% both)")
    print()
    sizes = [2_000, 5_000, 10_000, 25_000, 50_000]
    prices = [2.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0]
    hdr = "  price |" + "".join(f"{s/1000:>9.0f}k" for s in sizes)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for p in prices:
        cells = []
        for s in sizes:
            sh = max(1, int(s / p))
            us = us_costs(p, sh, "tiered")["pct"] + IMPACT_PCT
            cells.append(f"{us:>9.3f}%")
        print(f"  ${p:>5.0f} |" + "".join(cells))
    inr_ref = india_costs(500.0, 100)
    print()
    print(f"  India reference (any size, percentage-based): "
          f"{inr_ref['pct']:.3f}%  "
          f"[fees {inr_ref['fees_pct']:.3f} + impact {inr_ref['impact_pct']:.3f}]")
    print()

    # ── 4. does it flip? ───────────────────────────────────────────────────
    print("-- does the sign flip? -----------------------------------------------")
    g_corr = gross + args.bt36_lift
    print(f"  pool gross as measured           {gross:+.4f}%")
    print(f"  + BT36 live-fill lift ({args.bt36_lift:+.4f})   {g_corr:+.4f}%   "
          f"<- APPROXIMATE, see note")
    print()
    for label, g in (("as-measured", gross), ("BT36-corrected", g_corr)):
        print(f"  [{label}]")
        print(f"     India  ({in_cost:.3f}%)      net {g - in_cost:+.4f}%")
        for p, s in ((20.0, 10_000), (50.0, 10_000), (100.0, 25_000)):
            sh = max(1, int(s / p))
            us = us_costs(p, sh, "tiered")["pct"] + IMPACT_PCT
            flag = "POSITIVE" if g - us > 0 else ""
            print(f"     US ${p:.0f} / ${s/1000:.0f}k ({us:.3f}%)  net {g - us:+.4f}%  {flag}")
        print()

    out = ROOT / "research/backtests/bt39_us_repricing.csv"
    d[["date", "symbol", "year", "entry", "qty", "gross_pct",
       "in_pct", "in_fees_pct", "us_tiered_pct", "us_fixed_pct"]].to_csv(out, index=False)
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()


# ── appendix: impact that respects the tick ─────────────────────────────────
# The flat 0.100% above is the engine's Indian assumption applied uniformly so
# the fee stacks could be compared fairly. It is not realistic across a 100x
# price range: US quotes are in 1-cent ticks, so a $5 stock cannot have a
# spread tighter than 0.20% while a $100 stock routinely quotes 1-2 bps.
# Crossing half the spread per side is the floor on impact for a marketable
# order, which every stop and every triggered buy-stop is.
# CORRECTION 2026-09-17: bps_floor was 5.0, which is the Indian model's PER-SIDE
# impact, not its spread. 5 bps/side is half of a 10 bps round-trip spread, so
# comparing a 5 bps US spread against it charged the US half as much impact as
# India wherever the tick did not bind — i.e. above ~$10, exactly where the US
# was being reported as cheaper. The floored (<= $10) rows are unaffected.
def tick_aware_impact_pct(price_usd: float, bps_floor: float = 10.0) -> float:
    spread = max(0.01, price_usd * bps_floor / 1e4)     # 1-cent tick floor
    return 100.0 * spread / price_usd                    # half-spread x 2 sides


def appendix() -> None:
    print()
    print("-- APPENDIX: same grid, impact floored at the 1-cent tick -----------")
    print("   marketable orders cross half the spread each way; a 1-cent tick on")
    print("   a cheap stock IS a large percentage, and no venue choice fixes it")
    print()
    sizes = [5_000, 10_000, 25_000]
    print("  price |  spread |" + "".join(f"{s/1000:>9.0f}k" for s in sizes) + "   vs India")
    print("  " + "-" * 62)
    for p in [2.0, 5.0, 10.0, 25.0, 50.0, 100.0, 200.0]:
        imp = tick_aware_impact_pct(p)
        cells = []
        for s in sizes:
            sh = max(1, int(s / p))
            cells.append(f"{us_costs(p, sh, 'tiered')['pct'] + imp:>9.3f}%")
        sh = max(1, int(10_000 / p))
        delta = (us_costs(p, sh, "tiered")["pct"] + imp) - 0.206
        print(f"  ${p:>5.0f} | {imp:>6.3f}% |" + "".join(cells) +
              f"   {delta:+.3f} pp {'WORSE' if delta > 0 else 'better'}")


if __name__ == "__main__":
    appendix()
