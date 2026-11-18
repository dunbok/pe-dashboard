"""Run-time gating for the GitHub Actions cron.

GitHub cron is UTC and best-effort, so the workflow fires at BOTH 13:15 and
14:15 UTC (= 9:15 ET in summer / winter respectively). The firing that lands at
8:15 ET is too early and exits. GitHub can also deliver a firing many hours late;
a run that starts any time after 9:45 ET (up to LATE_CUTOFF) still produces
today's table, using the 9:45 ET price recovered from Yahoo's 1-minute bars
(see price_asof). A day that already has a real table never runs twice.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .config import DATA

NY = ZoneInfo("America/New_York")
TARGET = time(9, 45)
WINDOW_START = time(8, 50)   # earlier than this: it's the wrong-DST firing (8:15 ET)
LATE_CUTOFF = time(23, 0)    # later than this: too late to trust the day's 1-minute data, skip


def is_trading_day(d: date) -> bool:
    import pandas_market_calendars as mcal

    nyse = mcal.get_calendar("NYSE")
    return not nyse.schedule(start_date=d, end_date=d).empty


def already_ran(d: date) -> bool:
    """True only if a *real* (non-forced, full-list) run already produced d's table.
    Test runs (--force / --only / --no-wait) are flagged in latest.json and never block."""
    import json

    path = DATA.parent / "docs" / "data" / "latest.json"
    if not path.exists():
        return False
    try:
        latest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return latest.get("date") == d.isoformat() and not latest.get("forced", False)


def should_run(now: datetime, ran_check=already_ran, trading_check=is_trading_day) -> tuple[bool, str]:
    now = now.astimezone(NY)
    stamp = now.strftime("%Y-%m-%d %H:%M %Z")
    if not trading_check(now.date()):
        return False, f"{stamp}: not an NYSE trading day, exiting"
    t = now.time()
    if t < WINDOW_START:
        return False, f"{stamp}: too early (this is the other DST cron firing), exiting"
    if t >= LATE_CUTOFF:
        return False, f"{stamp}: past {LATE_CUTOFF:%H:%M} ET, too late for today, exiting"
    if ran_check(now.date()):
        return False, f"{stamp}: today's table already exists, exiting"
    if t < TARGET:
        return True, f"{stamp}: trading day, will wait until {TARGET:%H:%M} ET"
    return True, (f"{stamp}: trading day but already past {TARGET:%H:%M} ET (late start), "
                  f"running now with the {TARGET:%H:%M} ET price")


def price_asof(now: datetime) -> datetime:
    """The moment prices are taken: always 9:45:00 ET of the run's New York date, even if
    this run started late (the 1-minute bars still hold the 9:45 price)."""
    now = now.astimezone(NY)
    return datetime.combine(now.date(), TARGET, tzinfo=NY)


def wait_until_target(now: datetime, sleep=_time.sleep, clock=None) -> datetime:
    """Sleep until 9:45:00 ET today (no-op if already past). Returns the time reached."""
    clock = clock or (lambda: datetime.now(NY))
    now = now.astimezone(NY)
    target = datetime.combine(now.date(), TARGET, tzinfo=NY)
    while True:
        remaining = (target - clock()).total_seconds()
        if remaining <= 0:
            return clock()
        sleep(min(remaining, 30))
