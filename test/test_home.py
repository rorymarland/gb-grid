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
BACKEND = BASE + '-backend'

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
    return store, homemod.Home(store, server.Status(), request=request, key=key, account=account, base=BASE, backend=BACKEND)


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
private = [c for c in calls if '/v1/products/' not in c[0]]
public = [c for c in calls if '/v1/products/' in c[0]]
check('first run is a handful of account requests', len(private) <= 16, len(private))
check('other tariffs use the public API only, without credentials', public and all(not (hd or {}).get('Authorization') for _, hd, _ in public),
      len(public))

# Credentials only ever go to the configured base, whatever "next" says
import urllib.parse  # noqa: E402
hosts = {urllib.parse.urlsplit(url).netloc for url, _, _ in calls}
check('only the configured hosts were contacted', hosts == {urllib.parse.urlsplit(BASE).netloc}, hosts)
check('the key only goes with meter readings', all(
    (hd.get('Authorization', '').startswith('Basic ')) == ('/consumption/' in url) for url, hd, _ in calls if '/graphql/' not in url))
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

# ---- other tariffs ----------------------------------------------------------

check('found the tariffs to compare', set(home.products) == {'flexible', 'agile', 'go', 'fixed'} and home.region == 'N',
      (home.products, home.region))
check('no business, export or heat-pump tariffs', all(code not in str(home.products) for code in ('BUS-', 'OUTGOING', 'COSY')))
cmp = home.view(now)['compare']
elec = {o['key']: o for o in cmp['fuels']['elec']['options']}
gas = {o['key']: o for o in cmp['fuels']['gas']['options']}
check('electricity options', list(elec) == ['yours', 'flexible', 'agile', 'go', 'fixed'] and all(o['complete'] for o in elec.values()), list(elec))
check('gas options', list(gas) == ['yours', 'flexible', 'fixed'], list(gas))
start = server.uk_midnight_utc(server.uk_clock(now)[0] - server.timedelta(days=365))
end = server.uk_midnight_utc(server.uk_clock(now)[0])
slots = store.query("SELECT t, kwh FROM home WHERE fuel = 'elec' AND t >= ? AND t < ?", (start, end))
days = len({server.uk_midnight_utc(server.uk_clock(t)[0]) for t, _ in slots})
kwh = sum(k for _, k in slots)
check('fixed at today\'s price', abs(elec['fixed']['cost'] - (kwh * 23.9 + 47.0 * days)) < 1, (elec['fixed']['cost'], kwh * 23.9 + 47.0 * days))
check('agile half hour by half hour', abs(elec['agile']['cost'] - (sum(k * mock.agile_p(t) for t, k in slots) + 48.0 * days)) < 1)
go = sum(k * (8.5 if 1800 <= t % 86400 < 5.5 * 3600 else 28.0) for t, k in slots) + 49.5 * days
check('go cheap overnight', abs(elec['go']['cost'] - go) < 1, (elec['go']['cost'], go))
change = int(now) // 86400 * 86400 - 40 * 86400
flex = sum(k * (25.5 if t >= change else 24.5) for t, k in slots) + 53.0 * days
check('flexible at the direct debit price', abs(elec['flexible']['cost'] - flex) < 1, (elec['flexible']['cost'], flex))
check('months add up', all(abs(sum(o['months']) - o['cost']) < 1 for o in elec.values()))
check('differences against yours', all(abs(o['diff'] - (o['cost'] - elec['yours']['cost'])) < 0.2 for o in elec.values()))

# ---- Saving Sessions and points ----------------------------------------------

sv = home.view(now)['sessions']
check('points', sv['points'] == 4321 and sv['pounds'] == 5.4, sv and sv['points'])
ids = [e['id'] for e in sv['past']] + [e['id'] for e in sv['upcoming']]
check('sessions for this region only', sorted(ids) == [101, 102, 103], ids)
first = [e for e in sv['past'] if e['id'] == 101][0]
check('points Octopus gave', first['given'] == 1240 and first['joined'])
other = [e for e in sv['past'] if e['id'] == 102][0]
check('estimated points', other['estimate'] == round(other['saved'] * 1600) and not other['joined'])
# The baseline worked out independently: previous 10 weekdays, skipping the other session's day
ev = mock.parse(mock.octopus_backend('savingSessions ' + mock.OCTOPUS_ACCOUNT, 'x')['data']['savingSessions']['events'][1]['startAt'])
day = server.uk_clock(ev)[0]
skip = {server.uk_clock(mock.parse(e['startAt']))[0] for e in mock.octopus_backend('savingSessions', 'x')['data']['savingSessions']['events']}
dates, d = [], day
while len(dates) < 10:
    d -= server.timedelta(days=1)
    if d.weekday() < 5 and d not in skip:
        dates.append(d)
offset = ev - server.uk_midnight_utc(day)
expected = 0
for off in (offset, offset + 1800):
    values = [store.scalar("SELECT kwh FROM home WHERE fuel = 'elec' AND t = ?", (server.uk_midnight_utc(x) + off,)) for x in dates]
    expected += sum(values) / len(values)
check('baseline as Octopus works it out', abs(other['baseline'] - expected) < 0.001 and other['baseline_complete'], (other['baseline'], expected))

# Octopus's rules, with readings made up by hand: a session on Tuesday 25 August 2026
s2, h2 = make()
day = server.date(2026, 8, 25)
start = server.uk_midnight_utc(day) + 17 * 3600              # 17:00 to 18:00 BST
rows = []
d = day
for _ in range(30):
    d -= server.timedelta(days=1)
    for off, kwh in ((17 * 3600, 0.40), (17 * 3600 + 1800, 0.60)):
        rows.append(('elec', server.uk_midnight_utc(d) + off, kwh if d.weekday() < 5 else 0.9, 'meter', 100.0))
