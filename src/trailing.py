"""Point-in-time trailing-12-month GAAP diluted EPS and daily trailing P/E.

Rules (from PLAN.md):
- Use each period's first-filed value, known from its filing date (no look-ahead).
- Split-adjust every fact to today's share basis *before* combining, using the
  splits that happened after its filing date (prices are split-adjusted to today).
- Quarterly TTM = 4 contiguous quarters; Q4 (and any quarter only given as YTD)
  is derived as YTD/FY minus the shorter YTD with the same start.
- Fallback for annual-only filers (20-F): latest fiscal-year EPS.
- Non-positive EPS -> blank P/E; those days are excluded from mean/median.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import timedelta

import pandas as pd

from .config import ADR_RATIOS_FILE

QUARTER_DAYS = (80, 100)
ANNUAL_DAYS = (350, 380)
QUARTERLY_STALE_DAYS = 200   # last quarter must end within this many days of t
ANNUAL_STALE_DAYS = 550      # annual EPS for 20-F filers (filed ~4 months after FY end)
MIN_WINDOW_COVERAGE = 0.8    # share of trading days with a P/E needed to show a 5y/10y stat


@dataclass
class Period:
    start: pd.Timestamp
    end: pd.Timestamp
    val: float
    known: pd.Timestamp  # date the value became public

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def load_adr_ratios() -> dict[str, float]:
    if not ADR_RATIOS_FILE.exists():
        return {}
    raw = json.loads(ADR_RATIOS_FILE.read_text(encoding="utf-8"))
    return {k.upper(): float(v) for k, v in raw.items() if not k.startswith("_")}


def _split_adjust(val: float, filed: pd.Timestamp, splits: pd.Series) -> float:
    factor = math.prod(splits[splits.index > filed].tolist()) if len(splits) else 1.0
    return val / factor


def adjust_for_splits(facts: list[dict], splits: pd.Series, close: pd.Series | None = None) -> list[Period]:
    """Split-adjust every fact to today's share basis, guarding against SEC tagging typos.

    Guards (both only fire on absurd values, so legit restatements are untouched):
    1. If the first-filed value is far bigger than the latest-filed value for the
       same period (|first| > 4*|latest| + 0.5, after split adjustment), the
       first filing was mis-tagged: use the later, corrected value.
    2. If a value is still > 10x the stock price when it became known, drop it
       (blank beats wrong; the TTM chain simply has a gap there).
    """
    out = []
    for f in facts:
        filed = pd.Timestamp(f["filed"])
        val = _split_adjust(f["val"], filed, splits)
        if "alt_val" in f:
            alt = _split_adjust(f["alt_val"], pd.Timestamp(f["alt_filed"]), splits)
            if abs(val) > 4 * abs(alt) + 0.5:
                val = alt
        if close is not None and len(close):
            px = close.asof(filed)
            if pd.notna(px) and px > 0 and abs(val) > 10 * px:
                continue
        out.append(Period(pd.Timestamp(f["start"]), pd.Timestamp(f["end"]), val, filed))
    return out


def _in(days: int, rng: tuple[int, int]) -> bool:
    return rng[0] <= days <= rng[1]


def build_quarters(periods: list[Period]) -> list[Period]:
    """Discrete quarters, including ones derived from YTD / full-year differences."""
    q: dict[pd.Timestamp, Period] = {}
    for p in periods:
        if _in(p.days, QUARTER_DAYS):
            q.setdefault(p.end, p)

    # YTD differences: same start, ends one quarter apart (6M-3M, 9M-6M, FY-9M).
    by_start: dict[pd.Timestamp, list[Period]] = {}
    for p in periods:
        by_start.setdefault(p.start, []).append(p)
    for group in by_start.values():
        group.sort(key=lambda p: p.end)
        for i, long in enumerate(group):
            if long.end in q or long.days < 150:
                continue
            for short in group[:i]:
                gap = (long.end - short.end).days
                if _in(gap, QUARTER_DAYS):
                    q[long.end] = Period(short.end + timedelta(days=1), long.end,
                                         long.val - short.val, max(long.known, short.known))
                    break

    # FY minus three known quarters, when the 9M YTD is missing.
    for a in periods:
        if not _in(a.days, ANNUAL_DAYS) or a.end in q:
            continue
        inside = [x for x in q.values() if x.start >= a.start - timedelta(days=7) and x.end < a.end]
        if len(inside) == 3:
            q[a.end] = Period(max(x.end for x in inside) + timedelta(days=1), a.end,
                              a.val - sum(x.val for x in inside),
                              max([a.known] + [x.known for x in inside]))
    return sorted(q.values(), key=lambda p: p.end)


def build_annuals(periods: list[Period]) -> list[Period]:
    a: dict[pd.Timestamp, Period] = {}
    for p in periods:
        if _in(p.days, ANNUAL_DAYS):
            a.setdefault(p.end, p)
    return sorted(a.values(), key=lambda p: p.end)


def ttm_at(t: pd.Timestamp, quarters: list[Period], annuals: list[Period]) -> tuple[float, str] | None:
    """(TTM EPS, basis 'Q' or 'A') known as of date t, or None."""
    known = [p for p in quarters if p.known <= t]
    if len(known) >= 4:
        last4 = known[-4:]
        contiguous = all(_in((b.end - a.end).days, QUARTER_DAYS) for a, b in zip(last4, last4[1:]))
        if contiguous and (t - last4[-1].end).days <= QUARTERLY_STALE_DAYS:
            return sum(p.val for p in last4), "Q"
    known_a = [p for p in annuals if p.known <= t]
    if known_a and (t - known_a[-1].end).days <= ANNUAL_STALE_DAYS:
        return known_a[-1].val, "A"
    return None


def ttm_series(index: pd.DatetimeIndex, quarters: list[Period], annuals: list[Period]) -> pd.DataFrame:
    """Daily TTM EPS (local currency, per ordinary share) and basis."""
    vals, basis = [], []
    for t in index:
        r = ttm_at(t, quarters, annuals)
        vals.append(r[0] if r else float("nan"))
        basis.append(r[1] if r else None)
    return pd.DataFrame({"ttm_eps": vals, "basis": basis}, index=index)


def window_stats(pe: pd.Series, years: int, today: pd.Timestamp) -> dict:
    win = pe[pe.index > today - pd.DateOffset(years=years)]
    valid = win.dropna()
    coverage = len(valid) / len(win) if len(win) else 0.0
    if coverage < MIN_WINDOW_COVERAGE or valid.empty:
        return {"mean": None, "median": None, "coverage": round(coverage, 2)}
    return {"mean": float(valid.mean()), "median": float(valid.median()), "coverage": round(coverage, 2)}


def trailing_pe(ticker: str, eps: dict | None, px: pd.DataFrame, fx: pd.Series | None,
                adr_ratios: dict[str, float]) -> dict:
    """Everything step 4 needs for one ticker. Missing data -> None, with a note."""
    result = {"ticker": ticker, "ttm_eps_usd": None, "basis": None, "pe_series": None,
              "pe_5y": {}, "pe_10y": {}, "note": ""}
    if eps is None:
        result["note"] = "no SEC CIK"
        return result
    if not eps.get("facts"):
        result["note"] = "no EPS facts in EDGAR"
        return result
    if px.empty:
        result["note"] = "no price history"
        return result

    ratio = 1.0
    if eps.get("foreign"):
        if ticker.upper() not in adr_ratios:
            result["note"] = "foreign filer: ADR ratio unknown (add to adr_ratios.json)"
            return result
        ratio = adr_ratios[ticker.upper()]
    currency = eps.get("currency", "USD")
    if currency != "USD" and fx is None:
        result["note"] = f"no FX history for {currency}"
        return result

    from .prices import splits as split_events
    periods = adjust_for_splits(eps["facts"], split_events(px), px["Close"])
    quarters, annuals = build_quarters(periods), build_annuals(periods)
    ttm = ttm_series(px.index, quarters, annuals)

    eps_usd = ttm["ttm_eps"] * ratio
    if fx is not None:
        eps_usd = eps_usd * fx.reindex(px.index).ffill()
    pe = px["Close"] / eps_usd
    pe[eps_usd <= 0] = float("nan")

    today = px.index.max()
    last = ttm.iloc[-1]
    result.update({
        "ttm_eps_usd": None if pd.isna(eps_usd.iloc[-1]) else float(eps_usd.iloc[-1]),
        "basis": last["basis"],
        "currency": currency,
        "pe_series": pe,
        "pe_5y": window_stats(pe, 5, today),
        "pe_10y": window_stats(pe, 10, today),
        "last_quarter_end": quarters[-1].end.date().isoformat() if quarters else None,
    })
    if pd.isna(last["ttm_eps"]):
        result["note"] = "no recent EPS (stale or gaps)"
    elif last["ttm_eps"] <= 0:
        result["note"] = "negative TTM EPS"
    return result
