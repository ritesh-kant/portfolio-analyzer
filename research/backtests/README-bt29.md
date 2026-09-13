# BT29 — regenerating the candlestick trade report

Two commands, from the repository root. Nothing else is needed: both read the
cached 1-minute bars already in `research/backtests/.cache_upstox/1m/` and the
trade files already in `research/backtests/bt17_trades*.csv`.

## 1. Score every past trade (~4 minutes on 8 cores)

```bash
apps/signal-engine/.venv/bin/python research/backtests/bt29_pattern_on_trades.py --jobs 8
```

For each of the ~11,800 past momentum trades it re-runs the v3 detector on that
session's 5-minute bars with `closed_through` set to the fill timestamp and the
three bars before it, computes the pullback ordinal, and records the size of the
nearest formation. It prints the analysis and writes four files:

| file | what it is |
| --- | --- |
| `bt29_trades_scored.csv` | every trade + what v3 saw at the fill |
| `bt29_summary.csv` | the contrasts over the whole pool |
| `bt29_clean_pool.csv` | **the population the report uses** |
| `bt29_summary_clean.csv` | the contrasts over that population |

The clean population is decided in code, not by hand. A source run qualifies
only if it is one trade per symbol-day, has effectively no breakeven-lock
scratches, and is not the killed multi-entry arm. The run prints which files
passed, so the filter is auditable rather than a list someone typed once.

Useful flags: `--limit N` for a smoke test, `--jobs 1` to debug in one process,
`--min-n` for the minimum detections before a per-pattern cell is reported.

## 2. Build the HTML (~30 seconds)

```bash
apps/signal-engine/.venv/bin/python research/backtests/bt29_trade_report.py --max-charts 240
```

Writes `research/backtests/bt29_trade_report.html`: one dark candlestick chart
per trade with BUY / STOP / EXIT levels, the holding period shaded, every
formation shaded and bracket-labelled with its strength multiple, and a sortable
trade table filterable by what v3 saw, outcome, pullback ordinal and formation
size. Charts are a deterministic stratified sample that oversamples the trades
the test is about; `--seed` changes which ones, `--max-charts 0`-ish values keep
the file small.

## 3. Open it

The file is ~2.6 MB, which the desktop app's `file://` preview refuses, so serve
it:

```bash
python3 -m http.server 8899 --directory research/backtests
```

Then open `http://localhost:8899/bt29_trade_report.html`. The same server also
serves the BT27 detector-audit reports.

## Related

* Pre-registration and decision rule:
  `research/hypotheses/2026-09-12-pattern-confirmation-on-past-trades.md`
* Findings, including why the first population was wrong:
  `research/specs/candlestick-detector-evaluation.md` §3
* Pattern definitions and the `strength` field:
  `research/specs/candlestick-recognition-v2.md`
* Detector audit (patterns without trades): `bt27_pattern_visual_report.py`
* Unconditional census: `bt28_pattern_census.py`
