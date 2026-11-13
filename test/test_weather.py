#!/usr/bin/env python3
"""Tests for the weather layer (Open-Meteo): hand calculations, then the mock
(run: python3 test/test_weather.py)."""
import os
import sys
import tempfile
import threading
import time
from datetime import date
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, '..', 'app')
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

import server  # noqa: E402
sys.argv = [sys.argv[0], '0', APP]
import mock_upstream as mock  # noqa: E402
from gridlive import weather as wx  # noqa: E402
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
names = [p[0] for p in wx.WIND_POINTS]
check('nearest: Hornsea', wx.WIND_POINTS[wx.nearest(wx.WIND_POINTS, 53.88, 1.9)][0] == 'Hornsea')
check('nearest: Whitelee', wx.WIND_POINTS[wx.nearest(wx.WIND_POINTS, 55.68, -4.27)][0] == 'the Central Belt and Southern Uplands')
farms = [{'fuel': 'WIND', 'capacity': 1200, 'lat': 53.88, 'lon': 1.9}, {'fuel': 'WIND', 'capacity': 1300, 'lat': 53.95, 'lon': 1.7},
         {'fuel': 'WIND', 'capacity': 500, 'lat': 55.68, 'lon': -4.27}, {'fuel': 'CCGT', 'capacity': 2000, 'lat': 53.9, 'lon': 1.8},
         {'fuel': 'WIND', 'capacity': None, 'lat': 58.2, 'lon': -3.0}]
w = wx.wind_weights(farms)
check('wind weights: by capacity, gas and unknowns left out', [(p[0], p[3]) for p in w] == [('the Central Belt and Southern Uplands', 500), ('Hornsea', 2500)], w)
check('wind weights: none known, all equal', len(wx.wind_weights([])) == len(wx.WIND_POINTS))
check('temperature weights: the ONS total for Britain', close(sum(p[3] for p in wx.TEMP_POINTS), 65.68, 0.001))

# Weighted means: 10 at weight 3 and 20 at weight 1 is 12.5; with the second missing, 10; with
# only the lighter one, under half the weight, nothing
merged = wx.weighted([{0: 10.0, 1: 10.0, 2: None}, {0: 20.0, 1: None, 2: 20.0}], [3, 1])
check('weighted: both', close(merged[0], 12.5))
check('weighted: one missing', close(merged[1], 10.0))
check('weighted: under half the weight', 2 not in merged, merged)

check('median: odd and even', wx.median([3, 1, 2]) == 2 and wx.median([4, 1, 3, 2]) == 2.5 and wx.median([]) is None)
pts = [(0.2, 1), (0.7, 3), (0.9, 2), (1.1, 10), (1.5, 30), (2.5, 7)]
check('binned: medians, bins too small dropped', wx.binned(pts, 1.0, least=2) == [[0.5, 2, 3], [1.5, 20, 2]], wx.binned(pts, 1.0, least=2))
line = [(x, 40 - 0.8 * x) for x in range(12)]
slope, intercept = wx.fit(line)
check('fit: an exact line', close(slope, -0.8) and close(intercept, 40))
check('fit: too few points', wx.fit(line[:9]) is None)
check('thin: every nth', wx.thin(list(range(10)), 5) == [0, 2, 4, 6, 8] and wx.thin([1, 2], 5) == [1, 2])

# relations(), by hand
store = server.Store(tempfile.mkdtemp())
now = local_epoch(date(2026, 11, 13), 12, 0)
h0 = now - 10 * 86400 - (now - 10 * 86400) % 3600
# Wind: an hour from h0 with 6,000 then 6,600 MW made and 150 MWh turned down in the second half
# hour, capacity 30,000: (6,000 + 6,900) / 2 / 30,000 = 21.5%, against (8 + 10) / 2 = 9 m/s
store.write('INSERT INTO lh (t, wind, solar) VALUES (?, ?, ?)', [(h0, 6000, 1000), (h0 + 1800, 6600, 3000)], history=False)
store.write('INSERT INTO fwind (t, fc, cap) VALUES (?, ?, ?)', [(h0, 0, 30000), (h0 + 1800, 0, 30000)], history=False)
store.write('INSERT INTO wcurt_hh (t, mwh) VALUES (?, ?)', [(h0 + 1800, 150)], history=False)
store.write('INSERT INTO wx (k, t, v) VALUES (?, ?, ?)', [('wind', h0, 8.0), ('wind', h0 + 3600, 10.0),
                                                        # Sunshine for the hour from h0 is stamped at its end
                                                        ('sun', h0, 999.0), ('sun', h0 + 3600, 400.0)], history=False)
r = wx.relations(store, now)
check('relations: wind against the wind', r['wind']['points'] == [(9.0, 21.5)], r['wind']['points'])
check('relations: solar against the sunshine for the same hour', r['sun']['points'] == [(400.0, 2.0)], r['sun']['points'])

# Temperature: ten working days and two weekend days, demand 40 - 0.8 x temperature GW on working days
temp_store = server.Store(tempfile.mkdtemp())
rows_d, rows_t = [], []
for k in range(14):
    d = date(2026, 10, 26 + k) if k < 6 else date(2026, 11, k - 5)
    temp = 2.0 + k
    weekend = d.weekday() >= 5
    gw = (40 - 0.8 * temp) * (0.9 if weekend else 1.0)
    for sp in range(48):
        t = local_epoch(d, 0, 0) + sp * 1800
        rows_d.append((t, 0, round(gw * 1000)))
    for hr in range(24):
        rows_t.append(('temp', local_epoch(d, 0, 0) + hr * 3600, temp))
