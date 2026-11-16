"""Daily close history (split-adjusted, not dividend-adjusted) and split events.

Cache: data/prices/{SYMBOL}.csv. Updated incrementally; if a new split makes the
cached history disagree with Yahoo's, the whole history is re-downloaded.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .config import PRICES_DIR

HISTORY_YEARS = 11


def _download(symbol: str, start: str) -> pd.DataFrame:
    import yfinance as yf

    for attempt in range(3):
        try:
            df = yf.Ticker(symbol).history(start=start, auto_adjust=False, actions=True)
            if df is not None and not df.empty:
                df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
                df = df[["Close", "Stock Splits"]].rename(columns={"Stock Splits": "Split"})
                # Never cache today's bar: mid-session it is a partial close.
                today_et = pd.Timestamp(datetime.now(ZoneInfo("America/New_York")).date())
                return df[df.index < today_et]
            return pd.DataFrame(columns=["Close", "Split"])
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def empty() -> pd.DataFrame:
    return pd.DataFrame({"Close": [], "Split": []}, index=pd.DatetimeIndex([]))


def cached(symbol: str) -> pd.DataFrame:
    """Whatever is on disk (used when Yahoo fails), else an empty frame."""
    path = PRICES_DIR / f"{symbol}.csv"
    if path.exists():
        return pd.read_csv(path, index_col="Date", parse_dates=True)
    return empty()


def history(symbol: str) -> pd.DataFrame:
    """DataFrame indexed by date with Close and Split (ratio, 0 = no split)."""
    PRICES_DIR.mkdir(parents=True, exist_ok=True)
    path = PRICES_DIR / f"{symbol}.csv"
    full_start = (date.today() - timedelta(days=365 * HISTORY_YEARS + 10)).isoformat()

    if not path.exists():
        df = _download(symbol, full_start)
        df.to_csv(path, index_label="Date")
        return df

    cached = pd.read_csv(path, index_col="Date", parse_dates=True)
    if cached.empty:
        df = _download(symbol, full_start)
    else:
        last = cached.index.max()
        if last.date() >= date.today() - timedelta(days=1):
            return cached
        fresh = _download(symbol, (last - timedelta(days=10)).date().isoformat())
        overlap = cached.index.intersection(fresh.index)
        split_since = (fresh.loc[fresh.index > last, "Split"] > 0).any()
        mismatch = any(
            abs(cached.at[d, "Close"] / fresh.at[d, "Close"] - 1) > 0.005 for d in overlap
        )
        if split_since or mismatch or len(overlap) == 0:
            df = _download(symbol, full_start)
        else:
            df = pd.concat([cached, fresh[fresh.index > last]])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.to_csv(path, index_label="Date")
    return df


def splits(px: pd.DataFrame) -> pd.Series:
    s = px["Split"]
    return s[s > 0]


def fx_to_usd(currency: str) -> pd.Series | None:
    """Daily USD per 1 unit of `currency`. None if unavailable."""
    if currency == "USD":
        return None
    px = history(f"{currency}USD=X")
    return px["Close"] if not px.empty else None
