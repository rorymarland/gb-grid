#!/usr/bin/env python3
"""Tests for the year in review: figures against hand calculations, UK days, your home's
year and saving a finished year (run: python3 test/test_review.py)."""
import json
import os
import sys
import tempfile
import threading
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'app'))

import server  # noqa: E402
from gridlive import review as rv  # noqa: E402
from gridlive.home_views import ViewsMixin  # noqa: E402
from gridlive.timeutil import local_epoch, settlement_start, uk_midnight_utc  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


def half_hours(day, n=None):
    """Every settlement period's start on a UK day (46, 48 or 50 of them)."""
    count = (uk_midnight_utc(day + timedelta(days=1)) - uk_midnight_utc(day)) // 1800
    return [settlement_start(day, sp) for sp in range(1, (n or count) + 1)]


# ---- SQL helpers: UK days and years --------------------------------------------

store = server.Store(tempfile.mkdtemp())
q1 = lambda sql, *p: store.query(sql, p)[0][0]  # noqa: E731
# 00:30 BST on 1 July is 23:30 UTC on 30 June: the UK day is 1 July
t = local_epoch(date(2026, 7, 1), 0, 30)
check('ukday in summer', q1('SELECT ukday(?)', t) == uk_midnight_utc(date(2026, 7, 1)))
check('ukday in winter', q1('SELECT ukday(?)', local_epoch(date(2026, 1, 5), 23, 30)) == uk_midnight_utc(date(2026, 1, 5)))
check('ukyear at new year', q1('SELECT ukyear(?)', uk_midnight_utc(date(2027, 1, 1))) == 2027
      and q1('SELECT ukyear(?)', uk_midnight_utc(date(2027, 1, 1)) - 1) == 2026)
check('ukmonth', q1('SELECT ukmonth(?)', t) == 7)
check('ukday of null', q1('SELECT ukday(NULL)') is None)
check('bounds', rv.bounds(2026) == (uk_midnight_utc(date(2026, 1, 1)), uk_midnight_utc(date(2027, 1, 1))))
check('year earlier', rv.year_earlier(date(2028, 2, 29)) == date(2027, 2, 28) and rv.year_earlier(date(2026, 3, 1)) == date(2025, 3, 1))

# ---- the grid's year, by hand ---------------------------------------------------
# Two days of 2025 and two of 2026 (1 and 2 January), every half hour the same within a day.
# 2026, day 1: gas 10,000, wind 6,000 (+2,000 embedded), nuclear 4,000 MW; ci 200; price 50
# 2026, day 2: gas 4,000, wind 14,000 (+2,000), nuclear 4,000; ci 100; price -10
# Totals per half hour: 22,000 and 24,000 MW
LH = 'INSERT INTO lh (t, gas, coal, nuclear, wind, wind_emb, hydro, imports, biomass, other, solar, storage, generation, ci) VALUES (?, ?, 0, ?, ?, ?, 0, 0, 0, 0, 0, 0, ?, ?)'
rows, prices = [], []
for day, gas, wind, ci, price in ((date(2026, 1, 1), 10000, 6000, 200, 50.0), (date(2026, 1, 2), 4000, 14000, 100, -10.0),
                                  (date(2025, 1, 1), 12000, 4000, 250, 80.0), (date(2025, 1, 2), 12000, 4000, 250, 80.0)):
    for hh in half_hours(day):
        rows.append((hh, gas, 4000, wind, 2000, gas + wind + 2000 + 4000, ci))
        prices.append((hh, price))
store.write(LH, rows, history=False)
store.write('INSERT INTO lp (t, price) VALUES (?, ?)', prices, history=False)

