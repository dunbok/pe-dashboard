"""Step 4: assemble the daily table.

Two phases so the slow work happens before 9:45 ET:
  prepare()   - EDGAR, price history, Alpha Vantage budget, Yahoo info, P/E histories
  finalize()  - needs the 9:45 prices: current P/Es, comparison column, outputs

Rule everywhere: no data -> None (blank cell). Never zeros, never guesses.
"""
from __future__ import annotations

import json
import math
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import alphavantage as av_mod, edgar, prices, watchlist
from .config import DATA, ROOT
from .forward import forward_pe
from .trailing import adjust_for_splits, build_quarters, load_adr_ratios, trailing_pe

NY = ZoneInfo("America/New_York")
DOCS = ROOT / "docs"
DAILY_DIR = DATA / "daily"
FWD_HISTORY_FILE = DATA / "fwd_pe_history.csv"
SERIES_YEARS = 10

COLUMNS = [
    "ticker", "tv_symbol", "name", "section", "price", "price_basis",
    "trailing_pe", "trailing_eps_src", "forward_pe",
    "tr_5y_mean", "tr_5y_median", "tr_10y_mean", "tr_10y_median",
    "fwd_5y_mean", "fwd_5y_median", "fwd_10y_mean", "fwd_10y_median",
    "fwd_vs_5y_median_pct", "fwd_hist_src", "note",
]


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _r(x, nd=2):
    v = _f(x)
    return None if v is None else round(v, nd)


@dataclass
class Prep:
    sym: watchlist.Symbol
    info: dict
    eps: dict | None
    px: pd.DataFrame
    tr: dict
    fw: dict
    notes: list[str] = field(default_factory=list)


def _info(sym: str) -> dict:
    import yfinance as yf

    for attempt in range(3):
        try:
            return yf.Ticker(sym).get_info() or {}
        except Exception:
            time.sleep(2 ** attempt)
    return {}


def prepare(symbols: list[watchlist.Symbol], log=print) -> list[Prep]:
    ysyms = [s.yahoo for s in symbols]
    cik_map = edgar.ticker_to_cik()
    ratios = load_adr_ratios()

    log(f"[prep] EDGAR + price history for {len(ysyms)} tickers")
    eps, px, notes = {}, {}, {y: [] for y in ysyms}
    for y in ysyms:
        try:
            eps[y] = edgar.get_eps(y, cik_map)
        except Exception as e:
            log(f"  EDGAR {y}: {type(e).__name__}: {e} (using cache)")
            eps[y] = edgar.cached(y)
            notes[y].append("EDGAR unreachable, cached EPS")
        try:
            px[y] = prices.history(y)
        except Exception as e:
            log(f"  prices {y}: {type(e).__name__}: {e} (using cache)")
            px[y] = prices.cached(y)
            notes[y].append("Yahoo price history unreachable, cached")

    latest = {y: (eps[y] or {}).get("latest_filing") for y in ysyms}
    last_split = {}
    for y in ysyms:
        sp = prices.splits(px[y])
        last_split[y] = sp.index.max().date().isoformat() if len(sp) else None
    log("[prep] Alpha Vantage refresh")
    try:
        s = av_mod.refresh(ysyms, latest, last_split, log=log)
        log(f"  AV: fetched {len(s['fetched'])}, still due {len(s['still_due'])}, "
            f"never fetched {s['never_fetched']}, calls today {s['calls_today']}/25")
    except Exception as e:
        log(f"  AV refresh failed: {type(e).__name__}: {e}")

    log("[prep] Yahoo info")
    with ThreadPoolExecutor(4) as ex:
        infos = dict(zip(ysyms, ex.map(_info, ysyms)))

    fx_cache: dict = {}

    def fx(cur):
        if cur in (None, "USD"):
            return None
        if cur not in fx_cache:
            try:
                fx_cache[cur] = prices.fx_to_usd(cur)
            except Exception:
                fx_cache[cur] = None
        return fx_cache[cur]

    log("[prep] P/E histories")
    out = []
    for s in symbols:
        y = s.yahoo
        info, e, p = infos[y], eps[y], px[y]
        if not info:
            notes[y].append("Yahoo info unavailable")
        cur = (e or {}).get("currency")
        tr = trailing_pe(y, e, p, fx(cur) if cur else None, ratios)
        gaapq = []
        if e and e.get("facts") and not e.get("foreign") and not p.empty:
            gaapq = build_quarters(adjust_for_splits(e["facts"], prices.splits(p), p["Close"]))
        fw = forward_pe(y, p["Close"], av_mod.load(y), gaapq, _f(info.get("forwardEps")),
                        _f(info.get("trailingEps"))) if not p.empty else \
            {"source": None, "pe_series": None, "pe_5y": {}, "pe_10y": {}, "note": "no price history"}
        out.append(Prep(s, info, e, p, tr, fw, notes[y]))
    return out


