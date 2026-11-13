#!/usr/bin/env python3
"""Tests for the Constraints page: figures against hand calculations, and polling NESO's
constraint data from the mock (run: python3 test/test_constraints.py)."""
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
sys.argv = [sys.argv[0], '0', APP]   # the mock reads its port and app folder from argv
import mock_upstream as mock  # noqa: E402
from gridlive import constraints as cn  # noqa: E402
from gridlive.timeutil import local_epoch, settlement_start, uk_clock  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ---- small helpers -----------------------------------------------------------

check('code: hyphen', cn.code('SSE-SP') == 'SSE_SP')
check('code: spaces and case', cn.code(' sse sp ') == 'SSE_SP')
check('label: known', cn.label('SCOTEX') == ('Scotland to England', 'B6'))
check('label: unknown', cn.label('ESTEX') == (None, None))
check('file end: four-digit year', cn.file_end('Wind BOA Volumes 2025/26') == date(2026, 3, 31))
check('file end: two-digit year', cn.file_end('Thermal Constraint Costs Data 26-27') == date(2027, 3, 31))
check('file end: no year', cn.file_end('Wind BOA Volumes') is None)

# summary(): ratios 0.5, 0.95, 1.2 and -0.1 (flowing the other way); the zero limit is left out
# of the ratios but still counted in n. Mean of max(0, ratio): (0.5 + 0.95 + 1.2 + 0) / 4 = 0.6625
peak, mean, near, over, n, lim, maxflow = cn.summary([(500, 1000), (950, 1000), (1200, 1000), (-100, 1000), (300, 0)])
check('summary: peak', peak == 1.2, peak)
check('summary: mean', close(mean, 0.6625), mean)
check('summary: near (90% or more)', near == 2, near)
check('summary: over', over == 1, over)
check('summary: n counts every half hour', n == 5, n)
check('summary: mean limit', lim == 1000, lim)
check('summary: highest flow', maxflow == 1200, maxflow)
check('summary: no limits', cn.summary([(10, 0), (20, None)]) == (0.0, 0.0, 0, 0, 2, 0, 20))

# ---- the page's figures, from rows set by hand ---------------------------------

store = server.Store(tempfile.mkdtemp())
TODAY = date(2026, 11, 13)
NOW = local_epoch(TODAY, 12, 0)
iso = date.isoformat

store.write('INSERT INTO bday (grp, d, peak, mean, near, over, n, lim, maxflow) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', [
    ('SCOTEX', '2026-11-14', 1.5, 0.8, 10, 6, 48, 5000, 7500),     # tomorrow
    ('SSE_SP', '2026-11-14', 0.95, 0.5, 2, 0, 48, 3600, 3420),
    ('SCOTEX', '2026-11-13', 1.2, 0.7, 5, 3, 48, 5000, 6000),      # today: left out of the totals
    ('SCOTEX', '2026-11-12', 1.1, 0.7, 3, 2, 48, 5000, 5500),
    ('SCOTEX', '2026-10-20', 1.3, 0.7, 8, 4, 48, 5000, 6500),
    ('SCOTEX', '2026-10-01', 1.4, 0.7, 12, 10, 48, 5000, 7000),    # more than 30 days ago
    ('SSHARN3', '2026-11-01', 1.2, 0.6, 7, 5, 48, 5500, 6600),     # two variants of one cost code
    ('SSHARN', '2026-11-01', 1.05, 0.6, 2, 1, 48, 6000, 6300),
], history=False)
store.write('INSERT INTO bcost (grp, d, cost) VALUES (?, ?, ?)', [
    ('SCOTEX', '2026-11-10', 1000000), ('SCOTEX', '2026-09-01', 2500000), ('SSHARN', '2026-11-05', 400000)], history=False)
store.write('INSERT INTO cday (d, thermal, voltage, inertia, loss, thermal_mwh) VALUES (?, ?, ?, ?, ?, ?)', [
    ('2026-11-05', 10e6, 1e6, 0.5e6, 0.5e6, 20000), ('2026-10-01', 5e6, 0, 0, 0, 9000)], history=False)
store.write('INSERT INTO wcurt (d, unit, name, mwh) VALUES (?, ?, ?, ?)', [
    ('2026-11-12', 'SGRWO-1', 'Seagreen 1', 30000), ('2026-11-12', 'SGRWO-2', 'Seagreen 2', 20000),
    ('2026-11-13', 'SGRWO-1', 'Seagreen 1', 50), ('2026-09-01', 'MOWWO-1', 'Moray West 1', 1000)], history=False)
