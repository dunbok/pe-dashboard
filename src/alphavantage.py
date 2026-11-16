"""Alpha Vantage EARNINGS: quarterly adjusted ("Street") EPS history.

Free tier: 25 requests/day, 5/minute. One call returns a ticker's full history,
so each ticker is fetched once (backfill) and then only after it reports again.

Cache:  data/eps_av/{SYMBOL}.json
State:  data/av_backfill_state.json  {"calls": {"date", "count"}, "tickers": {SYM: {...}}}
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import requests

from .config import AV_STATE_FILE, EPS_AV_DIR, alphavantage_key

URL = "https://www.alphavantage.co/query"
DAILY_LIMIT = 25
MIN_SECONDS_BETWEEN_CALLS = 12.5   # 5/minute
STALE_DAYS = 100                   # refresh at least this often

_last_call = 0.0


class RateLimited(Exception):
    pass


def av_symbol(yahoo: str) -> str | None:
    """Alpha Vantage symbol, or None for non-US listings (not worth the budget)."""
    if "." in yahoo or "=" in yahoo or yahoo.startswith("^"):
        return None
    return yahoo.replace("-", ".")


def load_state() -> dict:
    if AV_STATE_FILE.exists():
        return json.loads(AV_STATE_FILE.read_text(encoding="utf-8"))
    return {"calls": {"date": "", "count": 0}, "tickers": {}}


def save_state(state: dict) -> None:
    AV_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    AV_STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


def calls_left(state: dict) -> int:
    today = date.today().isoformat()
    if state["calls"].get("date") != today:
        state["calls"] = {"date": today, "count": 0}
    return max(0, DAILY_LIMIT - state["calls"]["count"])


def load(yahoo: str) -> dict | None:
    path = EPS_AV_DIR / f"{yahoo}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch(yahoo: str, state: dict) -> dict:
    """One API call. Raises RateLimited if Alpha Vantage refuses."""
    global _last_call
    key = alphavantage_key()
    if not key:
        raise RuntimeError("ALPHAVANTAGE_API_KEY is not set")
    wait = MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()
    r = requests.get(URL, params={"function": "EARNINGS", "symbol": av_symbol(yahoo), "apikey": key}, timeout=60)
    state["calls"]["count"] += 1
    r.raise_for_status()
    d = r.json()
    if "Note" in d or "Information" in d:
        # Over the limit (message text varies). Don't count it as fetched.
        state["calls"]["count"] = DAILY_LIMIT
        raise RateLimited(d.get("Note") or d.get("Information"))

    quarters = []
    for q in d.get("quarterlyEarnings", []):
        eps = _num(q.get("reportedEPS"))
        if eps is None or not q.get("reportedDate"):
            continue
        quarters.append({
            "end": q["fiscalDateEnding"],
            "reported": q["reportedDate"],
            "time": q.get("reportTime", ""),
            "eps": eps,
            "est": _num(q.get("estimatedEPS")),
        })
    quarters.sort(key=lambda q: q["end"])
    out = {"symbol": yahoo, "fetched": date.today().isoformat(), "quarters": quarters}
    EPS_AV_DIR.mkdir(parents=True, exist_ok=True)
    (EPS_AV_DIR / f"{yahoo}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    state["tickers"][yahoo] = {
        "fetched": out["fetched"],
        "status": "ok" if quarters else "no_data",
        "last_reported": quarters[-1]["reported"] if quarters else None,
    }
    return out


def needs_refresh(yahoo: str, state: dict, edgar_latest: str | None, last_split: str | None) -> int | None:
    """Priority (lower = sooner) or None if the cache is fine."""
    s = state["tickers"].get(yahoo)
    if not s:
        return 0                                   # never fetched: backfill
    fetched = s["fetched"]
    if edgar_latest and edgar_latest > fetched:
        return 1                                   # filed with the SEC since our fetch
    if last_split and last_split > fetched:
        return 1                                   # split since fetch: AV history rescaled
    if fetched < (date.today() - timedelta(days=STALE_DAYS)).isoformat():
        return 2                                   # periodic safety refresh
    return None


def refresh(symbols: list[str], edgar_latest: dict[str, str | None],
            last_split: dict[str, str | None], log=print) -> dict:
    """Fetch as many due tickers as today's budget allows. Returns summary."""
    state = load_state()
    due = []
    for i, sym in enumerate(symbols):
        if av_symbol(sym) is None:
            continue
        p = needs_refresh(sym, state, edgar_latest.get(sym), last_split.get(sym))
        if p is not None:
            due.append((p, i, sym))
    due.sort()
    done, failed = [], []
    for _, _, sym in due:
        if calls_left(state) <= 0:
            break
        try:
            fetch(sym, state)
            done.append(sym)
            log(f"  AV {sym}: {state['tickers'][sym]['status']}")
        except RateLimited:
            # Don't log the message: Alpha Vantage echoes the API key in it.
            log("  AV daily limit reached; remaining tickers wait for the next run")
            break
        except Exception as e:  # network etc.: try again next run
            failed.append(sym)
            # requests errors embed the URL (with the API key), so log only the type.
            log(f"  AV {sym} failed: {type(e).__name__}")
        finally:
            save_state(state)
    pending = [s for _, _, s in due if s not in done]
    never = sum(1 for s in symbols if av_symbol(s) and s not in state["tickers"])
    return {"fetched": done, "failed": failed, "still_due": pending, "never_fetched": never,
            "calls_today": state["calls"]["count"]}
