"""Time helpers. Everything internal is UTC epoch seconds; settlement periods follow UK clock time."""

from datetime import date, datetime, timedelta, timezone


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


def _last_sunday(year, month):
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


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


def uk_offset(t):
    """Seconds Europe/London is ahead of UTC at epoch t (BST starts and ends at 01:00 UTC)."""
    year = datetime.fromtimestamp(t, timezone.utc).year
    start = _last_sunday(year, 3)
    end = _last_sunday(year, 10)
    starts = int(datetime(start.year, start.month, start.day, 1, tzinfo=timezone.utc).timestamp())
    ends = int(datetime(end.year, end.month, end.day, 1, tzinfo=timezone.utc).timestamp())
    return 3600 if starts <= t < ends else 0


def uk_clock(t):
    """(local date, 'HH:MM') in Europe/London for UTC epoch t."""
    local = datetime.fromtimestamp(t + uk_offset(t), timezone.utc)
    return local.date(), local.strftime('%H:%M')


def local_epoch(day, hours, minutes):
    """UTC epoch of a Europe/London wall-clock time (the earlier one when ambiguous)."""
    base = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) + int(hours) * 3600 + int(minutes) * 60
    return base - 3600 if uk_offset(base - 3600) else base