store.write('INSERT INTO bflow (grp, t, flow, lim) VALUES (?, ?, ?, ?)', [
    ('SCOTEX', NOW - 8 * 86400, 1, 5000),        # older than the chart's week
    ('SCOTEX', NOW - 3600, 5500, 5000), ('SCOTEX', NOW - 1800, 6000, 5000), ('SCOTEX', NOW, 4000, 5000),
    ('SSE_SP', NOW, 3000, 3600)], history=False)
# 25 complete days of transmission wind at 4,000 MW: 4,000 x 48 / 2 = 96,000 MWh a day
lh = []
for k in range(25):
    d = date(2026, 10, 19) + timedelta(days=k)
    lh += [(settlement_start(d, sp), 4000) for sp in range(1, 49)]
store.write('INSERT INTO lh (t, wind) VALUES (?, ?)', lh, history=False)

v = cn.view(store, None, NOW)
check('view: days', v['days']['today']['d'] == '2026-11-13' and v['days']['tomorrow']['d'] == '2026-11-14', v['days'])
tomorrow = v['days']['tomorrow']['groups']
check('view: tomorrow ranked by peak', [g['code'] for g in tomorrow] == ['SCOTEX', 'SSE_SP'], tomorrow)
check('view: tomorrow labels', tomorrow[0]['label'] == 'Scotland to England' and tomorrow[0]['boundary'] == 'B6' and tomorrow[0]['over'] == 6)
check('view: focus defaults to SCOTEX', v['focus'] == 'SCOTEX')
check('view: series is the last week', [p[1] for p in v['series']] == [5500, 6000, 4000], v['series'])
check('view: focus asked for', cn.view(store, None, NOW, 'SSE_SP')['focus'] == 'SSE_SP')
check('view: unknown focus falls back', cn.view(store, None, NOW, 'NOPE')['focus'] == 'SCOTEX')

groups = {g['code']: g for g in v['groups']}
sc = groups.get('SCOTEX', {})
# 30 days back from 13 November is 14 October: 20 October and 12 November count; today doesn't
check('view: over30', sc.get('over30') == 2 + 4, sc)
check('view: near30', sc.get('near30') == 3 + 8, sc)
check('view: n30', sc.get('n30') == 96, sc)
check('view: over365', sc.get('over365') == 2 + 4 + 10 and sc.get('n365') == 144, sc)
check('view: cost30', sc.get('cost30') == 1000000, sc)
check('view: cost365', sc.get('cost365') == 3500000, sc)
sh = groups.get('SSHARN', {})
check('view: variants merged under the cost code', sorted(sh.get('variants', [])) == ['SSHARN', 'SSHARN3'] and 'SSHARN3' not in groups, v['groups'])
check('view: merged row takes the variant most often over', sh.get('over365') == 5 and sh.get('n365') == 48, sh)
check('view: merged row keeps its cost', sh.get('cost30') == 400000 and sh.get('flows') is True, sh)
check('view: groups by cost', [g['code'] for g in v['groups']][:2] == ['SCOTEX', 'SSHARN'], v['groups'])

c = v['costs']
check('view: latest cost day', c['latest'] == {'d': '2026-11-05', 'cost': 12000000}, c['latest'])
check('view: costs, 30 days', c['d30'] == {'cost': 12000000, 'days': 1}, c['d30'])
check('view: costs, 365 days', c['d365'] == {'cost': 17000000, 'days': 2}, c['d365'])
# The chart's 60 days go back to 14 September, so both days are in it
check('view: cost days', c['days'] == [['2026-10-01', 5000000, 0, 0, 0], ['2026-11-05', 10000000, 1000000, 500000, 500000]], c['days'])

w = v['wind']
check('view: wind latest day', w['latest'] == '2026-11-13' and w['latest_mwh'] == 50, (w['latest'], w['latest_mwh']))
check('view: wind 30 days', w['mwh30'] == 50050, w['mwh30'])
check('view: wind 365 days', w['mwh365'] == 51050, w['mwh365'])
check('view: farms grouped by name', [(f['n'], f['mwh']) for f in w['farms30']] == [('Seagreen', 50050)], w['farms30'])
# Share over the 25 days with both figures: 50,000 / (25 x 96,000 + 50,000) = 0.020408
share = w['share'] or {}
check('view: share days', share.get('days') == 25 and share.get('from') == '2026-10-19' and share.get('to') == '2026-11-12', share)
check('view: share made', share.get('made') == 2400000, share)
check('view: share down', share.get('down') == 50000, share)
check('view: share', share.get('share') == 0.0204, share)

