#!/usr/bin/env python3
"""Tests for what's being built (NESO's TEC register): hand calculations, then the mock
(run: python3 test/test_pipeline.py)."""
import os
import sys
import tempfile
import threading
from datetime import date, timedelta
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, '..', 'app')
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

import server  # noqa: E402
sys.argv = [sys.argv[0], '0', APP]
import mock_upstream as mock  # noqa: E402
from gridlive import pipeline as pl  # noqa: E402
from gridlive.timeutil import uk_midnight_utc  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


# ---- what kind of plant ------------------------------------------------------------------
cases = {
    'Energy Storage System': 'battery',
    'Demand;Energy Storage System;Reactive Compensation': 'battery',
    'Energy Storage System;PV Array (Photo Voltaic/solar)': 'solar',
    'Demand;PV Array (Photo Voltaic/solar)': 'solar',
    'Wind Offshore': 'offshore', 'Energy Storage System;Wind Onshore': 'onshore',
    'CCGT (Combined Cycle Gas Turbine);Energy Storage System;OCGT (Open Cycle Gas Turbine)': 'gas',
    'Oil & AGT (Advanced Gas Turbine)': 'gas', 'Gas Reciprocating': 'gas', 'CHP (Combined Heat and Power)': 'gas',
    'Pump Storage': 'pumped', 'Energy Storage System;Pump Storage;Thermal': 'mixed', 'Hydro': 'hydro', 'Nuclear': 'nuclear',
    'Energy Storage System;PV Array (Photo Voltaic/solar);Wind Onshore': 'mixed',
    'Biomass': 'other', 'Tidal': 'other', 'Hydrogen': 'other', 'Waste': 'other',
    'Reactive Compensation': 'demand', 'Substation': 'demand', 'Interconnector': 'interconnector',
    'LAES (Liquid Air Energy Storage)': 'battery', '': 'other', None: 'other',
}
for text, kind in cases.items():
    check('kind: %s' % text, pl.plant_kind(text) == kind, pl.plant_kind(text))

# ---- reading rows ------------------------------------------------------------------------
rows = pl.parse_rows([
    {'Project Name': ' A ', 'Customer Name': 'C', 'Connection Site': 'S', 'MW Connected': 0.0, 'MW Increase / Decrease': 92.4,
     'Cumulative Total Capacity (MW)': 92.4, 'MW Effective From': '2034-10-31', 'Project Status': 'Scoping', 'Agreement Type': 'Embedded',
     'HOST TO': 'NGET', 'Plant Type': 'Energy Storage System', 'Project Number': 'PRO-1', 'Gate': 1},
    {'Project Name': 'B', 'MW Connected': 400, 'MW Increase / Decrease': None, 'MW Effective From': '2020-01-01T00:00:00',
     'Project Status': 'Built', 'Plant Type': 'Wind Offshore', 'Project Number': 'PRO-2', 'Gate': None},
    {'Project Name': 'no status', 'Project Status': 'Withdrawn', 'Project Number': 'PRO-3'},
    {'Project Name': 'no number', 'Project Status': 'Built'},
])
check('rows: two kept', len(rows) == 2, rows)
check('rows: a queuing one', rows[0] == ('PRO-1', 'A', 'C', 'S', 'NGET', 'Embedded', 'scoping', 'Energy Storage System', 'battery', 0.0, 92.4, 92.4, '2034-10-31', '1'), rows[0])
check('rows: a built one, total from its parts', rows[1][6] == 'built' and rows[1][9] == 400 and rows[1][10] == 0 and rows[1][11] == 400 and rows[1][12] == '2020-01-01' and rows[1][13] == '', rows[1])

# ---- the page, by hand -------------------------------------------------------------------
store = server.Store(tempfile.mkdtemp())
today = date(2026, 11, 15)
now = uk_midnight_utc(today) + 12 * 3600


def row(num, status, kind_text, connected, change, eff, gate='', agreement='Direct Connection', name=None):
    return (num, name or num, 'Cust', 'Site ' + num, 'NGET', agreement, status, kind_text, pl.plant_kind(kind_text), connected, change,
            connected + change, eff, gate)


store.write('INSERT INTO tec (num, name, customer, site, host, agreement, status, plant, kind, connected, change, total, eff, gate) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', [
                row('b1', 'built', 'Wind Offshore', 1000, 0, '2019-05-01'),
                row('b2', 'built', 'CCGT (Combined Cycle Gas Turbine)', 800, 200, '2027-01-01'),     # an extension still to come
                row('q1', 'building', 'Energy Storage System', 0, 300, '2027-01-30', '2'),              # due within the year
                row('q2', 'consented', 'Energy Storage System', 0, 100, '2027-11-14', '1'),            # the day before a year is up
                row('q3', 'scoping', 'Energy Storage System;PV Array (Photo Voltaic/solar)', 0, 50, '2027-11-15', '1'),  # a year on: not within it
                row('q4', 'awaiting', 'Wind Onshore', 0, 40, '2025-06-01', '', 'Embedded'),            # date passed, not built
                row('q5', 'scoping', 'Wind Offshore', 0, 1500, None),                                  # no date
                row('q6', 'scoping', 'Reactive Compensation', 0, 10, '2030-01-01'),
            ], history=False)
