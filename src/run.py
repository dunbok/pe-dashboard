"""Daily run.

    python -m src.run                  # normal run
    python -m src.run --only NVDA,TSM  # subset (testing)
    python -m src.run --force          # skip the trading-day / time checks (testing)

Time logic (9:15 ET start, wait until 9:45 ET, NYSE calendar) lives in src/schedule.py.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import pipeline, watchlist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated Yahoo symbols (testing)")
    ap.add_argument("--force", action="store_true", help="skip the trading-day and time checks")
    ap.add_argument("--no-wait", action="store_true", help="don't sleep until 9:45 ET")
    args = ap.parse_args()

    from . import schedule
    now = datetime.now(pipeline.NY)
    if args.force:
        print("[schedule] --force: skipping trading-day / time checks")
    else:
        ok, why = schedule.should_run(now)
        print(f"[schedule] {why}")
        if not ok:
            return 0

    name, symbols = watchlist.build()
    kept = [s for s in symbols if s.keep]
    if args.only:
        want = {x.strip().upper() for x in args.only.split(",")}
        kept = [s for s in kept if s.yahoo.upper() in want]
    print(f'[watchlist] "{name}": {len(kept)} stocks')

    preps = pipeline.prepare(kept)

    if args.force or args.no_wait:
        asof = datetime.now(pipeline.NY)
    else:
        asof = schedule.wait_until_target(now)
    print(f"[run] fetching prices as of {asof:%Y-%m-%d %H:%M:%S %Z}")
    live = pipeline.live_prices([p.sym.yahoo for p in preps], asof)
    rows = pipeline.finalize(preps, live)
    # A test run (forced / subset / no wait) must never count as today's real run.
    forced = args.force or args.no_wait or bool(args.only)
    pipeline.write_outputs(rows, preps, asof, forced=forced)
    return 0


if __name__ == "__main__":
    sys.exit(main())
