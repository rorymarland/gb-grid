"""Britain's gas: where it comes from and goes, linepack and price, from National Gas's open
operational data (no key; https://api.nationalgas.com/operationaldata/v1).

  /instantaneousflow/sites   the last ten minutes of flows (mcm/day) at every entry point, by
                             category of use, and linepack (mcm), every two minutes: polled every
                             five minutes and kept here, so the gas day can be charted
  /publications/gasday       daily figures by publication ID: allocations by use and source (kWh,
                             two days behind), closing linepack, the System Average Price, the
                             demand forecast and the seasonal normal temperature

National Gas blocks clients that ask too often (16 requests in 15 seconds were refused with 429),
so requests are spaced out, and a refusal stops requests for half an hour."""

import json
import time
from datetime import timedelta

from . import fetch
from .config import GAS_CALORIFIC_VALUE, NATIONAL_GAS, log
from .fetch import FetchError, as_float
from .timeutil import local_epoch, parse_time, uk_clock

API = NATIONAL_GAS
FLOW_EVERY = 300            # live flows: the API keeps ten minutes of two-minute readings
DAILY_EVERY = 3 * 3600      # daily figures
BACKFILL_DAYS = 366
SPACING = 20                # seconds between backfill requests
BLOCKED_FOR = 1800          # after a 429
KEEP_FLOWS = 3 * 86400

# Entry points by where the gas comes from. St Fergus also lands Norwegian gas (through Vesterled),
# so 'north sea' is UK fields and some of Norway's; Langeled brings Norwegian gas to Easington.
SOURCES = {
    'norway': ('EASINGTON LANGELED',),
    'lng': ('GRAIN NTS 1', 'GRAIN NTS 2', 'MILFORD HAVEN - DRAGON', 'MILFORD HAVEN - SOUTH HOOK'),
    'northsea': ('ST FERGUS MOBIL', 'ST FERGUS NSMP', 'ST FERGUS SHELL', 'TEESSIDE CATS', 'TEESSIDE PX', 'BACTON PERENCO',
                 'BACTON SEAL', 'BACTON SHELL', 'EASINGTON DIMLINGTON', 'THEDDLETHORPE', 'BARROW'),
    'continent': ('BACTON BBL', 'BACTON IC'),
    'storage': ('ALDBROUGH', 'EASINGTON ROUGH ST', 'HILLTOP', 'HOLE HOUSE FARM', 'HOLFORD', 'HORNSEA', 'STUBLACH'),
}
SOURCE_ORDER = ['norway', 'northsea', 'lng', 'continent', 'storage', 'other']
# Uses, by words in National Gas's category names
USES = (('ldz', 'ldz'), ('power', 'power station'), ('industrial', 'industrial'), ('storage', 'storage'), ('exports', 'export'))
USE_ORDER = ['ldz', 'power', 'industrial', 'storage', 'exports']

# Daily publications: our key for each ID
DAILY = {
    'PUBOBJ1289': 'ndm',            # Allocations, NDM exit total, D+2 (kWh): homes and small businesses
    'PUBOBJ1288': 'dmc',            # Allocations, DMC (LDZ) total, D+2: larger users on local networks
    'PUBOBJ1287': 'industrial',     # Allocations, NTS industrial total, D+2
    'PUBOBJ1286': 'power',          # Allocations, NTS power stations total, D+2
    'PUBOBJ521': 'storage_in',      # Allocations, storage exit total, D+2 (gas into storage)
    'PUBOBJ519': 'exports',         # Allocations, interconnector exit total, D+2
    'PUBOBJ518': 'lng',             # Allocations, LNG importation total, D+2
    'PUBOBJ515': 'imports',         # Allocations, interconnector entry total, D+2
    'PUBOBJ517': 'storage_out',     # Allocations, storage entry total, D+2 (gas out of storage)
    'PUBOB694': 'linepack',         # Closing linepack, actual (mcm)
    'PUBOB603': 'sap',              # System Average Price, actual day (p/kWh)
    'PUBOB623': 'demand_fc',        # Demand forecast, NTS (mcm)
    'PUBOBJ510': 'temp_normal',     # Temperature, seasonal normal, D (°C)
    'PUBOB30': 'pclp',              # Predicted closing linepack (mcm), hourly: the latest
}
ENERGY = ('ndm', 'dmc', 'industrial', 'power', 'storage_in', 'exports', 'lng', 'imports', 'storage_out')   # kWh


