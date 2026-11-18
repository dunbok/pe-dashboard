"""Daily data sanity checks, run before outputs are written.

severe  -> the run aborts (GitHub Action goes red, yesterday's published data stays up)
warning -> written into latest.json ("warnings") and shown in a banner on the page
"""
from __future__ import annotations

import pandas as pd

from .config import DATA

DAILY_DIR = DATA / "daily"


def _previous(day: str) -> pd.DataFrame | None:
    files = sorted(p for p in DAILY_DIR.glob("*.csv") if p.stem < day)
    return pd.read_csv(files[-1]) if files else None


STAT_PAIRS = [(f"{k}_{w}_mean", f"{k}_{w}_median") for k in ("tr", "fwd") for w in ("5y", "10y")]


def _scrub_history(r: dict, warn: list[str]) -> None:
    """Blank implausible historical mean/median cells (e.g. near-zero EPS blowing up an average)."""
    for mean_k, med_k in STAT_PAIRS:
        for k in (mean_k, med_k):
            v = r.get(k)
            if v is not None and not (0 < v <= 500):
                warn.append(f"{r['ticker']}: {k} = {v} implausible, blanked")
                r[k] = None
        mean, med = r.get(mean_k), r.get(med_k)
        if mean is not None and med is not None and mean > 5 * med:
            warn.append(f"{r['ticker']}: {mean_k} {mean} far above median {med}, blanked")
            r[mean_k] = None


def check(rows: list[dict], day: str, subset: bool = False) -> tuple[list[str], list[str]]:
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
                warn.append(f"{r['ticker']}: price moved {100 * (cur / old - 1):+.0f}% vs last run (split or bad quote?)")

    for r in rows:
        t = r["ticker"]
        _scrub_history(r, warn)
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
