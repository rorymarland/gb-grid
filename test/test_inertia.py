#!/usr/bin/env python3
"""Tests for system inertia: NESO's history, the fit and the estimate. Hand calculations, then the
mock (run: python3 test/test_inertia.py)."""
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
from gridlive import inertia as ine  # noqa: E402
from gridlive.timeutil import settlement_start, uk_midnight_utc  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


# ---- reading NESO's rows ---------------------------------------------------------------
rows = ine.parse_records([
    {'_id': 1, 'Settlement Date': '2017-04-01T00:00:00', 'Settlement Period': 1, 'Outturn Inertia': 215, 'Market Provided Inertia': 218},
    {'_id': 2, 'Settlement Date': '2026-07-26', 'Settlement Period': 48, 'Outturn Inertia': 108, 'Market Provided Inertia': 76},
    # 25 October 2026 is 25 hours long in the UK, so it has a 50th period: 23:30 to midnight, GMT
    {'_id': 3, 'Settlement Date': '2026-10-25', 'Settlement Period': 50, 'Outturn Inertia': 150, 'Market Provided Inertia': None},
    {'_id': 4, 'Settlement Date': '2026-07-26', 'Settlement Period': 'x', 'Outturn Inertia': 100},
    {'_id': 5, 'Settlement Date': '2026-07-26', 'Settlement Period': 3, 'Outturn Inertia': 0},
    {'_id': 6, 'Settlement Date': '', 'Settlement Period': 3, 'Outturn Inertia': 120},
])
# 1 April 2017 was in BST, so its first period starts at 23:00 UTC on 31 March
check('rows: the 2017 date form, in BST', rows[0] == (1491001200, 215.0, 218.0), rows[:1])
# 26 July 2026, period 48: 23:30 BST is 22:30 UTC
check('rows: the later date form', rows[1] == (settlement_start(date(2026, 7, 26), 48), 108.0, 76.0) and rows[1][0] == 1785105000, rows[1])
check('rows: period 50 on the long day is 23:30 GMT', rows[2][0] == 1792971000 and rows[2][2] is None, rows[2:3])
check('rows: bad or empty ones left out', len(rows) == 3)

# ---- the fit ---------------------------------------------------------------------------
# Exact: y = 30 + 6 a - 2 b
samples = [([a, b], 30 + 6 * a - 2 * b) for a in range(10) for b in range(0, 20, 3)]
coef = ine.fit(samples)
check('fit: recovers a known line', all(close(c, e, 1e-3) for c, e in zip(coef, [30, 6, -2])), coef)
# A feature that never moves (coal since 2024) doesn't break it
coef = ine.fit([([a, 0.0], 10 + a) for a in range(20)])
check('fit: a feature stuck at nothing', coef and close(coef[0], 10, 1e-3) and close(coef[1], 1, 1e-3) and close(coef[2], 0, 1e-6), coef)
check('solve: singular', ine.solve([[1, 2], [2, 4]], [1, 2]) is None)
# Errors, by hand: predictions 110 and 190 against 100 and 200
e = ine.errors([0, 1], [([110], 100), ([190], 200)])
check('errors: mean absolute', close(e['mae'], 10) and close(e['mape'], (10 / 100 + 10 / 200) / 2 * 100) and close(e['bias'], 0) and e['n'] == 2, e)

# The features from the generation mix: gas includes coal, wind includes the small farms, storage only when generating
lh = ine.lh_features((10000, 500, 5000, 2000, 300, -400, 8000, 1500, 2500, 3000))
check('features: historic mix', lh == [10.5, 5.0, 2.0, 0.3, 0.0, 9.5, 2.5, 3.0], lh)
check('features: missing', ine.lh_features((None, 0, 5000, 2000, 300, 0, 8000, 1500, 2500, 3000)) is None)
m = server.Grid.mix({'CCGT': 9000, 'OCGT': 500, 'COAL': 0, 'NUCLEAR': 5000, 'BIOMASS': 2000, 'NPSHYD': 300, 'PS': 600, 'BESS': 400,
                     'WIND': 8000, 'INTFR': 2000, 'INTIRL': -500}, 1500, 2500)
check('features: live mix the same way', ine.mix_features(m) == [9.5, 5.0, 2.0, 0.3, 0.6, 9.5, 2.5, 2.0], ine.mix_features(m))