# Too few complete days for a fair share: no figure rather than a misleading one
short = server.Store(tempfile.mkdtemp())
short.write('INSERT INTO lh (t, wind) VALUES (?, ?)', lh[:19 * 48], history=False)
check('view: no share under 20 days', cn.view(short, None, NOW)['wind']['share'] is None)

# _summarise_raw: each UK day's summary from the half hours
raw = server.Store(tempfile.mkdtemp())
c1 = cn.Constraints(raw)
t0 = local_epoch(TODAY, 0, 0)
raw.write('INSERT INTO bflow (grp, t, flow, lim) VALUES (?, ?, ?, ?)', [
    ('SCOTEX', t0, 4500, 5000), ('SCOTEX', t0 + 1800, 6000, 5000), ('SCOTEX', t0 - 1800, 9999, 5000)], history=False)
c1._summarise_raw()
row = raw.query('SELECT peak, mean, near, over, n, lim, maxflow FROM bday WHERE grp = ? AND d = ?', ('SCOTEX', '2026-11-13'))
# 0.9 and 1.2: mean 1.05, one near (0.9 counts), one over; the 23:30 half hour belongs to 12 November
check('raw summary: today', row == [(1.2, 1.05, 2, 1, 2, 5000, 6000)], row)
check('raw summary: day before', raw.query('SELECT n FROM bday WHERE d = ?', ('2026-11-12',)) == [(1,)])

# ---- polling the mock ----------------------------------------------------------

httpd = ThreadingHTTPServer(('127.0.0.1', 0), mock.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
cn.NESO = 'http://127.0.0.1:%d/neso' % httpd.server_address[1]

live = server.Store(tempfile.mkdtemp())
job = cn.Constraints(live)
started = time.time()
job.poll()
now = time.time()
check('poll: flows', live.scalar('SELECT COUNT(*) FROM bflow') > 1000)
check('poll: daily summaries back a year', live.scalar('SELECT COUNT(DISTINCT d) FROM bday') >= 365, live.scalar('SELECT COUNT(DISTINCT d) FROM bday'))
check('poll: costs', live.scalar('SELECT COUNT(*) FROM bcost') > 300)
check('poll: cost codes normalised (SSE-SP)', live.scalar("SELECT COUNT(*) FROM bcost WHERE grp = 'SSE_SP'") > 0
      and live.scalar("SELECT COUNT(*) FROM bcost WHERE grp LIKE '%-%'") == 0)
check('poll: breakdown', live.scalar('SELECT COUNT(*) FROM cday') > 100)

# Wind turned down: the mock's MW in each half hour, halved to MWh, by hand
yesterday = uk_clock(now)[0] - timedelta(days=1)
for unit, name, cap in mock.BOA_UNITS[:3]:
    by_hand = sum(mock.boa_mw(unit, cap, settlement_start(yesterday, sp)) for sp in range(1, 49)) / 2
    got = live.scalar('SELECT mwh FROM wcurt WHERE d = ? AND unit = ?', (iso(yesterday), unit))
    check('poll: %s MWh yesterday' % unit, (got or 0) == 0 if by_hand == 0 else close(got, by_hand, 0.02), (got, by_hand))
hh = live.scalar('SELECT SUM(mwh) FROM wcurt_hh WHERE t >= ? AND t < ?', (settlement_start(yesterday, 1), settlement_start(yesterday, 1) + 86400))
units = live.scalar('SELECT SUM(mwh) FROM wcurt WHERE d = ?', (iso(yesterday),))
check('poll: half hours add up to the units', close(hh or 0, units or 0, 0.5), (hh, units))

page = cn.view(live, None, now)
check('poll: the page has tomorrow or today', bool(page['days']['today']['groups'] or page['days']['tomorrow']['groups']))
check('poll: a year in a reasonable time', time.time() - started < 120, time.time() - started)

# A second poll only fetches what's new and changes nothing it shouldn't
before = live.scalar('SELECT COUNT(*) FROM wcurt')
job.poll()
check('poll: second poll is stable', live.scalar('SELECT COUNT(*) FROM wcurt') == before)

httpd.shutdown()
print('constraints tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
