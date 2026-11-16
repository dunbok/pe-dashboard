"""Step 2 sanity check: our EDGAR-based trailing P/E vs Yahoo's.

Usage: python -m src.check_trailing NVDA AAPL JPM ...
"""
from __future__ import annotations

import sys

from . import edgar, prices
from .trailing import load_adr_ratios, trailing_pe


def fmt(x, nd=2):
    return "" if x is None else f"{x:.{nd}f}"


def main(tickers: list[str]) -> None:
    import yfinance as yf

    cik_map = edgar.ticker_to_cik()
    ratios = load_adr_ratios()
    hdr = f"{'Ticker':<7}{'Basis':>6}{'EPS(us)':>9}{'EPS(Y)':>9}{'EPSdiff':>8}{'PE(us)':>8}{'PE(Y)':>8}" \
          f"{'5yMean':>8}{'5yMed':>8}{'10yMean':>8}{'10yMed':>8}  {'LastQ':<11}Note"
    print(hdr)
    print("-" * len(hdr))
    for t in tickers:
        try:
            eps = edgar.get_eps(t, cik_map)
            px = prices.history(t)
            fx = prices.fx_to_usd(eps["currency"]) if eps and eps.get("currency") not in (None, "USD") else None
            r = trailing_pe(t, eps, px, fx, ratios)
            info = yf.Ticker(t).get_info() or {}
        except Exception as e:
            print(f"{t:<7} ERROR {type(e).__name__}: {e}")
            continue
        y_eps, y_pe = info.get("trailingEps"), info.get("trailingPE")
        price = float(px["Close"].iloc[-1]) if not px.empty else None
        our_pe = price / r["ttm_eps_usd"] if r["ttm_eps_usd"] and r["ttm_eps_usd"] > 0 else None
        diff = f"{(r['ttm_eps_usd'] / y_eps - 1) * 100:+.0f}%" if r["ttm_eps_usd"] and y_eps else ""
        print(f"{t:<7}{r['basis'] or '':>6}{fmt(r['ttm_eps_usd']):>9}{fmt(y_eps):>9}{diff:>8}"
              f"{fmt(our_pe, 1):>8}{fmt(y_pe, 1):>8}"
              f"{fmt(r['pe_5y'].get('mean'), 1):>8}{fmt(r['pe_5y'].get('median'), 1):>8}"
              f"{fmt(r['pe_10y'].get('mean'), 1):>8}{fmt(r['pe_10y'].get('median'), 1):>8}"
              f"  {r.get('last_quarter_end') or '':<11}{r['note']}")


if __name__ == "__main__":
    main([a.upper() for a in sys.argv[1:]] or ["NVDA", "AAPL", "JPM", "WM", "MSFT", "LLY", "HOOD", "TSM", "ASML", "NVO", "CAT"])
