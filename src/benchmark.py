"""S&P 500 benchmark row (SPY) for the dashboard.

SPY is an ETF, so it has no SEC earnings of its own. Its numbers come from:
  * price               Yahoo, 9:45 ET (same as the stocks)
  * trailing P/E        multpl.com monthly S&P 500 P/E (as-reported earnings, back to 1871);
                        5y/10y mean and median are computed here from that table
  * forward P/E         FactSet "Earnings Insight" weekly PDF: forward 12-month P/E plus its
                        5-year and 10-year averages (FactSet publishes no medians)
Each source is independent. If one fails, its cells fall back to the last good values (flagged
with their date) or stay blank. Nothing is guessed. Output: docs/data/benchmark.json.
"""
from __future__ import annotations

import io
import json
import re
import statistics
from datetime import datetime

from .config import DATA, ROOT

DOCS = ROOT / "docs"
OUT = DOCS / "data" / "benchmark.json"
HISTORY = DATA / "benchmark_history.csv"
UA = {"User-Agent": "pe-dashboard personal dashboard (benchmark row)"}
MULTPL_URL = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
FACTSET_URL = "https://www.factset.com/earningsinsight"
PE_MIN, PE_MAX = 5.0, 100.0


def _ok(v: float | None) -> bool:
    return v is not None and PE_MIN <= v <= PE_MAX


# ---------------------------------------------------------------- multpl (trailing)
_ROW = re.compile(
    r"<td>\s*([A-Z][a-z]{2} \d{1,2}, \d{4})\s*</td>\s*<td>(?:\s*<abbr[^>]*>[^<]*</abbr>)?\s*(?:&#x2002;|&nbsp;|\s)*([\d.]+)\s*</td>")


def parse_multpl(html: str) -> dict:
    """-> {'current': float, 'current_date': str, 'monthly': [(date, pe), ...] newest first}"""
    rows = []
    for d, v in _ROW.findall(html):
        dt = datetime.strptime(d, "%b %d, %Y").date()
        rows.append((dt, float(v)))
    if len(rows) < 240:
        raise ValueError(f"multpl table looks wrong: only {len(rows)} rows")
    rows.sort(key=lambda r: r[0], reverse=True)
    current_date, current = rows[0]
    monthly = [(d, v) for d, v in rows if d.day == 1]      # one point per month
    if not _ok(current):
        raise ValueError(f"multpl current P/E {current} outside {PE_MIN}-{PE_MAX}")
    return {"current": current, "current_date": current_date.isoformat(), "monthly": monthly}


def window_stats(monthly: list, years: int, min_cover: float = 0.8) -> dict:
    """Mean/median of the newest `years*12` monthly points; blank if too few months."""
    want = years * 12
    vals = [v for _, v in monthly[:want] if _ok(v)]
    if len(vals) < want * min_cover:
        return {"mean": None, "median": None}
    return {"mean": round(statistics.fmean(vals), 1), "median": round(statistics.median(vals), 1)}


# ---------------------------------------------------------------- FactSet (forward)
def parse_factset(text: str) -> dict:
    t = re.sub(r"\s+", " ", text)
    t = re.sub(r"(\d)- ?year", r"\1-year", t)          # PDF line breaks split "10-\nyear"
    fwd = re.search(r"forward 12-month P/E ratio for the S&P 500 is ([\d.]+)\. This P/E ratio is (?:above|below) the "
                    r"5-year average \(([\d.]+)\) and (?:above|below) the 10-year average \(([\d.]+)\)", t)
    trl = re.search(r"trailing 12-month P/E ratio is ([\d.]+), which is (?:above|below) the 5-year average of ([\d.]+) "
                    r"and (?:above|below) the 10-year average of ([\d.]+)", t)
    day = re.search(r"EARNINGS INSIGHT ([A-Z][a-z]+ \d{1,2}, \d{4})", t)
    if not fwd:
        raise ValueError("FactSet forward P/E sentence not found (layout changed?)")
    f, f5, f10 = (float(x) for x in fwd.groups())
    if not all(_ok(x) for x in (f, f5, f10)):
        raise ValueError(f"FactSet forward values out of range: {f}, {f5}, {f10}")
    out = {"forward_pe": f, "fwd_5y_mean": f5, "fwd_10y_mean": f10,
           "date": datetime.strptime(day.group(1), "%B %d, %Y").date().isoformat() if day else None,
           "trailing_pe": None}
    if trl and _ok(float(trl.group(1))):
        out["trailing_pe"] = float(trl.group(1))
    return out


def _fetch_factset() -> dict:
    import requests
    from pypdf import PdfReader

    r = requests.get(FACTSET_URL, headers=UA, timeout=60)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise ValueError("FactSet page did not return a PDF")
    reader = PdfReader(io.BytesIO(r.content))
    return parse_factset("\n".join((p.extract_text() or "") for p in reader.pages))


