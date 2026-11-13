#!/usr/bin/env python3
"""Tests for National Gas's flows, linepack, allocations and price: hand calculations, then the
mock (run: python3 test/test_gas.py)."""
import os
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, '..', 'app')
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

import server  # noqa: E402
sys.argv = [sys.argv[0], '0', APP]
import mock_upstream as mock  # noqa: E402
from gridlive import gas as gs  # noqa: E402
from gridlive.timeutil import local_epoch  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ---- by hand ---------------------------------------------------------------------------
# 52 mcm/d at 39.5 MJ/m³: 52e6 × 39.5e6 J / 86,400 s = 23.77 GW
check('mcm/d to GW', close(gs.mcmd_to_gw(52), 52e6 * 39.5e6 / 86400 / 1e9) and close(round(gs.mcmd_to_gw(52), 1), 23.8))
check('source: Langeled is Norway', gs.source_of('EASINGTON LANGELED') == 'norway')
check('source: Grain is LNG', gs.source_of('Grain NTS 1') == 'lng')
check('source: Bacton BBL is the continent', gs.source_of('BACTON BBL') == 'continent')
check('source: Rough is storage', gs.source_of('EASINGTON ROUGH ST') == 'storage')
check('source: an unknown site is other', gs.source_of('NEW TERMINAL') == 'other')
check('use: by words in the category', [gs.use_of(n) for n in ('LDZ Offtake Flow', 'Power Station Demand Flow', 'Industrial Demand Flow',
                                                               'Storage Demand Flow', 'Interconnector Export Demand Flow', 'NTS Demand Flow')] ==
      ['ldz', 'power', 'industrial', 'storage', 'exports', None])

# The gas day runs 05:00 to 05:00 UK time, in winter (GMT) and summer (BST)
winter, summer = date(2026, 1, 15), date(2026, 7, 15)
check('gas day: winter start is 05:00 GMT', gs.gas_day_start(winter) == local_epoch(winter, 5, 0) and time.gmtime(gs.gas_day_start(winter)).tm_hour == 5)
check('gas day: summer start is 04:00 UTC', time.gmtime(gs.gas_day_start(summer)).tm_hour == 4)
check('gas day: 04:59 belongs to the day before', gs.gas_day(gs.gas_day_start(winter) - 60) == winter - timedelta(days=1))
check('gas day: 05:00 begins it', gs.gas_day(gs.gas_day_start(winter)) == winter)
check('gas day: 23:00 BST', gs.gas_day(local_epoch(summer, 23, 0)) == summer)
check('gas day: 03:00 BST the next morning', gs.gas_day(local_epoch(summer + timedelta(days=1), 3, 0)) == summer)


