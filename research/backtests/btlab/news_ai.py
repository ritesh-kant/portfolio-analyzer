"""Classify a run's NSE filings with a local Ollama model, instead of the keyword rules.

Only the filings that fall in some trade's 24h window AND that the keyword rules call "unclear"
are classified (a few hundred for a 5-year run, not the whole cache). Each filing is judged on NSE's own one-line description
(subject + body text) and nothing else: no price, no company size, no later events, and the model
is asked for the TYPE of filing, never a price view, so it cannot leak what the stock did next.

Labels are cached per (filing, model, prompt version) under `.cache_nse_news/_ai/`, so re-running a
model, or a second run that shares filings, costs nothing. The keyword tier is kept next to the AI
tier on every filing (`kw` vs `k`), and the run reports how often they disagree, so the classifier
is auditable rather than trusted.

Ollama must be running (`ollama serve`). Cloud-hosted models (`:cloud`, `-cloud`) send the filing
text to ollama.com; they are listed but marked, and the default is a local one.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from datetime import datetime, timezone

import httpx
import pandas as pd

from . import news, service, store
from .paths import RUNS_DIR

OLLAMA = "http://localhost:11434"
PROMPT_VERSION = "p1"
CATEGORIES = ["results", "order_or_contract", "acquisition_merger_divestment", "fundraising_or_buyback",
              "dividend", "rating_change", "regulatory_or_legal_action", "management_change",
              "call_presentation_or_media", "routine_compliance", "other"]
MATERIAL_CATS = {"results", "order_or_contract", "acquisition_merger_divestment", "fundraising_or_buyback",
                 "dividend", "rating_change", "regulatory_or_legal_action"}
UNCLEAR_CATS = {"management_change", "other"}

SYSTEM = (
    "You label one corporate filing that an Indian listed company made to the stock exchange. "
    "Decide what TYPE of filing it is, using only the text given. Do not guess how the share price moved.\n"
    "Categories: results (quarterly/annual financial results or performance), order_or_contract (new orders, "
    "contracts, awards, agreements), acquisition_merger_divestment, fundraising_or_buyback (QIP, rights issue, "
    "preferential allotment, debt raise, buyback), dividend (declaration), rating_change (credit rating action), "
    "regulatory_or_legal_action (penalty, court or regulator order, fraud, search/raid), management_change "
    "(CEO/CFO/director/auditor change), call_presentation_or_media (analyst call schedule, transcript, investor "
    "presentation, newspaper copy, plain press release with no event), routine_compliance (trading window, share "
    "certificate loss, shareholding, AGM/postal ballot notice, certificates, ESOP allotment, record date), other.\n"
    "material = true only if the filing itself announces something that could move the share price: results, a new "
    "order/contract with an amount, an acquisition/merger/demerger, a fund raise or buyback, a dividend declaration, "
    "a rating change, or a significant regulatory/legal action. Calls, presentations, schedules and compliance "
    "paperwork are never material."
)
SCHEMA = {"type": "object", "properties": {"category": {"type": "string", "enum": CATEGORIES},
                                           "material": {"type": "boolean"}},
          "required": ["category", "material"]}

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


# ------------------------------------------------------------------ ollama

def models() -> dict:
    """{running, models: [{name, gb, cloud}]} from the local Ollama; embedding models are left out."""
    try:
        r = httpx.get(f"{OLLAMA}/api/tags", timeout=3)
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return {"running": False, "models": [], "error": repr(e)[:120]}
    out = []
    for m in r.json().get("models", []):
        caps = m.get("capabilities") or []
        name = m["name"]
        if "embedding" in caps or "embed" in name:
            continue
        cloud = bool(m.get("remote_host")) or name.endswith(("cloud", ":cloud"))
        out.append({"name": name, "gb": round(m.get("size", 0) / 1e9, 1), "cloud": cloud,
                    "params": (m.get("details") or {}).get("parameter_size", "")})
    out.sort(key=lambda m: (m["cloud"], m["gb"]))
    return {"running": True, "models": out}


def _safe(model: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", model)


def _label_file(model: str):
    d = news.CACHE / "_ai"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_safe(model)}.{PROMPT_VERSION}.json"


def load_labels(model: str) -> dict:
    f = _label_file(model)
    try:
        return json.loads(f.read_text()) if f.exists() else {}
    except ValueError:
        return {}


def _user(symbol: str, x: dict) -> str:
    subj = x["h"].split(": ", 1)[1] if ": " in x["h"] else x["h"]
    return f"Company: {symbol}\nNSE subject: {subj}\nNSE description: {x.get('b') or '(none)'}"


def unload(model: str) -> None:
    """Free the model's RAM right away (Ollama otherwise keeps it loaded for minutes)."""
    try:
        httpx.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=10)
    except Exception:  # noqa: BLE001 - best effort
        pass


