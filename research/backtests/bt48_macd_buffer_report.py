"""BT48 — the trades a MACD "open" buffer admits, each on its own chart.

Operator question (2026-09-23): the strict `warrior_strict` rule refuses a
breakout when the 1-minute MACD histogram is smaller than on the previous bar.
A buffer lets it shrink by up to N%. The 1-year replay said the extra trades
lose; this report draws every one of them so the claim can be checked by eye.

For each trade the buffer ADDS (present in the buffered run, absent from the
strict run), the card shows:

  * the session's 1-minute candles, VWAP, EMAs, the trade's levels (BT32);
  * the MACD panel drawn from the ENGINE's own values — 1-minute MACD(12/26/9)
    warmed with the same prior-session bars bt17 gave the engine — not the
    browser's recomputation from today's bars alone, which is wrong for the
    first ~35 minutes of every session, i.e. exactly where these trades live;
  * the decision candle, the two histogram bars the rule compares, and the dip;
  * a re-run of the engine's confirmation function on that candle with the
    strict rule and with the buffer, asserting the refusal really was MACD;
  * what the strict rule did on the same stock that day, and how far price
    went for / against the trade (in R) before it exited.

Descriptive report of an already-replayed window. Not a new test.

  apps/signal-engine/.venv/bin/python research/backtests/bt48_macd_buffer_report.py
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "signal-engine"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bt32_strategy_report as bt32  # noqa: E402
from src.momentum_trader import exits  # noqa: E402
from src.momentum_trader.engine import (  # noqa: E402
    GUIDE_PULLBACK_ORDINALS,
    VOL_BASELINE_MIN_BARS,
    EngineConfig,
    GuideGates,
    _attention_confirmation,
    _macd_open_state,
)
from src.momentum_trader.indicators import macd  # noqa: E402
from src.momentum_trader.setups import MICRO_PAUSE_MAX_BARS, micro_pullback  # noqa: E402

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "bt48_assets"
WARM_SESSIONS = 5  # bt17 under --warrior-strict (warm_context) — see simulate_symbol


def load_runs(tag: str) -> pd.DataFrame:
    return pd.concat([pd.read_csv(HERE / f"bt17_trades_{tag}_1y{y}.csv") for y in ("25", "26")],
                     ignore_index=True)


def guide_cfg(tol: float) -> EngineConfig:
    """The warrior_strict entry checklist, as bt17 --warrior-strict builds it."""
    return EngineConfig(require_micro_pullback=True, require_light_pullback_volume=True,
                        require_macd_positive_open=True,
                        allowed_pullback_ordinals=GUIDE_PULLBACK_ORDINALS,
                        peak_hours_only=True, warm_context=True,
                        vol_baseline_min_bars=VOL_BASELINE_MIN_BARS,
                        use_fixed_target=True, macd_open_tolerance=tol)


def engine_macd(symbol: str, day: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame | None]:
    """Today's 1m bars and the warm-up bars bt17 handed the engine for them."""
    df = bt32.read_cache(symbol, day.year)
    dates = df.index.tz_localize(None).normalize()
    days = sorted(set(dates))
    i = days.index(day.normalize())
    warm_days = set(days[max(0, i - WARM_SESSIONS):i])
    warm = df[dates.isin(warm_days)]
    return df[dates == day.normalize()], (warm if not warm.empty else None)


def macd_series(bars: pd.DataFrame, warm: pd.DataFrame | None) -> pd.DataFrame:
    """MACD line / signal / histogram exactly as `exits.indicator_frame` builds it."""
    joined = bars
    if warm is not None:
        joined = pd.concat([warm.iloc[-exits.WARMUP_BARS:], bars])
        joined = joined[~joined.index.duplicated(keep="last")].sort_index()
    m = macd(joined["close"], exits.MACD_FAST, exits.MACD_SLOW, exits.MACD_SIGNAL)
    n = len(bars)
    out = pd.DataFrame({"macd": m.line.iloc[-n:], "signal": m.signal.iloc[-n:],
                        "hist": m.hist.iloc[-n:]})
    # guard: identical to the number the entry rule reads
    ref = exits.indicator_frame(bars, warm)["macd_hist"]
    assert (out["hist"] - ref).abs().max() < 1e-9
    return out


def r2(x: float | None) -> float | None:
    return None if x is None or pd.isna(x) else round(float(x), 4)