def gas_day(t):
    """The gas day (05:00 to 05:00 UK time) that UTC epoch t falls in."""
    day, clock = uk_clock(t)
    return day if clock >= '05:00' else day - timedelta(days=1)


def gas_day_start(day):
    return local_epoch(day, 5, 0)


def mcmd_to_gw(mcmd):
    """Million cubic metres a day to gigawatts, at the calorific value for gas meters (MJ/m³)."""
    return mcmd * 1e6 * GAS_CALORIFIC_VALUE * 1e6 / 86400 / 1e9


def source_of(site):
    name = site.upper()
    for key, sites in SOURCES.items():
        if name in sites:
            return key
    return 'other'


def use_of(name):
    low = name.lower()
    for key, word in USES:
        if word in low:
            return key
    return None


class Gas:
    def __init__(self, store):
        self.store = store
        self.blocked_until = 0
        self.daily_checked = 0
        self.version = 0

    def _request(self, path, body=None):
        now = time.time()
        if now < self.blocked_until:
            raise FetchError('National Gas asked us to slow down; trying again after %s' % uk_clock(self.blocked_until)[1])
        try:
            _, raw, _ = fetch.http_get(API + path, data=None if body is None else json.dumps(body).encode(),
                                       extra_headers=None if body is None else {'Content-Type': 'application/json'}, timeout=90)
        except FetchError as error:
            if 'HTTP 429' in str(error):
                self.blocked_until = now + BLOCKED_FOR
                log.warning('[gas] National Gas refused a request as too many; waiting half an hour')
            raise
        try:
            return json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise FetchError('Invalid JSON from api.nationalgas.com')

    # ---- live flows ------------------------------------------------------------

    def fetch_flows(self):
        """The last ten minutes of two-minute flows. Returns the readings stored."""
        payload = self._request('/instantaneousflow/sites')
        groups = payload.get('instantaneousFlow') if isinstance(payload, dict) else None
        if not isinstance(groups, list):
            raise FetchError('Unexpected response shape from National Gas')
        at = {}

        def add(t, key, value):
            at.setdefault(t, {})[key] = value
        for group in groups:
            kind = str(group.get('description') or '').lower()
            for site in group.get('sites') or []:
                name = str(site.get('siteName') or '').strip()
                for point in site.get('siteGasDetail') or []:
                    try:
                        t = parse_time(point['applicableAt'])
                    except (KeyError, TypeError, ValueError):
                        continue
                    v = as_float(point.get('flowRate'))
                    if v is None:
                        continue
                    if kind.startswith('supply from entry'):
                        add(t, 'site:' + name, v)
                    elif kind == 'total supply':
                        add(t, 'supply', v)
                    elif kind.startswith('demand by category') and use_of(name):
                        add(t, 'use:' + use_of(name), v)
                    elif kind == 'total demand':
                        add(t, 'demand', v)
                    elif 'linepack' in kind:
                        add(t, 'linepack', v)
        rows = []
        for t, values in at.items():
            # Sources: entry points added up by where the gas comes from; anything unaccounted for
            # (a new site, or a terminal not listed by entry point) counts as 'other'
            sources = {}
            for key, v in values.items():
                if key.startswith('site:'):
                    src = source_of(key[5:])
                    sources[src] = sources.get(src, 0.0) + v
            if 'supply' in values and sources:
                rest = values['supply'] - sum(sources.values())
                if rest > 0.5:
                    sources['other'] = sources.get('other', 0.0) + rest
            for src, v in sources.items():
                values['src:' + src] = v
            rows += [(t, k, round(v, 3)) for k, v in values.items()]
        self.store.write('INSERT OR REPLACE INTO gas_flow (t, k, v) VALUES (?, ?, ?)', rows, history=False)
        return len(at)

    # ---- daily figures -----------------------------------------------------------

    def fetch_daily(self, first, last, ids):
        """Daily publications between two gas days. Returns values stored."""
        payload = self._request('/publications/gasday', {'fromDate': first.isoformat(), 'toDate': last.isoformat(),
                                                         'publicationIds': list(ids), 'latestValue': 'Y'})
        if not isinstance(payload, list):
            raise FetchError('Unexpected response shape from National Gas publications')
        rows = []
        for item in payload:
            key = DAILY.get(item.get('publicationId'))
            if not key:
                continue
            latest = {}
            for p in item.get('publications') or []:
                v = as_float(p.get('value'))
                d = str(p.get('applicableFor') or '')[:10]
                if v is None or not d:
                    continue
                stamp = str(p.get('applicableAt') or '')
                if d not in latest or stamp >= latest[d][0]:
                    latest[d] = (stamp, v)
            rows += [(d, key, round(v, 4)) for d, (_, v) in latest.items()]
        self.store.write('INSERT OR REPLACE INTO gas_daily (d, k, v) VALUES (?, ?, ?)', rows, history=False)
        return len(rows)

    def backfilled(self):
        done = set((self.store.scalar("SELECT v FROM meta WHERE k = 'gas_backfilled'") or '').split(','))
        return all(pid in done for pid in DAILY if pid != 'PUBOB30')

    def backfill(self, today):
        """A year of each daily figure, one request each, spaced out. Once."""
        done = set((self.store.scalar("SELECT v FROM meta WHERE k = 'gas_backfilled'") or '').split(','))
        for pid in DAILY:
            if pid in done or pid == 'PUBOB30':
                continue
            self.fetch_daily(today - timedelta(days=BACKFILL_DAYS), today, [pid])
            done.add(pid)
            self.store.write("INSERT OR REPLACE INTO meta (k, v) VALUES ('gas_backfilled', ?)", [(','.join(sorted(done - {''})),)], history=False)
            time.sleep(SPACING)
        return len(done)

    # ---- the job ------------------------------------------------------------------

    def poll(self):
        now = time.time()
        self.fetch_flows()
        if now - self.daily_checked >= DAILY_EVERY:
            today = gas_day(now)
            time.sleep(2)
            self.fetch_daily(today - timedelta(days=10), today + timedelta(days=1), DAILY)
            self.daily_checked = now
            if not self.backfilled():
                time.sleep(SPACING)
                self.backfill(today)
                log.info('[gas] a year of National Gas daily figures downloaded')
        self.store.write('DELETE FROM gas_flow WHERE t < ?', [(now - KEEP_FLOWS,)], history=False)
        self.version += 1


