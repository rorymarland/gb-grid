#!/usr/bin/env python3
"""Tests for Your home: the Octopus client against the mock API (run: python3 test/test_home.py)."""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, '..', 'app')
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

import server  # noqa: E402
sys.argv = [sys.argv[0], '0', APP]   # the mock reads its port and app folder from argv
import mock_upstream as mock  # noqa: E402
from gridlive import home as homemod  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


httpd = ThreadingHTTPServer(('127.0.0.1', 0), mock.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = 'http://127.0.0.1:%d/octopus' % httpd.server_address[1]

calls = []
inject = {'auth_error_once': False}


def request(url, headers=None, data=None):
    """The real HTTP client, counting requests and checking where credentials go."""
    calls.append((url, dict(headers or {}), data))
    if inject['auth_error_once'] and data and b'smartMeterTelemetry' in data:
        inject['auth_error_once'] = False
        return 200, json.dumps({'errors': [{'message': 'Signature of the JWT has expired.', 'extensions': {
            'errorType': 'AUTHORIZATION', 'errorCode': 'KT-CT-1124', 'errorDescription': 'Token expired.'}}]}).encode()
    return homemod.default_request(url, headers, data)


def make(key=mock.OCTOPUS_KEY, account=mock.OCTOPUS_ACCOUNT):
    store = server.Store(tempfile.mkdtemp())
    return store, homemod.Home(store, server.Status(), request=request, key=key, account=account, base=BASE)


# ---- off unless configured -------------------------------------------------

store, off = make(key='')
check('disabled view', off.view(time.time()) == {'enabled': False})
check('disabled tick does nothing', off.tick(time.time()) is None and not calls)

# ---- a bad key: a clear message, and the key never in it --------------------

store, bad = make(key='sk_live_wrongkey000000')
problem = bad.tick(time.time())
check('bad key reported', problem and 'OCTOPUS_API_KEY' in problem, problem)
check('bad key not echoed', 'sk_live_wrongkey000000' not in (problem or ''))
before = len(calls)
check('bad key backs off', bad.tick(time.time() + 20) is None and len(calls) == before)
store, nokey = make(key='not a key!')
check('malformed key caught before sending', 'sk_live_' in (nokey.tick(time.time()) or '') and len(calls) == before)
store, wrong = make(account='A-99999999')
check('unknown account reported', 'OCTOPUS_ACCOUNT' in (wrong.tick(time.time()) or ''))

# ---- the first run: meters, live readings, rates and the meter's history ------

calls.clear()
store, home = make()
now = time.time()
problem = home.tick(now)
check('first tick ok', problem == '', problem)
check('found the meters', home.meters and home.meters['elec']['device'] == mock.OCTOPUS_DEVICE and home.meters['gas']['units'] == 'm³',
      home.meters)
check('live reading', home.live and abs(home.live['w'] - mock.home_w(home.live['t'])) < 1, home.live)
elec_meter = store.scalar("SELECT COUNT(*) FROM home WHERE fuel = 'elec' AND src = 'meter'")
gas_meter = store.scalar("SELECT COUNT(*) FROM home WHERE fuel = 'gas'")
check('backfilled about 400 days', 380 * 48 < elec_meter < 402 * 48 and 380 * 48 < gas_meter, (elec_meter, gas_meter))
check('today from the Home Mini', store.scalar("SELECT COUNT(*) FROM home WHERE fuel = 'elec' AND src = 'live'") >= 1)
first_calls = len(calls)
check('first run is a handful of requests', first_calls <= 14, first_calls)

# Credentials only ever go to the configured base, whatever "next" says
hosts = {url.split('/octopus/')[0] for url, _, _ in calls}
check('only the base host was contacted', hosts == {BASE.split('/octopus')[0]}, hosts)
check('basic auth on REST, token on GraphQL', all(
    ('Authorization' in hd and hd['Authorization'].startswith('Basic ')) if '/graphql/' not in url else True
    for url, hd, _ in calls))
path = '/v1/electricity-meter-points/%s/meters/%s/consumption/' % (mock.MPAN, mock.ELEC_SERIAL)
calls.clear()
paged = home._rest(path, {'period_from': server.iso(now - 3 * 86400), 'page_size': 50})
check('pages followed', len(paged) >= 100 and any('page=3' in url for url, _, _ in calls), len(paged))
check('next link host ignored', all(url.startswith(BASE) for url, _, _ in calls))

# Gas in cubic metres is converted to kWh
t = store.scalar("SELECT MAX(t) FROM home WHERE fuel = 'gas'")
kwh = store.scalar("SELECT kwh FROM home WHERE fuel = 'gas' AND t = ?", (t,))
check('gas converted from m³', abs(kwh - mock.home_gas_kwh(t)) < 0.01, (kwh, mock.home_gas_kwh(t)))

# A live estimate never replaces a meter reading
t = store.scalar("SELECT MAX(t) FROM home WHERE fuel = 'elec' AND src = 'meter'")
kwh = store.scalar("SELECT kwh FROM home WHERE fuel = 'elec' AND t = ?", (t,))
home._write([('elec', t, kwh + 5, 'live', None)])
check('meter beats live', store.scalar("SELECT kwh FROM home WHERE fuel = 'elec' AND t = ?", (t,)) == kwh)

# Rates follow the tariff in force: the current one, and the earlier one
rates = home._rates()
unit = rates[('elec', 'unit')]
check('two electricity tariffs', len(unit) == 2 and unit[-1][2] == 24.86 and unit[0][2] == 26.95, unit)
check('tariffs meet without overlap', unit[0][1] == unit[1][0], unit)

# ---- the page's figures --------------------------------------------------------

view = home.view(now)
check('view enabled', view['enabled'] and view['meters'] == {'elec': True, 'gas': True, 'live': True}, view.get('meters'))
check('thirty-one days', len(view['days']) == 31 and view['days'][-1]['d'] == server.uk_clock(now)[0].isoformat())
# Yesterday's electricity cost: each half hour at the unit rate, plus the standing charge
yesterday = view['days'][-2]
start = yesterday['t']
rows = store.query("SELECT kwh FROM home WHERE fuel = 'elec' AND t >= ? AND t < ?", (start, start + 86400 + 3600))
day_rows = store.query("SELECT t, kwh FROM home WHERE fuel = 'elec' AND t >= ? AND t < ?",
                       (start, view['days'][-1]['t']))
expected = sum(k for _, k in day_rows) * 24.86 + 49.08
check('electricity cost', yesterday['e_cost'] is not None and abs(yesterday['e_cost'] - expected) < 0.2, (yesterday, expected))
check('day complete', yesterday['e_full'], yesterday)
check('gas a day behind', view['days'][-2]['g'] is None and view['days'][-3]['g'] is not None)
check('months are whole', all(m['e_days'] == m['length'] for m in view['months'][:-1]),
      [(m['d'], m['e_days'], m['length']) for m in view['months']])
check('timing compares like with like', view['timing'] is None or (view['timing']['slots'] >= 7 * 48))
check('flat tariff', view['tariffs']['elec']['flat'] and view['tariffs']['elec']['costed'])
text = json.dumps(view)
for secret in (mock.MPAN, mock.ELEC_SERIAL, mock.MPRN, mock.GAS_SERIAL, mock.OCTOPUS_DEVICE, mock.OCTOPUS_ACCOUNT, mock.OCTOPUS_KEY):
    check('no identifiers in the API: ' + secret[:4], secret not in text)

# ---- staying inside Octopus's limit: about 100 requests an hour per account ----


def hour(viewing):
    calls.clear()
    home.token_expires = float('inf')   # the mock's tokens expire in real time, not the simulated clock
    clock = now + 9 * 3600 if viewing else now + 7 * 3600
    end = clock + 3600
    while clock < end:
        if viewing:
            home.viewed = clock
        home.tick(clock)
        clock += 10
    return len(calls)


idle, watched = hour(False), hour(True)
print('Octopus requests in an hour: %d idle, %d with the page open' % (idle, watched))
check('idle hour', idle <= 20, idle)
check('viewing hour', 55 <= watched <= 75, watched)

# ---- a rejected token is renewed and the query tried again ---------------------

calls.clear()
inject['auth_error_once'] = True
home.next['live'] = 0
check('token renewed', home.tick(now + 12 * 3600) == '', home.problem)
check('renewal used a new token', any(b'obtainKrakenToken' in (data or b'') for _, _, data in calls))

# ---- failures back off ------------------------------------------------------------

mock.FAIL.add('octopus')
home.next = {}
first = home.tick(now + 20 * 3600)
check('failure reported', first and '503' in first, first)
calls.clear()
check('backs off after a failure', home.tick(now + 20 * 3600 + 15) is None and not calls)
mock.FAIL.clear()
check('recovers', home.tick(now + 20 * 3600 + 400) == '', home.problem)

httpd.shutdown()
print('home tests: ' + ('all passed' if not failures else '%d failed' % failures))
sys.exit(1 if failures else 0)
