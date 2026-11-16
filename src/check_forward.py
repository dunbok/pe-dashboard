"""Step 3 check: AV backfill for a few tickers + forward P/E history.

Usage: python -m src.check_forward NVDA AAPL ...   (uses 1 AV call per ticker not yet cached)
"""
from __future__ import annotations

import sys

from . import alphavantage as av_mod, edgar, prices
from .forward import forward_pe
from .trailing import adjust_for_splits, build_quarters


def fmt(x, nd=1):
    return "" if x is None else f"{x:.{nd}f}"


def main(tickers: list[str]) -> None:
    import yfinance as yf

    cik_map = edgar.ticker_to_cik()
    eps_cache, px_cache, edgar_latest, last_split = {}, {}, {}, {}
    for t in tickers:
        eps_cache[t] = edgar.get_eps(t, cik_map)
        px_cache[t] = prices.history(t)
        edgar_latest[t] = (eps_cache[t] or {}).get("latest_filing")
        sp = prices.splits(px_cache[t])
        last_split[t] = sp.index.max().date().isoformat() if len(sp) else None

    summary = av_mod.refresh(tickers, edgar_latest, last_split)
    print(f"AV: fetched {len(summary['fetched'])}, calls today {summary['calls_today']}/25, "
          f"still due {summary['still_due']}\n")

    hdr = f"{'Ticker':<7}{'Src':<11}{'AV4Q':>7}{'Y-TTM':>7}{'FwdEPS':>8}{'FwdPE(Y)':>9}" \
          f"{'5yMean':>8}{'5yMed':>8}{'10yMean':>8}{'10yMed':>8}{'1yAgo':>7}  Note"
    print(hdr)
    print("-" * len(hdr))
    for t in tickers:
        info = yf.Ticker(t).get_info() or {}
        av = av_mod.load(t)
        gaapq = []
        if eps_cache[t] and eps_cache[t].get("facts") and not eps_cache[t].get("foreign"):
            gaapq = build_quarters(adjust_for_splits(eps_cache[t]["facts"], prices.splits(px_cache[t]), px_cache[t]["Close"]))
        r = forward_pe(t, px_cache[t]["Close"], av, gaapq, info.get("forwardEps"), info.get("trailingEps"))
        av4 = sum(q["eps"] for q in av["quarters"][-4:]) if av and av.get("quarters") else None
        pe = r["pe_series"]
        year_ago = None
        if pe is not None:
            s = pe[pe.index <= pe.index.max() - __import__("pandas").DateOffset(years=1)].dropna()
            year_ago = float(s.iloc[-1]) if len(s) else None
        print(f"{t:<7}{r['source'] or '':<11}{fmt(av4, 2):>7}{fmt(info.get('trailingEps'), 2):>7}"
              f"{fmt(info.get('forwardEps'), 2):>8}{fmt(info.get('forwardPE')):>9}"
              f"{fmt(r['pe_5y'].get('mean')):>8}{fmt(r['pe_5y'].get('median')):>8}"
              f"{fmt(r['pe_10y'].get('mean')):>8}{fmt(r['pe_10y'].get('median')):>8}{fmt(year_ago):>7}  {r['note']}")


if __name__ == "__main__":
    main([a.upper() for a in sys.argv[1:]] or ["NVDA", "AAPL", "JPM", "WM", "MSFT", "LLY", "HOOD", "TSM", "ASML", "NVO", "CAT"])