# ---- the page --------------------------------------------------------------------

def gas_generation(store):
    """(t, MW) of the latest gas-fired generation (combined and open cycle), or None."""
    row = store.query("SELECT t, SUM(mw) FROM gen WHERE fuel IN ('CCGT', 'OCGT') AND t = (SELECT MAX(t) FROM gen) GROUP BY t")
    return (row[0][0], row[0][1]) if row and row[0][1] is not None else None


def home_gas_rate(store, now):
    """Your gas unit rate now (p/kWh, with VAT), when Octopus is set up and has given one."""
    row = store.query("SELECT p FROM home_rate WHERE fuel = 'gas' AND kind = 'unit' AND start <= ? AND (end IS NULL OR end > ?) "
                      'ORDER BY start DESC LIMIT 1', (now, now))
    return row[0][0] if row else None


def view(store, now, gas_generation=None, home_rate=None):
    """What /api/gas returns. gas_generation: (t, MW) of the latest gas-fired generation;
    home_rate: your gas unit rate (p/kWh), when Octopus is set up."""
    latest = store.scalar('SELECT MAX(t) FROM gas_flow')
    if latest is None:
        return {'ready': False}
    now_values = dict(store.query('SELECT k, v FROM gas_flow WHERE t = ?', (latest,)))
    day = gas_day(latest)
    start = gas_day_start(day)
    sources = {k[4:]: v for k, v in now_values.items() if k.startswith('src:')}
    uses = {k[4:]: v for k, v in now_values.items() if k.startswith('use:')}
    sites = sorted(([k[5:], source_of(k[5:]), v] for k, v in now_values.items() if k.startswith('site:') and v > 0.05), key=lambda s: -s[2])
    # The gas day so far, in four-minute steps
    series = {}
    for t, k, v in store.query("SELECT t, k, v FROM gas_flow WHERE t >= ? AND k IN ('supply', 'demand', 'linepack', 'use:power') ORDER BY t", (start,)):
        series.setdefault(t - t % 240, {})[k] = v
    today_rows = [[t, p.get('supply'), p.get('demand'), p.get('use:power'), p.get('linepack')] for t, p in sorted(series.items())]

    daily = {}
    for d, k, v in store.query('SELECT d, k, v FROM gas_daily WHERE d >= ?', ((day - timedelta(days=BACKFILL_DAYS)).isoformat(),)):
        daily.setdefault(d, {})[k] = v
    today_pub = daily.get(day.isoformat(), {})
    year = []
    for d in sorted(daily):
        r = daily[d]
        if all(k in r for k in ('ndm', 'dmc', 'industrial', 'power')):
            year.append([d] + [None if r.get(k) is None else round(r[k] / 1e6, 1) for k in ENERGY] +
                        [r.get('linepack'), r.get('sap')])
    sap = [[d, daily[d]['sap']] for d in sorted(daily) if 'sap' in daily[d]]
    power = None
    if 'power' in uses and gas_generation:
        thermal = mcmd_to_gw(uses['power'])
        power = {'gas_mcmd': round(uses['power'], 1), 'thermal_gw': round(thermal, 2), 'elec_gw': round(gas_generation[1] / 1000, 2),
                 'elec_t': gas_generation[0], 'efficiency': round(gas_generation[1] / 1000 / thermal, 3) if thermal > 1 else None}
    # Where the gas day began: the reading at 05:00 if we have one, or National Gas's closing figure for the day before
    opening = store.query("SELECT v FROM gas_flow WHERE k = 'linepack' AND t >= ? AND t < ? ORDER BY t LIMIT 1", (start, start + 900))
    opening = opening[0][0] if opening else daily.get((day - timedelta(days=1)).isoformat(), {}).get('linepack')
    return {
        'ready': True, 'now': int(now), 't': latest, 'gas_day': day.isoformat(), 'day_start': start,
        'supply': now_values.get('supply'), 'demand': now_values.get('demand'), 'linepack': now_values.get('linepack'),
        'sources': [[k, round(sources[k], 2)] for k in SOURCE_ORDER if sources.get(k)],
        'uses': [[k, round(uses[k], 2)] for k in USE_ORDER if k in uses],
        'sites': [[n, src, round(v, 2)] for n, src, v in sites],
        'today': {'rows': today_rows, 'linepack_open': opening, 'pclp': today_pub.get('pclp'),
                  'demand_fc': today_pub.get('demand_fc'), 'temp_normal': today_pub.get('temp_normal')},
        'power': power,
        'year': year, 'year_columns': ['d'] + list(ENERGY) + ['linepack', 'sap'],
        'sap': sap[-1] if sap else None,
        'home_rate': home_rate,
        'cv': GAS_CALORIFIC_VALUE,
    }
