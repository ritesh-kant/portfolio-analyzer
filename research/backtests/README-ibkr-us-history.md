# IBKR US 1-minute history

Why this exists: the US arm could only be replayed inside a ~30-day window.
`bt49_us_watchlist_audit.py` reads its session bars from Yahoo, which keeps
1-minute data for about a month, so a US session older than that could not be
audited at all (see `README-bt49-us-watchlist.md`). IB Gateway serves the same
regular-hours TRADES bars going back years, which is what this tool downloads.

One parquet per contract, indexed in America/New_York, no order methods on the
connection (`readonly=True`). The `ib-async` dependency is an optional extra of
the signal-engine app and is already installed in its venv.

## One-time: let the API in

IB Gateway → **Configure → Settings → API → Settings**:

- **Enable ActiveX and Socket Clients** — this is the switch that makes Gateway
  listen at all; without it nothing is bound and every connection is refused.
- **Socket port** — 4001 for a live login, 4002 for paper is IB's default, but
  Gateway shows *its* port here, and that is the one to pass as `--port`. (The
  `jts.ini` on this machine carries `LocalServerPort=4000`.)
- **Read-Only API** — on. The module asks for a read-only connection anyway.
- **Trusted IPs** — must contain `127.0.0.1`.

Sign in and leave Gateway running. `nc -z 127.0.0.1 <port>` is the quickest way
to confirm it is listening before starting a long download.

## The download

```bash
apps/signal-engine/.venv/bin/python research/backtests/ibkr_us_history.py \
  --symbols-file research/backtests/.cache_ibkr_us/us_watchlist_2026-09-24_to_10-02.txt \
  --start 2026-01-01 --end 2026-10-02 --port 4001

pnpm bt:ibkr --symbols AAPL,MSFT --start 2026-01-01 --end 2026-10-02 --port 4001
```

Bars land in `research/backtests/.cache_ibkr_us/1m/<SYMBOL>_<conId>_<start year>.parquet`
(GBs — the directory is gitignored) plus a `manifest.json` describing the run:
every contract with its bar count, first and last timestamp, pages fetched and
gaps, then `skipped` and `failed` if the batch had any. The year in the name is
the requested `--start` year, so ask for one year at a time.
Each weekly page is merged and written before the next request, so the run is
resumable: interrupt it and start it again, and it only fetches what is missing.
Re-running over a range that is already fully cached re-fetches it and dedupes
by timestamp — slower, never wrong.

## How long it takes

One request per symbol per week, because IBKR asks that a request return "only
a few thousand bars" and a week of 1-minute regular-session bars is ~1,950.
2026-01-01 → 2026-10-02 is ~39 pages per symbol:

| Pace | Per symbol | 90 symbols |
| --- | --- | --- |
| `--pause 1.0` (default) | ~1–2 min | ~1–2 hours |
| `--pause 10` | ~6–7 min | ~10 hours |

IBKR's documented pacing rule is written for bars of 30 seconds or less (60
requests per ten minutes); for 1-minute bars the hard limit is lifted, but IB
still load-balances and will throttle a client that hammers it. The default is
polite; `--pause 10` sits exactly inside the documented rule.

## What an empty reply means

IBKR answers both "nothing traded in this window" and "this request failed"
with an empty bar list. The reply is judged before the cursor moves, so a
failure can never be written down as a quiet week:

| Verdict | Reply | What the tool does |
| --- | --- | --- |
| gap | `162 … HMDS query returned no data` | steps back one page, prints it, records the range in the manifest |
| transient | `162 … Pacing violation`, or no reply before the timeout | retries (5 attempts, growing backoff, 10-minute cooldown on pacing) |
| fatal | not subscribed, no market data permission, invalid contract | stops the run, quoting IBKR |

Anything unrecognised is treated as transient and will stop the run if it
persists. Weeks are never silently stepped over, for the same reason the tool
never manufactures a missing or zero-volume minute.

A symbol IBKR will not resolve as one US stock — a unit (`AFJKU`), a warrant
(`GLNDW`), a typo — is recorded under `skipped` in the manifest and the batch
carries on with the rest; the watchlist snapshot in `.cache_ibkr_us/` contains
several of those.

## Code

| File | Role |
| --- | --- |
| `research/backtests/ibkr_us_history.py` | the downloader (this tool) |
| `research/backtests/.cache_ibkr_us/` | parquet cache + `manifest.json` (gitignored) |
| `research/backtests/README-bt49-us-watchlist.md` | the Yahoo-based US session audit this replaces for older sessions |