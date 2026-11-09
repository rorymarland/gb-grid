"""Frequency: readings backfill, NESO's inertia estimates, the response held,
and spotting frequency events in the 15-second readings."""

import re
import statistics
import time
from datetime import date

from . import fetch
from .config import ELEXON, NESO, log
from .fetch import FetchError, as_float
from .store import UPSERT_FREQ, UPSERT_HH, UPSERT_RESP
from .timeutil import iso_minute, parse_time, settlement_start


# --------------------------------------------------------------------------
# Frequency response services, as NESO's auction results name them
# --------------------------------------------------------------------------

# code -> (service, direction). "Low" services push power in when frequency
# falls; "high" services take it out when frequency rises.
RESPONSE_PRODUCTS = {
    'DCL': ('dc', 'low'), 'DCH': ('dc', 'high'),
    'DML': ('dm', 'low'), 'DMH': ('dm', 'high'),
    'DRL': ('dr', 'low'), 'DRH': ('dr', 'high'),
    'PQR': ('qr', 'up'), 'NQR': ('qr', 'down'),
    'PSR': ('sr', 'up'), 'NSR': ('sr', 'down'),
}
LONG_NAMES = [
    (re.compile(r'dynamic\s+containment', re.I), 'DC'), (re.compile(r'dynamic\s+moderation', re.I), 'DM'),
    (re.compile(r'dynamic\s+regulation', re.I), 'DR'), (re.compile(r'quick\s+reserve', re.I), 'QR'),
    (re.compile(r'slow\s+reserve', re.I), 'SR'),
]


def product_code(name):
    """'DCL', 'Dynamic Containment LF', 'Positive Quick Reserve' -> 'DCL', 'DCL', 'PQR'."""
    text = str(name or '').strip()
    upper = text.upper().replace(' ', '')
    if upper in RESPONSE_PRODUCTS:
        return upper
    for pattern, stem in LONG_NAMES:
        if pattern.search(text):
            low = re.search(r'\b(LF|low|negative)\b', text, re.I) if stem in ('DC', 'DM', 'DR') else None
            high = re.search(r'\b(HF|high)\b', text, re.I) if stem in ('DC', 'DM', 'DR') else None
            if stem in ('DC', 'DM', 'DR'):
                return stem + ('L' if low else 'H' if high else '?')
            if re.search(r'\bpositive\b', text, re.I):
                return 'P' + stem
            if re.search(r'\bnegative\b', text, re.I):
                return 'N' + stem
    return None


def newest_resource(package, wanted=None):
    """The newest datastore resource of a NESO package, optionally by name."""
    result = fetch.get_json(NESO + '/api/3/action/package_show?id=' + package, timeout=60).get('result') or {}
    resources = [r for r in result.get('resources', []) if r.get('datastore_active')]
    if wanted:
        named = [r for r in resources if wanted(str(r.get('name') or ''))]
        resources = named or resources
    if not resources:
        raise FetchError('NESO %s has no datastore resources' % package)
    # Yearly files are named like "GB System Inertia - 2026-2027"; newest name last
    resources.sort(key=lambda r: (str(r.get('name') or ''), str(r.get('created') or '')))
    return resources[-1]


