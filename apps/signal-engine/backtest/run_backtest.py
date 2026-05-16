"""run_backtest — CLI entry point for the walk-forward backtest harness.

Usage examples:

    # Full 2021-2024 walk-forward backtest (Nifty ~100 stock universe)
    python -m backtest.run_backtest \\
        --start 2021-01-01 \\
        --end   2024-12-31 \\
        --capital 1000000 \\
        --output results/

    # Quick smoke test on a 3-month range, small universe
    python -m backtest.run_backtest \\
        --start 2023-01-01 \\
        --end   2023-06-30 \\
        --symbols RELIANCE.NS TCS.NS INFY.NS HDFCBANK.NS \\
        --window 2 --step 1

    # Recalibrate Kelly table from last run's trade log
    python -m backtest.run_backtest --recalibrate \\
        --trades backtest/results/<run_id>/trades.csv

    # Force-refresh all cached data then run
    python -m backtest.run_backtest --start 2022-01-01 --end 2023-12-31 --refresh
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Walk-forward backtest for the NSE signal engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Mutually exclusive top-level modes
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--recalibrate", action="store_true",
        help="Recalibrate Kelly table from trade CSV instead of running backtest",
    )

    # Backtest parameters
    p.add_argument("--start",   default="2021-01-01", help="Backtest start date (YYYY-MM-DD)")
    p.add_argument("--end",     default="2024-12-31", help="Backtest end date (YYYY-MM-DD)")
    p.add_argument("--capital", type=float, default=1_000_000.0,
                   help="Initial capital per fold in rupees (default: ₹10,00,000)")
    p.add_argument("--window",  type=int, default=3, help="Test window months (default: 3)")
    p.add_argument("--step",    type=int, default=1, help="Step months between folds (default: 1)")
    p.add_argument("--symbols", nargs="+", default=None,
                   help="Symbol list (default: full Nifty ~100 universe from SECTOR_STOCKS)")
    p.add_argument("--confidence", type=float, default=60.0,
                   help="Minimum signal confidence to trade (default: 60)")
    p.add_argument("--refresh", action="store_true",
                   help="Force re-download all data even if cache exists")
    p.add_argument("--output",  default=None,
                   help="Results directory prefix (default: auto timestamped)")
    p.add_argument("--no-save", action="store_true",
                   help="Print console report only; don't write CSV files")

    # Recalibration parameters
    p.add_argument("--trades",    default=None, help="Path to trades.csv for --recalibrate")
    p.add_argument("--source",    choices=["backtest", "paper"], default="backtest",
                   help="Trade data source for --recalibrate (default: backtest)")
    p.add_argument("--dry-run",   action="store_true",
                   help="Preview recalibration diff without writing")
    p.add_argument("--confirm",   action="store_true",
                   help="Write recalibration without interactive prompt")

    # Logging
    p.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging")

    return p


async def _run(args: argparse.Namespace) -> int:
    """Async entrypoint. Returns exit code."""

    if args.recalibrate:
        from backtest.recalibrate import recalibrate
        await recalibrate(
            source=args.source,
            trades_csv=args.trades,
            dry_run=args.dry_run,
            confirm=args.confirm,
        )
        return 0

    # ── Backtest run ──────────────────────────────────────────────────────────
    from backtest.engine import BacktestConfig, run_backtest
    from backtest.report import print_and_save

    config = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        test_window_months=args.window,
        step_months=args.step,
        min_signal_confidence=args.confidence,
        refresh_data=args.refresh,
        symbols=args.symbols or [],
    )

    run_id = args.output or datetime.now().strftime("%Y%m%d_%H%M%S")

    config_desc = (
        f"{config.start_date} → {config.end_date}  |  "
        f"capital=₹{config.initial_capital:,.0f}  |  "
        f"window={config.test_window_months}m  step={config.step_months}m  |  "
        f"min_conf={config.min_signal_confidence:.0f}  |  "
        f"universe={'custom' if args.symbols else 'full'}"
    )

    print(f"\n  Starting backtest: {config_desc}")
    agg = await run_backtest(config)

    if args.no_save:
        from backtest.report import print_fold_table, print_aggregate_summary
        print_fold_table(agg.fold_results)
        print_aggregate_summary(agg, config_desc)
    else:
        print_and_save(agg, run_id=run_id, config_desc=config_desc)

    # Exit 1 if gate fails so CI/scripts can detect a failing strategy
    return 0 if agg.passes_gate else 1


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    exit_code = asyncio.run(_run(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