start = uk_midnight_utc(date(2026, 1, 1))
f = rv.mix_figures(store, start, uk_midnight_utc(date(2026, 1, 3)))
# Energy: each half hour's MW x 0.5 h. Day 1: 48 x 22,000 / 2 = 528,000 MWh; day 2: 48 x 24,000 / 2 = 576,000
check('mix: TWh', f['twh'] == round(1104000 / 1e6, 2), f['twh'])
gas_mwh = 48 * (10000 + 4000) / 2            # 336,000
wind_mwh = 48 * (8000 + 16000) / 2           # 576,000, including the embedded wind
check('mix: gas share', close(f['mix']['gas'], round(gas_mwh / 1104000, 4)), f['mix'])
check('mix: wind share with embedded', close(f['mix']['wind'], round(wind_mwh / 1104000, 4)), f['mix'])
check('mix: zero-carbon', close(f['zero'], round((wind_mwh + 48 * 8000 / 2) / 1104000, 4)), f['zero'])
# Carbon weighted by generation: (200 x 22,000 + 100 x 24,000) / 46,000 = 147.8, against a plain average of 150
check('mix: weighted carbon', f['ci'] == round((200 * 22000 + 100 * 24000) / 46000), f['ci'])
check('mix: price', close(f['price'], 20.0), f['price'])
check('mix: hours below zero', f['negative_hours'] == 24, f['negative_hours'])
check('mix: transmission wind only', close(f['wind_tx_twh'], round(48 * 20000 / 2 / 1e6, 2)), f['wind_tx_twh'])

check('covered: to the end of the last whole day', rv.covered(store, start, rv.bounds(2026)[1]) == uk_midnight_utc(date(2026, 1, 3)))
store.write(LH, [(settlement_start(date(2026, 1, 3), 1), 1, 1, 1, 1, 4, 100)], history=False)   # one half hour of the 3rd
check('covered: a part day is left out', rv.covered(store, start, rv.bounds(2026)[1]) == uk_midnight_utc(date(2026, 1, 3)))
store.write('DELETE FROM lh WHERE t = ?', [(settlement_start(date(2026, 1, 3), 1),)], history=False)

records = [{'key': 'cleanest-day', 't': uk_midnight_utc(date(2026, 1, 2))}, {'key': 'wind-peak', 't': 0}]
g = rv.grid_year(store, 2026, records)
check('year: days so far', g['days'] == 2 and not g['whole'], (g['days'], g['whole']))
check('year: before is the same two days of 2025', g['before'] and g['before']['ci'] == 250 and g['before']['half_hours'] == 96, g['before'])
check('year: one month so far, not whole', len(g['months']) == 1 and g['months'][0]['m'] == 1 and not g['months'][0]['whole'], g['months'])
bests = {b['key']: b for b in g['bests']}
check('bests: cleanest day', bests['cleanest-day']['t'] == uk_midnight_utc(date(2026, 1, 2)) and bests['cleanest-day']['value'] == 100, bests['cleanest-day'])
check('bests: cleanest day is the all-time record', bests['cleanest-day']['record'] is True)
check('bests: dirtiest day', bests['dirtiest-day']['value'] == 200 and bests['dirtiest-day']['record'] is False)
check('bests: windiest day includes embedded wind', bests['windiest-day']['value'] == 16000, bests['windiest-day'])
check('bests: lowest price', bests['price-low']['value'] == -10, bests['price-low'])
check('bests: not a record elsewhere', bests['wind-peak']['record'] is False)
check('year: history has the year so far', g['history'][-1]['year'] == 2026 and g['history'][-1]['so_far'] is True, g['history'][-1:])
check('year: history skips years without enough data', all(h['year'] == 2026 for h in g['history']), [h['year'] for h in g['history']])
check('year: no constraints data', g['constraints'] is None)
check('year: no coal', g['last_coal'] is None)
check('year: nothing for an empty year', rv.grid_year(store, 2024) is None)

# Wind turned down and constraint costs, for the same two days. Share of transmission wind:
# 4,000 MWh turned down / (480,000 made + 4,000)
store.write('INSERT INTO wcurt (d, unit, name, mwh) VALUES (?, ?, ?, ?)',
            [('2026-01-01', 'A-1', 'A', 3000), ('2026-01-02', 'A-1', 'A', 1000), ('2025-12-31', 'A-1', 'A', 9e9)], history=False)
store.write('INSERT INTO cday (d, thermal, voltage, inertia, loss, thermal_mwh) VALUES (?, ?, ?, ?, ?, ?)',
            [('2026-01-01', 1e6, 1e5, 0, 0, 0), ('2026-01-02', 2e6, 0, 5e4, 5e4, 0)], history=False)
