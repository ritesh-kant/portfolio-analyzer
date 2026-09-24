"""Telegram message bodies for the momentum arms (HTML parse mode).

One module for both markets so an NSE and a US alert read the same way: the
country flag says which arm sent it, 🟢 is an entry, 💰/🔻 is a profitable or
losing exit, one fact per line. Everything user-supplied (symbols, reasons)
is HTML-escaped — a name like M&M makes Telegram reject the whole message.

Numbers never carry a bare `a/b` form: Telegram auto-links digit runs like
`50021/51820` as phone numbers.
"""
from __future__ import annotations

import html
from collections import Counter
from collections.abc import Iterable

import pandas as pd

from .engine import AttentionEvent, ClosedTrade, Position

NSE_TAG = "🇮🇳 <b>NSE</b>"
US_TAG = "🇺🇸 <b>US</b>"


def _esc(s: object) -> str:
    return html.escape(str(s))


def pnl_emoji(net: float) -> str:
    if net > 0:
        return "💰"
    if net < 0:
        return "🔻"
    return "⚪"


def money(amount: float, cur: str, *, signed: bool = False, decimals: int = 0) -> str:
    """`+₹1,234` / `-₹56` / `$12.30` — the sign sits before the currency."""
    sign = "-" if amount < 0 else "+" if signed and amount > 0 else ""
    return f"{sign}{cur}{abs(amount):,.{decimals}f}"


