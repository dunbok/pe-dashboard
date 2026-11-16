"""Fetch and cache GAAP diluted EPS facts from SEC EDGAR companyfacts.

Cache: data/eps_edgar/{TICKER}.json holds only the EPS facts we use (the raw
companyfacts file is several MB). A ticker is re-fetched when the submissions
feed shows a periodic filing newer than the cache.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone

import requests

from .config import DATA, EPS_EDGAR_DIR, sec_user_agent

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TICKER_MAP_CACHE = DATA / "sec_company_tickers.json"

# Diluted EPS concepts, highest priority first. Companies sometimes switch
# concept over the years, so all are merged by period.
EPS_CONCEPTS = [
    ("us-gaap", "EarningsPerShareDiluted"),
    ("us-gaap", "EarningsPerShareBasicAndDiluted"),
    ("ifrs-full", "DilutedEarningsLossPerShare"),
    ("ifrs-full", "BasicAndDilutedEarningsLossPerShare"),
]
PERIODIC_FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A", "40-F", "6-K", "8-K"}
FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "6-K", "6-K/A"}

SCHEMA = 2   # bump to force a re-fetch of every cached ticker

_last_request = 0.0


def _get(url: str) -> requests.Response:
    """GET with SEC User-Agent, <=8 req/s, retries on 429/5xx."""
    global _last_request
    headers = {"User-Agent": sec_user_agent(), "Accept-Encoding": "gzip, deflate"}
    for attempt in range(5):
        wait = 0.125 - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code == 404:
            return r
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()
    return r


def ticker_to_cik() -> dict[str, int]:
    """Yahoo-style ticker (BRK-B) -> CIK. Cached for a day."""
    if TICKER_MAP_CACHE.exists() and time.time() - TICKER_MAP_CACHE.stat().st_mtime < 86400:
        raw = json.loads(TICKER_MAP_CACHE.read_text(encoding="utf-8"))
    else:
        raw = _get(TICKERS_URL).json()
        TICKER_MAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TICKER_MAP_CACHE.write_text(json.dumps(raw), encoding="utf-8")
    return {v["ticker"].upper(): int(v["cik_str"]) for v in raw.values()}


def latest_periodic_filing(cik: int) -> str | None:
    """Filing date (YYYY-MM-DD) of the newest periodic report."""
    r = _get(SUBMISSIONS_URL.format(cik=cik))
    if r.status_code == 404:
        return None
    recent = r.json().get("filings", {}).get("recent", {})
    dates = [d for f, d in zip(recent.get("form", []), recent.get("filingDate", [])) if f in PERIODIC_FORMS]
    return max(dates) if dates else None


def _extract(facts_json: dict) -> dict:
    """Pick EPS facts, choosing the unit (currency) with the most history."""
    facts = facts_json.get("facts", {})
    by_unit: dict[str, list[dict]] = {}
    for prio, (tax, concept) in enumerate(EPS_CONCEPTS):
        body = facts.get(tax, {}).get(concept)
        if not body:
            continue
        for unit, rows in body.get("units", {}).items():
            if not unit.endswith("/shares"):
                continue
            for r in rows:
                if not r.get("start") or not r.get("end") or r.get("val") is None:
                    continue
                by_unit.setdefault(unit, []).append({
                    "start": r["start"], "end": r["end"], "val": float(r["val"]),
                    "filed": r["filed"], "form": r.get("form", ""), "prio": prio,
                })
    if not by_unit:
        return {"schema": SCHEMA, "unit": None, "facts": [], "foreign": False}
    unit = max(by_unit, key=lambda u: len(by_unit[u]))
    rows = by_unit[unit]
    # Keep the first-filed value per period (point-in-time; avoids restatement
    # look-ahead). Ties on date -> higher-priority concept. Also remember the
    # latest-filed value ("alt_*"): the SEC data has tagging typos (e.g. BE's
    # Q3-2025 10-Q says EPS -380 instead of -0.38) that later filings correct,
    # and trailing.adjust_for_splits uses the alt when the first value is absurd.
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in sorted(rows, key=lambda r: (r["filed"], r["prio"])):
        groups.setdefault((r["start"], r["end"]), []).append(r)
    foreign = any(r["form"] in FOREIGN_FORMS for r in rows)
    out = []
    for g in groups.values():
        first, last = dict(g[0]), g[-1]
        if last["filed"] != first["filed"] and last["val"] != first["val"]:
            first["alt_val"], first["alt_filed"] = last["val"], last["filed"]
        first.pop("prio", None)
        out.append(first)
    out.sort(key=lambda r: (r["end"], r["start"]))
    return {"schema": SCHEMA, "unit": unit, "currency": unit.split("/")[0], "facts": out, "foreign": foreign}


def cached(ticker: str) -> dict | None:
    """Cached EPS facts on disk, no network (fallback when EDGAR is unreachable)."""
    path = EPS_EDGAR_DIR / f"{ticker.upper()}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def get_eps(ticker: str, cik_map: dict[str, int] | None = None, force: bool = False) -> dict | None:
    """Cached EPS facts for a ticker, refreshing if EDGAR has a newer filing.

    Returns None if the ticker has no SEC CIK (e.g. Tokyo listings).
    """
    cik_map = cik_map or ticker_to_cik()
    cik = cik_map.get(ticker.upper())
    if cik is None:
        return None
    path = EPS_EDGAR_DIR / f"{ticker.upper()}.json"
    cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    today = date.today().isoformat()

    if cached and cached.get("schema") != SCHEMA:
        force = True   # cache written by an older version of the extractor
    if cached and not force:
        if cached.get("checked") == today:
            return cached
        latest = latest_periodic_filing(cik)
        if latest is None or latest <= cached.get("latest_filing", ""):
            cached["checked"] = today
            path.write_text(json.dumps(cached, indent=1), encoding="utf-8")
            return cached

    r = _get(FACTS_URL.format(cik=cik))
    data = {"facts": []} if r.status_code == 404 else _extract(r.json())
    data.update({
        "ticker": ticker.upper(),
        "cik": cik,
        "latest_filing": max((f["filed"] for f in data["facts"]), default=""),
        "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checked": today,
    })
    if r.status_code != 404:
        latest = latest_periodic_filing(cik)
        if latest:
            data["latest_filing"] = max(data["latest_filing"], latest)
    EPS_EDGAR_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data