s2.write(server.UPSERT_HOME, rows, history=False)
slots, complete = h2._baseline(start, start + 3600, set())
check('baseline per half hour from 10 weekdays', complete and [round(x, 3) for x in slots] == [0.4, 0.6], (slots, complete))
# Used 0.30 then 0.75: 1.05 kWh against a 1.00 baseline overall, but the first half hour earns
s2.write(server.UPSERT_HOME, [('elec', start, 0.30, 'meter', 100.0), ('elec', start + 1800, 0.75, 'meter', 100.0)], history=False)
h2.sessions = {'at': 0, 'joined_campaign': True, 'can_join': True, 'region': 'N', 'joined': {},
               'events': [{'id': 9, 'start': start, 'end': start + 3600, 'reward': 1000, 'type': 'TURN_DOWN', 'regions': []},
                          {'id': 10, 'start': start - 7 * 86400, 'end': start - 7 * 86400 + 3600, 'reward': 500, 'type': 'TURN_UP', 'regions': []}]}
past = {e['id']: e for e in h2._sessions(start + 2 * 86400)['past']}
check('each half hour counts on its own', past[9]['saved'] == 0.1 and past[9]['estimate'] == 100 and past[9]['used'] == 1.05, past[9])
# The power-up a week earlier is a session day, so it's left out of the baseline
check('power-up earns for using more than usual', past[10]['kind'] == 'up' and past[10]['saved'] >= 0, past[10])
slots_skip, _ = h2._baseline(start, start + 3600, {day - server.timedelta(days=7)})
check('session days left out of the baseline', slots_skip == slots)
# A bank holiday is left out of a weekday baseline: Easter Monday 6 April 2026
easter_tue = server.date(2026, 4, 7)
e_start = server.uk_midnight_utc(easter_tue) + 17 * 3600
rows = []
d = easter_tue
for _ in range(20):
    d -= server.timedelta(days=1)
    kwh = 5.0 if d == server.date(2026, 4, 6) or d == server.date(2026, 4, 3) else 0.5
    rows.append(('elec', server.uk_midnight_utc(d) + 17 * 3600, kwh, 'meter', 100.0))
s2.write(server.UPSERT_HOME, rows, history=False)
slots_bh, _ = h2._baseline(e_start, e_start + 1800, set())
check('bank holidays left out of weekday baselines', slots_bh == [0.5], slots_bh)
check('bank holiday sessions use weekend days', h2._baseline(server.uk_midnight_utc(server.date(2026, 4, 6)) + 17 * 3600,
      server.uk_midnight_utc(server.date(2026, 4, 6)) + 17 * 3600 + 1800, set())[0] is not None)

# Octoplus failing doesn't make the feed look broken
mock.FAIL.add('octopus-backend')
home.next['sessions'] = 0
check('sessions failure kept off the feed', home.tick(now + 60) == '' and 'sessions' in home.extra_problem, home.extra_problem)
mock.FAIL.clear()
home.next['sessions'] = 0
home.tick(now + 120)
check('sessions recover', 'sessions' not in home.extra_problem, home.extra_problem)

# ---- live readings: kept for 24 hours, shown raw for an hour and averaged for longer ----

stored = store.scalar('SELECT COUNT(*) FROM home_live')
check('live readings stored', stored >= 600, stored)   # two hours caught up on the first run
one, six, day = home.view(now, 1)['live'], home.view(now, 6)['live'], home.view(now, 24)['live']
check('an hour of raw readings', one['step'] == 10 and 340 <= len(one['points']) <= 362, (one['step'], len(one['points'])))
check('six hours in one-minute averages', six['step'] == 60 and len(six['points']) <= 361, (six['step'], len(six['points'])))
check('a day in four-minute averages', day['step'] == 240 and len(day['points']) <= 361, (day['step'], len(day['points'])))
check('odd ranges fall back to an hour', home.view(now, 5)['live']['hours'] == 1)
check('averages are averages', all(130 <= p[1] <= 3500 for p in six['points']))
restarted = homemod.Home(store, server.Status(), request=request, key=mock.OCTOPUS_KEY, account=mock.OCTOPUS_ACCOUNT, base=BASE, backend=BACKEND)
check('latest reading survives a restart', restarted.live and restarted.live['t'] == home.live['t'])
calls.clear()
home.next['live'] = 0
home.tick(now + 30)
sent = [data for _, _, data in calls if data and b'smartMeterTelemetry' in data]
check('later polls only fetch what is new', sent and b'TEN_SECONDS' in sent[0] and
      json.loads(sent[0])['query'].count('readAt') >= 1 and
      server.parse_time(json.loads(sent[0])['query'].split('start: "')[1].split('"')[0]) >= home.live['t'] - 400,
      sent[0][:300] if sent else None)

# ---- staying inside Octopus's limit: about 100 requests an hour per account ----


def hour(mode, at):
    calls.clear()
    home.token_expires = float('inf')   # the mock's tokens expire in real time, not the simulated clock
    clock = now + at * 3600
    end = clock + 3600
    while clock < end:
        if mode == 'page':
            home.viewed = clock
        elif mode == 'display':
            home.displayed = clock
        home.tick(clock)
        clock += 10
    return len(calls)


home.viewed = home.displayed = 0
idle, watched, displayed = hour('idle', 7), hour('page', 9), hour('display', 11)
print('Octopus requests in an hour: %d idle, %d with the page open, %d with a Display running' % (idle, watched, displayed))
check('idle hour', idle <= 20, idle)
check('viewing hour', 55 <= watched <= 75, watched)
check('display hour', 28 <= displayed <= 40, displayed)

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
