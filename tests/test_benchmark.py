"""Parser and failure-handling tests for src/benchmark.py (no network).

Run:  python tests/test_benchmark.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import benchmark as b  # noqa: E402

results = []


def check(label, cond):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    results.append(bool(cond))


# ---- multpl-style table: current estimate row, then 300 monthly rows (newest first)
def fake_html(n=300, bad=False):
    rows = ['<tr><td>Nov 17, 2026</td><td> <abbr title="Estimate">†</abbr> 25.04 </td></tr>']
    y, m = 2026, 11
    for i in range(n):
        v = 20.0 + (i % 10)                       # 20..29, mean 24.5, median 24.5
        rows.append(f'<tr class="odd"><td>{["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][m-1]} 1, {y}</td>'
                    f'<td> &#x2002; {v:.2f} </td></tr>')
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return "<table id='datatable'>" + "".join(rows[: (5 if bad else None)]) + "</table>"


p = b.parse_multpl(fake_html())
check("multpl: current value and date", p["current"] == 25.04 and p["current_date"] == "2026-11-17")
check("multpl: monthly series excludes the mid-month current row", p["monthly"][0][0].day == 1 and len(p["monthly"]) == 300)
s5 = b.window_stats(p["monthly"], 5)
check("window stats: 60 months give mean/median", s5["mean"] is not None and s5["median"] is not None)
check("window stats: blank when not enough months", b.window_stats(p["monthly"][:30], 5) == {"mean": None, "median": None})
try:
    b.parse_multpl(fake_html(bad=True))
    check("multpl: a broken table is rejected", False)
except ValueError:
    check("multpl: a broken table is rejected", True)

# ---- FactSet-style text, including the PDF line break that splits "10-\nyear"
TXT = ("EARNINGS INSIGHT November 13, 2026 Valuation: The forward 12-month P/E ratio for the S&P 500 is 18.3. "
       "This P/E ratio is below the 5-year average\n(19.8) and below the 10-\nyear average (19.1). "
       "The trailing 12-month P/E ratio is 25.4, which is above the 5-year average of 24.4 and above the 10-\nyear average of 23.7.")
f = b.parse_factset(TXT)
check("factset: forward + averages", (f["forward_pe"], f["fwd_5y_mean"], f["fwd_10y_mean"]) == (18.3, 19.8, 19.1))
check("factset: trailing + report date", f["trailing_pe"] == 25.4 and f["date"] == "2026-11-13")
for label, txt in [("factset: changed wording is rejected", "The market was up this week."),
                   ("factset: out-of-range value is rejected", TXT.replace("is 18.3.", "is 183."))]:
    try:
        b.parse_factset(txt)
        check(label, False)
    except ValueError:
        check(label, True)

# ---- build(): one source failing must not break the other, and must fall back to the last good values
asof = datetime(2026, 11, 18, 9, 45)
good_m = lambda: p                                                   # noqa: E731
good_f = lambda: f                                                   # noqa: E731
boom = lambda: (_ for _ in ()).throw(RuntimeError("down"))           # noqa: E731
px = lambda a: (700.0, "9:45")                                       # noqa: E731

ok = b.build(asof, None, good_m, good_f, px)
r = ok["row"]
check("build: all sources -> filled row, no warnings", r["trailing_pe"] == 25.0 and r["forward_pe"] == 18.3 and not ok["warnings"])
check("build: forward medians stay blank (FactSet gives averages only)", r["fwd_5y_median"] is None and r["fwd_10y_median"] is None)

no_fs = b.build(asof, r, good_m, boom, px)
check("build: FactSet down -> reuses last good forward values and warns",
      no_fs["row"]["forward_pe"] == 18.3 and any("FactSet unavailable" in w for w in no_fs["warnings"]))
no_fs_cold = b.build(asof, None, good_m, boom, px)
check("build: FactSet down with no history -> forward blank, trailing intact",
      no_fs_cold["row"]["forward_pe"] is None and no_fs_cold["row"]["trailing_pe"] == 25.0)
no_mp = b.build(asof, r, boom, good_f, px)
check("build: multpl down -> forward intact, trailing from last good read",
      no_mp["row"]["forward_pe"] == 18.3 and no_mp["row"]["trailing_pe"] == 25.0)
diff = dict(f, trailing_pe=32.0)
check("build: trailing sources disagreeing by >5% raises a warning",
      any("differs" in w for w in b.build(asof, None, good_m, lambda: diff, px)["warnings"]))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