def build(row: pd.Series, tol: float, strict_day: pd.DataFrame) -> dict | None:
    d = bt32.build_day(row)
    if d is None:
        return None
    day = pd.Timestamp(row["date"])
    bars, warm = engine_macd(str(row["symbol"]), day)
    ind = macd_series(bars, warm)
    tz = bars.index.tz
    t_dec = pd.Timestamp(f"{day.date()} {row['trigger_time']}", tz=tz)
    upto = bars.loc[:t_dec]
    hist_now, hist_prev = _macd_open_state(upto, warm)
    strict_setup, strict_reason = _attention_confirmation(
        upto, 2.5, GuideGates(cfg=guide_cfg(0.0), warmup_1m=warm))
    buf_setup, buf_reason = _attention_confirmation(
        upto, 2.5, GuideGates(cfg=guide_cfg(tol), warmup_1m=warm))
    pull = micro_pullback(upto, max_pause_bars=MICRO_PAUSE_MAX_BARS, require_light_volume=True)
    pause_n = int(pull.meta.get("pause_bars", 1)) if pull is not None else 0
    pause = [ts.strftime("%H:%M") for ts in upto.index[-1 - pause_n:-1]] if pause_n else []

    entry, stop = float(row["entry"]), float(row["stop"])
    risk = entry - stop
    t_in = pd.Timestamp(f"{day.date()} {row['entry_time']}", tz=tz)
    t_out = pd.Timestamp(f"{day.date()} {row['exit_time']}", tz=tz)
    held = bars.loc[t_in:t_out]
    mfe_r = (float(held["high"].max()) - entry) / risk if risk > 0 and len(held) else None
    mae_r = (float(held["low"].min()) - entry) / risk if risk > 0 and len(held) else None

    d.update({
        "decision_time": t_dec.strftime("%H:%M"),
        "pause": pause,
        "hist_now": r2(hist_now), "hist_prev": r2(hist_prev),
        "dip_pct": round(100.0 * (hist_prev - hist_now) / hist_prev, 2) if hist_prev else None,
        "tolerance_pct": round(100.0 * tol, 1),
        "strict_reason": strict_reason, "buffer_reason": buf_reason,
        "rule_check_ok": strict_reason == "attention_macd_not_open" and buf_setup is not None,
        "mfe_r": r2(mfe_r), "mae_r": r2(mae_r),
        "strict_same_day": [
            {"in": str(s.entry_time), "out": str(s.exit_time), "exit": str(s.exit_reason),
             "gross_pct": round(float(s.gross_pct), 3)}
            for s in strict_day.itertuples()
        ],
        # engine values per 1-minute bar, aligned with d["bars"]
        "eng": [[r2(a), r2(b), r2(c)] for a, b, c in zip(ind["macd"], ind["signal"], ind["hist"])],
    })
    assert len(d["eng"]) == len(d["bars"]), "engine series must align with the shipped bars"
    return d


def build_html(title: str, sub: str, data: dict) -> str:
    """BT32's page with this report's own assets and a 1m-first timeframe menu."""
    page = bt32.build_html(title, sub, data)
    page = page.replace((bt32.ASSETS / "report.css").read_text(), (ASSETS / "report.css").read_text())
    page = page.replace((bt32.ASSETS / "report.js").read_text(), (ASSETS / "report.js").read_text())
    page = page.replace(
        '<option value="5m">5-minute (what the engine decides on)</option>\n'
        '          <option value="1m">1-minute</option>',
        '<option value="1m">1-minute (where the MACD check runs; engine values)</option>\n'
        '          <option value="5m">5-minute (MACD recomputed in browser, unwarmed)</option>')
    return page


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", default="macdtol_0")
    ap.add_argument("--buffered", default="macdtol_0p05")
    ap.add_argument("--tolerance", type=float, default=0.05)
    ap.add_argument("--output", default="research/backtests/bt48_macd_buffer_report.html")
    a = ap.parse_args()

    strict, buf = load_runs(a.strict), load_runs(a.buffered)
    key = ["date", "symbol", "trigger_time"]
    m = buf.merge(strict[key], on=key, how="left", indicator=True)
    added = buf[(m["_merge"] == "left_only").values]
    rows = []
    for _, r in added.iterrows():
        same = strict[(strict.date == r.date) & (strict.symbol == r.symbol)]
        d = build(r, a.tolerance, same)
        if d is not None:
            rows.append(d)
    bad = [d["symbol"] for d in rows if not d["rule_check_ok"]]
    print(f"{len(rows)} added trades; MACD rule re-check "
          + ("OK on all" if not bad else f"FAILED on {bad}"))
    for d in rows:
        print(f"  {d['date']} {d['symbol']:11s} {d['decision_time']} hist {d['hist_prev']:+.3f} → "
              f"{d['hist_now']:+.3f} (−{d['dip_pct']:.1f}%)  pause {d['pause']}  "
              f"MFE {d['mfe_r']:+.2f}R MAE {d['mae_r']:+.2f}R  {d['exit_reason']}  "
              f"gross {d['gross_pct']:+.2f}%  strict that day: {len(d['strict_same_day'])} trade(s)")

    data = {"trades": rows, "summary": bt32.summarise(rows),
            "weak_strength": bt32.STRENGTH_WEAK_BELOW, "rules_version": bt32.PATTERN_RULES_VERSION}
    sub = (
        f"The {len(rows)} trades a <b>{100 * a.tolerance:.0f}% MACD buffer</b> adds to "
        "<code>warrior_strict</code> over 2025-09-23 → 2026-09-22 (bt17 --warrior-strict --live-fill "
        "--multi-entry): each was refused by the strict rule <i>only</i> because the 1-minute MACD "
        "histogram was smaller than on the previous candle. The MACD panel on the 1-minute view is "
        "the <b>engine's own series</b>, warmed with the same prior-session bars, and each card "
        "re-runs the engine's confirmation check on the decision candle under both rules. "
        "Costs shown twice (bt17 stress and real). Descriptive report of an already-replayed "
        "window — not a new test."
    )
    out = ROOT / a.output
    out.write_text(build_html("BT48 — trades a MACD buffer lets in", sub, data))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