c = rv.grid_year(store, 2026, records)['constraints']
check('constraints: turned down', c['down_twh'] == 0.0 and c['down_from'] == '2026-01-01', c)
check('constraints: share', close(c['down_share'], round(4000 / (480000 + 4000), 4)), c)
check('constraints: cost', c['cost'] == 3200000 and c['cost_from'] == '2026-01-01' and c['cost_to'] == '2026-01-02', c)

# Moments: frequency events are counted, with only the biggest listed
ev = [('freq-1', settlement_start(date(2026, 1, 1), 10), 'frequency', 3, json.dumps(['Frequency dipped to 49.7 Hz']), '[]', json.dumps({'change': -0.3})),
      ('freq-2', settlement_start(date(2026, 1, 2), 10), 'frequency', 3, json.dumps(['Frequency dipped to 49.6 Hz']), '[]', json.dumps({'change': -0.4})),
      ('freq-3', settlement_start(date(2026, 1, 2), 20), 'frequency', 2, json.dumps(['A small one']), '[]', json.dumps({'change': -0.9})),
      ('rec-1', settlement_start(date(2026, 1, 2), 30), 'record', 3, json.dumps(['Most wind power on record']), '[]', '{}'),
      ('old', settlement_start(date(2025, 12, 31), 30), 'record', 3, json.dumps(['Last year']), '[]', '{}')]
store.write('INSERT INTO ev (id, t, kind, level, title, detail, data, updated) VALUES (?, ?, ?, ?, ?, ?, ?, 0)', ev, history=False)
g = rv.grid_year(store, 2026, records)
check('moments: count of major frequency events', g['frequency_events'] == 2, g['frequency_events'])
check('moments: the biggest major one, and the rest', [m['title'][0] for m in g['moments']] == ['Frequency dipped to 49.6 Hz', 'Most wind power on record'], g['moments'])

# ---- your home's year --------------------------------------------------------------

class FakeHome(ViewsMixin):
    def __init__(self, store):
        self.store, self.enabled, self.lock = store, True, threading.Lock()
        self.sessions = None
        self.data_version = 0


home_store = server.Store(tempfile.mkdtemp())
home = FakeHome(home_store)
# 2 January: 0.5 kWh every half hour at 100 g/kWh; 3 January: 1 kWh in each half hour at 200 g/kWh, gas 2 kWh
# Prices: 25p/kWh and 50p a day for electricity; 6p/kWh and 30p a day for gas
readings = [('elec', hh, 0.5, 'meter', 100.0) for hh in half_hours(date(2026, 1, 2))]
readings += [('elec', hh, 1.0, 'meter', 200.0) for hh in half_hours(date(2026, 1, 3))]
readings += [('gas', hh, 2.0, 'meter', None) for hh in half_hours(date(2026, 1, 3))]
home_store.write('INSERT INTO home (fuel, t, kwh, src, ci) VALUES (?, ?, ?, ?, ?)', readings, history=False)
home_store.write('INSERT INTO home_rate (fuel, kind, start, end, p) VALUES (?, ?, ?, ?, ?)',
                 [('elec', 'unit', 0, None, 25.0), ('elec', 'standing', 0, None, 50.0), ('gas', 'unit', 0, None, 6.0), ('gas', 'standing', 0, None, 30.0)], history=False)
home.sessions = {'joined': {1: {'start': local_epoch(date(2026, 1, 2), 17, 0), 'end': local_epoch(date(2026, 1, 2), 18, 0), 'given': 400, 'type': 'TURN_DOWN'},
                            2: {'start': local_epoch(date(2026, 1, 3), 17, 0), 'end': local_epoch(date(2026, 1, 3), 18, 0), 'given': None, 'type': 'TURN_DOWN'},
                            3: {'start': local_epoch(date(2025, 12, 1), 17, 0), 'end': local_epoch(date(2025, 12, 1), 18, 0), 'given': 999, 'type': 'TURN_DOWN'}}}
