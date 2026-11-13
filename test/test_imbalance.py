#!/usr/bin/env python3
"""Tests for Elexon's imbalance prices, NESO's trades outside the Balancing Mechanism and the day's
temperature: hand calculations, then the mock (run: python3 test/test_imbalance.py)."""
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
from gridlive import imbalance as im  # noqa: E402
from gridlive import review as rv  # noqa: E402
from gridlive.timeutil import settlement_start, uk_clock  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ---- a day's figures, by hand ----------------------------------------------------
store = server.Store(tempfile.mkdtemp())
day = date(2026, 7, 1)
halves = [settlement_start(day, sp) for sp in range(1, 49)]
# Prices 100, 50, 150 in the first three half hours; the market at 80 throughout
store.write('INSERT INTO sysp (t, d, sp, sell, buy, niv) VALUES (?, ?, ?, ?, ?, ?)',
            [(halves[0], '2026-07-01', 1, 100, 100, 200), (halves[1], '2026-07-01', 2, 50, 50, -300), (halves[2], '2026-07-01', 3, 150, 150, 0)], history=False)
store.write("INSERT INTO hh (k, t, v) VALUES ('price', ?, 80)", [(t,) for t in halves[:3]], history=False)
store.write('INSERT INTO bsad (t, d, sp, buy, sell, buy_mwh, sell_mwh, n) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            [(halves[0], '2026-07-01', 1, 1000, -400, 10, -5, 2), (halves[1], '2026-07-01', 2, 0, -100, 0, -2, 1)], history=False)
s = im.day_summary(store, day)
check('summary: half hours priced', s['periods'] == 3, s)
check('summary: mean', close(s['mean'], 100.0), s)                         # (100 + 50 + 150) / 3
check('summary: highest and lowest', s['high'] == [halves[2], 150] and s['low'] == [halves[1], 50], s)
check('summary: short and long', s['short'] == 1 and s['long'] == 1, s)     # the third, at zero, is neither
check('summary: market price', close(s['market_mean'], 80.0) and close(s['spread'], 20.0), s)
check('summary: trades', s['trades'] == {'net': 500, 'bought': 1000, 'sold': -500, 'bought_mwh': 10, 'sold_mwh': 7, 'actions': 3}, s.get('trades'))
rows = im.day_rows(store, day)
check('rows: every half hour of the day', len(rows) == 48 and rows[3][1] is None)
check('rows: first half hour', rows[0] == [halves[0], 100, 200, 80, 600], rows[0])
check('rows: clock change day has 50', len(im.day_rows(store, date(2026, 10, 25))) == 50)
check('summary: an empty day', im.day_summary(store, date(2026, 7, 2)) == {'d': '2026-07-02', 'periods': 0})

# ---- temperatures over a span -------------------------------------------------------
store.write('INSERT INTO temp (d, temp, normal, high, low) VALUES (?, ?, ?, ?, ?)',
            [((date(2026, 2, 1) + timedelta(days=k)).isoformat(), 4.0 + k % 3, 5.0, 7.0, 3.0) for k in range(28)], history=False)
check('temperatures: by date', im.temperatures(store, date(2026, 2, 1), date(2026, 2, 2)) == {'2026-02-01': (4.0, 5.0), '2026-02-02': (5.0, 5.0)})
# February 2026: 28 days of 4, 5, 6, 4, 5, 6 ... (ten 4s, nine 5s, nine 6s) = 139 / 28 = 4.96
check('review: a month\'s temperature', rv.mean_temperature(store, date(2026, 2, 1), date(2026, 2, 28)) == [round(139 / 28, 1), 5.0])
check('review: too few days, no figure', rv.mean_temperature(store, date(2026, 2, 1), date(2026, 3, 31)) is None)

# ---- from the mock ---------------------------------------------------------------------
httpd = ThreadingHTTPServer(('127.0.0.1', 0), mock.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
im.ELEXON = 'http://127.0.0.1:%d/bmrs/api/v1' % httpd.server_address[1]
live = server.Store(tempfile.mkdtemp())
job = im.Imbalance(live)
now = time.time()
today = uk_clock(now)[0]
yesterday = today - timedelta(days=1)

check('prices: a whole day', job.fetch_prices(yesterday) == (settlement_start(today, 1) - settlement_start(yesterday, 1)) // 1800)
t = settlement_start(yesterday, 30)
price, niv = mock.imbalance(t)
check('prices: one half hour', live.query('SELECT sell, buy, niv FROM sysp WHERE t = ?', (t,)) == [(price, price, niv)])

check('trades: half hours', job.fetch_trades(settlement_start(yesterday, 1), settlement_start(today, 1)) >= 46)
# By hand: every half hour sells 30 MWh for £1,500; a short one also buys 120 MWh for £16,800
short = sum(1 for sp in range(1, 49) if mock.imbalance(settlement_start(yesterday, sp))[1] > 0)
day_total = im.day_summary(live, yesterday)['trades']
check('trades: bought', day_total['bought'] == 16800 * short and day_total['bought_mwh'] == 120 * short, (day_total, short))
check('trades: sold', day_total['sold'] == -1500 * 48 and day_total['sold_mwh'] == 30 * 48, day_total)
check('trades: actions (empty rows not counted)', day_total['actions'] == 48 + short, day_total)

check('temperatures: the last ten days', job.fetch_temps(today - timedelta(days=10), today) == 11)
expected = mock.temperature(yesterday)
check('temperatures: a day', live.query('SELECT temp, normal, high, low FROM temp WHERE d = ?', (yesterday.isoformat(),)) ==
      [(expected['temperature'], expected['temperatureReferenceAverage'], expected['temperatureReferenceHigh'], expected['temperatureReferenceLow'])])
got = job.backfill_temps(today)
check('backfill: stops where the record starts', live.scalar('SELECT MIN(d) FROM temp') == mock.TEMP_START.isoformat(), live.scalar('SELECT MIN(d) FROM temp'))
check('backfill: every day since', live.scalar('SELECT COUNT(*) FROM temp') == (today - mock.TEMP_START).days + 1)
check('backfill: once', job.backfill_temps(today) == 0 and got > 1000)

live.write('DELETE FROM bsad', [()], history=False)
job.poll()
check('poll: a week of prices', live.scalar('SELECT COUNT(DISTINCT d) FROM sysp') == im.PRICE_DAYS + 1)
check('poll: trades for yesterday and today, a day at a time', live.scalar('SELECT COUNT(DISTINCT d) FROM bsad') == 2, live.query('SELECT d, COUNT(*) FROM bsad GROUP BY d'))
check('poll: today so far', 0 < live.scalar('SELECT COUNT(*) FROM sysp WHERE d = ?', (today.isoformat(),)) <= 50)
live.write('INSERT INTO sysp (t, d, sp, sell, buy, niv) VALUES (?, ?, ?, ?, ?, ?)', [(0, '2000-01-01', 1, 1, 1, 1)], history=False)
job.poll()
check('poll: old prices pruned', live.scalar("SELECT COUNT(*) FROM sysp WHERE d = '2000-01-01'") == 0)

# The autumn clock change: a 25-hour day, asked for in two halves (the mock refuses more than 24 hours)
autumn = server.Store(tempfile.mkdtemp())
real_clock = im.uk_clock
im.uk_clock = lambda t: (date(2026, 10, 26), '12:00')
try:
    im.Imbalance(autumn).poll()
finally:
    im.uk_clock = real_clock
check('clock change: all 50 half hours of trades', autumn.scalar("SELECT COUNT(*) FROM bsad WHERE d = '2026-10-25'") == 50,
      autumn.scalar("SELECT COUNT(*) FROM bsad WHERE d = '2026-10-25'"))
check('clock change: all 50 prices', autumn.scalar("SELECT COUNT(*) FROM sysp WHERE d = '2026-10-25'") == 50)

# The balancing payload carries both days
grid = server.Grid(live, server.Status())
payload = grid.balancing()['imbalance']
check('payload: today and yesterday', set(payload) == {'today', 'yesterday'} and payload['yesterday']['summary']['periods'] >= 46)

# When Elexon fails, the job says so
im.ELEXON = 'http://127.0.0.1:1/nothing'
fresh = im.Imbalance(server.Store(tempfile.mkdtemp()))
try:
    fresh.poll()
    check('poll: fails loudly with no data', False)
except server.FetchError:
    pass

httpd.shutdown()
print('imbalance tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