# ---- months, the latest and the lowest -------------------------------------------------
store = server.Store(tempfile.mkdtemp())
march = uk_midnight_utc(date(2026, 3, 1))
april = uk_midnight_utc(date(2026, 4, 1))        # 23:00 UTC on 31 March: BST began on the 29th
may = uk_midnight_utc(date(2026, 5, 1))
check('month start in BST', april == 1774998000, april)
store.write('INSERT OR REPLACE INTO inertia (t, v, mkt) VALUES (?, ?, ?)',
            [(t, 100.0, 90.0) for t in range(march, april, 1800)] + [(t, 200.0, None) for t in range(april, may, 1800)]
            + [(may + 1800 * k, 150.0, 140.0) for k in range(48 * 5)] + [(april + 3600, 60.0, 50.0)], history=False)
months = ine.monthly(store)
check('months: split at UK midnight', [m[0] for m in months] == [march, april], months)
check('months: March all 100', months[0][1:] == [100.0, 100, 100, 90.0], months[0])
n_april = (may - april) // 1800
check('months: April with one low half hour', close(months[1][1], round((200 * (n_april - 1) + 60) / n_april, 1)) and months[1][2] == 60 and months[1][4] == 50.0, months[1])
check('months: May, five days, too few to show', len(months) == 2)
now = may + 5 * 86400 + 30 * 86400
pub = ine.published(store, now)
check('published: latest', pub['t'] == may + 1800 * 239 and pub['gvas'] == 150 and pub['market'] == 140, pub['t'])
check('published: stale after a week', pub['stale'] and pub['age_days'] == 30, (pub['stale'], pub['age_days']))
check('published: the week to then', pub['low'] == 150 and pub['high'] == 200 and pub['first'] == march)
check('published: lowest first, one a day', pub['lowest'][0] == {'t': april + 3600, 'gvas': 60} and pub['lowest'][1] == {'t': march, 'gvas': 100}
      and pub['lowest'][2] == {'t': march + 86400, 'gvas': 100} and len(pub['lowest']) == 5, pub['lowest'])
check('published: nothing yet', ine.published(server.Store(tempfile.mkdtemp()), now) is None)

# ---- the model: tested on months it hasn't seen ----------------------------------------
def build(noise):
    s = server.Store(tempfile.mkdtemp())
    start = 1_700_000_000 - 1_700_000_000 % 1800
    lh, ir = [], []
    for k in range(485 * 48):
        t = start + k * 1800
        gas = 8000 + 4000 * ((k * 7919) % 97) / 97
        nuclear = 4000 + 1000 * ((k * 104729) % 89) / 89
        wind = 2000 + 9000 * ((k * 1299709) % 83) / 83
        lh.append((t, gas, 0, nuclear, wind, 0, 300, 2000, 2000, 300, 0, 0, 0, 0))
        y = 30 + 6 * gas / 1000 + 5 * nuclear / 1000 - 0.5 * wind / 1000
        ir.append((t, y + noise(k), None))
    s.write('INSERT INTO lh (t, gas, coal, nuclear, wind, wind_emb, hydro, imports, biomass, other, solar, storage, generation, ci) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', lh, history=False)
    s.write('INSERT INTO inertia (t, v, mkt) VALUES (?, ?, ?)', ir, history=False)
    return s, start


good, start = build(lambda k: 0.0)
model = ine.build_model(good)
check('model: an exact relation passes', model['ok'] and model['tested']['mape'] < 0.01 and model['tested']['n'] == 120 * 48, model.get('tested'))
check('model: tested on the last 120 days', model['test_to'] == start + 485 * 48 * 1800 and model['test_from'] == model['test_to'] - 120 * 86400)
check('model: the gas coefficient', close(model['coef'][1], 6, 1e-3) and close(model['coef'][6], -0.5, 1e-3), model['coef'])
# 40 GVA·s either way at random against about 100: far beyond 10%
bad, _ = build(lambda k: 60 * (((k * 2654435761) % 1000) / 1000 - 0.5) * 2)
model = ine.build_model(bad)
check('model: too far out is not shown', not model['ok'] and 'out by' in model['why'] and model['tested']['mape'] > 10, model.get('why'))
check('model: no overlap', not ine.build_model(server.Store(tempfile.mkdtemp()))['ok'])

