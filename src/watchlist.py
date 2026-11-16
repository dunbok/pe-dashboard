"""Step 1: scrape the public TradingView "Stocks" watchlist, map symbols to Yahoo,
and keep only common stocks / ADRs (Yahoo quoteType == "EQUITY").

Usage:
    python -m src.watchlist            # print the filtered ticker list
    python -m src.watchlist --all      # also show what was dropped and why
    python -m src.watchlist --refresh  # ignore the cached quote types
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_URL = "https://www.tradingview.com/watchlists/36671425/"
OVERRIDES_FILE = ROOT / "symbol_overrides.json"
QUOTE_TYPE_CACHE = ROOT / "data" / "quote_types.json"
QUOTE_TYPE_TTL_DAYS = 30

# TradingView exchange -> Yahoo suffix. US venues get no suffix.
EXCHANGE_SUFFIX = {
    "NASDAQ": "", "NYSE": "", "AMEX": "", "CBOE": "", "NYSEARCA": "", "BATS": "", "OTC": "",
    "TSE": ".T",        # Tokyo
    "EURONEXT": ".PA",  # default Paris; Amsterdam/Brussels names go in overrides
    "XETR": ".DE", "FWB": ".F",
    "LSE": ".L",
    "KRX": ".KS", "KOSDAQ": ".KQ",
    "HKEX": ".HK",
    "TWSE": ".TW", "TPEX": ".TWO",
    "TSX": ".TO", "TSXV": ".V",
    "ASX": ".AX",
    "SIX": ".SW",
    "MIL": ".MI",
    "BME": ".MC",
    "OMXSTO": ".ST", "OMXCOP": ".CO", "OMXHEX": ".HE",
}

# Exchanges that never hold common stock: dropped without asking Yahoo.
NON_EQUITY_EXCHANGES = {
    "CRYPTO", "BINANCE", "COINBASE", "BITSTAMP", "COMEX", "NYMEX", "CBOT", "CME", "ICEUS",
    "TVC", "ECONOMICS", "SP", "DJ", "OANDA", "FX", "FX_IDC", "FTSE", "FRED", "INDEX",
    "CAPITALCOM", "PEPPERSTONE", "FOREXCOM", "SAXO", "GETTEX", "FINRA",
}


@dataclass
class Symbol:
    tv: str                 # e.g. "NASDAQ:NVDA"
    section: str | None     # watchlist section header, e.g. "SECTORS"
    yahoo: str | None = None
    quote_type: str | None = None
    keep: bool = False
    reason: str = ""


def fetch_watchlist(url: str = WATCHLIST_URL) -> tuple[str, list[tuple[str, str | None]]]:
    """Return (list name, [(tv_symbol, section), ...]) in watchlist order."""
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    resp.raise_for_status()
    blocks = re.findall(
        r'<script type="application/prs\.init-data\+json">(.*?)</script>', resp.text, re.S
    )
    for block in blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        lst = (data.get("sharedWatchlist") or {}).get("list")
        if lst and "symbols" in lst:
            out, section = [], None
            for s in lst["symbols"]:
                if s.startswith("###"):
                    section = s[3:].strip()
                elif ":" in s:
                    out.append((s, section))
            if not out:
                raise RuntimeError("Watchlist JSON found but contains no symbols")
            return lst.get("name", ""), out
    raise RuntimeError("Could not find sharedWatchlist JSON in the TradingView page")


def load_overrides() -> dict[str, str | None]:
    if OVERRIDES_FILE.exists():
        raw = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    return {}


def to_yahoo(tv: str, overrides: dict[str, str | None]) -> str | None:
    """Map a TradingView symbol to Yahoo. None = no Yahoo equivalent / skip."""
    if tv in overrides:
        return overrides[tv]
    exch, sym = tv.split(":", 1)
    if exch in NON_EQUITY_EXCHANGES:
        return None
    suffix = EXCHANGE_SUFFIX.get(exch)
    if suffix is None:
        return None
    if suffix == "":
        sym = sym.replace(".", "-")  # BRK.B -> BRK-B
    return sym + suffix


def _load_cache() -> dict:
    if QUOTE_TYPE_CACHE.exists():
        return json.loads(QUOTE_TYPE_CACHE.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    QUOTE_TYPE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    QUOTE_TYPE_CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True), encoding="utf-8")


def quote_types(yahoo_syms: list[str], refresh: bool = False) -> dict[str, str | None]:
    """Yahoo quoteType per symbol, cached in data/quote_types.json."""
    import yfinance as yf

    cache = {} if refresh else _load_cache()
    now = time.time()
    fresh = lambda e: e and now - e.get("ts", 0) < QUOTE_TYPE_TTL_DAYS * 86400
    todo = [s for s in yahoo_syms if not fresh(cache.get(s))]
    for i, sym in enumerate(todo, 1):
        qt = None
        for attempt in range(3):
            try:
                info = yf.Ticker(sym).get_info()
                qt = info.get("quoteType") if info else None
                break
            except Exception as e:  # yfinance raises assorted errors on throttling/404
                if attempt == 2:
                    print(f"  ! {sym}: {type(e).__name__}: {e}", file=sys.stderr)
                time.sleep(2 ** attempt)
        # Only cache real answers so a transient failure is retried next run.
        if qt:
            cache[sym] = {"quoteType": qt, "ts": now}
        print(f"  [{i}/{len(todo)}] {sym}: {qt}", file=sys.stderr)
    _save_cache(cache)
    return {s: (cache.get(s) or {}).get("quoteType") for s in yahoo_syms}


def build(refresh: bool = False) -> tuple[str, list[Symbol]]:
    name, raw = fetch_watchlist()
    overrides = load_overrides()
    symbols, seen = [], set()
    for tv, section in raw:
        if tv in seen:
            continue
        seen.add(tv)
        s = Symbol(tv=tv, section=section, yahoo=to_yahoo(tv, overrides))
        if s.yahoo is None:
            s.reason = "non-equity exchange / no Yahoo mapping"
        symbols.append(s)

    types = quote_types([s.yahoo for s in symbols if s.yahoo], refresh=refresh)
    kept_yahoo = set()
    for s in symbols:
        if not s.yahoo:
            continue
        s.quote_type = types.get(s.yahoo)
        if s.quote_type == "EQUITY":
            if s.yahoo in kept_yahoo:
                s.reason = "duplicate Yahoo symbol"
            else:
                s.keep = True
                kept_yahoo.add(s.yahoo)
        elif s.quote_type is None:
            s.reason = "Yahoo lookup failed"
        else:
            s.reason = f"quoteType={s.quote_type}"
    return name, symbols


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="also list dropped symbols")
    ap.add_argument("--refresh", action="store_true", help="ignore quote-type cache")
    ap.add_argument("--json", type=Path, help="write result to this JSON file")
    args = ap.parse_args()

    name, symbols = build(refresh=args.refresh)
    kept = [s for s in symbols if s.keep]
    dropped = [s for s in symbols if not s.keep]

    print(f'\nWatchlist "{name}": {len(symbols)} symbols -> {len(kept)} stocks kept, {len(dropped)} dropped\n')
    section = object()
    for s in kept:
        if s.section != section:
            section = s.section
            print(f"## {section or '(no section)'}")
        print(f"  {s.tv:<18} -> {s.yahoo}")
    if args.all:
        print("\nDropped:")
        for s in dropped:
            print(f"  {s.tv:<22} {s.yahoo or '-':<10} {s.reason}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps([asdict(s) for s in symbols], indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
