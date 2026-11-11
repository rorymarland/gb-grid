"""Your home: electricity and gas use from an Octopus Energy smart meter.

Off unless OCTOPUS_API_KEY and OCTOPUS_ACCOUNT are set. Three kinds of data:

  live      the Home Mini's 10-second readings (GraphQL smartMeterTelemetry),
            plus today's half hours from the same source
  meter     the smart meter's half-hourly readings (REST consumption), which
            arrive a day or so behind and replace the live half hours
  rates     the tariff's unit rates and standing charges (REST products)

Octopus allows roughly 100 requests an hour per account, shared with its own
app and anything else using the key, so live readings are fetched every
OCTOPUS_LIVE_SECONDS only while the Your home page is open, and every five
minutes otherwise. Everything is kept in the home and home_rate tables.

The API key never leaves this module: it isn't logged, stored or sent to any
host but OCTOPUS_BASE_URL, and the JSON API only returns totals (no account
number, meter serials or MPAN).
"""

import base64
import json
import re
import threading
import time
import urllib.parse
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

from . import fetch
from .config import (GAS_CALORIFIC_VALUE, GAS_KG_PER_KWH, HOME_ENABLED, OCTOPUS_ACCOUNT, OCTOPUS_API_KEY, OCTOPUS_BASE,
                     OCTOPUS_LIVE_SECONDS, log)
from .fetch import FetchError
from .store import UPSERT_HOME
from .timeutil import floor_to, iso, parse_time, settlement_periods, uk_clock, uk_midnight_utc

IDLE_SECONDS = 300            # live readings while nobody has the page open
VIEWING = 150                 # the page counts as open this long after it last asked
TODAY_SECONDS = 300           # today's half hours from the Home Mini
METER_SECONDS = 3 * 3600      # the meter's half-hourly readings
RATES_SECONDS = 12 * 3600
ACCOUNT_SECONDS = 12 * 3600
BACKFILL_DAYS = 400
LIVE_WINDOW = 1800            # seconds of 10-second readings fetched for the live chart
MAX_PAGES = 30
TOKEN_MARGIN = 300            # renew the token this long before it expires

KEY_RE = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
ACCOUNT_RE = re.compile(r'^A-[A-Z0-9]{4,16}$')
DEVICE_RE = re.compile(r'^[A-Za-z0-9-]{4,64}$')

ACCOUNT_QUERY = '''query {
  account(accountNumber: %s) {
    electricityAgreements(active: true) {
      meterPoint {
        mpan
        direction
        meters(includeInactive: false) {
          serialNumber
          activeFrom
          smartImportElectricityMeter { deviceId }
        }
        agreements(includeInactive: true) {
          validFrom
          validTo
          tariff { ... on TariffType { productCode tariffCode } }
        }
      }
    }
    gasAgreements(active: true) {
      meterPoint {
        mprn
        meters(includeInactive: false) {
          serialNumber
          activeFrom
          consumptionUnits
        }
        agreements(includeInactive: true) {
          validFrom
          validTo
          tariff { tariffCode productCode }
        }
      }
    }
  }
}'''
TELEMETRY = '''%s: smartMeterTelemetry(deviceId: %s, grouping: %s, start: %s, end: %s) {
    readAt
    consumptionDelta
    demand
  }'''


class OctopusError(FetchError):
    pass


