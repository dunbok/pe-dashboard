"""Run-time gating for the GitHub Actions cron.

GitHub cron is UTC and best-effort, so the workflow fires at BOTH 13:15 and
14:15 UTC (= 9:15 ET in summer / winter respectively). The other firing lands at
8:15 or 10:15 ET and must exit. This module decides, from the current New York
time alone, whether this firing is the right one, then waits for 9:45:00 ET.
"""
from __future__ import annotations

import time as _time
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from .config import DATA

NY = ZoneInfo("America/New_York")
TARGET = time(9, 45)
WINDOW_START = time(8, 50)   # earlier than this: it's the wrong-DST firing (8:15 ET)
WINDOW_END = time(10, 0)     # later than this: wrong-DST firing (10:15 ET) or hopelessly late


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
    if t >= WINDOW_END:
        return False, f"{stamp}: past {WINDOW_END:%H:%M} ET (other DST cron firing or too late), exiting"
    if ran_check(now.date()):
        return False, f"{stamp}: today's table already exists, exiting"
    if t < TARGET:
        return True, f"{stamp}: trading day, will wait until {TARGET:%H:%M} ET"
    return True, f"{stamp}: trading day but already past {TARGET:%H:%M} ET (late start), running now"


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