temp_store.write('INSERT INTO fdemand (t, fc, act) VALUES (?, ?, ?)', rows_d, history=False)
temp_store.write('INSERT INTO wx (k, t, v) VALUES (?, ?, ?)', rows_t, history=False)
r = wx.relations(temp_store, now)
check('relations: a point a day', len(r['temp']['points']) == 14, len(r['temp']['points']))
first = r['temp']['points'][0]
check('relations: a day as date, temperature, GW and weekend', first == ['2026-10-26', 2.0, 38.4, False], first)
check('relations: weekends marked', sum(1 for p in r['temp']['points'] if p[3]) == 4)
check('relations: each degree colder on a working day', r['temp']['per_degree'] == 0.8 and r['temp']['n_fit'] == 10, r['temp'])

# view(): sunshine now is the hour that ends at the next hour
v_store = server.Store(tempfile.mkdtemp())
hour = now - now % 3600
v_store.write('INSERT INTO wx (k, t, v) VALUES (?, ?, ?)', [('wind', hour, 7.5), ('temp', hour, 9.0), ('sun', hour, 100.0), ('sun', hour + 3600, 250.0)], history=False)
live = {'sources': [{'key': 'wind', 'gw': 12.3, 'parts': {'transmission': 10.1}}, {'key': 'solar', 'gw': 2.2}], 'demand_gw': 31.4, 'time': hour}
v = wx.view(v_store, now + 600, live)
check('view: now', v['current'] == {'wind': 7.5, 'temp': 9.0, 'sun': 250.0, 'hour': hour}, v['current'])
check('view: the grid now', v['grid'] == {'wind_gw': 12.3, 'wind_tx_gw': 10.1, 'solar_gw': 2.2, 'demand_gw': 31.4, 't': hour}, v['grid'])
check('view: nothing yet', wx.view(server.Store(tempfile.mkdtemp()), now) == {'ready': False})

# ---- from the mock ---------------------------------------------------------------------
httpd = ThreadingHTTPServer(('127.0.0.1', 0), mock.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
wx.OPEN_METEO = 'http://127.0.0.1:%d/openmeteo' % httpd.server_address[1]
live_store = server.Store(tempfile.mkdtemp())
job = wx.Weather(live_store)
points = wx.TEMP_POINTS
got = job.fetch('temp', points, 1)
check('fetch: a day back and eight ahead, hourly', got == 9 * 24, got)
t = live_store.scalar("SELECT MIN(t) FROM wx WHERE k = 'temp'") + 30 * 3600
expected = sum(mock.weather_value('temperature_2m', t, p[1], p[2]) * p[3] for p in points) / sum(p[3] for p in points)
check('fetch: weighted by population', close(live_store.scalar("SELECT v FROM wx WHERE k = 'temp' AND t = ?", (t,)), round(expected, 2), 0.006))
one = [('London', 51.51, -0.13, 1.0)]
check('fetch: one place (a single object back)', job.fetch('temp', one, 0) == 8 * 24)
mock.WEATHER_ASKED.clear()
job.fetch('wind', [('a', 54.0, 1.0, 2.0), ('b', 56.0, -3.0, 1.0)], 0)
check('fetch: wind asked for in m/s', mock.WEATHER_ASKED[-1].get('wind_speed_unit') == 'ms')

fresh = server.Store(tempfile.mkdtemp())
job = wx.Weather(fresh)
mock.WEATHER_ASKED.clear()
job.poll()
asked = [(q['hourly'], q['past_days']) for q in mock.WEATHER_ASKED]
check('poll: three requests, 92 days back the first time', sorted(asked) == sorted([(v, '92') for v in wx.VARIABLES.values()]), asked)
check('poll: wind at the equal-weight points without a station list', mock.WEATHER_ASKED[0]['latitude'].count(',') == len(wx.WIND_POINTS) - 1)
mock.WEATHER_ASKED.clear()
job.poll()
check('poll: then two days back', all(q['past_days'] == '2' for q in mock.WEATHER_ASKED) and len(mock.WEATHER_ASKED) == 3)
check('poll: about 100 days kept', fresh.scalar("SELECT COUNT(*) FROM wx WHERE k = 'sun'") == 100 * 24)

# One measure failing is logged; all three failing is reported
real_get = wx.fetch.get_json
wx.fetch.get_json = lambda url, timeout=30: (_ for _ in ()).throw(server.FetchError('down')) if 'temperature' in url else real_get(url, timeout)
job.poll()
wx.fetch.get_json = lambda url, timeout=30: (_ for _ in ()).throw(server.FetchError('down'))
try:
    job.poll()
    check('poll: all failing is reported', False)
except server.FetchError as error:
    check('poll: says which', 'wind' in str(error) and 'sun' in str(error))
wx.fetch.get_json = real_get

v = wx.view(fresh, time.time())
check('view: a week ahead from the mock', v['ready'] and len(v['week']) >= 9 * 24 - 2, len(v['week']))

httpd.shutdown()
print('weather tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
