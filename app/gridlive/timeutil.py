"""Time helpers. Everything internal is UTC epoch seconds; settlement periods follow UK clock time."""

import time
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache


# --------------------------------------------------------------------------
# Time helpers (everything internal is UTC epoch seconds)
# --------------------------------------------------------------------------

def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def iso_minute(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%MZ')


def parse_time(value):
    """Parse an ISO 8601 timestamp (naive values are treated as UTC)."""
    text = str(value).strip()
    if not text:
        raise ValueError('empty timestamp')
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


@lru_cache(maxsize=None)
def _last_sunday(year, month):
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


@lru_cache(maxsize=8192)
def uk_midnight_utc(day):
    """UTC epoch of local (Europe/London) midnight at the start of `day`.

    British Summer Time runs from 01:00 UTC on the last Sunday of March to
    01:00 UTC on the last Sunday of October, so local midnight is in BST
    for dates strictly after the March change, up to and including the
    October change day.
    """
    in_bst = _last_sunday(day.year, 3) < day <= _last_sunday(day.year, 10)
    base = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    return base - (3600 if in_bst else 0)


def settlement_start(day, period):
    """Start (UTC epoch) of a GB settlement period (1-based, local days)."""
    return uk_midnight_utc(day) + (int(period) - 1) * 1800


def settlement_periods(day):
    """Number of settlement periods in a UK day: 46, 48 or 50."""
    return int((uk_midnight_utc(day + timedelta(days=1)) - uk_midnight_utc(day)) // 1800)


MONTHS = {m: i + 1 for i, m in enumerate(
    ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'])}


def parse_settlement_date(value):
    text = str(value).strip()
    if len(text) >= 10 and text[4] == '-' and text[7] == '-':
        return date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    parts = text.replace('/', '-').split('-')
    if len(parts) == 3:
        day, month, year = parts
        month_number = int(month) if month.isdigit() else MONTHS[month[:3].upper()]
        return date(int(year[:4]), month_number, int(day))
    raise ValueError('unrecognised settlement date: ' + text)


def floor_to(t, step):
    return int(t) - int(t) % step


@lru_cache(maxsize=None)
def _bst_bounds(year):
    """UTC epochs when BST starts and ends in `year` (01:00 UTC on the last Sundays of March and October)."""
    start, end = _last_sunday(year, 3), _last_sunday(year, 10)
    return (int(datetime(start.year, start.month, start.day, 1, tzinfo=timezone.utc).timestamp()),
            int(datetime(end.year, end.month, end.day, 1, tzinfo=timezone.utc).timestamp()))


def uk_offset(t):
    """Seconds Europe/London is ahead of UTC at epoch t (BST starts and ends at 01:00 UTC)."""
    starts, ends = _bst_bounds(time.gmtime(t).tm_year)
    return 3600 if starts <= t < ends else 0


EPOCH_DAY = date(1970, 1, 1)


def uk_day(t):
    """The local (Europe/London) date at UTC epoch t: uk_clock's date, without formatting the time."""
    return EPOCH_DAY + timedelta(days=int((t + uk_offset(t)) // 86400))


def uk_clock(t):
    """(local date, 'HH:MM') in Europe/London for UTC epoch t."""
    local = int(t + uk_offset(t))
    minutes = local % 86400 // 60
    return EPOCH_DAY + timedelta(days=local // 86400), '%02d:%02d' % (minutes // 60, minutes % 60)


def local_epoch(day, hours, minutes):
    """UTC epoch of a Europe/London wall-clock time (the earlier one when ambiguous)."""
    base = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) + int(hours) * 3600 + int(minutes) * 60
    return base - 3600 if uk_offset(base - 3600) else base


def _easter(year):
    """Easter Sunday (the anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(year, month, (h + l - 7 * m + 114) % 31 + 1)


@lru_cache(maxsize=None)
def bank_holidays(year):
    """England and Wales bank holidays by the usual rules, with weekend substitutes.
    One-off holidays (a coronation, say) aren't included."""
    def first_monday(month):
        d = date(year, month, 1)
        return d + timedelta(days=(7 - d.weekday()) % 7)

    def last_monday(month):
        d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        return d - timedelta(days=d.weekday())

    days = set()
    new_year = date(year, 1, 1)
    days.add(new_year + timedelta(days={5: 2, 6: 1}.get(new_year.weekday(), 0)))
    easter = _easter(year)
    days.update({easter - timedelta(days=2), easter + timedelta(days=1)})
    # Early May is moved for VE Day anniversaries now and then; this keeps the usual rule
    days.update({first_monday(5), last_monday(5), last_monday(8)})
    christmas, boxing = date(year, 12, 25), date(year, 12, 26)
    if christmas.weekday() == 5:      # Saturday: Monday and Tuesday
        days.update({date(year, 12, 27), date(year, 12, 28)})
    elif christmas.weekday() == 6:    # Sunday: Monday (Boxing Day) and Tuesday
        days.update({boxing, date(year, 12, 27)})
    elif christmas.weekday() == 4:    # Friday: Boxing Day on Monday
        days.update({christmas, date(year, 12, 28)})
    else:
        days.update({christmas, boxing})
    return frozenset(days)
