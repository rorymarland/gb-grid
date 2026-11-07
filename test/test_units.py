#!/usr/bin/env python3
"""Unit tests for the per-station model (run: python3 test/test_units.py)."""
import os
import sys
import tempfile
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'app'))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))

import server  # noqa: E402
import geo  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


def utc(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


# Projection: the server and the build scripts must agree exactly
for lon in (-10.5, -7.3, -2.0, 0.0, 1.9, 4.03, 6.65, 9.13, 14.0):
    for lat in (45.0, 49.1, 51.5, 55.0, 59.5, 61.0):
        a, b = server.project(lon, lat), geo.project(lon, lat)
        check('project %s,%s' % (lon, lat), abs(a[0] - b[0]) < 1e-9 and abs(a[1] - b[1]) < 1e-9, (a, b))

# Settlement periods, including both clock changes
cases = [
    (utc(2026, 11, 6, 14, 25), (date(2026, 11, 6), 29)),
    (utc(2026, 7, 1, 23, 10), (date(2026, 7, 2), 1)),
    (utc(2026, 7, 1, 22, 59), (date(2026, 7, 1), 48)),
    (utc(2026, 3, 29, 0, 30), (date(2026, 3, 29), 2)),
    (utc(2026, 3, 29, 1, 0), (date(2026, 3, 29), 3)),
    (utc(2026, 3, 29, 22, 59), (date(2026, 3, 29), 46)),
    (utc(2026, 10, 24, 23, 0), (date(2026, 10, 25), 1)),
    (utc(2026, 10, 25, 23, 45), (date(2026, 10, 25), 50)),
]
for t, expected in cases:
    got = server.settlement_period_at(t)
    check('period %s' % server.iso(t), got == expected, (got, expected))
    start = server.settlement_start(*got)
    check('period start %s' % server.iso(t), start <= t < start + 1800, (start, t))

# Interpolation
segs = [(0, 100.0, 900, 200.0), (900, 200.0, 1800, 50.0)]
check('interp start', server.segment_level(segs, 0) == 100.0)
check('interp mid', abs(server.segment_level(segs, 450) - 150.0) < 1e-9)
check('interp second', abs(server.segment_level(segs, 1350) - 125.0) < 1e-9)
check('interp none', server.segment_level(segs, 2000) is None)
check('held after', server.held_level(segs, 2000) == 50.0)
check('held before', server.held_level(segs, -10) == 100.0)
check('short id', server.short_unit_id('T_DRAXX-1') == 'DRAXX-1' and server.short_unit_id('2__AEDF000') == 'AEDF000')

# Matching and the model, with a tiny station list
tmp = tempfile.mkdtemp()
csv_path = os.path.join(tmp, 'stations.csv')
with open(csv_path, 'w', encoding='utf-8') as handle:
    handle.write('id,name,fuel,lat,lon,nation,offshore,capacity_mw,units,unit_prefixes,name_pattern\n'
                 '1,Gasworks,CCGT,53.0,-1.0,England,0,1000,GASW-1 GASW-2,,\n'
                 '2,Big Wind,WIND,55.0,2.0,England,1,1200,,BIGWO-,big wind\n'
                 '3,Pumpy,PS,53.1,-4.0,Wales,0,1700,PUMP-1,,\n'
                 '4,Batteryville,BATTERY,52.0,0.0,England,0,100,,,batteryville\n'
                 '5,Atom,NUCLEAR,54.0,-3.0,England,0,1200,ATOM-1,,\n')
fleet = server.Fleet(csv_path)
check('fleet loaded', len(fleet.stations) == 5)
check('exact', fleet.resolve('GASW-1', 'T_GASW-1', None) == 1)
check('elexon id only', fleet.resolve('', 'T_GASW-2', None) == 1)
check('prefix', fleet.resolve('BIGWO-3', 'T_BIGWO-3', None) == 2)
check('name', fleet.resolve('BV-1', 'E_BV-1', {'n': 'Batteryville One', 'p': '', 'f': 'BATTERY'}) == 4)
check('name needs a matching fuel', fleet.resolve('XX-1', 'T_XX-1', {'n': 'Big Wind 2', 'p': '', 'f': 'CCGT'}) is None)
check('unknown', fleet.resolve('NOPE-1', 'T_NOPE-1', None) is None)

store = server.Store(os.path.join(tmp, 'data'))
units = server.Units(store, fleet)
units.reference = {
    'GASW-1': {'e': 'T_GASW-1', 'f': 'CCGT', 'p': 'Gas Co', 'n': '', 't': 'T', 'c': 500.0},
    'GASW-2': {'e': 'T_GASW-2', 'f': 'CCGT', 'p': 'Gas Co', 'n': '', 't': 'T', 'c': 500.0},
    'BIGWO-1': {'e': 'T_BIGWO-1', 'f': 'WIND', 'p': '', 'n': '', 't': 'T', 'c': None},
    'BV-1': {'e': 'E_BV-1', 'f': 'BATTERY', 'p': 'Batteryville Ltd', 'n': '', 't': 'E', 'c': 100.0},
    'LOST-1': {'e': 'T_LOST-1', 'f': 'WIND', 'p': 'Lost Wind Ltd', 'n': '', 't': 'T', 'c': 300.0},
    'IFA-1': {'e': 'I_IFA-1', 'f': 'INTFR', 'p': '', 'n': '', 't': 'I', 'c': None},
    'SUPP': {'e': '2__SUPP', 'f': '', 'p': 'Supplier', 'n': '', 't': 'S', 'c': None},
}
T = utc(2026, 11, 6, 14, 2, 30)
flat = lambda unit, mw: (('T_' if not unit.startswith(('I', '2')) else '') + unit, [(T - 150, mw, T + 1650, mw)])
pn = {
    'GASW-1': ['T_GASW-1', [(T - 150, 400.0, T + 1650, 400.0)]],
    'GASW-2': ['T_GASW-2', [(T - 150, 0.0, T + 1650, 0.0)]],
    'BIGWO-1': ['T_BIGWO-1', [(T - 150, 600.0, T + 1650, 600.0)]],
    'PUMP-1': ['T_PUMP-1', [(T - 150, -300.0, T + 1650, -300.0)]],
    'BV-1': ['E_BV-1', [(T - 150, 40.0, T + 1650, 40.0)]],
    'ATOM-1': ['T_ATOM-1', [(T - 150, 1000.0, T + 1650, 1000.0)]],
    'LOST-1': ['T_LOST-1', [(T - 150, 200.0, T + 1650, 200.0)]],
    'IFA-1': ['I_IFA-1', [(T - 150, 900.0, T + 1650, 900.0)]],
    'SUPP': ['2__SUPP', [(T - 150, -800.0, T + 1650, -800.0)]],
    'DEMAND-1': ['E_DEMAND-1', [(T - 150, -20.0, T + 1650, -20.0)]],
}
# The operator turns the wind farm down to 300 MW, then a later acceptance to 200 MW
units.boa = {
    ('BIGWO-1', 10): {'elexon': 'T_BIGWO-1', 'segments': {T - 600: (T - 600, 300.0, T + 600, 300.0)}},
    ('BIGWO-1', 11): {'elexon': 'T_BIGWO-1', 'segments': {T - 300: (T - 300, 200.0, T + 300, 200.0)}},
    ('ATOM-1', 3): {'elexon': 'T_ATOM-1', 'segments': {T + 600: (T + 600, 500.0, T + 900, 500.0)}},
}
measured = {'CCGT': 360.0, 'WIND': 360.0, 'NUCLEAR': 1100.0, 'PS': 0.0, 'OTHER': 600.0}
fuelinst_t = T - 150
units.compute(T, fuelinst_t, measured, pn, date(2026, 11, 6), 29, T)
snap = units.snapshot
st = snap['stations']
check('stations placed', set(st) == {1, 2, 3, 4, 5}, sorted(st))
check('gas scaled to measured', abs(st[1]['mw'] - 360.0) < 0.01, st[1])
check('wind uses latest acceptance then scales with the unplaced unit', abs(snap['scale']['WIND'] - 0.9) < 1e-6, snap['scale'])
check('wind station', abs(st[2]['mw'] - 180.0) < 0.01, st[2])
check('acceptance not yet started is ignored', abs(st[5]['mw'] - 1100.0) < 0.01, st[5])
check('pumping kept negative and unscaled', abs(st[3]['mw'] + 300.0) < 0.01, st[3])
check('battery unscaled without a measured total', abs(st[4]['mw'] - 40.0) < 0.01, st[4])
check('instructed count', st[2]['instructed'] == 1 and st[1]['instructed'] == 0)
unplaced = {u['unit']: u for u in snap['unplaced']}
check('unplaced lists the lost wind farm', 'T_LOST-1' in unplaced and abs(unplaced['T_LOST-1']['mw'] - 180.0) < 0.01, snap['unplaced'])
check('interconnector, supplier and demand units excluded', set(unplaced) == {'T_LOST-1'}, sorted(unplaced))
check('categories', abs(snap['categories']['wind']['mapped'] - 180.0) < 0.01 and abs(snap['categories']['wind']['unplaced'] - 180.0) < 0.01, snap['categories']['wind'])
check('reference capacity summed', st[1]['ref_cap'] == 1000.0, st[1])
history = store.query('SELECT id, mw FROM st WHERE t = ?', (fuelinst_t,))
check('history written', len(history) == 5, history)

# A wildly different estimate isn't forced to match (scale guard), and no FUELINST means no scaling
units.compute(T, fuelinst_t, {'CCGT': 4000.0, 'WIND': 360.0, 'NUCLEAR': 1100.0}, pn, date(2026, 11, 6), 29, T)
check('scale guard', units.snapshot['scale']['CCGT'] == 1.0, units.snapshot['scale'])
units.compute(T, None, {}, pn, date(2026, 11, 6), 29, T)
check('no measured totals', units.snapshot['basis'] == 'scheduled' and abs(units.snapshot['stations'][1]['mw'] - 400.0) < 0.01)

# Deleted BOALF amendments are dropped
class FakeUnits(server.Units):
    pass

payload = {'data': [
    {'nationalGridBmUnit': 'ATOM-1', 'bmUnit': 'T_ATOM-1', 'acceptanceNumber': 7, 'timeFrom': server.iso(T - 60),
     'timeTo': server.iso(T + 600), 'levelFrom': 800, 'levelTo': 800, 'amendmentFlag': 'ORI'},
    {'nationalGridBmUnit': 'ATOM-1', 'bmUnit': 'T_ATOM-1', 'acceptanceNumber': 7, 'timeFrom': server.iso(T - 60),
     'timeTo': server.iso(T + 600), 'levelFrom': 800, 'levelTo': 800, 'amendmentFlag': 'DEL'},
]}
original = server.get_json
server.get_json = lambda url, timeout=30: payload
try:
    units.boa = {}
    units.update_boalf(T)
finally:
    server.get_json = original
check('deleted acceptance removed', ('ATOM-1', 7) not in units.boa, units.boa)

print('units tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