def classify_one(client: httpx.Client, model: str, symbol: str, x: dict) -> dict:
    body = {"model": model, "stream": False, "format": SCHEMA, "think": False, "keep_alive": "30s",
            "options": {"temperature": 0, "num_ctx": 2048},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": _user(symbol, x)}]}
    r = client.post(f"{OLLAMA}/api/chat", json=body)
    r.raise_for_status()
    j = json.loads(r.json()["message"]["content"])
    cat = j.get("category") if j.get("category") in CATEGORIES else "other"
    mat = bool(j.get("material")) and cat in MATERIAL_CATS      # a material flag on a non-event category is ignored
    k = "material" if mat else "unclear" if cat in UNCLEAR_CATS else "routine"
    return {"k": k, "c": cat}


# ------------------------------------------------------------------ job

def _windows_filings(tr: pd.DataFrame, data: dict) -> dict[str, tuple[str, dict]]:
    """key -> (symbol, filing) for every filing inside some trade's 24h window that the keyword rules could not place.

    Only the "unclear" tier ("Press Release", "Updates", "General Updates", management changes…) is sent to the
    model: those subjects say nothing about the content. Subjects the keywords place with confidence (results,
    dividend, share-certificate notices, trading window…) keep their keyword tier, which also keeps the job short."""
    from datetime import date, timedelta, timezone as tz
    out: dict[str, tuple[str, dict]] = {}
    for _, r in tr.iterrows():
        sym = str(r["symbol"])
        info = data.get(sym)
        if not info:
            continue
        t1 = news._entry_dt(r).astimezone(tz.utc)
        t0 = t1 - news.LOOKBACK
        for x in info["rows"]:
            if x["t"] and t0 <= datetime.fromisoformat(x["t"]) <= t1 and news.tier(x["h"]) == "unclear":
                out[news.filing_key(sym, x)] = (sym, x)
    return out


async def _work(run_id: str, model: str, job: dict) -> None:
    tr = service.ensure_trades(run_id)
    # 1. make sure the filing text is cached for this run's stocks (one NSE request per symbol that lacks it)
    job["phase"] = "fetching filing text from NSE"
    fetch_job: dict = {}
    res = await news._run(run_id, tr, fetch_job, want_text=True)
    job["errors"] = fetch_job.get("errors", [])
    job["nse_requests"] = fetch_job.get("requests", 0)
    if job.get("cancel"):
        return
    # 2. classify each filing in a trade window that has no label yet
    data = {s: news._load_sym(s) for s in tr["symbol"].astype(str).unique() if news._sym_file(s).exists()}
    todo = _windows_filings(tr, data)
    labels = load_labels(model)
    need = [(k, v) for k, v in todo.items() if k not in labels]
    job.update(phase=f"classifying with {model}", total=len(todo), done=len(todo) - len(need), t0=time.time())
    with httpx.Client(timeout=120) as client:
        for n, (k, (sym, x)) in enumerate(need):
            if job.get("cancel"):
                break
            try:
                labels[k] = classify_one(client, model, sym, x)
            except Exception as e:  # noqa: BLE001
                job.setdefault("ai_errors", []).append(repr(e)[:160])
                if len(job["ai_errors"]) > 20:
                    raise RuntimeError(f"Ollama keeps failing: {job['ai_errors'][-1]}") from e
            job["done"] += 1
            if n % 25 == 0:
                _label_file(model).write_text(json.dumps(labels))
    _label_file(model).write_text(json.dumps(labels))
    apply_labels(run_id, model)


def apply_labels(run_id: str, model: str | None) -> dict:
    """Re-score the run's news result with `model`'s cached labels (None = back to the keyword rules)."""
    tr = service.ensure_trades(run_id)
    data = {s: news._load_sym(s) for s in tr["symbol"].astype(str).unique() if news._sym_file(s).exists()}
    labels = load_labels(model) if model else None
    res = news._classify(run_id, tr, data, set(), labels)
    if news.news_path(run_id).exists():     # keep the earlier check's time and any 'not fetched' markers' meaning
        try:
            old = json.loads(news.news_path(run_id).read_text())
            res["checked_at"] = old.get("checked_at", res["checked_at"])
        except ValueError:
            pass
    items = [it for t in res["trades"] for it in t["items"]]
    labelled = [it for it in items if "ai" in it]
    changed = [it for it in labelled if it["k"] != it["kw"]]
    res["classifier"] = ({"model": model, "prompt": PROMPT_VERSION, "filings": len(labelled),
                          "changed": len(changed),
                          "to_material": sum(1 for it in changed if it["k"] == "material"),
                          "from_material": sum(1 for it in changed if it["kw"] == "material"),
                          "applied_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                         if model else None)
    news.news_path(run_id).write_text(json.dumps(res))
    return res


def start(run_id: str, model: str) -> dict:
    rec = store.get_run(run_id)
    if rec is None:
        return {"error": "unknown run"}
    if (rec["config"]["base"].get("market") or "NSE") != "NSE":
        return {"error": "news check is NSE-only"}
    if not any(m["name"] == model for m in models()["models"]):
        return {"error": f"model {model!r} is not available in Ollama (is `ollama serve` running?)"}
    with _lock:
        cur = _jobs.get(run_id)
        if cur and cur["state"] == "running":
            return status(run_id)
        job = {"state": "running", "model": model, "phase": "starting", "done": 0, "total": 0, "started": time.time()}
        _jobs[run_id] = job

    def work() -> None:
        try:
            asyncio.run(_work(run_id, model, job))
            job["state"] = "cancelled" if job.get("cancel") else "done"
        except Exception as e:  # noqa: BLE001
            job["state"] = "failed"
            job["error"] = repr(e)[:300]
        finally:
            unload(model)

    threading.Thread(target=work, daemon=True).start()
    return status(run_id)


def cancel(run_id: str) -> bool:
    j = _jobs.get(run_id)
    if j and j["state"] == "running":
        j["cancel"] = True
        return True
    return False


def status(run_id: str) -> dict:
    j = _jobs.get(run_id)
    return {"job": ({k: v for k, v in j.items() if k != "cancel"} if j else None)}


def audit(run_id: str, limit: int = 60) -> list[dict]:
    """Filings where the AI and the keyword rules disagree, one row each (deduplicated), for eyeballing."""
    r = news.load_result(run_id)
    if not r or not r.get("classifier"):
        return []
    seen, out = set(), []
    for t in r["trades"]:
        for it in t["items"]:
            key = (t["symbol"], it["t"], it["h"])
            if "ai" in it and it["k"] != it["kw"] and key not in seen:
                seen.add(key)
                out.append({"symbol": t["symbol"], "date": t["date"], "subject": it["h"].split(": ", 1)[-1],
                            "text": it.get("b", ""), "keyword": it["kw"], "ai": it["k"], "category": it["ai"]})
    out.sort(key=lambda x: (x["ai"] != "material", x["keyword"] != "material"))
    return out[:limit]
