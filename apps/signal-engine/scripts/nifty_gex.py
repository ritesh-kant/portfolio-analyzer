"""NIFTY gamma-exposure snapshot: walls, gamma flip (HVL), 1-day range, per-strike bars.

    cd apps/signal-engine
    .venv/bin/python scripts/nifty_gex.py                     # expiries ≤ 45 days + nearest
    .venv/bin/python scripts/nifty_gex.py --max-days 10 --convention dealer_short_all
    .venv/bin/python scripts/nifty_gex.py --json /tmp/gex.json

Reads UPSTOX_ANALYTICS_TOKEN (or UPSTOX_ACCESS_TOKEN) from the root .env. One API
call per expiry plus one for the expiry list; read-only. Prototype — the levels
are an estimate built on an assumed dealer position (see src/options_trader/gex.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings  # noqa: E402
from src.momentum_trader.upstox import UpstoxAuthError  # noqa: E402
from src.options_trader.gex import Convention, GexLevels, OptionRow, compute_levels  # noqa: E402
from src.options_trader.upstox_chain import IST, UpstoxChainClient  # noqa: E402

CRORE = 1e7


def _cr(x: float) -> str:
    return f"{x / CRORE:+,.0f} cr"


def _lv(x: float | None) -> str:
    return f"{x:,.0f}" if x is not None else "—"


def _summary(name: str, lv: GexLevels) -> list[tuple[str, str]]:
    lo_hi = lv.expected_move
    walls_c = ", ".join(f"C{i + 1} {s:,.0f}" for i, s in enumerate(lv.call_walls)) or "—"
    walls_p = ", ".join(f"P{i + 1} {s:,.0f}" for i, s in enumerate(lv.put_walls)) or "—"
    return [
        ("", name),
        ("Net GEX (₹ per 1% move)", _cr(lv.net_gex)),
        ("Regime", lv.regime),
        ("Gamma flip (HVL)", _lv(lv.flip)),
        ("Call walls", walls_c),
        ("Put walls", walls_p),
        ("1D range (min – max)", f"{lo_hi[0]:,.0f} – {lo_hi[1]:,.0f}" if lo_hi else "—"),
    ]


def _histogram(lv: GexLevels, band: float = 0.03, width: int = 36) -> list[str]:
    near = [s for s in lv.strikes if abs(s.strike - lv.spot) <= lv.spot * band]
    if not near:
        return []
    peak = max(abs(s.net) for s in near) or 1.0
    marks: dict[float, str] = {c: f"C{i + 1}" for i, c in enumerate(lv.call_walls)}
    marks.update({p: f"P{i + 1}" for i, p in enumerate(lv.put_walls)})
    if lv.flip is not None:
        marks[lv.flip] = "HVL"
    out = []
    for s in reversed(near):
        n = round(abs(s.net) / peak * width)
        bar = ("+" if s.net >= 0 else "-") * n
        tag = " ".join(v for k, v in marks.items() if abs(k - s.strike) < 25)
        here = " ◀ spot" if abs(s.strike - lv.spot) < 25 else ""
        out.append(f"{s.strike:>8,.0f} {_cr(s.net):>12}  {bar:<{width}} {tag}{here}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--max-days", type=int, default=45, help="include expiries up to N days out")
    ap.add_argument("--convention", default="dealer_long_calls",
                    choices=["dealer_long_calls", "dealer_short_all"])
    ap.add_argument("--walls", type=int, default=3)
    ap.add_argument("--json", type=Path, help="also write the snapshot here")
    args = ap.parse_args()
    convention: Convention = args.convention

    settings = Settings()
    token = settings.upstox_analytics_token or settings.upstox_access_token
    if not token:
        print("FAIL: no UPSTOX_ANALYTICS_TOKEN or UPSTOX_ACCESS_TOKEN configured", file=sys.stderr)
        return 3
    client = UpstoxChainClient(token)
    now = datetime.now(IST)
    try:
        expiries = [e for e in client.expiries() if 0 <= (e - now.date()).days <= args.max_days]
        spot, rows = 0.0, []
        per_expiry: dict[str, list[OptionRow]] = {}
        for e in expiries:
            spot, chunk = client.chain(e)
            per_expiry[e.isoformat()] = chunk
            rows.extend(chunk)
    except UpstoxAuthError:
        print("FAIL: Upstox rejected the token — it needs refreshing", file=sys.stderr)
        return 4
    if not rows:
        print("FAIL: empty option chain", file=sys.stderr)
        return 5

    nearest_key = min(per_expiry)
    all_lv = compute_levels(rows, spot, now, convention, args.walls)
    near_lv = compute_levels(per_expiry[nearest_key], spot, now, convention, args.walls)

    print(f"NIFTY 50  spot {spot:,.2f}   {now:%Y-%m-%d %H:%M} IST   convention {convention}")
    print(f"expiries: {', '.join(per_expiry)}\n")
    left = _summary(f"ALL ≤{args.max_days}d", all_lv)
    right = _summary(f"NEAREST {nearest_key}", near_lv)
    for (label, a), (_, b) in zip(left, right, strict=True):
        print(f"{label:<26}{a:<34}{b}")
    print("\nNet GEX by strike, all expiries, ±3% of spot (+ = damping, - = amplifying):")
    print("\n".join(_histogram(all_lv)))

    if args.json:
        def dump(lv: GexLevels) -> dict[str, object]:
            return {
                "net_gex": lv.net_gex, "regime": lv.regime, "flip": lv.flip,
                "call_walls": lv.call_walls, "put_walls": lv.put_walls,
                "expected_move": lv.expected_move,
                "strikes": [{"strike": s.strike, "call": s.call, "put": s.put} for s in lv.strikes],
            }
        args.json.write_text(json.dumps({
            "symbol": "NIFTY", "spot": spot, "at": now.isoformat(), "convention": convention,
            "expiries": list(per_expiry), "all": dump(all_lv), "nearest": dump(near_lv),
        }, indent=1))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