def _fetch_multpl() -> dict:
    import requests

    r = requests.get(MULTPL_URL, headers=UA, timeout=60)
    r.raise_for_status()
    return parse_multpl(r.text)


# ---------------------------------------------------------------- price
def _price(asof: datetime) -> tuple[float | None, str | None]:
    from . import pipeline

    try:
        live = pipeline.live_prices(["SPY"], asof)
        if "SPY" in live:
            return live["SPY"]
    except Exception:
        pass
    try:
        import yfinance as yf
        h = yf.Ticker("SPY").history(period="5d", auto_adjust=False)["Close"].dropna()
        if len(h):
            return float(h.iloc[-1]), "prior close"
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------- build
def build(asof: datetime, previous: dict | None = None, fetch_multpl=_fetch_multpl, fetch_factset=_fetch_factset,
          price=_price) -> dict:
    warnings: list[str] = []
    prev = previous or {}
    row = {"ticker": "SPY", "name": "S&P 500 (SPY)", "section": "Benchmark", "tv_symbol": "AMEX:SPY"}

    p, basis = price(asof)
    row["price"] = round(p, 2) if p else None
    row["price_basis"] = basis

    mp = None
    try:
        mp = fetch_multpl()
        row["trailing_pe"] = round(mp["current"], 1)
        for y in (5, 10):
            s = window_stats(mp["monthly"], y)
            row[f"tr_{y}y_mean"], row[f"tr_{y}y_median"] = s["mean"], s["median"]
        row["multpl_date"] = mp["current_date"]
    except Exception as e:                                     # keep going: blank or last good values
        warnings.append(f"multpl unavailable ({type(e).__name__}: {e})")
        for k in ("trailing_pe", "tr_5y_mean", "tr_5y_median", "tr_10y_mean", "tr_10y_median", "multpl_date"):
            row[k] = prev.get(k)
        if prev.get("multpl_date"):
            warnings.append(f"trailing P/E shown from the last good read ({prev['multpl_date']})")

    fs = None
    try:
        fs = fetch_factset()
        row["forward_pe"] = fs["forward_pe"]
        row["fwd_5y_mean"], row["fwd_10y_mean"] = fs["fwd_5y_mean"], fs["fwd_10y_mean"]
        row["fwd_5y_median"] = row["fwd_10y_median"] = None   # FactSet publishes averages only
        row["factset_date"] = fs["date"]
    except Exception as e:
        warnings.append(f"FactSet unavailable ({type(e).__name__}: {e})")
        for k in ("forward_pe", "fwd_5y_mean", "fwd_10y_mean", "factset_date"):
            row[k] = prev.get(k)
        row["fwd_5y_median"] = row["fwd_10y_median"] = None
        if prev.get("factset_date"):
            warnings.append(f"forward P/E shown from the last good read ({prev['factset_date']})")

    # Cross-check the two trailing figures; a big gap means a definition or parsing problem.
    if mp and fs and fs.get("trailing_pe"):
        gap = abs(mp["current"] / fs["trailing_pe"] - 1)
        row["trailing_xcheck"] = fs["trailing_pe"]
        if gap > 0.05:
            warnings.append(f"trailing P/E differs {gap:.0%}: multpl {mp['current']:.1f} vs FactSet {fs['trailing_pe']:.1f}")

    row["note"] = ("Trailing: multpl.com monthly S&P 500 P/E (as-reported earnings); stats from its monthly table. "
                   "Forward: FactSet Earnings Insight (forward 12-month P/E; 5y/10y are averages, no medians).")
    return {"asof": asof.strftime("%Y-%m-%d %H:%M ET"), "row": row, "warnings": warnings}


def run(asof: datetime, forced: bool = False, log=print) -> None:
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8")).get("row", {})
        except (OSError, ValueError):
            prev = {}
    res = build(asof, prev)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, separators=(",", ":")), encoding="utf-8")
    r = res["row"]
    log(f"[benchmark] SPY price={r.get('price')} trailing={r.get('trailing_pe')} forward={r.get('forward_pe')} "
        f"warnings={len(res['warnings'])}")
    for w in res["warnings"]:
        log(f"[benchmark] WARNING {w}")
    if not forced and r.get("forward_pe") is not None:        # real daily snapshots, like the stock table
        day = asof.date().isoformat()
        lines = HISTORY.read_text(encoding="utf-8").splitlines() if HISTORY.exists() else ["date,trailing_pe,forward_pe,price"]
        lines = [x for x in lines if not x.startswith(day + ",")]
        lines.append(f"{day},{r.get('trailing_pe') or ''},{r['forward_pe']},{r.get('price') or ''}")
        HISTORY.write_text("\n".join(lines) + "\n", encoding="utf-8")
