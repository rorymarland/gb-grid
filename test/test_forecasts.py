#!/usr/bin/env python3
"""Tests for the forecast scorecard: scores against hand calculations, the day-ahead snapshots,
and downloading NESO's records from the mock (run: python3 test/test_forecasts.py)."""
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
from gridlive import forecasts as fc  # noqa: E402
from gridlive.timeutil import local_epoch, settlement_start, uk_midnight_utc  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ---- scores by hand -----------------------------------------------------------
# Forecasts 110, 90, 130, 100 against outcomes of 100 each: misses +10, -10, +30, 0
rows = [(0, 110, 100, 1000), (1, 90, 100, 1000), (2, 130, 100, 1000), (3, 100, 100, 2000)]
s = fc.score(rows)
check('score: typical miss', close(s['mae'], 12.5), s)                       # (10 + 10 + 30 + 0) / 4
check('score: lean', close(s['bias'], 7.5), s)                               # (10 - 10 + 30 + 0) / 4
check('score: as a share of the outcome', close(s['pct'], 0.125), s)
check('score: within a tenth', close(s['within'], 0.75), s)                  # three of the four within 10
check('score: share of capacity', close(s['cap_pct'], round((0.01 + 0.01 + 0.03 + 0) / 4, 4)), s)
check('score: latest capacity', s['cap'] == 2000)
check('score: no capacity, no share of it', 'cap_pct' not in fc.score([(0, 1, 2, None)]))
check('score: nothing', fc.score([]) is None)
check('headline by measure', fc.headline(s, 'cap') == s['cap_pct'] and fc.headline(s, 'pct') == s['pct'] and fc.headline(s, 'abs') == s['mae'])

# ---- when a forecast counts as the day before ---------------------------------------
t = local_epoch(date(2026, 7, 2), 9, 0)
check('cutoff: noon the day before, in summer', fc.cutoff(t) == local_epoch(date(2026, 7, 1), 12, 0))
check('cutoff: just after midnight', fc.cutoff(local_epoch(date(2026, 1, 2), 0, 0)) == local_epoch(date(2026, 1, 1), 12, 0))
check('cutoff: clock change day', fc.cutoff(local_epoch(date(2026, 10, 25), 3, 0)) == local_epoch(date(2026, 10, 24), 12, 0))
check('settlement period', fc.day_period('2026-07-02', 1) == settlement_start(date(2026, 7, 2), 1))

# ---- snapshots ---------------------------------------------------------------------
store = server.Store(tempfile.mkdtemp())
f = fc.Forecasts(store)
day = date(2026, 7, 2)
halves = [settlement_start(day, sp) for sp in range(1, 49)]
morning_before = local_epoch(day - timedelta(days=1), 9, 0)
store.write('INSERT INTO ci (region, t, forecast, actual, idx) VALUES (0, ?, ?, NULL, NULL)', [(t, 100) for t in halves], history=False)
store.write("INSERT INTO hh (k, t, v) VALUES ('fc_solar', ?, ?)", [(t, 2000.0) for t in halves], history=False)
f.snapshot(morning_before)
check('snapshot: taken the morning before', store.scalar("SELECT COUNT(*) FROM fsnap WHERE kind = 'ci'") == 48
      and store.scalar("SELECT COUNT(*) FROM fsnap WHERE kind = 'solar'") == 48)
# The forecast changes before noon: the snapshot follows it
store.write('UPDATE ci SET forecast = 120 WHERE region = 0', [()], history=False)
f.snapshot(local_epoch(day - timedelta(days=1), 11, 30))
check('snapshot: follows the forecast until noon', store.scalar("SELECT MIN(fc) FROM fsnap WHERE kind = 'ci'") == 120)
# ... and after noon it doesn't
store.write('UPDATE ci SET forecast = 200 WHERE region = 0', [()], history=False)
f.snapshot(local_epoch(day - timedelta(days=1), 15, 0))
check('snapshot: frozen after noon', store.scalar("SELECT MAX(fc) FROM fsnap WHERE kind = 'ci'") == 120)
# The day happens: actual carbon 100 throughout, solar 1,500 MW
store.write('UPDATE ci SET actual = 100 WHERE region = 0', [()], history=False)
store.write("INSERT INTO hh (k, t, v) VALUES ('pv', ?, ?)", [(t, 1500.0) for t in halves], history=False)
f.snapshot(uk_midnight_utc(day + timedelta(days=1)) + 3600)
check('snapshot: outcomes filled in', store.scalar('SELECT COUNT(*) FROM fsnap WHERE act IS NULL') == 0)
v = fc.view(store, uk_midnight_utc(day + timedelta(days=1)) + 3600)
kinds = {k['key']: k for k in v['kinds']}
check('own: carbon day before scored', kinds['ci']['n'] == 48 and close(kinds['ci']['day']['score']['mae'], 20.0), kinds['ci'].get('day'))
check('own: carbon leans high', close(kinds['ci']['day']['score']['bias'], 20.0))
check('own: solar scored', close(kinds['solar']['day']['score']['mae'], 500.0) and close(kinds['solar']['day']['score']['pct'], 1 / 3, 1e-4), kinds['solar'].get('day'))
check('own: kept since', kinds['ci']['collecting'] == morning_before)
# A snapshot taken long before noon (the dashboard was off that morning) doesn't count as the day before
stale = server.Store(tempfile.mkdtemp())
stale.write('INSERT INTO fsnap (kind, t, fc, made, act) VALUES (?, ?, ?, ?, ?)',
            [('ci', t, 150, local_epoch(day - timedelta(days=2), 9, 0), 100) for t in halves], history=False)