class FrequencyData:
    def __init__(self, store):
        self.store = store
        self.inertia_resource = None
        self.response_resource = None

    # ---- 15-second readings -------------------------------------------------

    def readings(self, start, end, timeout=60):
        url = ELEXON + '/system/frequency/stream?from=' + iso_minute(start) + '&to=' + iso_minute(end)
        items = fetch.get_json(url, timeout=timeout)
        if isinstance(items, dict) and isinstance(items.get('data'), list):
            items = items['data']
        if not isinstance(items, list):
            raise FetchError('Unexpected frequency response shape')
        rows = []
        for item in items:
            try:
                t = float(parse_time(item['measurementTime']))
                hz = float(item['frequency'])
            except (KeyError, TypeError, ValueError):
                continue
            if 45.0 < hz < 55.0:
                rows.append((t, hz))
        self.store.write(UPSERT_FREQ, rows, history=False)
        return len(rows)

    def backfill(self, days):
        """Fill missing days of readings, newest first, one day per request."""
        end = time.time()
        for _ in range(days):
            start = end - 86400
            have = self.store.scalar('SELECT COUNT(*) FROM freq WHERE t >= ? AND t < ?', (start, end)) or 0
            if have < 5000:   # a full day is 5,760 readings
                count = self.readings(start, end, timeout=120)
                log.info('[backfill] frequency %s to %s: %d readings', iso_minute(start), iso_minute(end), count)
                time.sleep(0.5)
            end = start

    # ---- NESO: system inertia, half-hourly, published after the day ---------

    def poll_inertia(self):
        resource = newest_resource('system-inertia', lambda n: 'inertia' in n.lower() and 'cost' not in n.lower())
        data = fetch.get_json(NESO + '/api/3/action/datastore_search?resource_id=%s&limit=700&sort=_id%%20desc'
                              % resource['id'], timeout=60).get('result') or {}
        rows = []
        for record in data.get('records') or []:
            keys = {k.lower().replace(' ', '').replace('_', ''): v for k, v in record.items()}
            try:
                day = str(keys.get('settlementdate') or '')[:10]
                period = int(keys.get('settlementperiod'))
            except (TypeError, ValueError):
                continue
            outturn, market = as_float(keys.get('outturninertia')), as_float(keys.get('marketprovidedinertia'))
            if not day or outturn is None:
                continue
            try:
                t = settlement_start(date.fromisoformat(day), period)
            except ValueError:
                continue
            rows.append(('inertia', t, outturn))
            if market is not None:
                rows.append(('inertia_mkt', t, market))
        if not rows:
            raise FetchError('The inertia data was empty')
        self.store.write(UPSERT_HH, rows, history=False)

    # ---- NESO: response and reserve held, from the daily auction results ---

    def poll_response(self):
        resource = newest_resource('eac-auction-results',
                                   lambda n: 'results summary' in n.lower() and 'archive' not in n.lower() and 'daily' not in n.lower())
        data = fetch.get_json(NESO + '/api/3/action/datastore_search?resource_id=%s&limit=1500&sort=deliveryStart%%20desc'
                              % resource['id'], timeout=90).get('result') or {}
        totals = {}
        for record in data.get('records') or []:
            code = product_code(record.get('auctionProduct'))
            if code not in RESPONSE_PRODUCTS:
                continue
            try:
                start, end = parse_time(record['deliveryStart']), parse_time(record['deliveryEnd'])
            except (KeyError, TypeError, ValueError):
                continue
            mw, price = as_float(record.get('clearedVolume')), as_float(record.get('clearingPrice'))
            # Quick and slow reserve windows can repeat across linked auctions: add volumes, keep the last price
            entry = totals.setdefault((start, code), [end, 0.0, price])
            entry[0] = max(entry[0], end)
            entry[1] += mw or 0.0
            if price is not None:
                entry[2] = price
        if not totals:
            raise FetchError('The response auction results were empty')
        rows = [(start, end, code, round(mw, 1), price) for (start, code), (end, mw, price) in totals.items()]
        self.store.write(UPSERT_RESP, rows, history=False)

    def poll_neso(self):
        errors = []
        for name, step in (('inertia', self.poll_inertia), ('response', self.poll_response)):
            try:
                step()
            except FetchError as error:
                errors.append(name + ': ' + str(error))
        if errors:
            raise FetchError('; '.join(errors))


def response_at(store, t):
    """MW of each service held at time t: {'DCL': 1100, ...}."""
    held = {}
    for code, mw in store.query('SELECT product, mw FROM resp WHERE start <= ? AND end > ?', (t, t)):
        held[code] = round(mw or 0)
    return held