def flows_payload(t, sites, supply, uses, demand, linepack):
    def site(name, v):
        return {'siteName': name, 'siteGasDetail': [{'applicableAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t)), 'flowRate': v}]}
    return {'instantaneousFlow': [
        {'description': 'Supply from entry points', 'sites': [site(n, v) for n, v in sites.items()]},
        {'description': 'Total Supply', 'sites': [site('TOTAL SUPPLY', supply)]},
        {'description': 'Demand by category', 'sites': [site(n, v) for n, v in uses.items()]},
        {'description': 'Total Demand', 'sites': [site('NTS Demand Flow', demand)]},
        {'description': 'Actual linepack', 'sites': [site('NTS Actual Linepack', linepack)]},
    ]}


store = server.Store(tempfile.mkdtemp())
job = gs.Gas(store)
day = date(2026, 1, 15)
start = gs.gas_day_start(day)
# Norway 70, the North Sea 40 + 20 = 60, LNG 50, storage 10, with 200 in total: 10 unaccounted for, so other
sites = {'EASINGTON LANGELED': 70.0, 'ST FERGUS NSMP': 40.0, 'TEESSIDE CATS': 20.0, 'GRAIN NTS 1': 50.0, 'HORNSEA': 10.0}
uses = {'LDZ Offtake Flow': 150.0, 'Power Station Demand Flow': 52.0, 'Industrial Demand Flow': 4.0, 'Interconnector Export Demand Flow': 14.0}
readings = [(start + 120, 340.0), (start + 3600, 338.5), (start + 7200, 336.0)]
for t, pack in readings:
    job._request = lambda path, body=None, t=t, pack=pack: flows_payload(t, sites, 200.0, uses, 220.0, pack)
    check('flows: a reading', job.fetch_flows() == 1)
got = dict(store.query('SELECT k, v FROM gas_flow WHERE t = ?', (start + 7200,)))
check('flows: sources added up', got['src:norway'] == 70 and got['src:northsea'] == 60 and got['src:lng'] == 50 and got['src:storage'] == 10, got)
check('flows: the rest is other', close(got['src:other'], 10.0), got)
check('flows: uses', got['use:ldz'] == 150 and got['use:power'] == 52 and got['use:exports'] == 14 and 'use:None' not in got, got)
check('flows: totals and linepack', got['supply'] == 200 and got['demand'] == 220 and got['linepack'] == 336, got)

# A small gap between the total and the entry points (rounding) is not 'other'
job._request = lambda path, body=None: flows_payload(start + 7320, {'EASINGTON LANGELED': 99.8}, 100.0, {}, 100.0, 336.0)
job.fetch_flows()
check('flows: under half an mcm/d is not other', store.scalar("SELECT COUNT(*) FROM gas_flow WHERE t = ? AND k = 'src:other'", (start + 7320,)) == 0)
store.write('DELETE FROM gas_flow WHERE t = ?', [(start + 7320,)], history=False)

# Daily figures: the latest value for each day wins; energy in kWh shows as GWh
publications = [
    {'publicationId': 'PUBOB30', 'publications': [
        {'applicableFor': '2026-01-15', 'applicableAt': '2026-01-15T09:00:00Z', 'value': '330.0'},
        {'applicableFor': '2026-01-15', 'applicableAt': '2026-01-15T14:00:00Z', 'value': '334.5'},
        {'applicableFor': '2026-01-15', 'applicableAt': '2026-01-15T11:00:00Z', 'value': '331.0'}]},
    {'publicationId': 'PUBOB623', 'publications': [{'applicableFor': '2026-01-15', 'applicableAt': '2026-01-15T04:00:00Z', 'value': '290'}]},
    {'publicationId': 'PUBOB694', 'publications': [{'applicableFor': '2026-01-14', 'applicableAt': '2026-01-15T06:00:00Z', 'value': '339.0'}]},
    {'publicationId': 'NOT_OURS', 'publications': [{'applicableFor': '2026-01-15', 'applicableAt': '2026-01-15T04:00:00Z', 'value': '1'}]},
]
for pid, key, v in (('PUBOBJ1289', 'ndm', 1.2e9), ('PUBOBJ1288', 'dmc', 2.0e8), ('PUBOBJ1287', 'industrial', 4.0e7), ('PUBOBJ1286', 'power', 6.5e8),
                    ('PUBOBJ521', 'storage_in', 0), ('PUBOBJ519', 'exports', 3.0e8), ('PUBOBJ518', 'lng', 5.25e8), ('PUBOBJ515', 'imports', 1.0e8),
                    ('PUBOBJ517', 'storage_out', 2.0e8), ('PUBOB603', 'sap', 7.25)):
    publications.append({'publicationId': pid, 'publications': [{'applicableFor': '2026-01-13', 'applicableAt': '2026-01-15T08:00:00Z', 'value': str(v)}]})
asked = []
job._request = lambda path, body=None: asked.append(body) or publications
check('daily: values stored', job.fetch_daily(date(2026, 1, 5), date(2026, 1, 16), gs.DAILY) == 13)
check('daily: one request with every id', len(asked) == 1 and set(asked[0]['publicationIds']) == set(gs.DAILY) and asked[0]['latestValue'] == 'Y', asked)
check('daily: the latest prediction of closing linepack', store.scalar("SELECT v FROM gas_daily WHERE d = '2026-01-15' AND k = 'pclp'") == 334.5)

payload = gs.view(store, start + 7300, gas_generation=(start + 7000, 7000.0), home_rate=6.1)
check('view: ready', payload['ready'] and payload['gas_day'] == '2026-01-15' and payload['day_start'] == start)
check('view: sources in order', payload['sources'] == [['norway', 70.0], ['northsea', 60.0], ['lng', 50.0], ['storage', 10.0], ['other', 10.0]], payload['sources'])
check('view: uses in order', payload['uses'] == [['ldz', 150.0], ['power', 52.0], ['industrial', 4.0], ['exports', 14.0]], payload['uses'])
check('view: sites largest first', [s[0] for s in payload['sites']][:2] == ['EASINGTON LANGELED', 'GRAIN NTS 1'])
check('view: the day so far, in four-minute steps', [r[0] for r in payload['today']['rows']] == [start + 120 - 120 % 240, start + 3600 - 3600 % 240, start + 7200 - 7200 % 240]
      and payload['today']['rows'][-1][1:] == [200.0, 220.0, 52.0, 336.0], payload['today']['rows'])
check('view: linepack at 05:00 is the first reading', payload['today']['linepack_open'] == 340.0)
check('view: the day\'s publications', payload['today']['pclp'] == 334.5 and payload['today']['demand_fc'] == 290)
# Power stations: 52 mcm/d is 23.77 GW of gas; 7 GW of electricity from it is 29.4%
p = payload['power']
check('view: power station gas', p['gas_mcmd'] == 52.0 and close(p['thermal_gw'], 23.77) and p['elec_gw'] == 7.0, p)
check('view: efficiency', close(p['efficiency'], round(7.0 / gs.mcmd_to_gw(52), 3)) and close(p['efficiency'], 0.294), p)
cols = payload['year_columns']
row = dict(zip(cols, payload['year'][0]))
check('view: a year row, in GWh', row['d'] == '2026-01-13' and row['ndm'] == 1200.0 and row['lng'] == 525.0 and row['storage_in'] == 0 and row['sap'] == 7.25, row)
check('view: days without allocations are left out', len(payload['year']) == 1, payload['year'])
check('view: latest price', payload['sap'] == ['2026-01-13', 7.25] and payload['home_rate'] == 6.1)

# Without a reading near 05:00, the gas day opens at National Gas's closing linepack for the day before
store.write('DELETE FROM gas_flow WHERE t = ?', [(start + 120,)], history=False)
check('view: opening falls back to yesterday\'s close', gs.view(store, start + 7300)['today']['linepack_open'] == 339.0)
check('view: nothing yet', gs.view(server.Store(tempfile.mkdtemp()), start) == {'ready': False})

# ---- from the mock ---------------------------------------------------------------------
httpd = ThreadingHTTPServer(('127.0.0.1', 0), mock.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
gs.API = 'http://127.0.0.1:%d/gas' % httpd.server_address[1]
gs.SPACING = 0
real_sleep = gs.time.sleep
slept = []
gs.time.sleep = lambda s: slept.append(s)
live = server.Store(tempfile.mkdtemp())
job = gs.Gas(live)
now = time.time()
check('mock: six readings', job.fetch_flows() == 6)
t = live.scalar('SELECT MAX(t) FROM gas_flow')
mock_sites, mock_supply, mock_uses, mock_demand, mock_pack = mock.gas_flows(t)
got = dict(live.query('SELECT k, v FROM gas_flow WHERE t = ?', (t,)))
check('mock: Norway is Langeled', close(got['src:norway'], mock_sites['EASINGTON LANGELED'], 1e-3), got)
check('mock: Barrow is listed only in the total, so other', close(got['src:other'], mock.GAS_BARROW, 1e-2), got.get('src:other'))
check('mock: demand and linepack', close(got['demand'], mock_demand) and close(got['linepack'], mock_pack))

# The volume threshold: every id for a year at once is refused, so the backfill asks one id at a time
try:
    job.fetch_daily(date.today() - timedelta(days=366), date.today(), gs.DAILY)
    check('mock: the volume threshold refuses a year of every id', False)
except server.FetchError as error:
    check('mock: the threshold is reported', '400' in str(error) or '2005' in str(error), str(error))
mock.GAS_ASKED.clear()
job.poll()
asked = len(mock.GAS_ASKED)
check('poll: flows, the recent days, then a year one id at a time', asked == 2 + len(gs.DAILY) - 1, asked)
check('poll: spaced out', slept.count(gs.SPACING) >= len(gs.DAILY) - 1, slept)
check('poll: backfilled', job.backfilled())
today = gs.gas_day(now)
check('poll: a year of allocations', live.scalar("SELECT COUNT(*) FROM gas_daily WHERE k = 'ndm'") >= 360)
check('poll: the latest prediction, not the earlier one', live.scalar("SELECT v FROM gas_daily WHERE k = 'pclp' AND d = ?", (today.isoformat(),)) == 347.0)
d = today - timedelta(days=30)
check('poll: an allocation, as published', live.scalar("SELECT v FROM gas_daily WHERE k = 'power' AND d = ?", (d.isoformat(),)) == round(mock.gas_value('power', d), 4))
mock.GAS_ASKED.clear()
job.poll()
check('poll: within three hours, only flows', len(mock.GAS_ASKED) == 1, len(mock.GAS_ASKED))

# Old flows are pruned
live.write('INSERT INTO gas_flow (t, k, v) VALUES (?, ?, ?)', [(now - 4 * 86400, 'demand', 1.0)], history=False)
job.poll()
check('poll: flows over three days old pruned', live.scalar('SELECT COUNT(*) FROM gas_flow WHERE t < ?', (now - gs.KEEP_FLOWS,)) == 0)

# Too many requests: a 429 stops requests for half an hour
mock.GAS_LIMIT = (0, 60)
blocked = gs.Gas(server.Store(tempfile.mkdtemp()))
try:
    blocked.fetch_flows()
    check('429: refused', False)
except server.FetchError:
    pass
check('429: blocked for half an hour', close(blocked.blocked_until - time.time(), gs.BLOCKED_FOR, 5))
mock.GAS_LIMIT = None
before = len(mock.GAS_ASKED)
try:
    blocked.fetch_flows()
    check('429: no request while blocked', False)
except server.FetchError as error:
    check('429: says when it will try again', 'slow down' in str(error))
check('429: nothing asked while blocked', len(mock.GAS_ASKED) == before)

# The page's API
grid_payload = gs.view(live, time.time())
check('api: ready from the mock', grid_payload['ready'] and grid_payload['sources'][0][0] == 'norway' and len(grid_payload['year']) >= 360)

gs.time.sleep = real_sleep
httpd.shutdown()
print('gas tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
