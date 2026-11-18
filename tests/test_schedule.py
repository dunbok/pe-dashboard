"""Simulate the GitHub cron firings across DST changes, holidays and delays.

Run:  python tests/test_schedule.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import schedule  # noqa: E402

UTC = timezone.utc
NEVER_RAN = lambda d: False  # noqa: E731


def fire(y, m, d, hh, mm, ran=NEVER_RAN):
    """A cron firing at this UTC time (plus whatever delay is baked into hh:mm)."""
    return schedule.should_run(datetime(y, m, d, hh, mm, tzinfo=UTC), ran_check=ran)


def expect(label, got, want):
    ok = got[0] == want
    print(f"{'PASS' if ok else 'FAIL'}  {label}: run={got[0]}  ({got[1]})")
    return ok


results = []

# --- Summer (EDT, UTC-4): 13:15 UTC = 9:15 ET is right, 14:15 UTC = 10:15 ET is wrong
results += [
    expect("EDT Tue 13:15 UTC (9:15 ET)", fire(2026, 7, 14, 13, 15), True),
    expect("EDT Tue 14:15 UTC (10:15 ET) runs as a backup if nothing ran yet", fire(2026, 7, 14, 14, 15), True),
    expect("EDT Tue 14:15 UTC (10:15 ET) skips when the 9:15 run already made today's table",
           fire(2026, 7, 14, 14, 15, ran=lambda d: True), False),
    expect("EDT 13:35 UTC (20 min late -> 9:35 ET)", fire(2026, 7, 14, 13, 35), True),
    expect("EDT 13:50 UTC (35 min late -> 9:50 ET, late start)", fire(2026, 7, 14, 13, 50), True),
    expect("EDT 14:15 UTC delayed to 14:40 (10:40 ET) still runs", fire(2026, 7, 14, 14, 40), True),
]
# --- GitHub delivering the cron many hours late (seen live: ~8h) must still produce a table
results += [
    expect("EST 21:47 UTC (16:47 ET, ~7.5h late) runs", fire(2026, 11, 17, 21, 47), True),
    expect("EST 22:19 UTC (17:19 ET) runs", fire(2026, 11, 17, 22, 19), True),
    expect("EST 22:19 UTC (17:19 ET) skips when today's table exists", fire(2026, 11, 17, 22, 19, ran=lambda d: True), False),
    expect("EST 04:30 UTC next day (23:30 ET) too late", fire(2026, 12, 16, 4, 30), False),
    expect("Late firing on a holiday (Thanksgiving 17:00 ET) still skipped", fire(2026, 11, 26, 22, 0), False),
]
# --- Winter (EST, UTC-5): 13:15 UTC = 8:15 ET is wrong, 14:15 UTC = 9:15 ET is right
results += [
    expect("EST Tue 13:15 UTC (8:15 ET, wrong-DST firing)", fire(2026, 12, 15, 13, 15), False),
    expect("EST Tue 14:15 UTC (9:15 ET)", fire(2026, 12, 15, 14, 15), True),
    expect("EST 13:15 UTC delayed 20 min (8:35 ET) still rejected", fire(2026, 12, 15, 13, 35), False),
    expect("EST 14:30 UTC (9:30 ET)", fire(2026, 12, 15, 14, 30), True),
]
# --- DST transition weeks (US: 2nd Sun March, 1st Sun Nov)
results += [
    expect("Fri before spring-forward 2027-03-12, 14:15 UTC (EST: 9:15 ET)", fire(2027, 3, 12, 14, 15), True),
    expect("Mon after spring-forward 2027-03-15, 13:15 UTC (EDT: 9:15 ET)", fire(2027, 3, 15, 13, 15), True),
    expect("Mon after spring-forward 2027-03-15, 14:15 UTC (10:15 ET) backup run", fire(2027, 3, 15, 14, 15), True),
    expect("Fri before fall-back 2026-10-30, 13:15 UTC (EDT: 9:15 ET)", fire(2026, 10, 30, 13, 15), True),
    expect("Mon after fall-back 2026-11-02, 14:15 UTC (EST: 9:15 ET)", fire(2026, 11, 2, 14, 15), True),
    expect("Mon after fall-back 2026-11-02, 13:15 UTC (8:15 ET)", fire(2026, 11, 2, 13, 15), False),
]
# --- Holidays and weekends (the cron is Mon-Fri, but NYSE holidays fall on weekdays)
results += [
    expect("Thanksgiving 2026-11-26 (EST 14:15 UTC)", fire(2026, 11, 26, 14, 15), False),
    expect("Day after Thanksgiving half-day 2026-11-27 (EST 14:15 UTC)", fire(2026, 11, 27, 14, 15), True),
    expect("Christmas 2026-12-25 (EST 14:15 UTC)", fire(2026, 12, 25, 14, 15), False),
    expect("Independence Day observed Fri 2026-07-03 (EDT 13:15 UTC)", fire(2026, 7, 3, 13, 15), False),
    expect("Good Friday 2027-03-26 (EDT 13:15 UTC)", fire(2027, 3, 26, 13, 15), False),
    expect("Saturday 2026-11-14 (EST 14:15 UTC)", fire(2026, 11, 14, 14, 15), False),
]
# --- No double run: today's table already committed
results += [
    expect("Right firing but today's CSV already exists", fire(2026, 12, 15, 14, 15, ran=lambda d: True), False),
]

# --- wait_until_target: sleeps in <=30s chunks and stops at 9:45:00 ET
fake = {"now": datetime(2026, 12, 15, 14, 14, 3, tzinfo=UTC)}   # 9:14:03 ET (EST)
slept = []


def fake_sleep(s):
    slept.append(s)
    fake["now"] = fake["now"].fromtimestamp(fake["now"].timestamp() + s, tz=UTC)


reached = schedule.wait_until_target(fake["now"], sleep=fake_sleep, clock=lambda: fake["now"])
et = reached.astimezone(schedule.NY)
ok = (et.hour, et.minute) == (9, 45) and et.second == 0 and max(slept) <= 30
print(f"{'PASS' if ok else 'FAIL'}  wait_until_target reached {et:%H:%M:%S %Z} after {len(slept)} sleeps (max {max(slept):.0f}s)")
results.append(ok)

late = datetime(2026, 12, 15, 14, 52, tzinfo=UTC)
ok = schedule.wait_until_target(late, sleep=lambda s: (_ for _ in ()).throw(AssertionError("slept")),
                                clock=lambda: late) == late
print(f"{'PASS' if ok else 'FAIL'}  wait_until_target returns immediately when already past 9:45")
results.append(ok)

# --- price_asof: always 9:45:00 ET of the run's NY date, even for a run at 17:19 ET
for label, t in [("on time (9:46 ET)", datetime(2026, 11, 17, 14, 46, tzinfo=UTC)),
                 ("late (17:19 ET)", datetime(2026, 11, 17, 22, 19, tzinfo=UTC))]:
    a = schedule.price_asof(t)
    ok = (a.year, a.month, a.day, a.hour, a.minute, a.second) == (2026, 11, 17, 9, 45, 0) and a.utcoffset().total_seconds() == -5 * 3600
    print(f"{'PASS' if ok else 'FAIL'}  price_asof {label} -> {a:%Y-%m-%d %H:%M:%S %Z}")
    results.append(ok)

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
