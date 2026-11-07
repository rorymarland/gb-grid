"""Grid notices (NESO system warnings) in plain English."""

import re
import time
from datetime import date, timedelta

from .timeutil import local_epoch, uk_clock


# --------------------------------------------------------------------------
# Grid notices: NESO's system warnings in plain English
# --------------------------------------------------------------------------

# Warning type -> (title, level, what it means). "strong" notices get a red banner.
NOTICE_KINDS = {
    'ELECTRICITY MARGIN NOTICE': ('Electricity Margin Notice', 'warning',
                                  'Spare generating capacity is forecast to fall below the margin NESO needs'),
    'CAPACITY MARKET NOTICE': ('Capacity Market Notice', 'warning',
                               'Spare capacity is forecast to fall below the Capacity Market threshold'),
    'HIGH RISK OF DEMAND REDUCTION': ('High risk of demand reduction', 'strong',
                                      'There is a high risk that some demand will have to be disconnected'),
    'DEMAND CONTROL IMMINENT': ('Demand control imminent', 'strong',
                                'NESO expects to order controlled disconnections of demand'),
    'NEGATIVE RESERVE ACTIVE POWER MARGIN': ('Negative reserve warning', 'warning',
                                             'NESO may not have enough room to turn generation down'),
    'DEMAND FLEXIBILITY SERVICE': ('Demand Flexibility Service', 'warning',
                                   'Households and businesses are being paid to use less electricity'),
}
# Routine messages that never need a banner
ROUTINE_NOTICES = ('SO-SO TRADES', 'IT SYSTEMS OUTAGE')
NOTICE_PERIOD = re.compile(r'from\s+(\d{1,2})[:.]?(\d{2})\s*(?:hrs?)?\s+to\s+(\d{1,2})[:.]?(\d{2})\s*(?:hrs?)?\s+on\s+'
                           r'(?:[A-Za-z]+\s+)?(\d{1,2})/(\d{1,2})/(\d{4})', re.I)
NOTICE_BETWEEN = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})\s+between\s+(\d{1,2}):(\d{2})\s+and\s+(\d{1,2}):(\d{2})', re.I)
NOTICE_COMMENCE = re.compile(r'Commencement time of notice\s*:\s*(\d{1,2}):(\d{2})\s+on\s+(\d{1,2})/(\d{1,2})/(\d{4})', re.I)
NOTICE_SHORTFALL = re.compile(r'shortfall\s+(\d[\d,]*)\s*MW', re.I)
NOTICE_REGION = re.compile(r'(?:regions?:\s*|\bFor\s+)([A-Za-z][A-Za-z ]{2,40}?)\s+region', re.I)


def describe_notice(t, kind, body, now=None):
    """Plain-English summary of one system warning, with its period if stated."""
    now = time.time() if now is None else now
    title, level, meaning = NOTICE_KINDS.get(kind, (kind.title(), 'warning', None))
    text = body or ''
    start = end = None
    match = NOTICE_PERIOD.search(text)
    if match:
        h0, m0, h1, m1, dd, mm, yyyy = (int(g) for g in match.groups())
        try:
            day = date(yyyy, mm, dd)
            start, end = local_epoch(day, h0, m0), local_epoch(day, h1, m1)
            if end <= start:
                end = local_epoch(day + timedelta(days=1), h1, m1)
        except ValueError:
            start = end = None
    else:
        match = NOTICE_BETWEEN.search(text)
        if match:
            dd, mm, yyyy, h0, m0, h1, m1 = (int(g) for g in match.groups())
            try:
                day = date(yyyy, mm, dd)
                start, end = local_epoch(day, h0, m0), local_epoch(day, h1, m1)
            except ValueError:
                start = end = None
        else:
            match = NOTICE_COMMENCE.search(text)
            if match:
                h0, m0, dd, mm, yyyy = (int(g) for g in match.groups())
                try:
                    start = local_epoch(date(yyyy, mm, dd), h0, m0)
                except ValueError:
                    start = None
    upper = text.upper()
    cancelled = 'CANCELLATION' in upper or 'HAS BEEN CANCELLED' in upper or 'NOTICE CANCELLED' in upper
    if meaning is None:
        first = next((line.strip() for line in text.split('\n')
                      if line.strip() and not line.strip().upper().startswith('FROM')), '')
        meaning = first[:160].rstrip('.') or title
    phrase = meaning
    region = NOTICE_REGION.search(text) if kind == 'NEGATIVE RESERVE ACTIVE POWER MARGIN' else None
    if region:
        phrase += ' in the ' + ' '.join(region.group(1).split()).title().replace(' To ', ' to ') + ' area'
    today = uk_clock(now)[0]
    if start is not None:
        start_day, start_clock = uk_clock(start)
        when = '' if start_day == today else ' tomorrow' if start_day == today + timedelta(days=1) \
            else ' on ' + start_day.strftime('%a ') + str(start_day.day) + start_day.strftime(' %b')
        phrase += (' from ' + start_clock + (' to ' + uk_clock(end)[1] if end else '') + when)
    shortfall = NOTICE_SHORTFALL.search(text)
    if shortfall:
        phrase += ', with a shortfall of ' + shortfall.group(1) + ' MW'
    return {
        'id': kind[:24] + '-' + str(int(t)), 'kind': kind, 'title': title, 'level': level,
        'summary': phrase + '.', 'text': text, 'published': int(t), 'start': start, 'end': end,
        'cancelled': cancelled, 'routine': kind in ROUTINE_NOTICES,
    }
