"""Unit tests for src/health.py (no network).  Run: python -m pytest tests"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import health  # noqa: E402


def row(t, **kw):
    r = {"ticker": t, "price": 100.0, "trailing_pe": 20.0, "forward_pe": 18.0,
         "net_debt_ebitda": 1.0, "interest_cover": 10.0, "lev_src": "SEC", "_lev_yahoo": None, "note": None}
    r.update(kw)
    return r


def prev_df(n=10):
    return pd.DataFrame([{"ticker": f"T{i}", "price": 100.0, "trailing_pe": 20.0} for i in range(n)])


def run(rows, prev=None):
    health._previous = lambda day: prev  # noqa: SLF001
    return health.check(rows, "2026-01-02")


def test_row_count_drop_is_severe():
    sev, _ = run([row(f"T{i}") for i in range(8)], prev_df())
    assert any("row count" in s for s in sev)


def test_trailing_coverage_drop_is_severe():
    rows = [row(f"T{i}", trailing_pe=None if i < 5 else 20.0) for i in range(10)]
    sev, _ = run(rows, prev_df())
    assert any("trailing P/E available" in s for s in sev)


def test_clean_run_no_flags():
    sev, warn = run([row(f"T{i}") for i in range(10)], prev_df())
    assert not sev and not warn


def test_added_removed_and_price_jump_warn():
    rows = [row(f"T{i}") for i in range(1, 10)] + [row("NEW")]
    rows[0]["price"] = 200.0
    _, warn = run(rows, prev_df())
    text = " | ".join(warn)
    assert "no longer in table: T0" in text and "new in table: NEW" in text and "price moved" in text


def test_pe_and_leverage_range_warnings():
    _, warn = run([row("A", trailing_pe=600.0, net_debt_ebitda=20.0, interest_cover=1500.0)], None)
    text = " | ".join(warn)
    assert "trailing_pe" in text and "net debt/EBITDA" in text and "interest cover" in text


def test_historical_stats_scrubbed():
    r = row("CRDO", fwd_5y_mean=2.2e17, fwd_5y_median=15.0, tr_5y_mean=60.0, tr_5y_median=10.0,
            tr_10y_mean=20.0, tr_10y_median=18.0)
    _, warn = run([r], None)
    assert r["fwd_5y_mean"] is None and r["tr_5y_mean"] is None
    assert r["fwd_5y_median"] == 15.0 and r["tr_10y_mean"] == 20.0
    assert sum("blanked" in w for w in warn) == 2


def test_subset_skips_previous_comparison():
    def boom(day):
        raise AssertionError("should not load")
    health._previous = boom  # noqa: SLF001
    sev, _ = health.check([row("A")], "2026-01-02", subset=True)
    assert not sev