def gql_time(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')


def as_number(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def single_rate(tariff):
    """True for single-rate tariffs (E-1R-..., G-1R-...); day/night and multi-rate tariffs aren't costed."""
    parts = str(tariff or '').split('-')
    return len(parts) > 2 and parts[1] == '1R'


def gas_kwh(value, units):
    """Gas meters report kWh (SMETS1) or cubic metres (SMETS2)."""
    if str(units or '').strip().lower().startswith('m'):
        return value * 1.02264 * GAS_CALORIFIC_VALUE / 3.6
    return value


def parse_agreements(point):
    out = []
    for agreement in point.get('agreements') or []:
        tariff = agreement.get('tariff') or {}
        code, product = tariff.get('tariffCode'), tariff.get('productCode')
        if not code or not product:
            continue
        try:
            start = parse_time(agreement['validFrom']) if agreement.get('validFrom') else 0
            end = parse_time(agreement['validTo']) if agreement.get('validTo') else None
        except (TypeError, ValueError):
            continue
        out.append({'start': start, 'end': end, 'product': product, 'tariff': code})
    out.sort(key=lambda a: a['start'])
    return out


def parse_account(data):
    """The import electricity meter and the gas meter, from the account query."""
    account = (data or {}).get('account')
    if not account:
        raise OctopusError('Octopus has no account ' + OCTOPUS_ACCOUNT + ' for this API key. Check OCTOPUS_ACCOUNT.')
    meters = {}
    for agreement in account.get('electricityAgreements') or []:
        point = agreement.get('meterPoint') or {}
        if str(point.get('direction') or '').upper() == 'EXPORT' or not point.get('mpan'):
            continue
        listed = [m for m in point.get('meters') or [] if m.get('serialNumber')]
        if not listed:
            continue
        # The active smart meter: one with a device ID first, then the most recently fitted
        listed.sort(key=lambda m: (bool((m.get('smartImportElectricityMeter') or {}).get('deviceId')),
                                   m.get('activeFrom') or ''), reverse=True)
        meter = listed[0]
        device = (meter.get('smartImportElectricityMeter') or {}).get('deviceId')
        found = {'mpan': str(point['mpan']), 'serial': str(meter['serialNumber']),
                 'device': device if device and DEVICE_RE.match(str(device)) else None,
                 'agreements': parse_agreements(point)}
        if 'elec' not in meters or (found['device'] and not meters['elec']['device']):
            meters['elec'] = found
    for agreement in account.get('gasAgreements') or []:
        point = agreement.get('meterPoint') or {}
        listed = [m for m in point.get('meters') or [] if m.get('serialNumber')]
        if not point.get('mprn') or not listed:
            continue
        listed.sort(key=lambda m: m.get('activeFrom') or '', reverse=True)
        meters.setdefault('gas', {'mprn': str(point['mprn']), 'serial': str(listed[0]['serialNumber']),
                                  'units': listed[0].get('consumptionUnits') or 'kWh',
                                  'agreements': parse_agreements(point)})
    if 'elec' not in meters and 'gas' not in meters:
        raise OctopusError('Octopus lists no electricity or gas meter on account ' + OCTOPUS_ACCOUNT + '.')
    return meters


def default_request(url, headers=None, data=None):
    """(status, body bytes). Never follows a redirect to another host with the key."""
    status, body, _ = fetch.http_get(url, extra_headers=headers, data=data, timeout=40)
    return status, body


class Home:
    def __init__(self, store, status=None, request=None, key=None, account=None, base=None):
        self.store, self.status = store, status
        self.request = request or default_request
        self.key = OCTOPUS_API_KEY if key is None else key
        self.account = OCTOPUS_ACCOUNT if account is None else account
        self.base = (OCTOPUS_BASE if base is None else base).rstrip('/')
        self.enabled = HOME_ENABLED if key is None else bool(self.key and self.account)
        self.lock = threading.Lock()
        self.token, self.token_expires = None, 0
        self.meters = None
        self.live = None              # {'t', 'w', 'points': [[t, w], ...]}
        self.live_at = 0              # when live readings were last fetched
        self.viewed = 0               # when the page last asked for data
        self.next, self.fails = {}, {}
        self.version = 0
        self.cache = None
        self.problem = None

    # ---- the API -----------------------------------------------------------

    def _auth_basic(self):
        return 'Basic ' + base64.b64encode((self.key + ':').encode('ascii')).decode('ascii')

    def _json(self, url, headers=None, data=None):
        _, body = self.request(url, headers, data)
        try:
            return json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise OctopusError('Invalid JSON from Octopus')

    def _graphql(self, query, token=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = token
        data = self._json(self.base + '/v1/graphql/', headers, json.dumps({'query': query}).encode('utf-8'))
        errors = data.get('errors') if isinstance(data, dict) else None
        if errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            ext = first.get('extensions') or {}
            cause = OctopusQueryError(ext) if ext else None
            raise OctopusError('Octopus: ' + str(ext.get('errorDescription') or first.get('message') or 'error')
                               + (' (' + str(ext['errorCode']) + ')' if ext.get('errorCode') else '')) from cause
        return (data or {}).get('data') or {}

    def _get_token(self, now):
        if self.token and now < self.token_expires - TOKEN_MARGIN:
            return self.token
        if not KEY_RE.match(self.key or ''):
            raise OctopusError("OCTOPUS_API_KEY doesn't look like an Octopus API key (it starts sk_live_).")
        try:
            data = self._graphql('mutation { obtainKrakenToken(input: { APIKey: %s }) { token } }' % json.dumps(self.key))
        except OctopusError as error:
            if 'KT-CT-1139' in str(error) or 'Authentication failed' in str(error):
                raise OctopusError("Octopus didn't accept the API key. Check OCTOPUS_API_KEY "
                                   '(Octopus account, Personal details, API access).') from None
            raise
        token = ((data.get('obtainKrakenToken') or {}).get('token') or '').strip()
        if not token:
            raise OctopusError('Octopus returned no token for the API key.')
        expires = now + 3000
        try:
            payload = token.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            expires = float(claims.get('exp') or expires)
        except (IndexError, ValueError, TypeError):
            pass
        self.token, self.token_expires = token, expires
        return token

    def _query(self, query, now, prefix=''):
        """An authorised GraphQL query; a rejected token is renewed and the query tried once more."""
        for attempt in (1, 2):
            token = self._get_token(now)
            try:
                return self._graphql(query, prefix + token)
            except OctopusError as error:
                cause = error.__cause__
                if attempt == 1 and isinstance(cause, OctopusQueryError) and cause.auth:
                    self.token = None
                    continue
                raise

    def _rest(self, path, params, pages=MAX_PAGES):
        """Every page of a REST list. Only the page number is taken from `next`,
        so the key is only ever sent to OCTOPUS_BASE_URL."""
        results, page = [], 1
        for _ in range(pages):
            query = dict(params, **({'page': page} if page > 1 else {}))
            data = self._json(self.base + path + '?' + urllib.parse.urlencode(query),
                              {'Authorization': self._auth_basic()})
            if not isinstance(data, dict):
                raise OctopusError('Unexpected response from Octopus')
            results.extend(r for r in data.get('results') or [] if isinstance(r, dict))
            following = data.get('next')
            if not following or pages == 1:
                return results
            try:
                page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(following).query)['page'][0])
            except (KeyError, ValueError, IndexError):
                return results
        log.warning('[home] stopped after %d pages of %s', MAX_PAGES, path.split('/')[2])
        return results

    # ---- the steps ---------------------------------------------------------

    def discover(self, now):
        if not ACCOUNT_RE.match(self.account or ''):
            raise OctopusError("OCTOPUS_ACCOUNT should look like A-1234ABCD (it's on your Octopus bills and account page).")
        try:
            data = self._query(ACCOUNT_QUERY % json.dumps(self.account), now)
        except OctopusError as error:
            if "API key" in str(error):
                raise
            raise OctopusError("Octopus wouldn't show account " + self.account + ' to this API key. Check OCTOPUS_ACCOUNT. ('
                               + str(error) + ')') from None
        meters = parse_account(data)
        if self.meters is None:
            log.info('[home] found %s%s', 'an electricity meter' + (' with a Home Mini' if meters.get('elec', {}).get('device')
                     else ' (no Home Mini, so no live readings)') if 'elec' in meters else 'no electricity meter',
                     ' and a gas meter' if 'gas' in meters else '')
        self.meters = meters

    def intensities(self, start, end):
        """Grid carbon intensity per half hour: the live feed, then the long-term history."""
        out = {}
        for t, ci in self.store.query('SELECT t, ci FROM lh WHERE t >= ? AND t < ? AND ci IS NOT NULL', (start, end)):
            out[int(t)] = float(ci)
        for t, forecast, actual in self.store.query(
                'SELECT t, forecast, actual FROM ci WHERE region = 0 AND t >= ? AND t < ?', (start, end)):
            value = actual if actual is not None else forecast
            if value is not None:
                out[int(t)] = float(value)
        return out

    def _write(self, rows):
        changed = self.store.write(UPSERT_HOME, rows, history=False)
        if changed:
            self.version += 1
        return changed

    def poll_live(self, now, today):
        device = (self.meters or {}).get('elec', {}).get('device')
        if not device:
            return
        parts = [TELEMETRY % ('live', json.dumps(device), 'TEN_SECONDS', json.dumps(gql_time(now - LIVE_WINDOW)),
                              json.dumps(gql_time(now + 60)))]
        if today:
            day, _ = uk_clock(now)
            start = uk_midnight_utc(day)
            if now - start < 3600:   # just after midnight: finish yesterday too
                start = uk_midnight_utc(day - timedelta(days=1))
            parts.append(TELEMETRY % ('today', json.dumps(device), 'HALF_HOURLY', json.dumps(gql_time(start)),
                                      json.dumps(gql_time(uk_midnight_utc(day + timedelta(days=1))))))
        data = self._query('query {\n  ' + '\n  '.join(parts) + '\n}', now, prefix='JWT ')
        points = []
        for item in data.get('live') or []:
            try:
                t, w = parse_time(item['readAt']), as_number(item.get('demand'))
            except (KeyError, TypeError, ValueError):
                continue
            if w is not None:
                points.append([t, round(w)])
        points.sort()
        with self.lock:
            self.live_at = now
            if points:
                self.live = {'t': points[-1][0], 'w': points[-1][1], 'points': points}
                self.version += 1
        if today:
            slots = {}
            for item in data.get('today') or []:
                try:
                    t, wh = floor_to(parse_time(item['readAt']), 1800), as_number(item.get('consumptionDelta'))
                except (KeyError, TypeError, ValueError):
                    continue
                if wh is not None and wh >= 0:
                    slots[t] = slots.get(t, 0) + wh / 1000
            if slots:
                ci = self.intensities(min(slots), max(slots) + 1800)
                self._write([('elec', t, round(kwh, 4), 'live', ci.get(t)) for t, kwh in sorted(slots.items())])

    def poll_meter(self, now):
        for fuel in ('elec', 'gas'):
            info = (self.meters or {}).get(fuel)
            if not info:
                continue
            have = self.store.scalar("SELECT MAX(t) FROM home WHERE fuel = ? AND src = 'meter'", (fuel,))
            start = int(have) - 2 * 86400 if have else floor_to(now - BACKFILL_DAYS * 86400, 1800)
            q = lambda value: urllib.parse.quote(value, safe='')  # noqa: E731
            path = ('/v1/electricity-meter-points/%s/meters/%s/consumption/' % (q(info['mpan']), q(info['serial']))
                    if fuel == 'elec' else
                    '/v1/gas-meter-points/%s/meters/%s/consumption/' % (q(info['mprn']), q(info['serial'])))
            try:
                results = self._rest(path, {'period_from': iso(start), 'period_to': iso(now + 3600),
                                            'page_size': 25000, 'order_by': 'period'})
            except FetchError as error:
                if 'HTTP 404' in str(error):
                    raise OctopusError("Octopus has no half-hourly readings for your %s meter yet."
                                       % ('electricity' if fuel == 'elec' else 'gas')) from None
                raise
            slots = {}
            for item in results:
                try:
                    t, end = parse_time(item['interval_start']), parse_time(item['interval_end'])
                    value = float(item['consumption'])
                except (KeyError, TypeError, ValueError):
                    continue
                if end - t != 1800 or t % 1800 or value < 0:
                    continue
                slots[t] = gas_kwh(value, info.get('units')) if fuel == 'gas' else value
            if not slots:
                continue
            ci = self.intensities(min(slots), max(slots) + 1800) if fuel == 'elec' else {}
            added = self._write([(fuel, t, round(kwh, 4), 'meter', ci.get(t)) for t, kwh in sorted(slots.items())])
            if not have:
                log.info('[home] %s: %d half hours from %s', 'electricity' if fuel == 'elec' else 'gas', added,
                         uk_clock(min(slots))[0].isoformat())
        self.refresh_ci(now, everything=True)

    def refresh_ci(self, now, everything=False):
        """Carbon intensity firms up after the event; missing values may arrive later."""
        rows = self.store.query("SELECT t, ci FROM home WHERE fuel = 'elec' AND (t >= ?%s)"
                                % (' OR ci IS NULL' if everything else ''), (now - 2 * 86400,))
        if not rows:
            return
        known = self.intensities(min(r[0] for r in rows), max(r[0] for r in rows) + 1800)
        updates = [(known[t], t) for t, ci in rows if t in known and (ci is None or abs(ci - known[t]) > 0.05)]
        if updates and self.store.write("UPDATE home SET ci = ? WHERE fuel = 'elec' AND t = ?", updates, history=False):
            self.version += 1

    def poll_rates(self, now):
        lo_all = now - BACKFILL_DAYS * 86400
        for fuel in ('elec', 'gas'):
            info = (self.meters or {}).get(fuel)
            if not info:
                continue
            rows = []
            for agreement in info['agreements']:
                if not single_rate(agreement['tariff']):
                    continue
                lo = max(agreement['start'], lo_all)
                hi = min(agreement['end'] or now + 2 * 86400, now + 2 * 86400)
                if hi <= lo:
                    continue
                q = lambda value: urllib.parse.quote(value, safe='')  # noqa: E731
                path = '/v1/products/%s/%s/%s/' % (q(agreement['product']), 'electricity-tariffs' if fuel == 'elec'
                                                   else 'gas-tariffs', q(agreement['tariff']))
                for kind, leaf in (('unit', 'standard-unit-rates/'), ('standing', 'standing-charges/')):
                    results = self._rest(path + leaf, {'period_from': iso(lo), 'period_to': iso(hi), 'page_size': 1500})
                    direct = [r for r in results if r.get('payment_method') in (None, 'DIRECT_DEBIT')] or results
                    for item in direct:
                        try:
                            start = parse_time(item['valid_from'])
                            end = parse_time(item['valid_to']) if item.get('valid_to') else None
                            value = float(item['value_inc_vat'])
                        except (KeyError, TypeError, ValueError):
                            continue
                        start = max(start, agreement['start'])
                        if agreement['end'] is not None:
                            end = agreement['end'] if end is None else min(end, agreement['end'])
                        if end is None or end > start:
                            rows.append((fuel, kind, start, end, value))
            if self.store.replace('DELETE FROM home_rate WHERE fuel = ?', (fuel,),
                                  'INSERT OR REPLACE INTO home_rate (fuel, kind, start, end, p) VALUES (?, ?, ?, ?, ?)', rows):
                self.version += 1

    # ---- scheduling --------------------------------------------------------

    def _due(self, name, now):
        return now >= self.next.get(name, 0)

    def _schedule(self, name, now, ok, interval):
        if ok:
            self.fails[name] = 0
            self.next[name] = now + interval
        else:
            self.fails[name] = self.fails.get(name, 0) + 1
            self.next[name] = now + min(3600, 60 * 2 ** (self.fails[name] - 1))

    def tick(self, now):
        """Run whatever is due. Returns None if nothing was, else the first error (or '')."""
        if not self.enabled:
            return None
        viewing = now - self.viewed < VIEWING
        due_account = self._due('account', now)
        due_today = self._due('today', now)
        due_live = self._due('live', now) or (viewing and not self.fails.get('live')
                                              and now - self.live_at >= OCTOPUS_LIVE_SECONDS - 2)
        steps = [name for name, due in (('account', due_account), ('live', due_live or due_today),
                                        ('rates', self._due('rates', now)), ('meter', self._due('meter', now))) if due]
        if self.meters is None and not due_account:   # waiting to retry the account: nothing else can run
            steps = []
        if not steps:
            return None
        if self.status:
            self.status.attempt('home')
        problem = ''
        for name in steps:
            if name != 'account' and self.meters is None:   # the account lookup just failed
                self._schedule(name, now, False, 0)
                continue
            try:
                if name == 'account':
                    self.discover(now)
                    self._schedule('account', now, True, ACCOUNT_SECONDS)
                elif name == 'live':
                    self.poll_live(now, due_today)
                    self._schedule('live', now, True, IDLE_SECONDS)
                    if due_today:
                        self._schedule('today', now, True, TODAY_SECONDS)
                        self.refresh_ci(now)
                elif name == 'rates':
                    self.poll_rates(now)
                    self._schedule('rates', now, True, RATES_SECONDS)
                elif name == 'meter':
                    self.poll_meter(now)
                    self._schedule('meter', now, True, METER_SECONDS)
            except Exception as error:   # one step failing shouldn't stop the others
                message = str(error) if isinstance(error, FetchError) else type(error).__name__ + ': ' + str(error)
                if not isinstance(error, FetchError):
                    log.debug('[home] %s traceback', name, exc_info=True)
                self._schedule(name, now, False, 0)
                if name == 'live' and due_today:
                    self._schedule('today', now, False, 0)
                problem = problem or message
        self.problem = problem or None
        if self.status:
            if problem:
                self.status.fail('home', problem)
            else:
                self.status.ok('home')
        return problem

    def data_time(self):
        with self.lock:
            return self.live['t'] if self.live else None

    # ---- the page's figures ------------------------------------------------

    def _rates(self):
        rates = {}
        for fuel, kind, start, end, p in self.store.query(
                'SELECT fuel, kind, start, end, p FROM home_rate ORDER BY fuel, kind, start'):
            rates.setdefault((fuel, kind), []).append((start, end, p))
        return rates

    def view(self, now):
        self.viewed = now
        if not self.enabled:
            return {'enabled': False}
        day, _ = uk_clock(now)
        key = (self.version, day, int(now // 60))
        cached = self.cache
        if cached and cached[0] == key:
            return cached[1]
        payload = self._build(now, day)
        self.cache = (key, payload)
        return payload

    def _build(self, now, today):
        # From the first of the month twelve months ago, so every month in the table is whole
        oldest = today.replace(day=1)
        for _ in range(12):
            oldest = (oldest - timedelta(days=1)).replace(day=1)
        since = uk_midnight_utc(min(oldest, today - timedelta(days=365)))
        rate_lists = self._rates()
        starts = {k: [row[0] for row in v] for k, v in rate_lists.items()}

        def rate(fuel, kind, t):
            table = rate_lists.get((fuel, kind))
            if not table:
                return None
            i = bisect_right(starts[(fuel, kind)], t) - 1
            if i < 0:
                return None
            start, end, p = table[i]
            return p if end is None or t < end else None

        days = {}

        def bucket(t):
            d = uk_clock(t)[0]
            item = days.get(d)
            if item is None:
                item = days[d] = {'e': 0.0, 'e_n': 0, 'e_ci_kwh': 0.0, 'e_kg': 0.0, 'e_cost': 0.0, 'e_cost_ok': True,
                                  'g': 0.0, 'g_n': 0, 'g_cost': 0.0, 'g_cost_ok': True, 'live': False}
            return item

        today_slots = []
        today_start = uk_midnight_utc(today)
        timing_from = uk_midnight_utc(today - timedelta(days=30))
        weighted, weight, flat, flat_n = 0.0, 0.0, 0.0, 0
        for t, kwh, src, ci in self.store.query(
                "SELECT t, kwh, src, ci FROM home WHERE fuel = 'elec' AND t >= ? ORDER BY t", (since,)):
            item = bucket(t)
            item['e'] += kwh
            item['e_n'] += 1
            item['live'] = item['live'] or src == 'live'
            if ci is not None:
                item['e_ci_kwh'] += kwh
                item['e_kg'] += kwh * ci / 1000
            unit = rate('elec', 'unit', t)
            if unit is None:
                item['e_cost_ok'] = False
            else:
                item['e_cost'] += kwh * unit
            if t >= today_start:
                today_slots.append([t, round(kwh, 3), None if ci is None else round(ci), src])
            elif t >= timing_from and ci is not None:
                weighted += kwh * ci
                weight += kwh
                flat += ci
                flat_n += 1
        for t, kwh in self.store.query("SELECT t, kwh FROM home WHERE fuel = 'gas' AND t >= ? ORDER BY t", (since,)):
            item = bucket(t)
            item['g'] += kwh
            item['g_n'] += 1
            unit = rate('gas', 'unit', t)
            if unit is None:
                item['g_cost_ok'] = False
            else:
                item['g_cost'] += kwh * unit

        def summarise(d, item):
            start = uk_midnight_utc(d)
            periods = settlement_periods(d)
            e_kg = None
            if item['e_n']:
                # Half hours without a carbon figure (rare) are counted at the day's average
                e_kg = item['e_kg'] * item['e'] / item['e_ci_kwh'] if item['e_ci_kwh'] > 0 else (0.0 if item['e'] == 0 else None)
            costs = {}
            for fuel, n, ok, value in (('elec', item['e_n'], item['e_cost_ok'], item['e_cost']),
                                       ('gas', item['g_n'], item['g_cost_ok'], item['g_cost'])):
                standing = rate(fuel, 'standing', start)
                costs[fuel] = round(value + standing, 1) if n and ok and standing is not None else None
            known = [costs[f] for f, n in (('elec', item['e_n']), ('gas', item['g_n'])) if n]
            return {
                'd': d.isoformat(), 't': start,
                'e': round(item['e'], 3) if item['e_n'] else None,
                'g': round(item['g'], 2) if item['g_n'] else None,
                'e_kg': None if e_kg is None else round(e_kg, 3),
                'g_kg': round(item['g'] * GAS_KG_PER_KWH, 3) if item['g_n'] else None,
                'e_cost': costs['elec'], 'g_cost': costs['gas'],
                'cost': round(sum(known), 1) if known and all(c is not None for c in known) else None,
                'e_full': item['e_n'] >= periods,
                'g_full': item['g_n'] >= periods,
                'live': item['live'],
            }

        summaries = {d: summarise(d, item) for d, item in days.items()}
        recent = []
        for back in range(30, -1, -1):
            d = today - timedelta(days=back)
            recent.append(summaries.get(d) or {'d': d.isoformat(), 't': uk_midnight_utc(d), 'e': None, 'g': None,
                                               'e_kg': None, 'g_kg': None, 'e_cost': None, 'g_cost': None, 'cost': None, 'e_full': False, 'g_full': False,
                                               'live': False})

        # Calendar months, newest last: the current one and the twelve before it
        months = []
        first = today.replace(day=1)
        for back in range(12, -1, -1):
            year, month = first.year, first.month - back
            while month < 1:
                month += 12
                year -= 1
            start = first.replace(year=year, month=month)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            items = [s for d, s in summaries.items() if start <= d < end]
            if not items:
                continue
            months.append(self._total(start, items, (end - start).days))

        year_items = [s for d, s in summaries.items() if d > today - timedelta(days=365)]
        current = int(floor_to(now, 1800))
        grid = self.store.query('SELECT forecast, actual, idx FROM ci WHERE region = 0 AND t = ?', (current,))
        grid_ci = None
        if grid:
            grid_ci = grid[0][1] if grid[0][1] is not None else grid[0][0]
        ahead = [[int(t), round(a if a is not None else f), idx] for t, f, a, idx in self.store.query(
            'SELECT t, forecast, actual, idx FROM ci WHERE region = 0 AND t >= ? AND t < ? ORDER BY t',
            (today_start, uk_midnight_utc(today + timedelta(days=1)))) if (a if a is not None else f) is not None]

        with self.lock:
            live = dict(self.live) if self.live else None
        if live:
            live['points'] = [p for p in live['points'] if p[0] >= now - LIVE_WINDOW]
        tariffs = {}
        for fuel in ('elec', 'gas'):
            info = (self.meters or {}).get(fuel)
            if not info:
                continue
            unit, standing = rate(fuel, 'unit', now), rate(fuel, 'standing', now)
            # One price at every time of day (a fixed or flexible tariff), rather than one that changes by the half hour
            nearby = {row[2] for row in rate_lists.get((fuel, 'unit'), [])
                      if row[0] < now + 86400 and (row[1] is None or row[1] > now - 2 * 86400)}
            current_agreement = next((a for a in reversed(info['agreements'])
                                      if a['start'] <= now and (a['end'] is None or now < a['end'])), None)
            tariffs[fuel] = {
                'unit': None if unit is None else round(unit, 3),
                'standing': None if standing is None else round(standing, 3),
                'flat': len(nearby) == 1,
                'costed': bool(current_agreement and single_rate(current_agreement['tariff'])),
            }
        first_t = self.store.scalar("SELECT MIN(t) FROM home WHERE fuel = 'elec'")
        return {
            'enabled': True,
            'now': now,
            'meters': {'elec': bool(self.meters and 'elec' in self.meters),
                       'gas': bool(self.meters and 'gas' in self.meters),
                       'live': bool(self.meters and (self.meters.get('elec') or {}).get('device'))} if self.meters else None,
            'live': live,
            'live_seconds': OCTOPUS_LIVE_SECONDS,
            'grid': {'ci': None if grid_ci is None else round(grid_ci), 'idx': grid[0][2] if grid else None},
            'today': {'d': today.isoformat(), 'start': today_start, 'slots': today_slots, 'ahead': ahead,
                      'periods': settlement_periods(today), 'total': summaries.get(today)},
            'days': recent,
            'months': months,
            'year': self._total(today - timedelta(days=364), year_items, 365) if year_items else None,
            'timing': {'yours': round(weighted / weight, 1), 'flat': round(flat / flat_n, 1), 'kwh': round(weight, 1),
                       'slots': flat_n} if weight > 0 and flat_n >= 7 * 48 else None,
            'tariffs': tariffs,
            'gas_factor': GAS_KG_PER_KWH,
            'since': first_t,
            'problem': self.problem,
        }

    @staticmethod
    def _total(start, items, length):
        def add(key):
            values = [s[key] for s in items if s[key] is not None]
            return round(sum(values), 2) if values else None
        def complete(key, n_key):
            # A total only when every day with readings has a figure
            values = [s[key] for s in items if s[n_key] is not None]
            return round(sum(values), 1) if values and all(v is not None for v in values) else None
        e_cost, g_cost = complete('e_cost', 'e'), complete('g_cost', 'g')
        has_g = any(s['g'] is not None for s in items)
        return {'d': start.isoformat(), 't': uk_midnight_utc(start), 'length': length, 'days': len(items),
                'e_days': sum(1 for s in items if s['e'] is not None), 'g_days': sum(1 for s in items if s['g'] is not None),
                'e': add('e'), 'g': add('g'), 'e_kg': add('e_kg'), 'g_kg': add('g_kg'), 'e_cost': e_cost, 'g_cost': g_cost,
                'cost': None if e_cost is None or (has_g and g_cost is None) else round(e_cost + (g_cost or 0), 1)}


class OctopusQueryError(Exception):
    """The extensions of a GraphQL error, attached as the cause of an OctopusError."""

    def __init__(self, ext):
        super().__init__(ext.get('errorCode'))
        self.code = ext.get('errorCode')
        kind = str(ext.get('errorType') or '').upper()
        description = str(ext.get('errorDescription') or '').lower()
        self.auth = kind in ('AUTHORIZATION', 'AUTHENTICATION') or 'token' in description or 'expired' in description


def run_home(home, stop):
    """Check every ten seconds whether anything is due (most checks are)."""
    stop.wait(4)
    while not stop.is_set():
        try:
            home.tick(time.time())
        except Exception:   # keep the loop alive whatever happens
            log.exception('[home] unexpected error')
        stop.wait(10)


def test_octopus():
    """Check the key, account and meters, with a handful of requests. Prints no full identifiers."""
    if not HOME_ENABLED:
        return 'Set OCTOPUS_API_KEY (or OCTOPUS_API_KEY_FILE) and OCTOPUS_ACCOUNT first.'
    home, now = Home(None), time.time()
    tail = lambda value: '…' + str(value)[-4:]  # noqa: E731
    q = lambda value: urllib.parse.quote(value, safe='')  # noqa: E731
    try:
        home.discover(now)
        print('Octopus accepted the API key and found account ' + home.account + '.')
        for fuel, name in (('elec', 'Electricity'), ('gas', 'Gas')):
            info = home.meters.get(fuel)
            if not info:
                print(name + ': no meter on the account.')
                continue
            current = next((a for a in reversed(info['agreements'])
                            if a['start'] <= now and (a['end'] is None or now < a['end'])), None)
            print('%s: meter %s, tariff %s%s' % (name, tail(info['serial']), current['tariff'] if current else 'unknown',
                                                 '' if not current or single_rate(current['tariff'])
                                                 else ' (more than one rate, so costs aren\'t shown)'))
            path = ('/v1/electricity-meter-points/%s/meters/%s/consumption/' % (q(info['mpan']), q(info['serial']))
                    if fuel == 'elec' else '/v1/gas-meter-points/%s/meters/%s/consumption/' % (q(info['mprn']), q(info['serial'])))
            try:
                rows = home._rest(path, {'period_from': iso(now - 4 * 86400), 'page_size': 200, 'order_by': 'period'}, pages=1)
                latest = max((r.get('interval_end') for r in rows if r.get('interval_end')), default=None)
                print('  Half-hourly readings: %d in the last four days%s' % (len(rows), ', up to ' + latest if latest else ''))
            except FetchError as error:
                print('  Half-hourly readings: none (' + str(error)[:120] + ')')
            if fuel == 'gas':
                print('  Reported in ' + str(info.get('units')))
                continue
            if not info['device']:
                print('  No Home Mini found, so there will be no live readings (half-hourly ones still work).')
                continue
            data = home._query('query {\n  ' + TELEMETRY % ('live', json.dumps(info['device']), 'TEN_SECONDS',
                                                           json.dumps(gql_time(now - 600)), json.dumps(gql_time(now + 60)))
                               + '\n}', now, prefix='JWT ')
            readings = [r for r in data.get('live') or [] if r.get('demand') is not None]
            if readings:
                last = readings[-1]
                print('  Home Mini: %d readings in the last ten minutes; latest %s W at %s'
                      % (len(readings), round(float(last['demand'])), uk_clock(parse_time(last['readAt']))[1]))
            else:
                print("  Home Mini: no readings in the last ten minutes. Check it's plugged in and online.")
    except FetchError as error:
        return str(error)
    print('All good. The Your home page fills in within a few minutes of starting.')
    return 0
