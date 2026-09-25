"""BT49 report — the US watchlist audit drawn setup by setup (BT32 format).

Reads the JSON written by `bt49_us_watchlist_audit.py` and writes ONE
self-contained HTML file: a per-stock verdict table, then one card per
guide-shaped setup with all 14 entry rules shown pass/fail, the price, MACD and
volume panels (levels as of the setup candle, the engine's warmed 1-minute
MACD), and what the trade would have done with every rule waived, at the real
US cost model.

Example (repository root):
  apps/signal-engine/.venv/bin/python research/backtests/bt49_us_watchlist_report.py \
      --input research/backtests/bt49_us_watchlist_2026-09-24.json
"""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = Path(__file__).resolve().parent / "bt49_assets"
RR = 2.0


def _money(x: float) -> str:
    return ("−$" if x < 0 else "$") + f"{abs(x):,.2f}"


def chosen_outcome(s: dict) -> tuple[dict, bool]:
    """The rules-waived outcome: the fill-time cost gate is lifted too when it
    was the thing that refused the fill."""
    nc = s.get("outcome_no_cost_gate")
    if nc is not None and nc.get("status") == "traded":
        return nc, True
    return s["outcome"], False


def card(stock: dict, s: dict, labels: dict[str, str]) -> dict:
    o, lifted = chosen_outcome(s)
    d = s["detail"]
    det = {
        "day_chg": f'{d["day_chg_pct"]}% on the day', "rvol": f'RVOL {d["rvol"]}',
        "trend_5m": f'5m context: {d["context"]}', "above_vwap": f'5m context: {d["context"]}',
        "strong_close": f'close at {d["close_pos"]} of the range (needs 0.60)',
        "volume": f'{d["vol_ratio"]}× recent volume (needs 2.5×)',
        "macd_pos": f'hist {d["macd_hist"]}', "macd_open": f'hist {d["macd_hist"]}',
        "headroom": str(d["headroom"]), "ordinal": f'pullback #{d["ordinal"]} ({d["ordinal_reason"]})',
        "cost": str(d["cost"]),
    }
    rec = {
        "symbol": stock["symbol"], "time": s["time"], "trigger": s["trigger"], "stop": s["stop"],
        "target": round(s["trigger"] + RR * (s["trigger"] - s["stop"]), 4),
        "risk_pct": s["risk_pct"], "pause": s["pause"], "levels": s["levels"],
        "gates": [[k, labels[k], bool(s["gates"][k]), det.get(k, "")] for k in labels],
        "failed_n": len(s["failed"]), "watched_live": bool(s["watched_live"]),
        "day_chg_pct": d["day_chg_pct"], "rvol": d["rvol"], "vol_ratio": d["vol_ratio"],
        "status": o["status"], "why": o.get("why", ""), "cost_lifted": lifted,
    }
    if o["status"] == "traded":
        rec.update({k: o[k] for k in ("entry", "exit", "exit_reason", "qty", "gross_usd",
                                      "costs_usd", "net_usd", "r_multiple", "mfe_r")})
        rec["target"] = o["target"]
        rec["entry_time"] = o["entry_time"][:5]
        rec["exit_time"] = o["exit_time"][:5]
    else:
        rec["net_usd"] = None
    return rec


