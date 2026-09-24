"""Completed-candle volume evidence; the alternative is research-only.

This module describes the push/pause/breakout sequence. It does not enable an
entry rule or change the active strategy's four-bar slope requirement.
"""

from __future__ import annotations

import math

import pandas as pd

from .indicators import price_volume_slopes, validate_bars
from .setups import FLAG_VOL_RATIO, MICRO_GREEN_RUN, MICRO_PAUSE_MAX_BARS, micro_pullback


def volume_confirmation_evidence(
    bars: pd.DataFrame, tz: str = "Asia/Kolkata"
) -> dict[str, object]:
    """JSON-safe evidence using only the supplied completed one-minute bars.

    `tz` is the exchange's own timezone, because "the session" is one local
    trading date. It was hard-coded to IST, which is right for NSE and wrong
    for the US: a US session runs 19:00-01:30 IST, so it crosses midnight in
    India and the evidence would silently restart at 14:30 ET.
    """
    validate_bars(bars)
    evidence: dict[str, object] = {"version": 1, "pattern": "none", "pattern_pass": False}
    if bars.empty:
        return evidence
    local_index = bars.index.tz_convert(tz)
    session = bars[local_index.normalize() == local_index[-1].normalize()]
    window = session.tail(MICRO_GREEN_RUN + MICRO_PAUSE_MAX_BARS + 1)
    values = window[["open", "high", "low", "close", "volume"]].to_numpy()
    if not all(math.isfinite(float(value)) for value in values.flat):
        return {**evidence, "data_error": "nonfinite"}
    if (window["volume"] < 0).any():
        return {**evidence, "data_error": "negative_volume"}
    contiguous = bool((window.index.to_series().diff().dropna() == pd.Timedelta(minutes=1)).all())
    evidence["consecutive_1m"] = contiguous
    evidence["bars"] = [
        {"time": at.isoformat(), **{col: float(row[col]) for col in bars.columns
                                   if col in ("open", "high", "low", "close", "volume")}}
        for at, row in window.iterrows()
    ]
    slopes = price_volume_slopes(session, 4)
    if slopes is not None:
        evidence.update(price_slope_pct_per_bar=slopes[0] * 100.0,
                        volume_slope_per_bar=slopes[1],
                        slope_pass=bool(slopes[0] > 0 and slopes[1] > 0))
    if not contiguous:
        return evidence
    pull = micro_pullback(session, max_pause_bars=MICRO_PAUSE_MAX_BARS)
    if pull is None:
        return evidence
    pause_len = int(pull.meta["pause_bars"])
    pause = session.iloc[-1 - pause_len:-1]
    push = session.iloc[-1 - pause_len - MICRO_GREEN_RUN:-1 - pause_len]
    push_vol = float(push["volume"].mean())
    pause_vol = float(pause["volume"].mean())
    breakout_vol = float(session["volume"].iloc[-1])
    close_above = float(session["close"].iloc[-1]) > pull.trigger
    evidence.update(
        pattern="micro_pullback", pause_bars=pause_len,
        push_volume=push_vol, pause_volume=pause_vol, breakout_volume=breakout_vol,
        pause_high=pull.trigger, breakout_close_above_pause=close_above,
        pause_to_push_volume=pause_vol / push_vol if push_vol > 0 else None,
        breakout_to_pause_volume=breakout_vol / pause_vol if pause_vol > 0 else None,
        pattern_pass=bool(push_vol > 0 and pause_vol <= FLAG_VOL_RATIO * push_vol
                          and breakout_vol > pause_vol and close_above),
    )
    return evidence