v = pl.view(store, now)
t = v['totals']
check('totals: connected is what built projects have', t['built'] == 1800 and t['increase_built'] == 200, t)
check('totals: the queue', t['pipeline'] == 300 + 100 + 50 + 40 + 1500 + 10 and t['n_pipeline'] == 6, t)
check('totals: the next 12 months, up to the day before', t['year_ahead'] == 400 and t['n_year_ahead'] == 2, t)
check('totals: embedded', t['embedded'] == 40)
check('late and undated', v['late'] == {'mw': 40, 'n': 1} and v['undated'] == {'mw': 1500, 'n': 1}, (v['late'], v['undated']))
check('soon: largest first', [p['num'] for p in v['soon']] == ['q1', 'q2'] and v['soon'][0]['eff'] == '2027-01-30' and v['soon'][0]['gate'] == '2')
by = {k['kind']: k for k in v['kinds']}
check('kinds: batteries', by['battery']['building'] == 300 and by['battery']['consented'] == 100 and by['battery']['n'] == 2 and by['battery']['built'] == 0, by['battery'])
check('kinds: offshore wind, built and scoping', by['offshore']['built'] == 1000 and by['offshore']['scoping'] == 1500, by['offshore'])
check('kinds: gas counts its connected only', by['gas']['built'] == 800 and by['gas']['n'] == 0)
check('kinds: the biggest queue first', v['kinds'][0]['kind'] == 'offshore', [k['kind'] for k in v['kinds']])
check('kinds: none empty', 'nuclear' not in by)
years = {y['year']: y for y in v['years']}
check('years: from this year, by stage', sorted(years) == [2027, 2030] and years[2027] == {'year': 2027, 'building': 300, 'consented': 100, 'awaiting': 0, 'scoping': 50}, v['years'])
check('gates', v['gates'] == {'2': 300, '1': 150, '': 1550}, v['gates'])
check('nothing yet', pl.view(server.Store(tempfile.mkdtemp()), now) == {'ready': False})

# ---- from the mock -----------------------------------------------------------------------
httpd = ThreadingHTTPServer(('127.0.0.1', 0), mock.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
pl.NESO = 'http://127.0.0.1:%d/neso' % httpd.server_address[1]
live = server.Store(tempfile.mkdtemp())
job = pl.Pipeline(live)
mock.TEC_ASKED.clear()
check('poll: every project', job.poll() == 400 and live.scalar('SELECT COUNT(*) FROM tec') == 400)
check('poll: in one request', mock.TEC_ASKED == [str(pl.LIMIT)], mock.TEC_ASKED)
check('poll: nothing again until NESO changes it', job.poll() == 0 and len(mock.TEC_ASKED) == 1)
records = mock.tec_records()
built = sum(r['MW Connected'] for r in records if r['Project Status'] == 'Built')
check('snapshot: built total kept', round(live.scalar("SELECT SUM(mw) FROM tec_hist WHERE status = 'built'")) == round(built))
check('snapshot: by the day NESO published it', live.query('SELECT DISTINCT d FROM tec_hist') == [('2026-11-13',)])
mock.TEC_VERSION[0] = '2026-11-17T10:00:00'
check('poll: a new version read again', job.poll() == 400 and len(mock.TEC_ASKED) == 2)
v = pl.view(live, now)
check('trend: two versions', [p['d'] for p in v['trend']] == ['2026-11-13', '2026-11-17'] and v['trend'][0]['pipeline'] == v['trend'][1]['pipeline'], v['trend'])
check('view: from the mock', v['ready'] and v['projects'] == 400 and v['version'] == '2026-11-17' and v['totals']['built'] == round(built))
mock.TEC_VERSION[0] = '2026-11-13T16:45:18.918641'
real = mock.tec_records


def short():
    return real()[:50]


mock.tec_records = short
live.set_meta('tec_version', 'old')
try:
    job.poll()
    check('poll: a short register is refused', False)
except server.FetchError as error:
    check('poll: says why', 'too short' in str(error), str(error))
check('poll: the last good register kept', live.scalar('SELECT COUNT(*) FROM tec') == 400)
mock.tec_records = real

httpd.shutdown()
print('pipeline tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
