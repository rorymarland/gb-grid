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
asks once per zone per poll, a second apart. Great Britain left ENTSO-E's data in 2021, so it
covers the neighbours only."""

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


def complete_intensities(generation):
    """[(t, g/kWh)] for the periods where enough of the zone's production types have reported:
    the latest period often arrives a type at a time, which would skew it."""
    kinds = set()
    for mix in generation.values():
        kinds |= set(mix)
    need = COMPLETE * len(kinds)
    rows = []
    for t, mix in sorted(generation.items()):
        if len(mix) >= need:
            v = intensity(mix)
            if v is not None:
                rows.append((t, round(v, 1)))
    return rows


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

    def poll(self):
        if not self.enabled:
            return
        now = time.time()
        end = now - now % 900 + 900
        errors, stored = [], 0
        for i, (country, (zone, eic)) in enumerate(ZONES.items()):
            last = self.store.scalar('SELECT MAX(t) FROM ext_ci WHERE zone = ?', (zone,))
            start = now - BACKFILL if last is None or last < now - BACKFILL else last - 3 * 3600
            start -= start % 900
            if i:
                time.sleep(SPACING)
            try:
                rows = complete_intensities(self.fetch_zone(eic, start, end))
            except FetchError as error:
                errors.append('%s: %s' % (country, error))
                if 'security token' in str(error):
                    break               # no point asking again with the same token
                continue
            if rows:
                self.store.write('INSERT OR REPLACE INTO ext_ci (zone, t, v) VALUES (?, ?, ?)', [(zone, t, v) for t, v in rows], history=False)
                stored += len(rows)
        if errors and (len(errors) == len(ZONES) or 'security token' in errors[-1]):
            raise FetchError('; '.join(errors))
        if errors:
            log.warning('[entsoe] %s', '; '.join(errors))
        return stored


def test_entsoe():
    """Check the token and each neighbour's last few hours, with one request a zone. Prints no token."""
    job = Entsoe(None)
    if not job.enabled:
        return 'Set ENTSOE_API_KEY (or ENTSOE_API_KEY_FILE) first.'
    now = time.time()
    failed = 0
    for i, (country, (zone, eic)) in enumerate(ZONES.items()):
        if i:
            time.sleep(SPACING)
        try:
            generation = job.fetch_zone(eic, now - 6 * 3600, now)
        except FetchError as error:
            print('%s: %s' % (country, error))
            if 'security token' in str(error):
                return 'ENTSO-E refused the token.'
            failed += 1
            continue
        rows = complete_intensities(generation)
        if not rows:
            print('%s: no figures in the last six hours yet' % country)
            continue
        t, v = rows[-1]
        mix = generation[t]
        total = sum(mw for mw in mix.values() if mw > 0)
        top = sorted(((mw, kind) for kind, mw in mix.items() if mw > 0), reverse=True)[:3]
        print('%s: %d g/kWh at %s UTC, from %d periods; biggest: %s' % (
            country, round(v), datetime.fromtimestamp(t, timezone.utc).strftime('%H:%M'), len(rows),
            ', '.join('%s %d%%' % (NAMES.get(kind, kind), round(100 * mw / total)) for mw, kind in top)))
    return 1 if failed == len(ZONES) else 0
