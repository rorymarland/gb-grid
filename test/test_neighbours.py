#!/usr/bin/env python3
"""Tests for the neighbours' carbon intensity (RTE for France, Energinet for Denmark): hand
calculations, then the mock (run: python3 test/test_neighbours.py)."""
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
sys.argv = [sys.argv[0], '0', APP]
import mock_upstream as mock  # noqa: E402
from gridlive import neighbours as nb  # noqa: E402
from gridlive.grid import Grid  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ---- by hand ---------------------------------------------------------------------------
carbon = {'France': {'v': 35}, 'Norway': {'v': 25}, 'Ireland': {'v': 458}}
# 2,000 MW from France at 35 and 1,000 MW from Norway at 25; Ireland is exporting, so it doesn't count
check('imports: weighted by what comes in', close(nb.imports_carbon({'France': 2000, 'Norway': 1000, 'Ireland': -500}, carbon), (2000 * 35 + 1000 * 25) / 3000))
check('imports: nothing coming in', nb.imports_carbon({'France': -2000, 'Ireland': 0.5}, carbon) is None)
check('imports: a country without a figure is left out', close(nb.imports_carbon({'France': 1000, 'Atlantis': 1000}, carbon), 35.0))

# Britain's own generation on NESO's factors: 10 GW of gas at 394 among 20 GW of generation
m = Grid.mix({'CCGT': 10000, 'NUCLEAR': 5000, 'WIND': 5000, 'INTFR': 2000, 'INTIRL': -500}, 0, 0)
check('mix: generation intensity leaves imports out', close(m['generation_ci'], 10000 * 394 / 20000), m['generation_ci'])
check('mix: the estimate still counts imports', close(m['carbon_estimate'], (10000 * 394 + 2000 * 53) / 22000), m['carbon_estimate'])

store = server.Store(tempfile.mkdtemp())
now = 1_794_600_000 - 1_794_600_000 % 1800
store.write('INSERT INTO ext_ci (zone, t, v) VALUES (?, ?, ?)',
            [('FR', now - 1800, 30), ('FR', now - 1500, 40), ('FR', now - 1200, 50), ('FR', now - 900, 44),
             ('DK1', now - 3 * 3600, 120)], history=False)
check('half hours: averaged', nb.half_hours(store, 'FR', now - 1800, now) == [[now - 1800, 41.0]], nb.half_hours(store, 'FR', now - 1800, now))
c = nb.country_carbon(store, now)
check('now: France live, its latest figure', c['France'] == {'v': 44, 'live': True, 't': now - 900, 'source': 'RTE', 'what': 'French generation'}, c['France'])
check('now: Denmark three hours old, so the estimate', c['Denmark'] == {'v': 150, 'live': False, 'source': 'estimate'}, c['Denmark'])
check('now: the rest typical', c['Netherlands'] == {'v': 474, 'live': False, 'source': 'NESO'} and c['Norway'] == {'v': 25, 'live': False, 'source': 'estimate', 'note': 'almost all hydro'})
check('now: nothing from the future', nb.country_carbon(store, now - 1700)['France']['t'] == now - 1800)

# The week: means only over half hours every series has
week_store = server.Store(tempfile.mkdtemp())
start = now - nb.BACKFILL + 1800
week_store.write('INSERT INTO ci (region, t, forecast, actual, idx) VALUES (0, ?, ?, ?, ?)',
                 [(start + k * 1800, 999, 100 + (k % 2) * 100, 'low') for k in range(100)], history=False)
week_store.write('INSERT INTO ext_ci (zone, t, v) VALUES (?, ?, ?)', [('FR', start + k * 1800, 40) for k in range(60)] +
                 [('DK1', start + k * 1800 + 300, 80) for k in range(10, 70)], history=False)
w = nb.week(week_store, now)
check('week: Britain uses the actual over the forecast', w['series']['GB'][0][1] == 100)
# Common half hours: 10 to 59, fifty of them, half at 100 and half at 200
check('week: common half hours', w['common_hours'] == 25.0, w['common_hours'])
check('week: means over them', w['means'] == {'GB': 150, 'FR': 40, 'DK1': 80}, w['means'])

# ---- from the mock ---------------------------------------------------------------------
httpd = ThreadingHTTPServer(('127.0.0.1', 0), mock.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = 'http://127.0.0.1:%d' % httpd.server_address[1]
nb.ODRE, nb.ENERGINET = base + '/odre', base + '/energinet'
live = server.Store(tempfile.mkdtemp())
job = nb.Neighbours(live)
t0 = time.time()
got = job.fetch_france(t0 - 2 * 86400)
check('France: two days, a page of 100 at a time', 185 <= got <= 192, got)
t = live.scalar("SELECT MAX(t) FROM ext_ci WHERE zone = 'FR'")
check('France: as RTE gives it', live.scalar("SELECT v FROM ext_ci WHERE zone = 'FR' AND t = ?", (t,)) == mock.france_ci(t))
check('France: every quarter hour', live.scalar("SELECT COUNT(*) FROM ext_ci WHERE zone = 'FR' AND t % 900 != 0") == 0)
got = job.fetch_denmark(3)
check('Denmark: three hours of five-minute figures', 34 <= got <= 36, got)
t = live.scalar("SELECT MAX(t) FROM ext_ci WHERE zone = 'DK1'")
check('Denmark: western Denmark only, in UTC', live.scalar("SELECT v FROM ext_ci WHERE zone = 'DK1' AND t = ?", (t,)) == mock.denmark_ci(t)
      and live.scalar("SELECT COUNT(DISTINCT zone) FROM ext_ci") == 2)

fresh = server.Store(tempfile.mkdtemp())
job = nb.Neighbours(fresh)
mock.ENERGINET_ASKED.clear()
job.poll()
check('poll: a week of France', fresh.scalar("SELECT COUNT(*) FROM ext_ci WHERE zone = 'FR'") >= 7 * 96 - 2)
check('poll: a week of Denmark in one request', fresh.scalar("SELECT COUNT(*) FROM ext_ci WHERE zone = 'DK1'") >= 7 * 288 - 2 and len(mock.ENERGINET_ASKED) == 1)
fresh.write('INSERT INTO ext_ci (zone, t, v) VALUES (?, ?, ?)', [('FR', 1000, 1)], history=False)
mock.ENERGINET_GAP = 60
try:
    job.poll()
    check('poll: Energinet refusing is reported', False)
except server.FetchError as error:
    check('poll: says which', 'Denmark' in str(error) and 'France' not in str(error), str(error))
mock.ENERGINET_GAP = None
check('poll: France still kept up to date when Denmark fails', fresh.scalar("SELECT MAX(t) FROM ext_ci WHERE zone = 'FR'") >= time.time() - 1900)
check('poll: old figures pruned', fresh.scalar('SELECT COUNT(*) FROM ext_ci WHERE t < ?', (time.time() - nb.KEEP,)) == 0)

nb.ODRE = base + '/nothing'
try:
    nb.Neighbours(server.Store(tempfile.mkdtemp())).fetch_france(t0 - 3600)
    check('France: a failure is raised', False)
except server.FetchError:
    pass

w = nb.week(fresh, time.time())
check('week: France and Denmark from the mock', len(w['series']['FR']) >= 335 and len(w['series']['DK1']) >= 335)

httpd.shutdown()
print('neighbour tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
