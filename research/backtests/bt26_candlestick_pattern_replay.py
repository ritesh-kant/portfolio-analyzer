"""Reproducible, pattern-only replay for one cached NSE symbol.

This is deliberately an *observational* backtest.  It answers whether strict,
fully completed 5-minute Morning Star / Morning Doji Star / Rising Three
formations would have produced a viable 2R intraday trade.  It does not claim
to be the production strategy: it bypasses the scanner's mover, RVOL,
catalyst, and one-minute-volume gates so the pattern itself can be measured.

Entry: open of the first 1-minute bar after the final 5-minute candle closes.
Exit: hard stop, then 2R target (conservative stop-first ordering if a bar
spans both), otherwise 15:15 close.  Size and costs use production code.

Example:
  python research/backtests/bt26_candlestick_pattern_replay.py --symbol ABSLAMC --year 2026
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))

from src.momentum_trader.candles import PatternMatch, completed_pattern_matches  # noqa: E402
from src.momentum_trader.engine import resample_5m  # noqa: E402
from src.momentum_trader.risk import plan_trade  # noqa: E402
from src.news_trader.trailing_sl import calc_costs  # noqa: E402


@dataclass(frozen=True)
class ReplayTrade:
    date: str
    pattern: str
    formed_at: str
    entry_at: str
    entry: float
    stop: float
    target: float
    exit_at: str
    exit: float
    reason: str
    qty: int
    gross_inr: float
    costs_inr: float
    net_inr: float


def _trade_day(bars: pd.DataFrame, match: PatternMatch) -> ReplayTrade | None:
    # 5m labels are candle starts; the last pattern candle closes five minutes
    # later.  Using the next minute's open prevents look-ahead.
    formed_start = pd.Timestamp(match.end)
    decision_at = formed_start + pd.Timedelta(minutes=5)
    future = bars[bars.index >= decision_at]
    if future.empty:
        return None
    entry_at, first = future.index[0], future.iloc[0]
    entry = float(first["open"])
    plan = plan_trade(entry, match.invalidation, risk_inr=500.0, max_notional_inr=50_000.0)
    if plan is None:
        return None

    # Scanner EOD is the bar that starts at 15:14 and closes at 15:15.
    trade_bars = future[future.index.time <= pd.Timestamp("15:14").time()]
    if trade_bars.empty:
        return None
    exit_at, exit_px, reason = trade_bars.index[-1], float(trade_bars["close"].iloc[-1]), "eod_close"
    for at, bar in trade_bars.iterrows():
        if float(bar["low"]) <= plan.stop:
            exit_at, exit_px, reason = at, max(float(bar["open"]), plan.stop), "stop"
            break
        if float(bar["high"]) >= plan.target:
            exit_at, exit_px, reason = at, plan.target, "target"
            break
    gross = (exit_px - entry) * plan.qty
    costs = float(calc_costs(entry, exit_px, plan.qty)["total"])
    return ReplayTrade(
        date=str(entry_at.date()), pattern=match.name, formed_at=decision_at.isoformat(),
        entry_at=entry_at.isoformat(), entry=entry, stop=plan.stop, target=plan.target,
        exit_at=exit_at.isoformat(), exit=exit_px, reason=reason, qty=plan.qty,
        gross_inr=gross, costs_inr=costs, net_inr=gross - costs,
    )


def replay(bars_1m: pd.DataFrame) -> list[ReplayTrade]:
    """Take the first valid strict formation each day, matching live one-trade policy."""
    trades: list[ReplayTrade] = []
    for _, day in bars_1m.groupby(bars_1m.index.normalize()):
        day = day[day.index.time <= pd.Timestamp("15:14").time()]
        tf5 = resample_5m(day)
        for end in range(3, len(tf5) + 1):
            matches = completed_pattern_matches(tf5.iloc[:end], "5m")
            if not matches:
                continue
            trade = _trade_day(day, matches[0])
            if trade is not None:
                trades.append(trade)
                break
    return trades


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="ABSLAMC")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--output", type=Path, help="optional CSV path")
    args = parser.parse_args()
    path = ROOT / "research" / "backtests" / ".cache_upstox" / "1m" / f"{args.symbol.upper()}_{args.year}.parquet"
    if not path.exists():
        raise SystemExit(f"cache not found: {path}")
    bars = pd.read_parquet(path)
    if bars.empty:
        raise SystemExit("cache has no bars")
    trades = replay(bars)
    frame = pd.DataFrame([asdict(trade) for trade in trades])
    if args.output:
        frame.to_csv(args.output, index=False)
        print(f"wrote {args.output}")
    print(f"{args.symbol.upper()} {args.year}: {len(trades)} first-pattern trades")
    if frame.empty:
        return 0
    print(frame.groupby("pattern")["net_inr"].agg(["count", "sum", "mean"]).round(2).to_string())
    print(f"total gross ₹{frame.gross_inr.sum():,.2f}; costs ₹{frame.costs_inr.sum():,.2f}; net ₹{frame.net_inr.sum():,.2f}")
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