def live_prices(ysyms: list[str], asof: datetime) -> dict[str, tuple[float, str]]:
    """Last 1-minute close at/before `asof`. Basis '9:45' if the bar is recent,
    'last close' if the market is closed (e.g. Tokyo)."""
    import yfinance as yf

    out: dict[str, tuple[float, str]] = {}
    try:
        df = yf.download(ysyms, period="1d", interval="1m", group_by="ticker",
                         progress=False, threads=True, auto_adjust=False)
    except Exception:
        return out
    for s in ysyms:
        try:
            d = (df[s]["Close"] if len(ysyms) > 1 else df["Close"]).dropna()
        except KeyError:
            continue
        if d.empty:
            continue
        idx = d.index
        d.index = (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(NY)
        d = d[d.index <= asof + pd.Timedelta(seconds=59)]
        if d.empty or d.index[-1].date() != asof.date():
            continue
        fresh = (asof - d.index[-1]).total_seconds() <= 600
        out[s] = (float(d.iloc[-1]), "9:45" if fresh else "last close")
    return out


def _yahoo_pe(info: dict, eps_key: str, pe_key: str, price: float) -> float | None:
    """price / Yahoo EPS, guarding against Yahoo giving EPS in another currency
    (some ADRs): if Yahoo's own P/E disagrees with price/EPS, trust its P/E."""
    eps, ype = _f(info.get(eps_key)), _f(info.get(pe_key))
    ref = _f(info.get("regularMarketPrice")) or _f(info.get("currentPrice"))
    if eps is None or eps <= 0:
        return None
    if ype and ref:
        if abs(ref / eps / ype - 1) < 0.1:
            return price / eps
        return ype * price / ref
    return price / eps


def finalize(preps: list[Prep], live: dict[str, tuple[float, str]]) -> list[dict]:
    rows = []
    for p in preps:
        y, info, tr, fw = p.sym.yahoo, p.info, p.tr, p.fw
        notes = list(p.notes)

        price, basis = None, None
        if y in live:
            price, basis = live[y]
        elif not p.px.empty:
            price, basis = float(p.px["Close"].iloc[-1]), "prior close"
        elif _f(info.get("regularMarketPrice")):
            price, basis = float(info["regularMarketPrice"]), "Yahoo quote"
        if price is not None and price <= 0:
            price = None

        t_pe, t_src = None, None
        if price:
            domestic_ok = (tr.get("basis") == "Q" and tr.get("ttm_eps_usd") is not None
                           and not (p.eps or {}).get("foreign"))
            if domestic_ok:
                if tr["ttm_eps_usd"] > 0:
                    t_pe, t_src = price / tr["ttm_eps_usd"], "SEC"
                else:
                    notes.append("negative TTM EPS")
            else:
                t_pe, t_src = _yahoo_pe(info, "trailingEps", "trailingPE", price), "Yahoo"
                if t_pe is None:
                    t_src = None
                    if _f(info.get("trailingEps")) is not None and info["trailingEps"] <= 0:
                        notes.append("negative TTM EPS")
        f_pe = _yahoo_pe(info, "forwardEps", "forwardPE", price) if price else None
        if price and f_pe is None and _f(info.get("forwardEps")) is not None and info["forwardEps"] <= 0:
            notes.append("negative forward EPS")

        f5 = fw.get("pe_5y", {}).get("median")
        vs = (f_pe / f5 - 1) * 100 if f_pe and f5 else None
        # The trailing-EPS note only matters when the trailing cell is blank.
        for n in (tr.get("note") if t_pe is None else None, fw.get("note")):
            if n and n not in notes:
                notes.append(n)

        rows.append({
            "ticker": y, "tv_symbol": p.sym.tv,
            "name": info.get("shortName") or info.get("longName"),
            "section": p.sym.section,
            "price": _r(price), "price_basis": basis,
            "trailing_pe": _r(t_pe, 1), "trailing_eps_src": t_src,
            "forward_pe": _r(f_pe, 1),
            "tr_5y_mean": _r(tr.get("pe_5y", {}).get("mean"), 1),
            "tr_5y_median": _r(tr.get("pe_5y", {}).get("median"), 1),
            "tr_10y_mean": _r(tr.get("pe_10y", {}).get("mean"), 1),
            "tr_10y_median": _r(tr.get("pe_10y", {}).get("median"), 1),
            "fwd_5y_mean": _r(fw.get("pe_5y", {}).get("mean"), 1),
            "fwd_5y_median": _r(f5, 1),
            "fwd_10y_mean": _r(fw.get("pe_10y", {}).get("mean"), 1),
            "fwd_10y_median": _r(fw.get("pe_10y", {}).get("median"), 1),
            "fwd_vs_5y_median_pct": _r(vs, 1),
            "fwd_hist_src": fw.get("source"),
            "note": "; ".join(notes) or None,
            "_fwd_eps": _f(info.get("forwardEps")),
        })
    return rows


def _series_file(p: Prep, outdir) -> bool:
    """Weekly trailing + forward P/E history for the dashboard chart."""
    frames = {}
    if p.tr.get("pe_series") is not None:
        frames["trailing"] = p.tr["pe_series"]
    if p.fw.get("pe_series") is not None:
        frames["forward"] = p.fw["pe_series"]
    if not frames:
        return False
    df = pd.DataFrame(frames)
    df = df[df.index > df.index.max() - pd.DateOffset(years=SERIES_YEARS)]
    wk = df.resample("W-FRI").last().dropna(how="all")
    if wk.empty:
        return False
    doc = {"dates": [d.date().isoformat() for d in wk.index]}
    for k in ("trailing", "forward"):
        doc[k] = [_r(v, 1) for v in wk[k]] if k in wk else [None] * len(wk)
    (outdir / f"{p.sym.yahoo}.json").write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    return True


def write_outputs(rows: list[dict], preps: list[Prep], asof: datetime, forced: bool = False, log=print) -> None:
    day = asof.date().isoformat()
    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS / "data" / "series").mkdir(parents=True, exist_ok=True)
    (DOCS / "archive").mkdir(parents=True, exist_ok=True)

    has_series = {p.sym.yahoo: _series_file(p, DOCS / "data" / "series") for p in preps}
    for r in rows:
        r["has_series"] = has_series.get(r["ticker"], False)

    df = pd.DataFrame(rows)
    csv_path = DAILY_DIR / f"{day}.csv"
    df[COLUMNS].to_csv(csv_path, index=False)
    shutil.copyfile(csv_path, DOCS / "archive" / f"{day}.csv")

    # Real forward P/E snapshots: over time these replace the approximation.
    # (Test runs are skipped: only real 9:45 runs should feed the real history.)
    if not forced:
        snap = pd.DataFrame([{"date": day, "ticker": r["ticker"], "price": r["price"],
                              "fwd_eps": r["_fwd_eps"], "forward_pe": r["forward_pe"]}
                             for r in rows if r["forward_pe"] is not None])
        if FWD_HISTORY_FILE.exists():
            old = pd.read_csv(FWD_HISTORY_FILE)
            snap = pd.concat([old[old["date"] != day], snap], ignore_index=True)
        snap.to_csv(FWD_HISTORY_FILE, index=False)

    dates = sorted(p.stem for p in DAILY_DIR.glob("*.csv"))
    (DOCS / "data" / "archive.json").write_text(json.dumps(dates), encoding="utf-8")

    public = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    latest = {
        "asof": asof.strftime("%Y-%m-%d %H:%M ET"),
        "date": day,
        "forced": forced,
        "generated": datetime.now(NY).isoformat(timespec="seconds"),
        "count": len(public),
        "rows": public,
    }
    (DOCS / "data" / "latest.json").write_text(json.dumps(latest, separators=(",", ":")), encoding="utf-8")
    log(f"[out] {csv_path.name}: {len(public)} rows; series files: {sum(has_series.values())}")