now = local_epoch(date(2026, 1, 10), 12, 0)
hy = rv.home_year(home, 2026, now)
tot = hy['total']
check('home: electricity', close(tot['e'], 24 + 48), tot)
check('home: gas', close(tot['g'], 96), tot)
# Electricity: 72 kWh x 25p + 2 days x 50p = 1,900p. Gas: 96 x 6p + 30p = 606p
check('home: electricity cost', close(tot['e_cost'], 1900), tot)
check('home: gas cost', close(tot['g_cost'], 606), tot)
check('home: cost', close(tot['cost'], 2506), tot)
# Carbon: 24 x 0.1 + 48 x 0.2 = 12 kg; gas 96 x 0.183 = 17.568 kg
check('home: electricity carbon', close(tot['e_kg'], 12.0), tot)
check('home: total carbon', close(hy['kg'], round(12 + 96 * hy['gas_factor'], 1)), hy['kg'])
check('home: starts after 1 January', hy['partial_start'] is True and hy['from'] == '2026-01-02', hy['from'])
check('home: busiest day', hy['busiest'] == {'d': '2026-01-03', 'e': 48.0}, hy['busiest'])
check('home: one month', len(hy['months']) == 1 and hy['months'][0]['m'] == 1 and hy['months'][0]['days'] == 2, hy['months'])
check('home: no year before', hy['before'] is None)
check('home: timing needs 30 days of readings', hy['timing'] is None)
check('home: sessions in the year', hy['sessions'] == {'joined': 2, 'points': 400, 'waiting': 1}, hy['sessions'])

# Timing: 30 days alternating 2 kWh at 100 g/kWh and 1 kWh at 300, with the two January days above.
# Yours, weighted by use: (30 x 12,000 + 2,400 + 9,600) / (30 x 72 + 72) = 372,000 / 2,232 = 166.7
# Evenly, each half hour alike: (30 x 48 x 200 + 48 x 100 + 48 x 200) / (30 x 48 + 96) = 196.9
timing = []
for k in range(30):
    for i, hh in enumerate(half_hours(date(2026, 2, 1) + timedelta(days=k), 48)):
        timing.append(('elec', hh, 2.0 if i % 2 == 0 else 1.0, 'meter', 100.0 if i % 2 == 0 else 300.0))
home_store.write('INSERT INTO home (fuel, t, kwh, src, ci) VALUES (?, ?, ?, ?, ?)', timing, history=False)
hy = rv.home_year(home, 2026, now)
check('home: timing yours', close(hy['timing']['yours'], round(500 / 3, 1)), hy['timing'])
check('home: timing evenly', close(hy['timing']['flat'], round(302400 / 1536, 1)), hy['timing'])
home.enabled = False
check('home: off', rv.home_year(home, 2026, now) is None)
home.enabled = True

# ---- the page: the year so far, then saved -------------------------------------------

class FakeGrid:
    def __init__(self):
        self.complete = True

    def longterm_summary(self):
        return {'ready': True, 'records': records}

    def longterm_extent(self):
        return {'complete': self.complete}


fake_grid = FakeGrid()
page = rv.Review(store, fake_grid, home)
during = page.view(2026, now)
check('view: during the year, not saved', during['saved'] is None and during['grid'] is not None, during.get('saved'))
check('view: years', 2026 in during['years'] and 2025 in during['years'], during['years'])
check('view: default year in January is the last one', page.view(None, now)['year'] == 2025)
check('view: default year later on is this one', page.view(None, local_epoch(date(2026, 3, 1), 12, 0))['year'] == 2026)
check('view: unknown year', page.view(1990, now)['grid'] is None)

# The whole of 2025: the year ends, data fills in, and two weeks on it's saved
for k in range(365):
    d = date(2025, 1, 1) + timedelta(days=k)
    if d in (date(2025, 1, 1), date(2025, 1, 2)):
        continue
    store.write(LH, [(hh, 12000, 4000, 4000, 2000, 22000, 250) for hh in half_hours(d)], history=False)
early = page.view(2025, uk_midnight_utc(date(2026, 1, 5)))
check('view: not saved within two weeks', early['saved'] is None and store.scalar('SELECT COUNT(*) FROM review') == 0)
fake_grid.complete = False
page.view(2025, uk_midnight_utc(date(2026, 1, 20)))
check('view: not saved while the history is still downloading', store.scalar('SELECT COUNT(*) FROM review') == 0)
fake_grid.complete = True
first = page.view(2025, uk_midnight_utc(date(2026, 1, 20)))
check('view: the first view after two weeks saves it', store.scalar('SELECT COUNT(*) FROM review') == 1 and first['grid']['whole'] and first['saved'])
store.write(LH.replace('INSERT', 'INSERT OR REPLACE'), [(settlement_start(date(2025, 6, 1), 1), 0, 0, 0, 0, 0, 999)], history=False)   # history changes later
again = page.view(2025, uk_midnight_utc(date(2026, 2, 1)))
check('view: a saved year reads the same', again['saved'] == uk_midnight_utc(date(2026, 1, 20)) and again['grid']['figures'] == first['grid']['figures'])
check('view: the saved year keeps its whole-year flag', again['grid']['whole'] is True)
check('view: a whole year has no so-far', again['grid']['history'][-1].get('so_far') is False)

