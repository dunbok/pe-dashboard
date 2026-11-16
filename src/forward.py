"""Approximate historical forward P/E.

True point-in-time consensus history isn't free, so (PLAN.md):
    forward P/E(t) = price(t) / EPS actually reported for the 4 fiscal quarters
                     ending after t
- Source: Alpha Vantage adjusted EPS (what forward estimates are based on).
  Fallback when a ticker isn't backfilled yet: EDGAR GAAP quarters (flagged).
- Quarters not reported yet (the last ~12 months of dates) are filled with
  today's consensus: Yahoo forwardEps / 4 per missing quarter (flagged).
- Every value from this module is an estimate ("≈" in the UI).
"""
from __future__ import annotations

import bisect

import pandas as pd

from .trailing import window_stats


def quarters_from_av(av: dict | None) -> list[tuple[pd.Timestamp, float]]:
    if not av:
        return []
    return [(pd.Timestamp(q["end"]), q["eps"]) for q in av.get("quarters", [])]


def quarters_from_edgar(gaap_quarters) -> list[tuple[pd.Timestamp, float]]:
    """trailing.Period objects (already split-adjusted)."""
    return [(p.end, p.val) for p in gaap_quarters]


def forward_series(close: pd.Series, quarters: list[tuple[pd.Timestamp, float]],
                   fwd_eps_now: float | None) -> pd.DataFrame:
    """Daily approx forward EPS / P/E, plus how many quarters were filled from estimates."""
    quarters = sorted(quarters)
    ends = [q[0] for q in quarters]
    eps = [q[1] for q in quarters]
    last_end = ends[-1] if ends else None
    fwd_vals, n_est = [], []
    for t in close.index:
        i = bisect.bisect_right(ends, t)          # first quarter ending after t
        future = eps[i:i + 4]
        missing = 4 - len(future)
        # Only fill with estimates near the present: older gaps mean missing data.
        if missing and (last_end is None or t < last_end - pd.Timedelta(days=370) or fwd_eps_now is None):
            fwd_vals.append(float("nan"))
            n_est.append(None)
            continue
        if missing == 4 and i == 0:
            fwd_vals.append(float("nan"))         # no history at all before t
            n_est.append(None)
            continue
        total = sum(future) + (missing * fwd_eps_now / 4 if missing else 0.0)
        fwd_vals.append(total)
        n_est.append(missing)
    df = pd.DataFrame({"fwd_eps": fwd_vals, "n_est": n_est}, index=close.index)
    df["pe"] = close / df["fwd_eps"]
    df.loc[df["fwd_eps"] <= 0, "pe"] = float("nan")
    return df


def av_consistent(av: dict | None, ref_ttm_eps: float | None) -> bool:
    """AV EPS for some ADRs is per ordinary share / home currency (e.g. NVO).
    Reject AV when its last 4 quarters are far from Yahoo's per-listed-share TTM."""
    if not av or not av.get("quarters"):
        return False
    if not ref_ttm_eps or ref_ttm_eps <= 0:
        return True  # nothing to compare against (e.g. loss-making)
    av4 = sum(q["eps"] for q in av["quarters"][-4:])
    return av4 > 0 and 0.5 <= av4 / ref_ttm_eps <= 2.0


def forward_pe(ticker: str, close: pd.Series, av: dict | None, gaap_quarters,
               fwd_eps_now: float | None, ref_ttm_eps: float | None = None) -> dict:
    av_ok = av_consistent(av, ref_ttm_eps)
    if av and av.get("quarters") and not av_ok:
        av = None
        rejected = "AV EPS doesn't match Yahoo's per-share basis; "
    else:
        rejected = ""
    if av_ok:
        src, quarters = "av", quarters_from_av(av)
    elif gaap_quarters:
        src, quarters = "edgar_gaap", quarters_from_edgar(gaap_quarters)
    else:
        return {"ticker": ticker, "source": None, "pe_series": None, "pe_5y": {}, "pe_10y": {},
                "note": rejected + "no EPS history for forward approximation"}
    df = forward_series(close, quarters, fwd_eps_now)
    today = close.index.max()
    return {
        "ticker": ticker,
        "source": src,
        "pe_series": df["pe"],
        "n_est": df["n_est"],
        "pe_5y": window_stats(df["pe"], 5, today),
        "pe_10y": window_stats(df["pe"], 10, today),
        "note": "" if src == "av" else rejected + "GAAP fallback (no usable AV data)",
    }