def _held(start: pd.Timestamp, end: pd.Timestamp) -> str:
    minutes = max(0, int((end - start).total_seconds() // 60))
    return f"{minutes}m" if minutes < 60 else f"{minutes // 60}h{minutes % 60:02d}m"


def entry_message(p: Position, *, fixed_exit: bool, cur: str = "₹", decimals: int = 2) -> str:
    """A new position, one fact per line.

    The target is always shown. Under a signal-based exit it is the plan's 2R
    price and nothing sells there, so the line says so rather than implying an
    order is resting at it.
    """
    plan = p.plan
    entry, stop, target = plan.entry, plan.stop, plan.target
    stop_pct = (stop - entry) / entry * 100.0
    tgt_pct = (target - entry) / entry * 100.0
    rr = (target - entry) / (entry - stop) if entry > stop else 0.0
    if fixed_exit:
        tgt_note = f"{rr:.1f}R, {tgt_pct:+.2f}% · {_esc(p.target_source)}"
    else:
        tgt_note = f"{rr:.1f}R, {tgt_pct:+.2f}% · <i>reference only — exit is signal-based</i>"
    risk_dp = 0 if cur == "₹" else 2
    return "\n".join([
        f"🟢 <b>ENTER {_esc(p.cand.symbol)}</b>",
        f"Setup: <code>{_esc(p.cand.setup.name)}</code>",
        f"Entry: <b>{money(entry, cur, decimals=decimals)}</b> × {plan.qty} "
        f"({money(plan.notional_inr, cur, decimals=risk_dp)})",
        f"Stop: <b>{money(stop, cur, decimals=decimals)}</b> "
        f"({stop_pct:+.2f}%, risk {money(plan.risk_inr, cur, decimals=risk_dp)})",
        f"Target: <b>{money(target, cur, decimals=decimals)}</b> ({tgt_note})",
        f"Catalyst: {'unknown' if p.cand.catalyst is None else p.cand.catalyst}",
    ])


def exit_message(t: ClosedTrade, *, cur: str = "₹", decimals: int = 2) -> str:
    """A closed position: the money first, then how it got there.

    R is the first tranche's price move over its initial stop distance — the
    unit the plan sized in, so -1.0R means "stopped out as planned".
    """
    net_dp = 0 if cur == "₹" else 2
    risk_per_share = t.entry - t.cand.setup.stop
    r = (t.exit - t.entry) / risk_per_share if risk_per_share > 0 else None
    move = f"{t.gross_pct:+.2f}%" + (f" · {r:+.1f}R" if r is not None else "")
    return "\n".join([
        f"{pnl_emoji(t.net_inr)} <b>EXIT {_esc(t.cand.symbol)}</b> "
        f"{money(t.net_inr, cur, signed=True, decimals=net_dp)}",
        f"Reason: <code>{_esc(t.exit_reason)}</code>",
        f"Price: {money(t.avg_entry, cur, decimals=decimals)} → "
        f"<b>{money(t.exit, cur, decimals=decimals)}</b> ({move})",
        f"Costs: {money(t.costs_inr, cur, decimals=net_dp)} · "
        f"held {_held(t.entry_time, t.exit_time)}",
    ])


def attention_message(a: AttentionEvent) -> str:
    """One line — attention is an observation, not an order.

    `reason` is e.g. ``pattern:doji:1m``; the candle tags usually repeat the
    pattern name, so they are shown only when they add something.
    """
    what = a.reason.removeprefix("pattern:").replace(":", " ")
    extra = [t for t in a.candle_tags if t not in what]
    tags = f" [{_esc(','.join(extra))}]" if extra else ""
    return (f"👀 <b>{_esc(a.symbol)}</b> {_esc(what)}{tags} · "
            f"{a.day_chg_pct:+.1f}% · RVOL {a.rvol:.1f}x")


def _shares(n: float) -> str:
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}K"
    return f"{n:.0f}"


def watchlist_message(
    symbol: str,
    *,
    price: float,
    day_chg_pct: float,
    rvol: float | None,
    float_shares: float | None,
    can_enter: bool = True,
    cur: str = "$",
) -> str:
    """A stock just passed the screen and is now watched — not an order.

    A criterion that did not run (no RVOL, no float) prints as `n/a`, never as
    a number it was not measured to be. `can_enter=False` is the no-history
    case: the engine watches it but refuses every entry without a profile.
    """
    rv = f"{rvol:.1f}x" if rvol is not None else "n/a"
    fl = _shares(float_shares) if float_shares is not None else "n/a"
    line = (f"📋 <b>{_esc(symbol)}</b> added to watchlist · "
            f"{money(price, cur, decimals=2)} · {day_chg_pct:+.1f}% · "
            f"RVOL {rv} · float {fl}")
    if not can_enter:
        line += "\n⚠️ no 1-minute history — watched, but the engine cannot enter it"
    return line


def top_reasons(reasons: Iterable[str], n: int = 3) -> str:
    counts = Counter(reasons)
    if not counts:
        return ""
    top = ", ".join(f"<code>{_esc(r)}</code> {c}" for r, c in counts.most_common(n))
    rest = sum(counts.values()) - sum(c for _, c in counts.most_common(n))
    return top + (f", other {rest}" if rest else "")


def eod_message(
    *,
    day: object,
    trades: list[ClosedTrade],
    attention: int,
    candidates: int,
    rejection_reasons: list[str],
    cur: str = "₹",
    extra: Iterable[str] = (),
) -> str:
    """The day in one message: result, funnel, why nothing (else) traded."""
    net = sum(t.net_inr for t in trades)
    wins = sum(1 for t in trades if t.net_inr > 0)
    net_dp = 0 if cur == "₹" else 2

    def pnl(x: float) -> str:
        return money(x, cur, signed=True, decimals=net_dp)

    lines = [
        f"🏁 <b>EOD {_esc(day)}</b>",
        f"{pnl_emoji(net) if trades else '⚪'} Trades: {len(trades)} "
        f"({wins}W/{len(trades) - wins}L) · net <b>{pnl(net)}</b>",
    ]
    if trades:
        by_setup = Counter(t.cand.setup.name for t in trades)
        lines.append("Setups: " + ", ".join(f"<code>{_esc(k)}</code> {v}"
                                            for k, v in by_setup.most_common()))
        best = max(trades, key=lambda t: t.net_inr)
        worst = min(trades, key=lambda t: t.net_inr)
        lines.append(f"Best {_esc(best.cand.symbol)} {pnl(best.net_inr)} · "
                     f"worst {_esc(worst.cand.symbol)} {pnl(worst.net_inr)}")
    lines.append(f"🔎 Funnel: {attention} attention → {candidates} candidates → "
                 f"{len(trades)} trades")
    if rejection_reasons:
        lines.append(f"🚫 Rejected {len(rejection_reasons):,}: {top_reasons(rejection_reasons)}")
    lines.extend(extra)
    return "\n".join(lines)