# ---- from the mock ---------------------------------------------------------------------
httpd = ThreadingHTTPServer(('127.0.0.1', 0), mock.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
base = 'http://127.0.0.1:%d' % httpd.server_address[1]
ine.NESO = base + '/neso'
live = server.Store(tempfile.mkdtemp())
mock.INERTIA_ASKED.clear()
t0 = time.time()
written = ine.poll(live, spacing=0)
years = [r['id'] for r in mock.inertia_resources() if r['id'] != 'in-cost']
check('poll: every year once, whole', sorted(mock.INERTIA_ASKED) == sorted(years), mock.INERTIA_ASKED)
expected = sum(len(mock.inertia_records(int(y[3:]))) for y in years)
check('poll: every half hour kept', live.scalar('SELECT COUNT(*) FROM inertia') == expected and written == expected, (written, expected))
first = live.query('SELECT t, v, mkt FROM inertia ORDER BY t LIMIT 1')[0]
check('poll: starts 1 April 2017 in BST', first[0] == 1491001200 and first[1] == mock.inertia_mock(1491001200) and first[2] == first[1] - 9, first)
last = live.scalar('SELECT MAX(t) FROM inertia')
check('poll: ends where NESO paused', uk_midnight_utc(mock.inertia_end()) - 1800 == last, (last, mock.inertia_end()))
mock.INERTIA_ASKED.clear()
ine.poll(live, spacing=0)
check('poll: nothing again while NESO changes nothing', mock.INERTIA_ASKED == [], mock.INERTIA_ASKED)
# NESO changes the newest year: just its last few days are read again
newest = years[-1] if mock.inertia_records(int(years[-1][3:])) else years[-2]
live.set_meta('inertia_res:' + newest, 'older')
ine.poll(live, spacing=0)
check('poll: a changed year read from a few days back', mock.INERTIA_ASKED == [newest + ' sql'] or (newest != years[-1] and mock.INERTIA_ASKED == [newest]), mock.INERTIA_ASKED)
check('poll: still every half hour once', live.scalar('SELECT COUNT(*) FROM inertia') == expected)

# The model on the mock's history: NESO's figures follow the spinning plant, so it passes
lhist = server.LongHistory(live)
lhist.resource = 'hgm'
import gridlive.longterm as lt  # noqa: E402
lt.NESO = base + '/neso'
after = last - 500 * 86400
while True:
    got = lhist.fetch_mix(limit=10000, after=after)
    newest_lh = live.scalar('SELECT MAX(t) FROM lh')
    if not got or newest_lh >= last:
        break
    after = newest_lh
model = ine.build_model(live)
check('mock model: passes the test', model['ok'] and model['tested']['mape'] < 5, model.get('tested'))
print('poll and fit seconds', round(time.time() - t0, 1))

# Through the Grid: the payload for the Frequency page
grid = server.Grid(live, server.Status())
now = time.time()
gen_t = int(now - now % 300)
fu, emb_wind, sol = mock.fuels(gen_t)
live.write(server.UPSERT_GEN, [(t, k, v) for t in range(gen_t - 3 * 3600, gen_t + 1, 300) for k, v in mock.fuels(t)[0].items()])
payload = grid.inertia_payload(now)
pub = payload['published']
check('payload: NESO paused, so stale', pub['stale'] and pub['age_days'] >= mock.INERTIA_PAUSED_DAYS and pub['paused'], pub['age_days'])
mock.INERTIA_NOTES[0] = 'This dataset contains an estimate of the outturn inertia.'
ine.poll(live, spacing=0)
check('payload: the pause noted from NESO’s own description', not ine.published(live, now)['paused'])
mock.INERTIA_NOTES[0] = 'Updates have been temporarily paused.'
ine.poll(live, spacing=0)
est = payload['estimate']
check('payload: an estimate now', est and est['t'] == gen_t and 20 < est['gvas'] < 400 and est['mape'] < 5, est and {k: est[k] for k in ('t', 'gvas', 'mape')})
expected_now = round(ine.predict(model['coef'], ine.mix_features(grid.mix(fu, None, None))))
check('payload: the latest five minutes, by hand', est['gvas'] == expected_now, (est['gvas'], expected_now))
check('payload: half hours before it', len(est['series']['t']) >= 6 and est['series']['t'] == sorted(set(est['series']['t'])))
check('payload: in the frequency page', 'published' in grid.frequency_payload('1h')['inertia'])
sb = grid.sandbox_payload()
check('sandbox: NESO’s latest from the new table', sb['published_inertia']['t'] == last)
check('sandbox: fitted on the latest year', sb['fit']['points'] <= 366 * 48 + 2 and sb['fit']['points'] > 300 * 48, sb['fit'])

# Rows kept in hh from before 1.18.0 move across when the table is new
folder = tempfile.mkdtemp()
old = server.Store(folder)
old.db.execute('DELETE FROM inertia')
old.write(server.UPSERT_HH, [('inertia', 1800, 150.0), ('inertia_mkt', 1800, 140.0), ('inertia', 3600, 151.0)], history=False)
old.db.close()
moved = server.Store(folder)
check('upgrade: hh inertia moved across', moved.query('SELECT t, v, mkt FROM inertia ORDER BY t') == [(1800, 150.0, 140.0), (3600, 151.0, None)])

httpd.shutdown()
print('inertia tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
