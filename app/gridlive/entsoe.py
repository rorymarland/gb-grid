"""Britain's neighbours' generation mix from the ENTSO-E Transparency Platform, turned into each
one's carbon intensity. Optional: it needs a free security token (ENTSOE_API_KEY), which ENTSO-E
gives on request to a registered user.

  GET https://web-api.tp.entsoe.eu/api?securityToken=...&documentType=A75&processType=A16
      &in_Domain=<bidding zone EIC>&periodStart=YYYYMMDDHHMM&periodEnd=YYYYMMDDHHMM   (UTC)

A75/A16 is "actual generation per production type": an XML GL_MarketDocument with a TimeSeries
for each production type (psrType), in MW, every 15, 30 or 60 minutes depending on the zone.
TimeSeries that carry an outBiddingZone_Domain instead of an inBiddingZone_Domain are
consumption (pumping water uphill, charging batteries) and are left out. Since 2023 some zones
send curve type A03, where a run of equal values is sent once, so a missing position repeats the
one before it; with curve type A01 a missing position is simply missing. No data gives an Acknowledgement_MarketDocument with reason code 999.

ENTSO-E allows 400 requests a minute per token and bans a token for a while beyond that; this
asks once per zone per poll, a second apart. Great Britain's figures stopped there in June
2021, so it covers the neighbours only."""

import json
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from . import fetch
from .config import ENTSOE, ENTSOE_API_KEY, log
from .fetch import FetchError
from .reference import ENTSOE_FACTORS
from .timeutil import parse_time

# Country -> (our zone key, ENTSO-E bidding zone EIC code)
ZONES = {
    'France': ('E:FR', '10YFR-RTE------C'),
    'Netherlands': ('E:NL', '10YNL----------L'),
    'Belgium': ('E:BE', '10YBE----------2'),
    'Norway': ('E:NO2', '10YNO-2--------T'),        # southern Norway, where North Sea Link lands
    'Denmark': ('E:DK1', '10YDK-1--------W'),        # western Denmark, where Viking Link lands
    'Ireland': ('E:IE', '10Y1001A1001A59C'),          # the all-island market, Ireland and Northern Ireland
}
BACKFILL = 7 * 86400
SPACING = 1.0               # seconds between requests
COMPLETE = 0.8              # a period counts once this share of the zone's production types have reported
KNOWN_DAYS = 7              # how long a zone's production types and daily peaks are remembered
MIN_SHARE = 0.2             # a period's total must be at least this share of the zone's usual daily peak
RULES = '2'                 # bump to throw away stored figures made under older rules
RESOLUTIONS = {'PT15M': 900, 'PT30M': 1800, 'PT60M': 3600}
NAMES = {'B01': 'biomass', 'B02': 'lignite', 'B03': 'gas from coal', 'B04': 'gas', 'B05': 'coal', 'B06': 'oil', 'B07': 'oil shale',
         'B08': 'peat', 'B09': 'geothermal', 'B10': 'pumped storage', 'B11': 'run-of-river hydro', 'B12': 'reservoir hydro',
         'B13': 'marine', 'B14': 'nuclear', 'B15': 'other renewable', 'B16': 'solar', 'B17': 'waste', 'B18': 'offshore wind',
         'B19': 'onshore wind', 'B20': 'other', 'B25': 'storage'}


def local(tag):
    return tag.rsplit('}', 1)[-1]


def child(node, name):
    for c in node:
        if local(c.tag) == name:
            return c
    return None


def children(node, name):
    return [c for c in node if local(c.tag) == name]


def text(node, name):
    c = child(node, name)
    return c.text.strip() if c is not None and c.text else None