def verdict(stock: dict, cards: list[dict], labels: dict[str, str]) -> dict:
    if "error" in stock:
        return {"symbol": stock["symbol"], "first": stock["first_passed_at"], "nodata": True,
                "move": "–", "setups": 0, "closest": "–", "waived": "–",
                "text": "Yahoo serves no bars for this symbol (a warrant). The engine could never "
                        "enter it — it should not have passed the screen."}
    d = stock["day"]
    fails = Counter(k for c in cards for k, _, ok, _ in c["gates"] if not ok)
    top = ", ".join(f"{labels[k]} ({n}/{len(cards)})" for k, n in fails.most_common(3))
    filled = [c for c in cards if c["status"] == "traded"]
    net = sum(c["net_usd"] for c in filled)
    prev = stock["prev_close"]
    move = (f'{100 * (d["high"] / prev - 1):+.0f}% at {d["hod_time"]} → '
            f'close {100 * (d["close"] / prev - 1):+.0f}%')
    watch = d.get("px_at_watch")
    note = ""
    if watch and d["high"] and stock["first_passed_at"] > d["hod_time"]:
        note = (f' The day\'s high came at {d["hod_time"]}, before it reached the live watchlist '
                f'at {stock["first_passed_at"]} ({100 * (watch / d["high"] - 1):+.0f}% off the high by then).')
    text = (f"{len(cards)} guide-shaped setups; none passed. Most-failed rules: {top}." + note
            if cards else "No guide-shaped setup all day.")
    return {"symbol": stock["symbol"], "first": stock["first_passed_at"], "nodata": False,
            "move": move, "setups": len(cards),
            "closest": min((c["failed_n"] for c in cards), default="–"),
            "waived": f"{len(filled)} fills, {_money(net)}" if cards else "–",
            "waived_net": net, "text": text}


