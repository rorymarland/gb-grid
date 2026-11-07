import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
import server as s

london = ZoneInfo('Europe/London')
bad = 0
d = date(2019, 1, 1)
while d <= date(2032, 12, 31):
    expected = int(datetime(d.year, d.month, d.day, tzinfo=london).timestamp())
    got = s.uk_midnight_utc(d)
    if got != expected:
        bad += 1
        if bad < 5: print('MISMATCH', d, got, expected)
    # settlement periods: count per day and contiguity
    nxt = s.uk_midnight_utc(d + timedelta(days=1))
    periods = (nxt - got) // 1800
    if periods not in (46, 48, 50): print('bad period count', d, periods); bad += 1
    d += timedelta(days=1)
print('uk_midnight_utc mismatches:', bad)
# spot checks
for text in ['2026-10-25', '25-OCT-2026', '2026-10-25T00:00:00Z', '25/10/2026']:
    print(text, s.parse_settlement_date(text))
print('2026-10-25 P1 ->', s.iso(s.settlement_start(date(2026,10,25), 1)), ' P50 ->', s.iso(s.settlement_start(date(2026,10,25), 50)))
print('2026-03-29 P1 ->', s.iso(s.settlement_start(date(2026,3,29), 1)), ' P46 ->', s.iso(s.settlement_start(date(2026,3,29), 46)))
print('2026-07-01 P1 ->', s.iso(s.settlement_start(date(2026,7,1), 1)))
print(s.parse_time('2018-01-20T12:00Z'), s.parse_time('2026-11-04T14:05:00.000Z'), s.parse_time('2026-11-04 14:05:00'))
sys.exit(1 if bad else 0)
