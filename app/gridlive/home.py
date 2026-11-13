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

The API key never leaves the Octopus client (octopus.py): it isn't logged,
stored or sent to any host but OCTOPUS_BASE_URL, and the JSON API only returns
totals (no account number, meter serials or MPAN).

This module polls and stores; home_views.py works out the page's figures,
home_sessions.py handles Octoplus and Saving Sessions, and tariffs.py the
comparison with other tariffs.
"""
import json
import threading
import time
import urllib.parse
from datetime import timedelta

from . import tariffs
from .config import HOME_ENABLED, OCTOPUS_ACCOUNT, OCTOPUS_API_KEY, OCTOPUS_BACKEND, OCTOPUS_BASE, \
    OCTOPUS_LIVE_SECONDS, log
from .fetch import FetchError
from .home_sessions import SessionsMixin
from .home_views import BACKFILL_DAYS, ViewsMixin
from .octopus import (ACCOUNT_QUERY, ACCOUNT_RE, TELEMETRY, OctopusApi, OctopusError, as_number,
                      default_request, gas_kwh, gql_time, parse_account, single_rate)
from .store import UPSERT_HOME
from .timeutil import floor_to, iso, parse_time, uk_clock, uk_midnight_utc

IDLE_SECONDS = 300            # live readings while nobody has the page open
VIEWING = 150                 # the page counts as open this long after it last asked
TODAY_SECONDS = 300           # today's half hours from the Home Mini
METER_SECONDS = 3 * 3600      # the meter's half-hourly readings
RATES_SECONDS = 12 * 3600
ACCOUNT_SECONDS = 12 * 3600
TARIFFS_SECONDS = 24 * 3600   # other tariffs' prices, from the public product API
SESSIONS_SECONDS = 1800       # Saving Sessions (announced a day or so ahead)
POINTS_SECONDS = 6 * 3600     # Octoplus points balance
LIVE_KEEP = 26 * 3600         # 10-second readings kept for the live chart (24 hours shown)
LIVE_CATCH_UP = 2 * 3600      # after a gap, how far back to fetch them
LIVE_OVERLAP = 300            # refetch a few minutes, for readings that arrived late
DISPLAY_SECONDS = 120         # live readings while a Display with the Your home slide is running


class Home(OctopusApi, SessionsMixin, ViewsMixin):
    def __init__(self, store, status=None, request=None, key=None, account=None, base=None, backend=None):
        self.store, self.status = store, status
        self.request = request or default_request
        self.key = OCTOPUS_API_KEY if key is None else key
        self.account = OCTOPUS_ACCOUNT if account is None else account
        self.base = (OCTOPUS_BASE if base is None else base).rstrip('/')
        # Octoplus and Saving Sessions are on Octopus's second GraphQL endpoint
        self.backend = (OCTOPUS_BACKEND if backend is None else backend).rstrip('/')
        self.enabled = HOME_ENABLED if key is None else bool(self.key and self.account)
        self.lock = threading.Lock()
        self.token, self.token_expires = None, 0
        self.meters = None
        self.live = None              # {'t', 'w', 'points': [[t, w], ...]}
        self.live_at = 0              # when live readings were last fetched
        self.viewed = 0               # when the page last asked for data
        self.displayed = 0            # when a Display last asked
        self.next, self.fails = {}, {}
        self.version = 0
        self.cache = None
        self.problem = None
        self.products, self.region = None, None      # other tariffs, for the comparison
        self.compare_cache = None
        self.sessions = None                         # Saving Sessions, from the backend API
        self.points = None                           # Octoplus points balance
        self.extra_problem = {}                      # comparison and Octoplus problems, kept off the feed status
        self.data_version = 0                        # meter readings and tariffs: the comparison's inputs
        if self.enabled and store is not None:
            self.restore()

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
        last = self.store.scalar('SELECT MAX(t) FROM home_live')
        since = max(now - LIVE_CATCH_UP, (last - LIVE_OVERLAP) if last else 0)
        parts = [TELEMETRY % ('live', json.dumps(device), 'TEN_SECONDS', json.dumps(gql_time(since)),
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
        if points:
            self.store.write('INSERT OR REPLACE INTO home_live (t, w) VALUES (?, ?)', points, history=False)
            self.store.write('DELETE FROM home_live WHERE t < ?', [(now - LIVE_KEEP,)], history=False)
        with self.lock:
            self.live_at = now
            if points:
                self.live = {'t': points[-1][0], 'w': points[-1][1]}
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
            # A week back: smart meter readings sometimes arrive days late, and this fills them in
            start = int(have) - 7 * 86400 if have else floor_to(now - BACKFILL_DAYS * 86400, 1800)
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
        self.data_version += 1

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
                    # Tariff prices are public: no need to send the key
                    results = self._rest(path + leaf, {'period_from': iso(lo), 'period_to': iso(hi), 'page_size': 1500}, auth=False)
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
            self.data_version += 1
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
        # Live readings: every OCTOPUS_LIVE_SECONDS while the page is open, every two
        # minutes while a Display shows the Your home slide, every five otherwise
        every = (OCTOPUS_LIVE_SECONDS if now - self.viewed < VIEWING else
                 max(OCTOPUS_LIVE_SECONDS, DISPLAY_SECONDS) if now - self.displayed < VIEWING else None)
        due_account = self._due('account', now)
        # Today's half hours ride along with a live request; on their own only when nothing else is asking
        due_today = now >= self.next.get('today', 0) - (60 if every is not None else 0)
        if every is not None and not self._due('live', now) and not (
                not self.fails.get('live') and now - self.live_at >= every - 2):
            due_today = False
        due_live = self._due('live', now) or (every is not None and not self.fails.get('live')
                                              and now - self.live_at >= every - 2)
        steps = [name for name, due in (('account', due_account), ('live', due_live or due_today),
                                        ('rates', self._due('rates', now)), ('meter', self._due('meter', now)),
                                        ('tariffs', self._due('tariffs', now)), ('sessions', self._due('sessions', now)),
                                        ('points', self._due('points', now))) if due]
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
                elif name == 'tariffs':
                    tariffs.run(self, now)
                    self.data_version += 1
                    self._schedule('tariffs', now, True, TARIFFS_SECONDS)
                elif name == 'sessions':
                    self.poll_sessions(now)
                    self._schedule('sessions', now, True, SESSIONS_SECONDS)
                elif name == 'points':
                    self.poll_points(now)
                    self._schedule('points', now, True, POINTS_SECONDS)
                if name in self.extra_problem:
                    del self.extra_problem[name]
            except Exception as error:   # one step failing shouldn't stop the others
                message = str(error) if isinstance(error, FetchError) else type(error).__name__ + ': ' + str(error)
                if not isinstance(error, FetchError):
                    log.debug('[home] %s traceback', name, exc_info=True)
                self._schedule(name, now, False, 0)
                if name == 'live' and due_today:
                    self._schedule('today', now, False, 0)
                if name in ('tariffs', 'sessions', 'points'):
                    # Extras: shown on the page, not as a failing feed (Octoplus may simply not apply)
                    if self.extra_problem.get(name) != message:
                        log.warning('[home] %s: %s', name, message)
                    self.extra_problem[name] = message
                    continue
                problem = problem or message
        self.problem = problem or None
        if self.status:
            if problem:
                self.status.fail('home', problem)
            else:
                self.status.ok('home')
        return problem

    def restore(self):
        """The latest reading kept from before a restart."""
        row = self.store.query('SELECT t, w FROM home_live ORDER BY t DESC LIMIT 1') if self.store else []
        if row:
            self.live = {'t': row[0][0], 'w': round(row[0][1])}

    def data_time(self):
        with self.lock:
            return self.live['t'] if self.live else None


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
        try:
            home.poll_points(now)
            home.poll_sessions(now)
            s = home.sessions
            print('Octoplus: %s points; Saving Sessions: %s, %d session%s listed'
                  % ('{:,}'.format(home.points) if home.points is not None else 'no',
                     'joined' if s['joined_campaign'] else 'not joined this season', len(s['events']), '' if len(s['events']) == 1 else 's'))
        except FetchError as error:
            print('Octoplus and Saving Sessions: not available (' + str(error)[:120] + '). The rest still works.')
    except FetchError as error:
        return str(error)
    print('All good. The Your home page fills in within a few minutes of starting.')
    return 0