def build_html(data: dict, cards: list[dict], stocks: list[dict]) -> str:
    css = (ASSETS / "report.css").read_text()
    js = (ASSETS / "report.js").read_text()
    s = data["summary"]
    aw = s["all_waived"]

    def chip(label: str, value: str, tone: str = "") -> str:
        return f'<span class="chip {tone}"><b>{html.escape(value)}</b> {html.escape(label)}</span>'

    tone = lambda x: "pos" if x > 0 else "neg" if x < 0 else ""  # noqa: E731
    chips = "".join([
        chip("stocks watchlisted", str(s["stocks"])),
        chip("with no data (warrants)", str(s["stocks"] - s["with_data"])),
        chip("guide-shaped setups", str(s["setups"])),
        chip("passed every rule", str(s["passed_all"])),
        chip("fewest rules failed by any setup", str(s["min_failed"])),
        chip("fills if every rule waived", str(aw["fills"])),
        chip("gross, before costs", _money(aw["gross_usd"]), tone(aw["gross_usd"])),
        chip("real costs", _money(aw["costs_usd"])),
        chip("net if waived", _money(aw["net_usd"]), tone(aw["net_usd"])),
        chip("winners", f'{aw["wins"]}/{aw["fills"]}'),
        chip("median net / trade", _money(aw["median_net_usd"]), tone(aw["median_net_usd"])),
    ])
    rows = "".join(
        f'<tr data-sym="{html.escape(v["symbol"])}"{" class=nodata" if v["nodata"] else ""}>'
        f'<td><b>{html.escape(v["symbol"])}</b></td><td>{v["first"]}</td><td>{html.escape(v["move"])}</td>'
        f'<td class="num">{v["setups"]}</td><td class="num">{v["closest"]}</td>'
        f'<td class="num {tone(v.get("waived_net", 0))}">{html.escape(v["waived"])}</td>'
        f'<td class="v">{html.escape(v["text"])}</td></tr>'
        for v in stocks)
    wl = data["watchlist"]
    sub = (
        f'Every stock the US arm put on its watchlist on <b>{data["date"]}</b> '
        f'({wl["passed"]} passed of {wl["considered"]} screened), checked from the outside. '
        "A <b>setup</b> is every distinct micro pullback the engine's own <code>micro_pullback</code> "
        "finds in the day's 1-minute bars (2+ green candles, a 1–2 candle red/doji pause, a candle "
        "breaking the pause high) with none of the extra rules applied, 09:30–15:10 ET. Each card "
        "shows all 14 entry rules judged <b>independently</b> with the engine's own functions — "
        "the live log stops at the first failure — on bars up to that candle only. "
        "The outcome line is what the trade would have done with <b>every rule waived</b>: armed as "
        "the live session arms (decision at the candle's close, 3-minute expiry, 1% chase cap), then "
        "managed by <code>engine.step</code> with its real exits, $50 risk / $5k cap, and the real US "
        "cost model (IBKR per-share fees + half-spread each side). One cost model is shown, not two: "
        "the US model already carries spread and impact, so there is no separate stress figure. "
        "The service was not running 10:00–11:42 ET, so setups before a stock's live-watchlist time are "
        "marked. <b>Descriptive audit of one session — not a test, and not grounds on its own to "
        "change a rule.</b>"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BT49 — US watchlist audit {data["date"]}</title><style>{css}</style></head><body>
<header>
  <h1>BT49 — was every watchlisted US stock really untradeable? ({data["date"]})</h1>
  <p>{sub}</p>
</header>
<div class="stats">{chips}</div>
<div class="wrap">
  <div id="charts">
    <div class="panel" id="stocks">
      <h3>Every watchlisted stock — click a row to show only its setups</h3>
      <div class="scroll" style="max-height:none"><table><thead><tr>
        <th>symbol</th><th>on live list</th><th>day: high → close</th><th class="num">setups</th>
        <th class="num">fewest rules failed</th><th class="num">if every rule waived</th><th>verdict</th>
      </tr></thead><tbody>{rows}</tbody></table></div>
    </div>
    <div id="charts-start"></div>
  </div>
  <div class="side">
    <div class="panel">
      <h3>Chart</h3>
      <div class="filters">
        <label>Timeframe <select id="f-tf">
          <option value="1m">1-minute (where the entry rules run; engine MACD)</option>
          <option value="5m">5-minute (MACD recomputed in browser, unwarmed)</option>
        </select></label>
      </div>
      <div class="legend" id="legend"></div>
    </div>
    <div class="panel">
      <h3>Filters</h3>
      <div class="filters">
        <label>Symbol <select id="f-sym"><option value="">all</option></select></label>
        <label>Watched <select id="f-live"><option value="">all</option>
          <option value="live">service was watching</option>
          <option value="pre">before live watchlist</option></select></label>
        <label>Outcome <select id="f-out"><option value="">all</option>
          <option value="close">≤3 rules failed</option>
          <option value="filled">would fill</option><option value="win">would win</option>
          <option value="loss">would lose</option><option value="nofill">no fill</option></select></label>
      </div>
      <p class="hint"><span id="n-shown"></span> · click a row to jump to its chart.</p>
      <div class="scroll"><table id="det"><thead><tr>
        <th data-k="symbol">symbol</th><th data-k="time">time</th><th data-k="watched_live">live</th>
        <th data-k="failed_n" class="num">failed</th><th data-k="exit_reason">if waived</th>
        <th data-k="net_usd" class="num">net $</th>
      </tr></thead><tbody></tbody></table></div>
    </div>
  </div>
</div>
<script>var DATA = {json.dumps(data["payload"])};</script>
<script>{js}</script>
</body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="research/backtests/bt49_us_watchlist_2026-09-24.json")
    ap.add_argument("--output", default=None)
    a = ap.parse_args()
    src = Path(a.input) if Path(a.input).is_absolute() else ROOT / a.input
    data = json.loads(src.read_text())
    labels = data["summary"]["gate_labels"]
    cards, stocks = [], []
    bars, eng, att, first = {}, {}, {}, {}
    for st in data["stocks"]:
        cs = [card(st, s, labels) for s in st["setups"]]
        cards.extend(cs)
        stocks.append(verdict(st, cs, labels))
        if st.get("bars"):
            bars[st["symbol"]] = st["bars"]
            eng[st["symbol"]] = st["eng"]
            att[st["symbol"]] = st.get("attention_replay", [])
            first[st["symbol"]] = st["first_passed_at"]
    data["payload"] = {"cards": cards, "bars": bars, "eng": eng, "att": att, "first": first,
                       "weak_strength": 0.0}
    out = (Path(a.output) if a.output else src.with_name(src.stem + "_report.html"))
    out.write_text(build_html(data, cards, stocks))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB, {len(cards)} setup cards, "
          f"{len(stocks)} stocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
