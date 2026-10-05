"""HTTP routes for the lab, mounted into the backtest dashboard (`bt_dashboard.py`).

  GET    /lab                          the page
  GET    /lab/static/<file>            its CSS/JS
  GET    /api/lab/meta                 plugin catalog, base rule, per-year build status, jobs
  POST   /api/lab/build                {years, base}  start building base data
  POST   /api/lab/apply                {years, base, patterns, plugins, ...}  run + save
  GET    /api/lab/runs                 every saved run (newest first)
  GET    /api/lab/runs.csv             the same, one row per run, for a spreadsheet
  GET    /api/lab/run/<id>             one run
  PATCH  /api/lab/run/<id>             {name, notes, starred}
  DELETE /api/lab/run/<id>
  GET    /lab/run/<id>                 the run's trades on candle charts (BT32 report)
  GET    /api/lab/run/<id>/trade/<i>   one trade's chart payload
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs

import pandas as pd

from . import base as base_mod
from . import plugins as P
from . import service, store
from .paths import BACKTESTS, STATIC

_detail_lock = threading.Lock()
_CT = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
       ".html": "text/html; charset=utf-8"}


def _bt32():
    sys.path.insert(0, str(BACKTESTS))
    import bt32_strategy_report as bt32
    return bt32


def _rule_from(body: dict) -> base_mod.BaseRule:
    cfg = service.parse_request({**body, "years": body.get("years") or [service.cached_years()[-1]]})
    return base_mod.BaseRule(**cfg["base"])


def describe_config(rec: dict) -> str:
    c = rec["config"]
    ind = [f'{P.REGISTRY[x["id"]].name}'
           + (" (" + ", ".join(f"{k}={v}" for k, v in x["params"].items()) + ")" if x["params"] else "")
           for x in c["plugins"]]
    b = c["base"]
    return (f"years {', '.join(map(str, c['years']))} · momentum {b['day_chg_min']:g}–{b['day_chg_max']:g}% "
            f"RVOL≥{b['rvol_min']:g} · {len(c['patterns'])} patterns · risk ₹{c['risk_inr']:g}/trade · "
            f"{c['max_trades'] or 'unlimited'}/day · indicators: " + ("; ".join(ind) if ind else "none (base trade)"))


def report_page(run_id: str) -> str | None:
    rec = store.get_run(run_id)
    if rec is None:
        return None
    bt32 = _bt32()
    tr = service.ensure_trades(run_id)
    if tr.empty:
        return None
    rows = [bt32.summary_fields(r) for _, r in tr.iterrows()]
    data = {"trades": rows, "summary": bt32.summarise(rows),
            "weak_strength": bt32.STRENGTH_WEAK_BELOW, "rules_version": bt32.PATTERN_RULES_VERSION,
            "lazy": True, "detail_url": f"/api/lab/run/{run_id}/trade/",
            "back": {"href": "/lab", "text": "← Back to the lab"}, "default_group": "exit_reason"}
    sub = (f"<b>{html.escape(rec['name'])}</b> · saved {rec['created_at']} · "
           f"{html.escape(describe_config(rec))}. Charts are built from the cached 1-minute bars "
           "when you scroll to them. The support/resistance drawn is recomputed as of the entry bar; "
           "the stop and target lines are the ones the lab used. Costs are shown twice "
           "(stressed, and realistic with the itemised MIS model).")
    return bt32.build_html(f"Lab run — {rec['name']}", sub, data)


def trade_detail(run_id: str, i: int) -> dict | None:
    rec = store.get_run(run_id)
    if rec is None:
        return None
    tr = service.ensure_trades(run_id)
    if not 0 <= i < len(tr):
        return None
    d = BACKTESTS / ".dashboard_cache" / f"lab-{run_id}"
    f = d / f"{i}.json"
    if f.exists():
        return json.loads(f.read_text())
    with _detail_lock:
        built = _bt32().build_detail(tr.iloc[i])
        if built is None:
            return {"_missing": True}
        d.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(built))
        return built


def runs_csv() -> str:
    cols = ["id", "created_at", "name", "years", "indicators", "trades", "win_pct", "mean_inr",
            "median_inr", "mean_ex_top5_inr", "net_real_inr", "net_real_pct_mean", "gross_pct_mean",
            "profit_factor", "max_drawdown_inr", "delta_mean_inr", "p_value", "trial_n", "notes"]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    for r in store.list_runs():
        m, c, vs = r["metrics"], r["config"], r.get("vs_base") or {}
        base_m = (store.get_run(r["base_id"]) or {}).get("metrics", {}) if r.get("base_id") else {}
        delta = round(m.get("mean_inr", 0) - base_m.get("mean_inr", 0), 2) if base_m else ""
        p = (vs.get("paired") or vs.get("kept_vs_removed") or {}).get("p", "")
        w.writerow([r["id"], r["created_at"], r["name"], "|".join(map(str, c["years"])),
                    "|".join(x["id"] for x in c["plugins"]), m.get("n", 0), m.get("win_pct", ""),
                    m.get("mean_inr", ""), m.get("median_inr", ""), m.get("mean_ex_top5_inr", ""),
                    m.get("net_real_inr", ""), m.get("net_real_pct_mean", ""), m.get("gross_pct_mean", ""),
                    m.get("profit_factor", ""), m.get("max_drawdown_inr", ""), delta, p,
                    r.get("trial_n", ""), r.get("notes", "")])
    return out.getvalue()


def _public(rec: dict) -> dict:
    return {**rec, "describe": describe_config(rec)}


def handle(h, method: str, path: str, query: str) -> bool:
    """Serve one request if it is the lab's. Returns False when it is not."""
    if not (path == "/lab" or path.startswith(("/lab/", "/api/lab/"))):
        return False
    q = parse_qs(query)

    def body() -> dict:
        n = int(h.headers.get("Content-Length") or 0)
        return json.loads(h.rfile.read(n) or b"{}") if n else {}

    try:
        if method == "GET":
            if path == "/lab":
                return h.send(200, (STATIC / "lab.html").read_bytes(), _CT[".html"]) or True
            m = re.fullmatch(r"/lab/static/([\w.\-]+)", path)
            if m and (STATIC / m.group(1)).is_file():
                f = STATIC / m.group(1)
                return h.send(200, f.read_bytes(), _CT.get(f.suffix, "application/octet-stream")) or True
            if path == "/api/lab/meta":
                return h.json(200, service.meta(base_mod.BaseRule())) or True
            if path == "/api/lab/jobs":
                return h.json(200, service.jobs()) or True
            if path == "/api/lab/runs":
                return h.json(200, [_public(r) for r in store.list_runs()]) or True
            if path == "/api/lab/runs.csv":
                return h.send(200, runs_csv().encode(), "text/csv; charset=utf-8") or True
            m = re.fullmatch(r"/api/lab/run/([\w\-]+)", path)
            if m:
                r = store.get_run(m.group(1))
                return (h.json(200, _public(r)) if r else h.json(404, {"error": "unknown run"})) or True
            m = re.fullmatch(r"/lab/run/([\w\-]+)", path)
            if m:
                page = report_page(m.group(1))
                return (h.send(200, page.encode(), _CT[".html"]) if page
                        else h.send(404, b"unknown run or no trades", "text/plain")) or True
            m = re.fullmatch(r"/api/lab/run/([\w\-]+)/trade/(\d+)", path)
            if m:
                d = trade_detail(m.group(1), int(m.group(2)))
                if d is None:
                    return h.json(404, {"error": "unknown trade"}) or True
                if d.get("_missing"):
                    return h.json(404, {"error": "no cached 1-minute bars for this symbol/day"}) or True
                return h.json(200, d) or True
        elif method == "POST":
            b = body()
            if path == "/api/lab/meta":      # meta for a non-default base rule
                return h.json(200, service.meta(_rule_from(b))) or True
            if path == "/api/lab/build":
                cfg = service.parse_request({**b, "plugins": []})
                return h.json(200, service.start_build(cfg["years"], base_mod.BaseRule(**cfg["base"]))) or True
            if path == "/api/lab/apply":
                try:
                    rec = service.apply(b, name=str(b.get("name") or ""), notes=str(b.get("notes") or ""))
                except service.NeedBuild as nb:
                    return h.json(409, {"need_build": nb.years}) or True
                return h.json(200, _public(rec) | {"base": _public(rec["base"]) if rec.get("base") else None}) or True
        elif method == "PATCH":
            m = re.fullmatch(r"/api/lab/run/([\w\-]+)", path)
            if m:
                b = body()
                r = store.update_run(m.group(1), **{k: b[k] for k in ("name", "notes", "starred") if k in b})
                return (h.json(200, _public(r)) if r else h.json(404, {"error": "unknown run"})) or True
        elif method == "DELETE":
            m = re.fullmatch(r"/api/lab/run/([\w\-]+)", path)
            if m:
                ok = store.delete_run(m.group(1))
                return h.json(200 if ok else 404, {"deleted": ok}) or True
        h.send(404, b"not found", "text/plain")
        return True
    except P.PluginError as exc:
        h.json(400, {"error": str(exc)})
        return True
    except (BrokenPipeError, ConnectionResetError):
        return True
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"lab error on {method} {path}: {exc!r}\n")
        import traceback
        traceback.print_exc()
        h.json(500, {"error": repr(exc)})
        return True