def stamp(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y%m%d%H%M')


def parse_generation(body):
    """{t: {psrType: MW}} from an A75 document; {} for ENTSO-E's no-data acknowledgement."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise FetchError('ENTSO-E sent something that isn\'t XML')
    if local(root.tag) == 'Acknowledgement_MarketDocument':
        reason = child(root, 'Reason')
        code, why = (text(reason, 'code'), text(reason, 'text')) if reason is not None else (None, None)
        if code == '999':
            return {}
        raise FetchError('ENTSO-E: %s' % (why or code or 'refused the request'))
    if local(root.tag) != 'GL_MarketDocument':
        raise FetchError('Unexpected document from ENTSO-E: %s' % local(root.tag))
    out = {}
    for series in children(root, 'TimeSeries'):
        if child(series, 'inBiddingZone_Domain.mRID') is None:
            continue                                # consumption: pumping, charging
        # A03: a point stands until the next one (or the period's end); A01: every point is sent
        blocks = text(series, 'curveType') == 'A03'
        psr = child(series, 'MktPSRType')
        kind = text(psr, 'psrType') if psr is not None else None
        if not kind:
            continue
        for period in children(series, 'Period'):
            interval = child(period, 'timeInterval')
            step = RESOLUTIONS.get(text(period, 'resolution') or '')
            if interval is None or not step:
                continue
            try:
                start, end = parse_time(text(interval, 'start')), parse_time(text(interval, 'end'))
            except (TypeError, ValueError):
                continue
            points = {}
            for p in children(period, 'Point'):
                try:
                    points[int(text(p, 'position'))] = float(text(p, 'quantity'))
                except (TypeError, ValueError):
                    continue
            last = None
            for position in range(1, (end - start) // step + 1):
                value = points.get(position, last if blocks else None)
                last = value
                if value is not None:
                    t = start + (position - 1) * step
                    out.setdefault(t, {})[kind] = out.get(t, {}).get(kind, 0.0) + value
    return out


def intensity(mix, factors=None):
    """g/kWh of a generation mix {psrType: MW}, by ENTSOE_FACTORS, or None with nothing generating.
    Unknown types count at NESO's factor for 'other'."""
    factors = factors or ENTSOE_FACTORS
    total = sum(mw for mw in mix.values() if mw > 0)
    if total <= 0:
        return None
    return sum(mw * factors.get(kind, factors['B20']) for kind, mw in mix.items() if mw > 0) / total


def day_of(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%d')


def complete_intensities(generation, memory=None, now=None):
    """([(t, g/kWh)], memory) for the periods that look whole. The latest periods often arrive a
    production type at a time, and some zones (Ireland's, often) send only a few types for hours,
    so a period counts only if:
      - at least COMPLETE of the types the zone has reported over the last KNOWN_DAYS have
        reported for it, and
      - its total is at least MIN_SHARE of the zone's usual daily peak (the second highest of the
        last KNOWN_DAYS days' peaks, so one odd day doesn't set it).
    `memory` is what was learnt before, {'types': {psrType: last seen}, 'days': {day: peak MW}};
    the one returned includes this request's periods, trimmed to KNOWN_DAYS."""
    memory = memory or {}
    types = dict(memory.get('types') or {})
    days = dict(memory.get('days') or {})
    for t, mix in generation.items():
        for kind in mix:
            types[kind] = max(types.get(kind, 0), t)
    latest = max([now or 0] + list(generation) + list(types.values()))
    types = {k: v for k, v in types.items() if v > latest - KNOWN_DAYS * 86400}
    need = COMPLETE * len(types)
    totals = {t: sum(mw for mw in mix.values() if mw > 0) for t, mix in generation.items()}
    typed = sorted(t for t, mix in generation.items() if len(mix) >= need and totals[t] > 0)
    for t in typed:
        days[day_of(t)] = max(days.get(day_of(t), 0.0), round(totals[t]))
    cutoff = day_of(latest - KNOWN_DAYS * 86400)
    days = {d: v for d, v in days.items() if d >= cutoff}
    peaks = sorted(days.values(), reverse=True)
    usual = peaks[1] if len(peaks) > 1 else (peaks[0] if peaks else 0.0)
    rows = []
    for t in typed:
        if totals[t] >= MIN_SHARE * usual:
            v = intensity(generation[t])
            if v is not None:
                rows.append((t, round(v, 1)))
    return rows, {'types': types, 'days': days, 'usual': usual}


def why_not(generation, t, memory):
    """Why period t didn't count, in words, for --test-entsoe."""
    mix = generation.get(t, {})
    known = memory.get('types') or {}
    missing = sorted(set(known) - set(mix))
    total = sum(mw for mw in mix.values() if mw > 0)
    if len(mix) < COMPLETE * len(known):
        return 'only %d of its %d usual types had reported (missing %s)' % (len(mix), len(known), ', '.join(NAMES.get(k, k) for k in missing[:6]))
    return 'its total, %d MW, is under %d%% of the usual daily peak of %d MW' % (total, MIN_SHARE * 100, memory.get('usual') or 0)


class Entsoe:
    def __init__(self, store, key=None):
        self.store = store
        self.key = ENTSOE_API_KEY if key is None else key

    @property
    def enabled(self):
        return bool(self.key)

    def fetch_zone(self, eic, start, end):
        query = {'securityToken': self.key, 'documentType': 'A75', 'processType': 'A16', 'in_Domain': eic,
                 'periodStart': stamp(start), 'periodEnd': stamp(end)}
        try:
            _, body, _ = fetch.http_get(ENTSOE + '?' + urllib.parse.urlencode(query), accept='application/xml', timeout=90)
        except FetchError as error:
            message = str(error)
            # Never let the token reach a log or the status page
            message = message.replace(self.key, '***') if self.key else message
            if 'HTTP 401' in message:
                raise FetchError('ENTSO-E refused the security token: check ENTSOE_API_KEY')
            if 'HTTP 429' in message:
                raise FetchError('ENTSO-E says too many requests; trying again next time')
            if 'HTTP 400' in message and 'No matching data' in message:
                return {}
            raise FetchError(message)
        return parse_generation(body)

    def memory(self, zone):
        try:
            return json.loads(self.store.get_meta('entsoe_zone:' + zone) or '{}')
        except ValueError:
            return {}

    def poll(self):
        if not self.enabled:
            return
        now = time.time()
        end = now - now % 900 + 900
        if self.store.get_meta('entsoe_rules') != RULES:
            # Figures from before the checks above could include half-reported periods: fetch the week again
            self.store.write("DELETE FROM ext_ci WHERE zone LIKE 'E:%'", [()], history=False)
            for _, (zone, _) in ZONES.items():
                self.store.set_meta('entsoe_zone:' + zone, '{}')
            self.store.set_meta('entsoe_rules', RULES)
        errors, stored = [], 0
        for i, (country, (zone, eic)) in enumerate(ZONES.items()):
            last = self.store.scalar('SELECT MAX(t) FROM ext_ci WHERE zone = ?', (zone,))
            start = now - BACKFILL if last is None or last < now - BACKFILL else last - 3 * 3600
            start -= start % 900
            if i:
                time.sleep(SPACING)
            try:
                rows, learnt = complete_intensities(self.fetch_zone(eic, start, end), self.memory(zone), now)
            except FetchError as error:
                errors.append('%s: %s' % (country, error))
                if 'security token' in str(error):
                    break               # no point asking again with the same token
                continue
            self.store.set_meta('entsoe_zone:' + zone, json.dumps({'types': learnt['types'], 'days': learnt['days']}))
            if rows:
                self.store.write('INSERT OR REPLACE INTO ext_ci (zone, t, v) VALUES (?, ?, ?)', [(zone, t, v) for t, v in rows], history=False)
                stored += len(rows)
        if errors and (len(errors) == len(ZONES) or 'security token' in errors[-1]):
            raise FetchError('; '.join(errors))
        if errors:
            log.warning('[entsoe] %s', '; '.join(errors))
        return stored


def test_entsoe():
    """Check the token and each neighbour's last three days, one request a zone, and show its latest
    period that counts, or why the latest it has doesn't. Prints no token."""
    job = Entsoe(None)
    if not job.enabled:
        return 'Set ENTSOE_API_KEY (or ENTSOE_API_KEY_FILE) first.'
    now = time.time()
    failed = 0
    for i, (country, (zone, eic)) in enumerate(ZONES.items()):
        if i:
            time.sleep(SPACING)
        try:
            generation = job.fetch_zone(eic, now - 3 * 86400, now)
        except FetchError as error:
            print('%s: %s' % (country, error))
            if 'security token' in str(error):
                return 'ENTSO-E refused the token.'
            failed += 1
            continue
        if not generation:
            print('%s: ENTSO-E has nothing for the last three days' % country)
            continue
        rows, memory = complete_intensities(generation, None, now)
        newest = max(generation)
        hhmm = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime('%a %H:%M')  # noqa: E731
        if not rows:
            print('%s: %d periods to %s UTC, none whole: the latest because %s' % (country, len(generation), hhmm(newest), why_not(generation, newest, memory)))
            continue
        t, v = rows[-1]
        mix = generation[t]
        total = sum(mw for mw in mix.values() if mw > 0)
        top = sorted(((mw, kind) for kind, mw in mix.items() if mw > 0), reverse=True)[:3]
        line = '%s: %d g/kWh at %s UTC (%d MW from %d types); biggest: %s' % (
            country, round(v), hhmm(t), total, len(mix),
            ', '.join('%s %d%%' % (NAMES.get(kind, kind), round(100 * mw / total)) for mw, kind in top))
        if newest > t:
            line += '. Later periods to %s UTC not yet whole: the latest because %s' % (hhmm(newest), why_not(generation, newest, memory))
        print(line)
    return 1 if failed == len(ZONES) else 0