def inertia_at(store, t):
    period = int(t // 1800) * 1800
    v = store.scalar("SELECT v FROM hh WHERE k = 'inertia' AND t = ?", (period,))
    return None if v is None else round(v)


# --------------------------------------------------------------------------
# Frequency events in 15-second readings
#
# A large generator or interconnector tripping pulls frequency down within a
# few seconds; response services and the remaining generators' governors then
# arrest the fall and pull it back over the next minute or two. At 15-second
# resolution the true lowest point (the nadir) is usually missed, so these are
# the lowest 15-second readings, not the nadir NESO would quote.
# --------------------------------------------------------------------------

STEP_HZ = 0.08          # a change at least this big within two readings (30 s)
LIMIT_LOW, LIMIT_HIGH = 49.8, 50.2   # operational limits
QUIET = 600             # one event per 10 minutes


def find_events(readings):
    """readings: [(t, hz)] sorted. Returns a list of event dicts."""
    events = []
    n = len(readings)
    last_start = -1e18
    i = 2
    while i < n:
        t, hz = readings[i]
        if t - readings[i - 2][0] > 50:     # a gap in the data
            i += 1
            continue
        prev = readings[i - 2:i]
        fall = hz - max(p[1] for p in prev)
        rise = hz - min(p[1] for p in prev)
        crossed_low = hz < LIMIT_LOW <= prev[-1][1]
        crossed_high = hz > LIMIT_HIGH >= prev[-1][1]
        direction = None
        if fall <= -STEP_HZ or crossed_low:
            direction = 'low'
        elif rise >= STEP_HZ or crossed_high:
            direction = 'high'
        if direction is None or t - last_start < QUIET:
            i += 1
            continue
        event = describe_event(readings, i, direction)
        if event:
            events.append(event)
            last_start = event['start']
            # skip past the recovery
            while i < n and readings[i][0] < event['start'] + QUIET:
                i += 1
            continue
        i += 1
    return events


def describe_event(readings, i, direction):
    t_trigger = readings[i][0]
    before = [hz for t, hz in readings if t_trigger - 180 <= t <= t_trigger - 45]
    if len(before) < 4:
        return None
    pre = statistics.median(before)
    sign = -1 if direction == 'low' else 1
    window = [(t, hz) for t, hz in readings if t_trigger - 45 <= t <= t_trigger + 300]
    peak_t, peak_hz = (min if sign < 0 else max)(window, key=lambda p: p[1])
    change = peak_hz - pre
    if abs(change) < 0.06:
        return None
    # When the excursion began: the reading before the first one clearly past the starting level
    start = t_trigger - 15
    previous = None
    for t, hz in readings:
        if t < t_trigger - 60:
            continue
        if t > peak_t:
            break
        if (hz - pre) * sign > 0.03 and previous is not None:
            start = previous
            break
        previous = t
    # Recovered: back within 0.05 Hz of the starting level, or of 50 Hz
    recovered = None
    for t, hz in readings:
        if t <= peak_t:
            continue
        if t > peak_t + 1800:
            break
        if abs(hz - pre) <= 0.05 or abs(hz - 50.0) <= 0.05:
            recovered = t
            break
    level = 3 if (peak_hz <= 49.7 or peak_hz >= 50.3 or abs(change) >= 0.2) else 2
    return {'start': int(start), 'direction': direction, 'pre': round(pre, 3), 'peak': round(peak_hz, 3), 'peak_t': int(peak_t),
            'change': round(change, 3), 'to_peak': int(peak_t - start), 'recovery': None if recovered is None else int(recovered - start),
            'outside': peak_hz < LIMIT_LOW or peak_hz > LIMIT_HIGH, 'level': level}


def frequency_stats(readings):
    """Spread and time outside the limits for a list of (t, hz)."""
    if not readings:
        return None
    values = [hz for _, hz in readings]
    n = len(values)
    mean = sum(values) / n
    sd = (sum((v - mean) ** 2 for v in values) / n) ** 0.5
    lo = min(readings, key=lambda p: p[1])
    hi = max(readings, key=lambda p: p[1])
    return {
        'samples': n, 'mean': round(mean, 4), 'sd_mhz': round(sd * 1000, 1),
        'within_005': round(100.0 * sum(1 for v in values if abs(v - 50) <= 0.05) / n, 1),
        'outside_01': round(100.0 * sum(1 for v in values if abs(v - 50) > 0.1) / n, 2),
        'outside_02_s': 15 * sum(1 for v in values if abs(v - 50) > 0.2),
        'min': {'t': int(lo[0]), 'hz': round(lo[1], 3)}, 'max': {'t': int(hi[0]), 'hz': round(hi[1], 3)},
    }


def histogram(readings, low=49.7, high=50.3, width=0.01):
    bins = int(round((high - low) / width))
    counts = [0] * bins
    for _, hz in readings:
        k = int((hz - low) / width)
        counts[min(max(k, 0), bins - 1)] += 1
    return {'from': low, 'width': width, 'counts': counts}
