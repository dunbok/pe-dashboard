"""Balance-sheet safety metrics from SEC EDGAR companyfacts (US GAAP, USD only).

Outputs per ticker: net_debt, ebitda_ttm, net_debt_ebitda, interest_cover (EBIT / interest expense).
Cache: data/leverage/{TICKER}.json (only the derived numbers). A ticker is re-fetched
when the EPS cache shows a periodic filing newer than the leverage cache.

Rule: no data -> None. EBITDA = operating income + D&A (not a reported line, so it is derived).
Banks / insurers / REITs are skipped by the pipeline (net debt / EBITDA is meaningless there).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from . import edgar
from .config import DATA

LEVERAGE_DIR = DATA / "leverage"
SCHEMA = 1
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A"}


def _facts(gaap: dict, concept: str, instant: bool) -> list[dict]:
    rows = []
    for r in gaap.get(concept, {}).get("units", {}).get("USD", []):
        if r.get("form") not in FORMS or r.get("val") is None or not r.get("end"):
            continue
        if instant and r.get("start"):
            continue
        if not instant and not r.get("start"):
            continue
        rows.append(r)
    return rows


def _latest_instant(gaap: dict, concepts: list[str]) -> tuple[float, str] | None:
    """Most recent balance-sheet value across the concepts (latest end date wins; latest filing for ties)."""
    best = None
    for c in concepts:
        for r in _facts(gaap, c, True):
            key = (r["end"], r["filed"])
            if best is None or key > best[0]:
                best = (key, float(r["val"]))
    return (best[1], best[0][0]) if best else None


def _instant_at(gaap: dict, concepts: list[str], end: str) -> float | None:
    """Sum-free lookup: value of the first concept that has a fact at exactly `end`."""
    for c in concepts:
        vals = sorted((r for r in _facts(gaap, c, True) if r["end"] == end), key=lambda r: r["filed"])
        if vals:
            return float(vals[-1]["val"])
    return None


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _ttm(gaap: dict, concepts: list[str], upto: str | None = None) -> tuple[float, bool] | None:
    """Trailing-twelve-month flow. Returns (value, is_fy_fallback).

    10-Q cash-flow / income facts are year-to-date, so TTM = last FY + current YTD - prior-year YTD.
    """
    for c in concepts:
        rows = _facts(gaap, c, False)
        if not rows:
            continue
        # one value per (start, end): the latest-filed (accepts restatements)
        per: dict[tuple[str, str], float] = {}
        for r in sorted(rows, key=lambda r: r["filed"]):
            per[(r["start"], r["end"])] = float(r["val"])
        ends = sorted({e for (_, e) in per})
        if upto:
            ends = [e for e in ends if e <= upto]
        if not ends:
            continue
        latest_end = ends[-1]
        cur = [(s, v) for (s, e), v in per.items() if e == latest_end]
        if not cur:
            continue
        s_cur, v_cur = max(cur, key=lambda sv: _days(sv[0], latest_end))
        span = _days(s_cur, latest_end)
        if 350 <= span <= 380:
            return v_cur, False
        # find the FY that ended just before this YTD started, and the prior-year YTD
        fy = [(k, v) for k, v in per.items() if 350 <= _days(k[0], k[1]) <= 380
              and 0 <= _days(k[1], s_cur) <= 10 + 1]
        py = [(k, v) for k, v in per.items()
              if abs(_days(k[1], latest_end) - 365) <= 10 and abs(_days(k[0], s_cur) - 365) <= 10]
        if fy and py:
            return fy[-1][1] + v_cur - py[-1][1], False
        # fallback: latest full fiscal year
        fys = sorted((k, v) for k, v in per.items() if 350 <= _days(k[0], k[1]) <= 380)
        if fys:
            return fys[-1][1], True
    return None


DEBT_TOTAL = ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"]
DEBT_NONCURRENT = ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "ConvertibleNotesPayableNoncurrent", "SeniorNotesNoncurrent"]
DEBT_CURRENT = ["LongTermDebtCurrent", "DebtCurrent", "ShortTermBorrowings"]
CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"]
ST_INV = ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]
EBIT = ["OperatingIncomeLoss"]
DA = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
      "DepreciationAmortizationAndAccretionNet", "DepreciationAmortizationAndOther"]
INTEREST = ["InterestExpense", "InterestExpenseDebt", "InterestExpenseNonoperating", "InterestAndDebtExpense"]


def compute(facts_json: dict) -> dict:
    gaap = facts_json.get("facts", {}).get("us-gaap", {})
    out = {"schema": SCHEMA, "net_debt": None, "ebitda_ttm": None, "net_debt_ebitda": None,
           "interest_cover": None, "balance_date": None, "note": None}
    if not gaap:
        out["note"] = "no US-GAAP data"
        return out

    # --- debt (latest balance sheet) ---
    tot = _latest_instant(gaap, DEBT_TOTAL)
    nc = _latest_instant(gaap, DEBT_NONCURRENT)
    anchor = max((x for x in (tot, nc) if x), key=lambda x: x[1], default=None)
    if anchor is None:
        # no long-term debt tagged: treat as zero only if some balance-sheet cash is reported
        cash0 = _latest_instant(gaap, CASH)
        if cash0 is None:
            out["note"] = "no debt/cash data"
            return out
        end = cash0[1]
        debt = 0.0
    else:
        end = anchor[1]
        if tot and tot[1] == end:
            debt = tot[0]
        else:
            cur = _instant_at(gaap, DEBT_CURRENT, end) or 0.0
            debt = (nc[0] if nc else 0.0) + cur
    cash_latest = _latest_instant(gaap, CASH)
    if cash_latest and _days(end, cash_latest[1]) > 120:
        out["note"] = "debt not tagged in latest filing"
        return out
    cash = _instant_at(gaap, CASH, end)
    if cash is None:
        out["note"] = "no cash value at balance date"
        return out
    cash += _instant_at(gaap, ST_INV, end) or 0.0
    net_debt = debt - cash
    out["net_debt"] = round(net_debt)
    out["balance_date"] = end

    # --- EBITDA + interest cover (TTM) ---
    ebit = _ttm(gaap, EBIT, end)
    da = _ttm(gaap, DA, end)
    fy_basis = False
    if ebit is not None and da is not None:
        ebitda = ebit[0] + da[0]
        fy_basis = ebit[1] or da[1]
        out["ebitda_ttm"] = round(ebitda)
        if ebitda > 0:
            out["net_debt_ebitda"] = round(net_debt / ebitda, 2)
        else:
            out["note"] = "negative EBITDA"
    else:
        out["note"] = "EBITDA inputs missing"
    intr = _ttm(gaap, INTEREST, end)
    if ebit is not None and intr is not None and intr[0] > 0:
        out["interest_cover"] = round(ebit[0] / intr[0], 1)
    elif debt <= 0 and ebit is not None:
        out["interest_cover"] = None   # no debt: coverage not meaningful
    if fy_basis:
        out["note"] = ((out["note"] + "; ") if out["note"] else "") + "EBITDA on last fiscal year"
    return out


def get(ticker: str, eps: dict | None, cik_map: dict[str, int], log=print) -> dict | None:
    """Cached leverage metrics; re-derived when EDGAR has a newer periodic filing than the cache."""
    cik = cik_map.get(ticker.upper())
    if cik is None:
        return None
    path = LEVERAGE_DIR / f"{ticker.upper()}.json"
    cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    latest = (eps or {}).get("latest_filing") or ""
    if cached and cached.get("schema") == SCHEMA and cached.get("latest_filing", "") >= latest:
        return cached
    r = edgar._get(edgar.FACTS_URL.format(cik=cik))
    if r.status_code == 404:
        return None
    data = compute(r.json())
    data.update({"ticker": ticker.upper(), "latest_filing": latest,
                 "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    LEVERAGE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return data


def cached(ticker: str) -> dict | None:
    path = LEVERAGE_DIR / f"{ticker.upper()}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