# ---- NESO's carbon intensity against its own mix ------------------------------------
# Two years where gas is half the mix and intensity 213 (1.08 x the 197 that half gas at 394 implies),
# then 2026 where March and April read 290 for the same mix: 36% out of line
odd_store = server.Store(tempfile.mkdtemp())
rows = []
d = date(2024, 1, 1)
while d < date(2027, 1, 1):
    ci = 290 if d.year == 2026 and d.month in (3, 4) else 213
    rows += [(hh, 10000, 0, 10000, 0, 20000, ci) for hh in half_hours(d)]
    d += timedelta(days=1)
odd_store.write(LH, rows, history=False)
og = rv.grid_year(odd_store, 2026, [])
check('check: baseline ratio', close(og['ci_check']['baseline'], round(213 / 197, 3), 0.001), og['ci_check'])
check('check: March and April flagged', og['ci_check']['odd'] == [3, 4], og['ci_check'])
check('check: how far out', close(og['ci_check']['excess'], round(290 / 213 - 1, 3), 0.002), og['ci_check'])
check('check: other years clean', rv.grid_year(odd_store, 2025, [])['ci_check']['odd'] == [])
check('check: needs a year of history', rv.grid_year(odd_store, 2024, [])['ci_check'] is None)
held = rv.Review(odd_store, fake_grid, None)
held.view(2026, uk_midnight_utc(date(2027, 1, 20)))
check('check: a flagged year is not saved in January', odd_store.scalar('SELECT COUNT(*) FROM review') == 0)
saved = held.view(2026, uk_midnight_utc(date(2027, 4, 2)))
check('check: but is from 1 April', odd_store.scalar('SELECT COUNT(*) FROM review') == 1 and saved['saved'])

# ---- re-reading NESO's last year, for its corrections ----------------------------------
from datetime import datetime, timezone  # noqa: E402
from urllib.parse import unquote  # noqa: E402
lh_store = server.Store(tempfile.mkdtemp())
lhist = server.LongHistory(lh_store)
lhist.resource = 'r'
base = int(server.time.time()) // 1800 * 1800 - 20 * 86400
neso = {base + i * 1800: 150 for i in range(20 * 48)}
asked = []


def fake_neso(url, timeout=None):
    since = unquote(url).split('"DATETIME" > \'')[1].split("'")[0]
    after = datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp()
    asked.append(after)
    keys = sorted(k for k in neso if k > after)[:10000]
    return {'result': {'records': [{'DATETIME': datetime.fromtimestamp(k, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'), 'GAS': 1000, 'COAL': 0, 'NUCLEAR': 0,
                                    'WIND': 1000, 'WIND_EMB': 0, 'HYDRO': 0, 'IMPORTS': 0, 'BIOMASS': 0, 'OTHER': 0, 'SOLAR': 0, 'STORAGE': 0,
                                    'GENERATION': 2000, 'CARBON_INTENSITY': neso[k]} for k in keys]}}


original = server.fetch.get_json
server.fetch.get_json = fake_neso
try:
    lhist.fetch_mix()
    check('refresh: first download', lh_store.scalar('SELECT COUNT(*) FROM lh') == 960)
    check('refresh: nothing changed', lhist.refresh_recent(400) == 0)
    for k in list(neso)[100:148]:
        neso[k] = 99                       # NESO revises a day
    asked.clear()
    check('refresh: a revised day is picked up', lhist.refresh_recent(400) == 48 and lh_store.scalar('SELECT COUNT(*) FROM lh WHERE ci = 99') == 48)
    check('refresh: in one request', len(asked) <= 2, asked)
finally:
    server.fetch.get_json = original

print('review tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
