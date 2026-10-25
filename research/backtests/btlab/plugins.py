"""The indicator registry: every checkbox in the lab is one `Plugin` here.

Two groups, because they act on different things:

  entry  decides WHICH base candidates are taken. A vectorised test over the
         candidate table, using only values stamped as of the decision bar.
  exit   decides how a taken trade is MANAGED. It edits an `ExitCfg` that
         `sim.simulate` replays on the 1-minute bars.

Adding an indicator is one entry in `ENTRY` or `EXIT`. The UI is generated from
`catalog()`, so nothing else changes. Every plugin carries a one-line plain
description and, for the ones the repo already measured, the BT it came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from .sim import ExitCfg


@dataclass(frozen=True)
class Param:
    key: str
    label: str
    default: float | int | str
    kind: str = "float"            # float | int | time | select
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plugin:
    id: str
    group: str                      # entry | exit
    name: str
    desc: str
    params: tuple[Param, ...] = ()
    exclusive: str = ""             # plugins sharing a non-empty value cannot be combined
    history: str = ""               # what the repo already measured about it
    entry: Callable[[pd.DataFrame, dict], pd.Series] | None = None
    exit: Callable[[ExitCfg, dict], None] | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id, "group": self.group, "name": self.name, "desc": self.desc,
            "exclusive": self.exclusive, "history": self.history,
            "params": [{"key": p.key, "label": p.label, "default": p.default, "kind": p.kind,
                        "min": p.min, "max": p.max, "step": p.step, "options": list(p.options)}
                       for p in self.params],
        }


def hhmm(s: str | int | float) -> int:
    """'10:30' -> minutes since midnight. A bare number is taken as minutes already."""
    if isinstance(s, (int, float)):
        return int(s)
    h, _, m = str(s).partition(":")
    return int(h) * 60 + int(m or 0)


# ------------------------------------------------------------------ entry plugins

def _time_window(df: pd.DataFrame, p: dict) -> pd.Series:
    return (df["decision_min"] >= hhmm(p["start"])) & (df["fill_min"] < hhmm(p["end"]))


def _momentum(df: pd.DataFrame, p: dict) -> pd.Series:
    return ((df["day_chg_pct"] >= p["day_chg_min"]) & (df["day_chg_pct"] <= p["day_chg_max"])
            & (df["rvol"] >= p["rvol_min"]))


def _rr(df: pd.DataFrame) -> pd.Series:
    return (df["target"] - df["fill"]) / (df["fill"] - df["stop"])


def _above_vwap(df: pd.DataFrame, p: dict) -> pd.Series:
    dist = (df["close5"] / df["vwap5"] - 1.0) * 100.0
    return (dist >= p["min_pct"]) & (dist <= p["max_pct"])


def _kind_ok(kind: pd.Series, want: str, swing_kind: str) -> pd.Series:
    if want == "swing":
        return kind.eq(swing_kind)
    if want == "not_round":
        return kind.astype(str) != "round"
    return pd.Series(True, index=kind.index)


def _support(df: pd.DataFrame, p: dict) -> pd.Series:
    dist = (df["fill"] - df["support"]) / df["fill"] * 100.0
    return (dist >= p["min_pct"]) & (dist <= p["max_pct"]) & _kind_ok(df["support_kind"], p["kind"], "pivot_low")


def _resistance(df: pd.DataFrame, p: dict) -> pd.Series:
    dist = (df["resistance"] - df["fill"]) / df["fill"] * 100.0
    return (dist >= p["min_pct"]) & (dist <= p["max_pct"]) & _kind_ok(df["resistance_kind"], p["kind"], "pivot_high")


def _news_pullback(df: pd.DataFrame, p: dict) -> pd.Series:
    one = p["timeframe"] == "1m"
    col = df["news_pullback_ord_1m" if one else "news_pullback_ord"]
    return (col >= p["min_ordinal"]) & (col <= p["max_ordinal"]) & df["news_in_pullback_1m" if one else "news_in_pullback"].astype(bool)


def _news(df: pd.DataFrame, p: dict) -> pd.Series:
    ok = df["news_move_pct"].notna()
    if p.get("trigger") == "news_bar":
        ok &= df["news_delay_min"].le(p["bar_window_min"])         # NaN (no filing) compares False
        ok &= df["news_move_pct"].ge(p["bar_min_move_pct"])        # never buy a bar that is down on the filing
    return ok


def news_bar_trigger(selected: list[dict]) -> bool:
    return any(x["id"] == "news" and x["params"].get("trigger") == "news_bar" for x in selected)


def first_news_bar(df: pd.DataFrame, selected: list[dict]) -> pd.DataFrame:
    """The news-bar trigger: of the rows that passed every entry indicator, only the first per stock per day.

    "First" is by decision bar, so the trade is the earliest bar at which the filing, its window and the
    price / volume tests all hold. One trade per stock per day, so a stop-out is not re-entered on the same
    story by a later bar (or by another filing of the same news cluster).
    """
    if df.empty or not news_bar_trigger(selected):
        return df
    ordered = df.sort_values(["date", "symbol", "decision_min", "fill_min"], kind="stable")
    return ordered[~ordered.duplicated(["date", "symbol"])]


ENTRY: tuple[Plugin, ...] = (
    Plugin("time_window", "entry", "Time of day",
           "Only take entries inside a clock window (decision after the start, fill before the end).",
           (Param("start", "From", "09:30", "time"), Param("end", "Until", "11:00", "time")),
           history="BT53: the 11:00 cap vs the 14:00 cutoff — removing it was KILLED 1/4.",
           entry=_time_window),
    Plugin("momentum_tighter", "entry", "Stronger momentum",
           "Tighten the base momentum band: day change and time-of-day relative volume.",
           (Param("day_chg_min", "Day chg ≥ %", 5.0, "float", 0, 50, 0.5),
            Param("day_chg_max", "Day chg ≤ %", 50.0, "float", 0, 100, 0.5),
            Param("rvol_min", "RVOL ≥", 5.0, "float", 0, 50, 0.5)),
           entry=_momentum),
    Plugin("above_vwap", "entry", "Above VWAP",
           "5-minute close inside a band above session VWAP (from 0% up to the limit): buyers are in control, optionally not stretched too far.",
           (Param("min_pct", "Above VWAP by ≥ %", 0.0, "float", -2, 20, 0.05),
            Param("max_pct", "Above VWAP by ≤ %", 20.0, "float", 0, 50, 0.05)),
           history="Part of the F1 uptrend test (BT22, killed with the bundle).",
           entry=_above_vwap),
    Plugin("vwap_cross", "entry", "Just crossed above VWAP",
           "The 5-minute close went from under VWAP to above it this many 5-minute bars before the pattern completed (0 = the pattern bar itself crossed) and has stayed above since. Stocks that opened above VWAP and never dipped under have no cross and are skipped.",
           (Param("min_bars", "Crossed ≥ bars ago", 0, "int", 0, 80, 1),
            Param("max_bars", "Crossed ≤ bars ago", 3, "int", 0, 80, 1)),
           entry=lambda df, p: (df["vwap_cross_bars"] >= p["min_bars"]) & (df["vwap_cross_bars"] <= p["max_bars"])),
    Plugin("ema9_over_ema20", "entry", "EMA9 over EMA20",
           "Short-term trend up on the 5-minute chart (EMA9 above EMA20).",
           history="BT30 one-minute agreement / BT22 F1.",
           entry=lambda df, p: df["ema9_5"] > df["ema20_5"]),
    Plugin("close_over_ema9", "entry", "Close over EMA9",
           "5-minute close above its EMA9: not pulling back through the fast average.",
           entry=lambda df, p: df["close5"] > df["ema9_5"]),
    Plugin("above_ema200", "entry", "Above EMA200",
           "5-minute close above the 200 EMA (about 2.7 sessions): the bigger trend is up.",
           entry=lambda df, p: df["close5"] > df["ema200_5"]),
    Plugin("macd5_positive", "entry", "MACD (5m) positive",
           "5-minute MACD histogram above zero: momentum is on the buyers' side.",
           entry=lambda df, p: df["macd5"] > 0),
    Plugin("macd1_open", "entry", "MACD (1m) positive & open",
           "1-minute MACD histogram above zero AND still widening: the guide's 'positive and open'.",
           history="BT48 replayed a 5–10% looser 'open' (2026-09-23).",
           entry=lambda df, p: (df["macd1"] > 0) & (df["macd1"] > df["macd1_prev"])),
    Plugin("volume_surge", "entry", "Volume surge on the pattern bar",
           "The last candle of the pattern traded this many times the recent 5-minute average volume.",
           (Param("min_ratio", "Volume ≥ × avg", 2.0, "float", 0, 20, 0.25),),
           history="BT31: the volume-surge entry lever was KILLED.",
           entry=lambda df, p: df["vol_ratio5"] >= p["min_ratio"]),
    Plugin("pattern_strength", "entry", "Pattern candle size",
           "Signal candle at least this multiple of the ten-candle average range (descriptive in the detector).",
           (Param("min_strength", "Strength ≥", 1.0, "float", 0, 10, 0.1),),
           history="BT29: strength gates nothing in the detector; 75% of formations are below 1.0.",
           entry=lambda df, p: df["strength"] >= p["min_strength"]),
    Plugin("min_reward_risk", "entry", "Minimum reward : risk",
           "Distance to the resistance target at least this many times the distance to the support stop.",
           (Param("min_rr", "R:R ≥", 2.0, "float", 0.5, 10, 0.25),),
           history="BT55: the 1R headroom veto reading earlier sessions' highs.",
           entry=lambda df, p: _rr(df) >= p["min_rr"]),
    Plugin("max_stop_distance", "entry", "Tighter stop",
           "Skip trades whose support stop is further than this below the entry.",
           (Param("max_pct", "Stop ≤ % below", 1.5, "float", 0.3, 3, 0.1),),
           entry=lambda df, p: (df["fill"] - df["stop"]) / df["fill"] * 100.0 <= p["max_pct"]),
    Plugin("pullback_ordinal", "entry", "Pullback number (from / to)",
           "Only bars on a chosen pullback of the day's move: from N to M, so 2 to 2 is 'only the 2nd' and 2 to 3 is 'the 2nd and 3rd'. Counted from the day's first sharp advance: each confirmed swing high after it advances the count (1 = the first pullback). For a count from the news instead, use News pullback number.",
           (Param("min_ordinal", "Pullback from", 1, "int", 1, 6, 1),
            Param("max_ordinal", "Pullback to", 2, "int", 1, 6, 1)),
           history="BT20 pullback ordinal (1st-2nd pullback). The exact from / to range is untested.",
           entry=lambda df, p: (df["pullback_ord"] >= p.get("min_ordinal", 1)) & (df["pullback_ord"] <= p["max_ordinal"])),
    Plugin("news_pullback", "entry", "News pullback number (from / to)",
           "Only bars that sit INSIDE a chosen pullback of the NEWS move, so the base candlestick pattern must form during the pullback (use it with the pattern trigger, not news_bar). Pullbacks are counted from the filing as confirmed swing highs on the chosen candle (5m or 1m): 1 = the first pullback after the news spike, from N to M (2 to 2 = only the 2nd, 2 to 3 = the 2nd and 3rd). A bar is inside the pullback only while the latest confirmed swing high has not been traded above since it printed; once price makes a new high the stock is rallying again, not pulling back, until a new peak is confirmed. Needs News ticked (its lookback and scope pick the filing). A swing high is confirmed 3 candles after it prints (15 minutes on 5m, 3 on 1m).",
           (Param("timeframe", "Swing highs on", "5m", "select", options=("5m", "1m")),
            Param("min_ordinal", "Pullback from", 1, "int", 1, 6, 1),
            Param("max_ordinal", "Pullback to", 1, "int", 1, 6, 1)),
           history="Untested. The day-advance count (Pullback number) was BT20. A bare swing-high count once labelled a new-high bar 'pullback 1', a bare % depth did not test for a pattern; hence the in-pullback phase.",
           entry=_news_pullback),
    Plugin("uptrend_f1", "entry", "Uptrend (F1)",
           "EMA9 over EMA20, above VWAP, and yesterday's close above the 20-day average.",
           history="BT22 quality bundle F1+F2+F3: KILLED.", entry=lambda df, p: df["f1_uptrend"]),
    Plugin("chart_quality_f2", "entry", "Clean chart (F2)",
           "The stock trades most minutes and is not mostly flat bars (no thin tape).",
           history="BT22 quality bundle: KILLED.", entry=lambda df, p: df["f2_chart"]),
    Plugin("surge_f3", "entry", "Surge with volume (F3)",
           "A sharp advance already printed today on heavy volume.",
           history="BT22 quality bundle: KILLED.", entry=lambda df, p: df["f3_surge"]),
    Plugin("rising_price_volume", "entry", "Price and volume rising",
           "Both the 4-bar price slope and the 4-bar volume slope are positive at the decision.",
           history="BT41 price/volume gate: KILLED (worse than random).",
           entry=lambda df, p: df["pv_rising"]),
    Plugin("daily_uptrend", "entry", "Daily uptrend",
           "Yesterday's close at or above the 20-day average.", entry=lambda df, p: df["daily_uptrend"]),
    Plugin("atr_band", "entry", "Volatility band",
           "Daily ATR as % of price inside a range: not too sleepy, not too wild.",
           (Param("lo", "ATR% ≥", 1.0, "float", 0, 20, 0.25), Param("hi", "ATR% ≤", 4.0, "float", 0, 20, 0.25)),
           history="BT23 volatility-scaled entry: KILLED.",
           entry=lambda df, p: (df["atr_pct"] >= p["lo"]) & (df["atr_pct"] <= p["hi"])),
    Plugin("away_from_round", "entry", "Away from round numbers",
           "The decision price is not sitting on a 0.50 round number.",
           (Param("min_pct", "Distance ≥ %", 0.15, "float", 0, 1, 0.05),),
           history="BT24 entry location.",
           entry=lambda df, p: df["dist_round_pct"] >= p["min_pct"]),
    Plugin("news", "entry", "News",
           "A qualifying company filing published inside the lookback before the base candlestick decision. No price test: that is the separate Market reaction indicator. NSE uses subject categories; US uses SEC acceptance time, which can lag the press release. Queries and share offerings are excluded. Needs cached filings. Entry trigger 'news_bar' drops the candlestick: the trade is the FIRST bar (per stock per day) after a filing that passes every ticked entry indicator, within the window and with price up at least the floor since the filing, bought at the next minute's open. It runs on the no-pattern base (switched automatically).",
           (Param("lookback_h", "News within last (h)", 24, "float", 1, 72, 1),
            Param("scope", "Counts as news", "material", "select", options=("material", "material+unclear")),
            Param("trigger", "Entry trigger", "pattern", "select", options=("pattern", "news_bar")),
            Param("bar_window_min", "News bar within (min of the filing; news_bar only)", 30, "int", 1, 390, 5),
            Param("bar_min_move_pct", "News bar: price up since filing ≥ % (news_bar only)", 0.5, "float", 0, 30, 0.1)),
           history="news-trader (BT12) found no drift after news on a next-day basis; tick Market reaction as well to ask the intraday question. The news_bar trigger is untested.",
           entry=_news),
    Plugin("market_reaction", "entry", "Market reaction",
           "The tape confirms the news: price is up at least this much since the filing at the base candlestick decision, on enough volume. It measures from the filing found by the News indicator, so tick News too (its lookback and scope apply). Session RVOL includes volume before the filing; post_news measures only today's volume after it against the same minutes in prior sessions. The base RVOL floor still applies.",
           (Param("min_move_pct", "Price up since news ≥ %", 2.0, "float", 0, 30, 0.5),
            Param("min_rvol", "RVOL ≥", 3.0, "float", 0, 50, 0.5),
            Param("volume_mode", "Volume confirmation", "session", "select", options=("session", "post_news"))),
           history="Split out of the old combined 'News + market reaction' indicator: News alone isolates the filing effect, adding this isolates the tape's confirmation.",
           entry=lambda df, p: (df["news_move_pct"] >= p["min_move_pct"]) &
                 (df["news_rvol"] if p["volume_mode"] == "post_news" else df["rvol"]).ge(p["min_rvol"])),
    Plugin("cvd_divergence", "entry", "CVD divergence (bullish)",
           "A bullish price/CVD divergence, as the chart's CVD indicator draws it: two swing lows of the same session where price and CVD disagree (regular: price lower low, CVD higher low; hidden: price higher low, CVD lower low). A swing low is only known after 'Pivot' candles each side, so the signal comes 'Pivot' candles after the second low closes: the decision bar must close within 'Within' minutes of that. With the pattern trigger off, this is when to buy. 'Near delta setup' also needs a sellers-trapped delta setup within 3 candles of the second low (the chart's '+ Δ' tag). CVD and delta are estimates from 1-minute bars, not trades.",
           (Param("timeframe", "Candles", "15m", "select", options=("15m", "5m")),
            Param("pivot", "Pivot (candles each side)", 3, "int", 1, 10, 1),
            Param("max_gap", "Swings at most (candles apart)", 30, "int", 2, 100, 1),
            Param("kinds", "Kinds", "all", "select", options=("all", "regular")),
            Param("with_delta", "Near delta setup", "off", "select", options=("off", "near")),
            Param("within_min", "Known within (min before decision)", 0, "int", 0, 120, 5)),
           history="Untested. Port of apps/web/lib/tv/cvd-divergence.ts.",
           entry=lambda df, p: df["cvd_div_ok"]),
    Plugin("delta_divergence", "entry", "Delta divergence (sellers trapped)",
           "The chart's delta-divergence setup, long side: a candle with a dominant LOWER wick and NEGATIVE footprint delta (sellers pushed into the low and were absorbed), confirmed by a later green candle with positive delta that closes above its high within 'Confirm within' candles. The signal is the confirming candle's close: the decision bar must close within 'Within' minutes of it.",
           (Param("timeframe", "Candles", "15m", "select", options=("15m", "5m")),
            Param("wick", "Wick ≥ % of range", 40.0, "float", 0, 100, 5),
            Param("dominance", "Wick ≥ × other wick", 2.0, "float", 1, 10, 0.5),
            Param("delta_pct", "|Delta| ≥ % of volume", 5.0, "float", 0, 100, 1),
            Param("rvol", "Volume ≥ × 20-candle mean", 1.0, "float", 0, 10, 0.25),
            Param("within", "Confirm within (candles)", 3, "int", 1, 10, 1),
            Param("within_min", "Known within (min before decision)", 0, "int", 0, 120, 5)),
           history="Untested. Port of apps/web/lib/tv/delta-divergence.ts.",
           entry=lambda df, p: df["delta_div_ok"]),
    Plugin("support_rule", "entry", "Support rule",
           "Only take trades whose structural support sits inside this distance below the fill; optionally a swing low, or anything but a round number.",
           (Param("min_pct", "Distance ≥ %", 0.3, "float", 0, 10, 0.1),
            Param("max_pct", "Distance ≤ %", 3.0, "float", 0.1, 10, 0.1),
            Param("kind", "Kind", "any", "select", options=("any", "swing", "not_round"))),
           entry=_support),
    Plugin("resistance_rule", "entry", "Resistance rule",
           "Only take trades with this much room to the structural resistance above the fill; optionally a swing high, or anything but a round number.",
           (Param("min_pct", "Headroom ≥ %", 1.0, "float", 0, 20, 0.1),
            Param("max_pct", "Headroom ≤ %", 10.0, "float", 0.1, 50, 0.1),
            Param("kind", "Kind", "any", "select", options=("any", "swing", "not_round"))),
           entry=_resistance),
)


# ------------------------------------------------------------------ exit plugins

def _set(**fields: Any) -> Callable[[ExitCfg, dict], None]:
    def apply(cfg: ExitCfg, p: dict) -> None:
        for k, v in fields.items():
            setattr(cfg, k, v)
    return apply


def _target_rr(cfg: ExitCfg, p: dict) -> None:
    cfg.target_mode, cfg.target_rr = "rr", float(p["rr"])
    cfg.stop_mult = float(p.get("stop_x", 1.0))


def _breakeven(cfg: ExitCfg, p: dict) -> None:
    cfg.breakeven, cfg.breakeven_r, cfg.breakeven_to = True, float(p["r"]), str(p["to"])


def _stale(cfg: ExitCfg, p: dict) -> None:
    cfg.stale_exit, cfg.stale_minutes, cfg.stale_r = True, int(p["minutes"]), float(p["r"])


def _tap(cfg: ExitCfg, p: dict) -> None:
    cfg.tap_touches = int(p["touches"])


EXIT: tuple[Plugin, ...] = (
    Plugin("tap_high_ceiling", "exit", "Tap-high ceiling",
           "Treat a price that 5-minute bars were rejected at repeatedly (highs within 0.15%, each closing back under, at least 2 bars apart) as resistance, even when the swing-pivot test missed it. If it is nearer than the base target, the target moves to 0.15% under it. Same rule as the engine's --tap-levels flag.",
           (Param("touches", "Taps needed", "3", "select", options=("2", "3")),),
           history="BT61 / 2026-10-08-tap-high-levels: LATENTVIEW 2024-03-28 had 487.20 x3 under a 494 spike; the pivot test saw none of it.",
           exit=_tap),
    Plugin("target_fixed_rr", "exit", "Fixed R target",
           "Replace the resistance target with a fixed multiple of the risk. Optionally scale the stop distance (R follows it).",
           (Param("rr", "Target at R", 2.0, "float", 0.5, 10, 0.25),
            Param("stop_x", "Stop distance × base (1 = unchanged)", 1.0, "float", 0.25, 5, 0.25)),
           exclusive="target",
           history="BT17 fixed 2R: 25% reach it.", exit=_target_rr),
    Plugin("checkpoint_stop", "exit", "Checkpoint stop",
           "Target stays at 2R; touching the resistance lifts the stop to 0.15% under it instead of selling.",
           exclusive="target", history="BT52: KILLED, then shipped live 2026-09-29.",
           exit=_set(target_mode="checkpoint")),
    Plugin("macd_fade_exit", "exit", "MACD fade",
           "Sell when the 5-minute MACD histogram rolls from positive to zero or below (once up half a risk).",
           exit=_set(macd_fade_exit=True)),
    Plugin("vwap_exit", "exit", "VWAP break",
           "Sell at the close of a 5-minute bar that closes under session VWAP (once up half a risk).",
           history="BT56 / 2026-10-03 VWAP hypothesis (US).", exit=_set(vwap_exit=True)),
    Plugin("volume_climax_exit", "exit", "Volume climax",
           "Sell a red 5-minute bar printed on 2.5× its recent average volume: sellers arrived in size (once up half a risk).",
           exit=_set(volume_climax_exit=True)),
    Plugin("resistance_reject_exit", "exit", "Resistance rejection",
           "Sell a red 5-minute bar that pushed into the target's resistance band and closed under it (once up half a risk).",
           exit=_set(resistance_reject_exit=True)),
    Plugin("swing_trail", "exit", "Swing-low trailing stop",
           "Ratchet the stop up under each confirmed 5-minute swing low (never down).",
           history="BT17 exit modes; removing trailing stops was exactly null (2026-09-20).",
           exit=_set(swing_trail=True)),
    Plugin("breakeven", "exit", "Breakeven stop",
           "After the price has run this many R, lift the stop (from the next bar) to entry or to cost-breakeven.",
           (Param("r", "After R", 1.0, "float", 0.25, 5, 0.25),
            Param("to", "Lift to", "entry", "select", options=("entry", "cost"))),
           history="BT46 breakeven at 1.5R (KILLED) / BT43 cost-aware stop (KILLED).", exit=_breakeven),
    Plugin("stale_exit", "exit", "Stale-trade exit",
           "Close at the market if, after N minutes, the trade has not reached the given R.",
           (Param("minutes", "After min", 45, "int", 5, 300, 5), Param("r", "Not reached R", 0.5, "float", 0.1, 3, 0.1)),
           exit=_stale),
)

# The pre-split combined indicator: not offered in the UI, but saved runs and their trades still resolve it.
LEGACY: tuple[Plugin, ...] = (
    Plugin("news_reaction", "entry", "News + market reaction",
           "Superseded by the separate News and Market reaction indicators; kept so saved runs still load and re-run.",
           (Param("lookback_h", "News within last (h)", 24, "float", 1, 72, 1),
            Param("min_move_pct", "Price up since news ≥ %", 2.0, "float", 0, 30, 0.5),
            Param("min_rvol", "RVOL ≥", 3.0, "float", 0, 50, 0.5),
            Param("scope", "Counts as news", "material", "select", options=("material", "material+unclear")),
            Param("volume_mode", "Volume confirmation", "session", "select", options=("session", "post_news")),
            Param("trigger", "Entry trigger", "pattern", "select", options=("pattern",))),
           entry=lambda df, p: (df["news_move_pct"] >= p["min_move_pct"]) &
                 (df["news_rvol"] if p["volume_mode"] == "post_news" else df["rvol"]).ge(p["min_rvol"])),
)

REGISTRY: dict[str, Plugin] = {p.id: p for p in (*ENTRY, *EXIT, *LEGACY)}
NEWS_IDS = ("news_reaction", "news", "market_reaction")      # indicators that read the filings cache


def news_params(selected: list[dict]) -> dict | None:
    """The stamping parameters for the filings cache, or None when no news indicator is selected.

    News owns the filing (lookback, scope); Market reaction owns the tape test (volume mode, RVOL floor).
    The legacy combined indicator carries all of them itself.
    """
    by_id = {x["id"]: x["params"] for x in selected}
    if "news_reaction" in by_id:
        return by_id["news_reaction"]
    if "news" not in by_id:
        return None
    r = by_id.get("market_reaction", {})
    return {**by_id["news"], "volume_mode": r.get("volume_mode", "session"), "min_rvol": r.get("min_rvol", 0.0)}


def catalog() -> list[dict[str, Any]]:
    return [p.describe() for p in (*ENTRY, *EXIT)]


class PluginError(ValueError):
    pass


def resolve_params(plugin: Plugin, given: dict | None) -> dict[str, Any]:
    """Defaults filled in, values coerced and clamped to the declared range."""
    given = given or {}
    if plugin.id in NEWS_IDS:
        unknown = set(given) - {p.key for p in plugin.params}
        if unknown:
            raise PluginError(f"{plugin.id}: unsupported parameter(s): " + ", ".join(sorted(unknown)))
    if "trigger" in {p.key for p in plugin.params} and plugin.id in NEWS_IDS:
        allowed = ("pattern", "news_bar") if plugin.id == "news" else ("pattern",)
        if given.get("trigger", "pattern") not in allowed:
            raise PluginError("News trigger '" + str(given["trigger"]) + "' has no implementation in this Lab. "
                              "Choose one of " + ", ".join(allowed) + " for a new experiment; the historical saved result is preserved.")
    out: dict[str, Any] = {}
    for p in plugin.params:
        v = given.get(p.key, p.default)
        try:
            if p.kind == "int":
                v = int(float(v))
            elif p.kind == "float":
                v = float(v)
            elif p.kind == "time":
                hhmm(v)
                v = str(v)
            elif p.kind == "select":
                v = str(v)
                if v not in p.options:
                    raise PluginError(f"{plugin.id}.{p.key} must be one of {p.options}")
        except (TypeError, ValueError) as exc:
            raise PluginError(f"{plugin.id}.{p.key}: bad value {v!r}") from exc
        if p.kind in ("int", "float"):
            if p.min is not None:
                v = max(p.min, v)
            if p.max is not None:
                v = min(p.max, v)
        out[p.key] = v
    if plugin.id in ("pullback_ordinal", "news_pullback"):
        if out["min_ordinal"] > out["max_ordinal"]:
            raise PluginError("Pullback from must not be above Pullback to")
        if plugin.id == "pullback_ordinal" and out["min_ordinal"] == 1:
            out.pop("min_ordinal")            # the default is dropped so a saved run keeps the key it always had
    if plugin.id == "news" and out.get("trigger") != "news_bar":
        out.pop("bar_window_min", None)       # both are meaningless for the pattern trigger; dropping them keeps every saved config key as it was
        out.pop("bar_min_move_pct", None)
    return out


def normalise(selected: list[dict]) -> list[dict]:
    """Validate a UI selection: known ids, no duplicates, no exclusive clashes."""
    seen: dict[str, str] = {}
    out: list[dict] = []
    ids: set[str] = set()
    for item in selected:
        plugin = REGISTRY.get(item.get("id", ""))
        if plugin is None:
            raise PluginError(f"unknown indicator {item.get('id')!r}")
        if plugin.id in ids:
            raise PluginError(f"{plugin.id} selected twice")
        ids.add(plugin.id)
        if plugin.exclusive:
            if plugin.exclusive in seen:
                raise PluginError(f"{plugin.name} and {REGISTRY[seen[plugin.exclusive]].name} "
                                  "cannot be combined")
            seen[plugin.exclusive] = plugin.id
        out.append({"id": plugin.id, "params": resolve_params(plugin, item.get("params"))})
    if "market_reaction" in ids and not ids & {"news", "news_reaction"}:
        raise PluginError("Market reaction measures the move since a filing: also tick News.")
    if "news_pullback" in ids and not ids & {"news", "news_reaction"}:
        raise PluginError("News pullback number counts from the filing: also tick News.")
    if "news" in ids and "news_reaction" in ids:
        raise PluginError("News + market reaction (saved) cannot be combined with News")
    return sorted(out, key=lambda x: x["id"])


def entry_mask(df: pd.DataFrame, selected: list[dict]) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for item in selected:
        plugin = REGISTRY[item["id"]]
        if plugin.entry is not None:
            mask &= plugin.entry(df, item["params"]).fillna(False).astype(bool)
    return mask


def exit_cfg(selected: list[dict]) -> ExitCfg:
    cfg = ExitCfg()
    for item in selected:
        plugin = REGISTRY[item["id"]]
        if plugin.exit is not None:
            plugin.exit(cfg, item["params"])
    return cfg
