"""Split detection used by the data-health price-jump warning.

Run:  python tests/test_health.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import health  # noqa: E402
from src.health import split_ratio  # noqa: E402

results = []


def check(label, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got}")
    results.append(ok)


# ratio guess: closest common ratio wins
check("exact 2-for-1", split_ratio(200.0, 100.0), "2-for-1")
check("exact 10-for-1", split_ratio(1000.0, 100.0), "10-for-1")
check("reverse 1-for-8", split_ratio(10.0, 80.0), "1-for-8")
check("3-for-2", split_ratio(150.0, 100.0), "3-for-2")
check("bad inputs", split_ratio(0, 5), None)
check("a -60% move matches no common ratio well enough", split_ratio(100.0, 40.0, tol=0.15), None)


# end to end through check(): the SNDK case (-90%), with and without Yahoo confirming a split
def run(lookup):
    prev = pd.DataFrame([{"ticker": "SNDK", "price": 797.24, "trailing_pe": 6.1}])
    orig = health._previous
    health._previous = lambda day: prev
    try:
        rows = [{"ticker": "SNDK", "price": 86.33, "trailing_pe": 0.7, "forward_pe": 2.4}]
        return health.check(rows, "2026-11-20", split_lookup=lookup)[1]
    finally:
        health._previous = orig


w = run(lambda t: "8-for-1")
check("Yahoo confirms 8-for-1 -> reported as a split, not a crash", any("8-for-1 stock split (confirmed by Yahoo)" in x for x in w), True)
w = run(lambda t: None)
check("no split listed -> hedged wording, never called a confirmed split",
      any("Yahoo lists no split" in x for x in w) and not any("confirmed" in x for x in w), True)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
