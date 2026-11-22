"""Daily data sanity checks, run before outputs are written.

severe  -> the run aborts (GitHub Action goes red, yesterday's published data stays up)
warning -> written into latest.json ("warnings") and shown in a banner on the page
"""
from __future__ import annotations

import pandas as pd

from .config import DATA

DAILY_DIR = DATA / "daily"


# (new shares per old share) ratios that real splits use
SPLIT_RATIOS = [(2, 1), (3, 1), (4, 1), (5, 1), (6, 1), (7, 1), (8, 1), (10, 1), (15, 1), (20, 1), (25, 1), (30, 1),
                (40, 1), (50, 1), (3, 2), (5, 4), (7, 4), (7, 5), (9, 5)]


def split_ratio(old: float, cur: float, tol: float = 0.2) -> str | None:
    """Label like '8-for-1' (or '1-for-8' reverse) if the price change is explained by a split, allowing
    +/-`tol` for the stock's own move on the day. None if no common ratio fits."""
    if not old or not cur or old <= 0 or cur <= 0:
        return None
    r = old / cur                       # >1: price fell (forward split); <1: price rose (reverse split)
    best = None                         # closest ratio wins, not the first one inside the tolerance
    for a, b in SPLIT_RATIOS:
        k = a / b
        for err, label in ((abs(r / k - 1), f"{a}-for-{b}"),
                           (abs(r * k - 1), f"1-for-{a}" if b == 1 else f"{b}-for-{a}")):
            if err <= tol and (best is None or err < best[0]):
                best = (err, label)
    return best[1] if best else None


def yahoo_recent_split(ticker: str, days: int = 10) -> str | None:
    """The split Yahoo actually lists for this ticker in the last `days` days, e.g. '8-for-1' (None if none)."""
    try:
        import yfinance as yf

        s = yf.Ticker(ticker).splits
        if s is None or not len(s):
            return None
        s = s[s.index >= (pd.Timestamp.now(tz=s.index.tz) - pd.Timedelta(days=days))]
        if not len(s):
            return None
        ratio = float(s.iloc[-1])
        if ratio >= 1:
            return f"{ratio:g}-for-1"
        return f"1-for-{1 / ratio:g}"
    except Exception:
        return None


def _previous(day: str) -> pd.DataFrame | None:
    files = sorted(p for p in DAILY_DIR.glob("*.csv") if p.stem < day)
    return pd.read_csv(files[-1]) if files else None


def check(rows: list[dict], day: str, subset: bool = False, split_lookup=None) -> tuple[list[str], list[str]]:
    split_lookup = split_lookup or yahoo_recent_split
    severe, warn = [], []
    prev = None if subset else _previous(day)
    n = len(rows)

    if prev is not None:
        pn = len(prev)
        if n < 0.9 * pn:
            severe.append(f"row count fell from {pn} to {n}")
        pt, nt = prev["trailing_pe"].notna().sum(), sum(r["trailing_pe"] is not None for r in rows)
        if pt and nt < 0.7 * pt:
            severe.append(f"trailing P/E available for {nt} stocks vs {pt} yesterday")
        gone = sorted(set(prev["ticker"]) - {r["ticker"] for r in rows})
        new = sorted({r["ticker"] for r in rows} - set(prev["ticker"]))
        if gone:
            warn.append("no longer in table: " + ", ".join(gone))
        if new:
            warn.append("new in table: " + ", ".join(new))
        pp = dict(zip(prev["ticker"], prev["price"]))
        for r in rows:
            old, cur = pp.get(r["ticker"]), r["price"]
            if old and cur and old > 0 and abs(cur / old - 1) > 0.4:
                chg = f"{100 * (cur / old - 1):+.0f}%"
                real = split_lookup(r["ticker"])           # confirmed by Yahoo's own split events
                guess = None if real else split_ratio(old, cur)
                if real:
                    warn.append(f"{r['ticker']}: price {chg} vs last run is a {real} stock split (confirmed by Yahoo), "
                                f"not a crash; EPS-based P/Es may be off until the next run")
                elif guess:
                    warn.append(f"{r['ticker']}: price {chg} vs last run is close to a {guess} split ratio but Yahoo lists "
                                f"no split, so it may be a real move or a bad quote")
                else:
                    warn.append(f"{r['ticker']}: price moved {chg} vs last run (no split found; bad quote?)")

    for r in rows:
        t = r["ticker"]
        for k, lo, hi in (("trailing_pe", 0, 500), ("forward_pe", 0, 500)):
            v = r.get(k)
            if v is not None and not (lo < v <= hi):
                warn.append(f"{t}: {k} = {v} looks off")
        nd, ic = r.get("net_debt_ebitda"), r.get("interest_cover")
        if nd is not None and nd > 15:
            warn.append(f"{t}: net debt/EBITDA {nd} very high, check")
        if ic is not None and ic > 1000:
            warn.append(f"{t}: interest cover {ic} implausible")
        tp, fp = r.get("trailing_pe"), r.get("forward_pe")
        if tp and fp and (tp / fp > 8 or fp / tp > 8):
            warn.append(f"{t}: trailing {tp} vs forward {fp} P/E differ wildly (EPS, split or currency error?)")
        y, sec = r.get("_lev_yahoo"), r.get("net_debt_ebitda")
        if y is not None and sec is not None and r.get("lev_src") == "SEC" and abs(y - sec) > max(1.5, 0.5 * max(abs(y), abs(sec))):
            warn.append(f"{t}: net debt/EBITDA SEC {sec} vs Yahoo {round(y, 1)} disagree")
        nt = r.get("note") or ""
        if "unreachable" in nt or "unavailable" in nt:
            warn.append(f"{t}: {nt}")
    return severe, warn