check('own: a stale snapshot is left out', {k['key']: k for k in fc.view(stale, time.time())['kinds']}['ci']['n'] == 0)

# ---- NESO's records, from the mock -------------------------------------------------------
httpd = ThreadingHTTPServer(('127.0.0.1', 0), mock.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
fc.NESO = 'http://127.0.0.1:%d/neso' % httpd.server_address[1]
from gridlive import constraints as cn  # noqa: E402
cn.NESO = fc.NESO     # the BOA files are listed by the constraints module
live = server.Store(tempfile.mkdtemp())
job = fc.Forecasts(live)
started = time.time()
added = job.fetch_all()
check('records: all three downloaded', all(isinstance(added[k], int) and added[k] > 50000 for k in ('wind', 'demand', 'carbon')), added)
check('records: wind turned down, by half hour', isinstance(added['turned down'], int) and added['turned down'] > 0, added)
check('records: finished years marked done', live.scalar("SELECT COUNT(*) FROM meta WHERE k LIKE 'fc_wcurt_%'") == 2)   # 2024/25 and 2025/26
# By hand: one half hour's turned-down MWh is the mock's MW summed over its units, halved
t_down = live.scalar('SELECT t FROM wcurt_hh WHERE mwh > 0 ORDER BY t LIMIT 1')
check('records: turned down MWh', close(live.scalar('SELECT mwh FROM wcurt_hh WHERE t = ?', (t_down,)),
                                        sum(mock.boa_mw(u, cap, t_down) for u, _, cap in mock.BOA_UNITS) / 2, 0.01))
check('records: wind from April 2018', live.scalar('SELECT MIN(t) FROM fwind') == settlement_start(date(2018, 4, 17), 1))
check('records: demand from April 2021', live.scalar('SELECT MIN(t) FROM fdemand') == settlement_start(date(2021, 4, 1), 1))
check('records: carbon from 2018', live.scalar('SELECT MIN(t) FROM fci') == uk_midnight_utc(date(2018, 1, 1)))
check('records: no gaps in wind', live.scalar('SELECT COUNT(*) FROM fwind') == (live.scalar('SELECT MAX(t) FROM fwind') - live.scalar('SELECT MIN(t) FROM fwind')) // 1800 + 1)
again = job.fetch_all()
check('records: caught up, nothing new', all(again[k] == 0 for k in ('wind', 'demand', 'carbon')), again)
# One row checked by hand against the mock's own numbers
t = settlement_start(date(2024, 6, 1), 20)
row = live.query('SELECT fc, cap FROM fwind WHERE t = ?', (t,))[0]
act = mock.history_row(t)['WIND']
check('records: a wind forecast', row == (round(act * (1 + 0.2 * mock.math.sin(t / 30000.0))), 11000 + 1700 * 6), (row, act))
print('download took %.0f s' % (time.time() - started))

# The page: wind joined with the generation history
lh = [(t, mock.history_row(t)['WIND']) for t in range(settlement_start(date(2026, 10, 1), 1), settlement_start(date(2026, 11, 1), 1), 1800)]
live.write('INSERT INTO lh (t, wind) VALUES (?, ?)', lh, history=False)
page = {k['key']: k for k in fc.view(live, time.time())['kinds']}
w = page['wind']
check('page: wind only where there\'s an outcome', w['n'] == len(lh), (w['n'], len(lh)))
check('page: latest day is the last of October', w['day']['d'] == '2026-10-31' and len(w['day']['series']) == 48, w['day']['d'])
# The outcome is what wind farms could have made: generated plus turned down (MWh in the half hour, x 2 for MW)
by_hand = fc.score([(t, live.scalar('SELECT fc FROM fwind WHERE t = ?', (t,)), a + 2 * (live.scalar('SELECT mwh FROM wcurt_hh WHERE t = ?', (t,)) or 0),
                     live.scalar('SELECT cap FROM fwind WHERE t = ?', (t,))) for t, a in lh if t >= uk_midnight_utc(date(2026, 10, 31))])
check('page: wind outcome includes turned down', any(p[2] != dict(lh)[p[0]] for p in w['day']['series']) or not live.scalar(
    'SELECT SUM(mwh) FROM wcurt_hh WHERE t >= ? AND t < ?', (uk_midnight_utc(date(2026, 10, 31)), uk_midnight_utc(date(2026, 11, 1)))))
# Over the whole month, the outcome is generated plus turned down
month_rows = fc.rows(live, 'wind', uk_midnight_utc(date(2026, 10, 1)), uk_midnight_utc(date(2026, 11, 1)))
down = dict(live.query('SELECT t, mwh FROM wcurt_hh'))
check('page: a month\'s outcomes by hand', all(close(r[2], dict(lh)[r[0]] + 2 * down.get(r[0], 0), 0.01) for r in month_rows) and any(down.get(r[0]) for r in month_rows))
check('page: day score', w['day']['score'] == by_hand, (w['day']['score'], by_hand))
check('page: one whole month', [m['m'] for m in w['months']] == ['2026-10'], w['months'])
check('page: no year before to compare with', w['year_ago'] is None)
check('page: worst days', len(w['worst']) == 5 and w['worst'][0]['mae'] >= w['worst'][-1]['mae'])
d = page['demand']
check('page: demand months since 2021', d['months'][0]['m'] == '2021-04' and d['year_ago'] is not None, d['months'][:1])
check('page: demand typical miss near 3% x 2/pi', 0.015 < d['d30']['pct'] < 0.024, d['d30'])
check('page: own forecasts waiting', page['ci']['n'] == 0 and page['solar']['n'] == 0)
check('page: cached', job.page(time.time()) is job.page(time.time()))

httpd.shutdown()
print('forecast tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
