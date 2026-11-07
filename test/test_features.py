#!/usr/bin/env python3
"""Tests for the newer pages' data handling (run: python3 test/test_features.py)."""
import os
import sys
import tempfile
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'app'))

import server  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


def utc(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


# ---- grid notices ---------------------------------------------------------

now = utc(2026, 10, 6, 13, 30)
emn = ('From : Power System Manager - NESO Electricity Control Centre\n\nELECTRICITY MARGIN NOTICE \n\n'
       'For the period:\nfrom 16:00 hrs to 23:00 hrs on Tuesday   06/10/2026  \n\n'
       'There is a reduced system margin. System margin shortfall 321 MW\n')
notice = server.describe_notice(utc(2026, 10, 6, 13, 16), 'ELECTRICITY MARGIN NOTICE', emn, now)
check('emn period start (BST)', notice['start'] == utc(2026, 10, 6, 15, 0), notice)
check('emn period end', notice['end'] == utc(2026, 10, 6, 22, 0), notice)
check('emn summary', 'from 16:00 to 23:00' in notice['summary'] and '321 MW' in notice['summary'], notice['summary'])
check('emn not cancelled', not notice['cancelled'])

cancel = ('NOTIFICATION CANCELLATION of GB TRANSMISSION SYSTEM WARNING \n\nELECTRICITY MARGIN NOTICE\n\n'
          'issued for the period from  16:00 hrs to 23:00 hrs on\nTuesday   06/10/2026 has been cancelled \n')
check('cancellation spotted', server.describe_notice(now, 'ELECTRICITY MARGIN NOTICE', cancel, now)['cancelled'])

nrapm = ('Inadequate Localised Negative Reserve Active Power Margin (NRAPM) in the following regions:\n\n'
         'BEAULY to DOUNREAY region (Scotland) from 23:00 hrs to 05:00 hrs on Saturday 24/10/2026\n')
notice = server.describe_notice(utc(2026, 10, 24, 20, 0), 'NEGATIVE RESERVE ACTIVE POWER MARGIN', nrapm, utc(2026, 10, 24, 20, 0))
check('overnight period ends next day', notice['end'] == utc(2026, 10, 25, 5, 0), notice)
check('region named', 'Beauly to Dounreay area' in notice['summary'], notice['summary'])

cmn = ('Electricity Capacity Market Notice Currently Active\n\nCommencement time of notice : 16:30 on 08/01/2025\n')
notice = server.describe_notice(utc(2025, 1, 8, 12, 4), 'CAPACITY MARKET NOTICE', cmn, utc(2025, 1, 8, 12, 30))
check('capacity market start', notice['start'] == utc(2025, 1, 8, 16, 30) and notice['end'] is None, notice)

dfs = 'DFS Tests have been instructed for Thursday \n21/03/2024 between 17:30 and 19:00. Times in GMT\n'
notice = server.describe_notice(utc(2024, 3, 21, 14, 15), 'DEMAND FLEXIBILITY SERVICE', dfs, utc(2024, 3, 21, 14, 30))
check('dfs window', notice['start'] == utc(2024, 3, 21, 17, 30) and notice['end'] == utc(2024, 3, 21, 19, 0), notice)

other = server.describe_notice(now, 'SO-SO TRADES', 'NATIONAL ENERGY SYSTEM OPERATOR NOTIFICATION of excess energy prices', now)
check('routine notices flagged', other['routine'])

check('uk clock in winter', server.uk_clock(utc(2026, 11, 7, 11, 5)) == (date(2026, 11, 7), '11:05'))
check('uk clock in summer', server.uk_clock(utc(2026, 7, 1, 23, 30)) == (date(2026, 7, 2), '00:30'))

# Active notices: the latest message per type decides, cancellations clear it
store = server.Store(tempfile.mkdtemp())
grid = server.Grid(store, server.Status())
real_time = server.time.time
try:
    server.time.time = lambda: now
    store.write(server.UPSERT_WARN, [(utc(2026, 10, 6, 13, 16), 'ELECTRICITY MARGIN NOTICE', emn),
                                     (utc(2026, 10, 6, 12, 0), 'SO-SO TRADES', 'Prices cover 23:00Hrs Today')], history=False)
    active = grid.alerts()['active']
    check('one active notice', len(active) == 1 and active[0]['kind'] == 'ELECTRICITY MARGIN NOTICE', active)
    store.write(server.UPSERT_WARN, [(utc(2026, 10, 6, 13, 25), 'ELECTRICITY MARGIN NOTICE', cancel)], history=False)
    check('cancelled notice cleared', grid.alerts()['active'] == [], grid.alerts())
finally:
    server.time.time = real_time

# ---- map time machine -------------------------------------------------------

store = server.Store(tempfile.mkdtemp())
grid = server.Grid(store, server.Status())
base = (int(server.time.time()) // 600) * 600 - 3 * 3600
store.write(server.UPSERT_ST, [(base + k * 300, sid, 100.0 * sid + k) for k in range(36) for sid in (1, 2)], history=False)
fuels = {'CCGT': 9000, 'NUCLEAR': 4000, 'WIND': 8000, 'BIOMASS': 1500, 'NPSHYD': 300, 'PS': 0, 'BESS': 0,
         'OTHER': 500, 'COAL': 0, 'OIL': 0, 'INTFR': 1500, 'INTNSL': -700}
store.write(server.UPSERT_GEN, [(base + k * 300, fuel, mw) for k in range(36) for fuel, mw in fuels.items()])
replay = grid.replay()
check('replay frames are ten minutes apart', replay['step'] == 600 and replay['t'][1] - replay['t'][0] == 600, replay['t'][:3])
check('replay covers the stored history', len(replay['t']) == 18, len(replay['t']))
check('station values averaged per frame', abs(replay['stations']['1'][0] - 100.5) <= 0.5 and abs(replay['stations']['2'][1] - 202.5) <= 0.5, replay['stations']['1'][:3])
check('interconnectors replayed', replay['links']['INTNSL'][0] == -700 and replay['links']['INTFR'][5] == 1500, replay['links'])
check('totals replayed', replay['totals']['nuclear'][3] == 4000 and replay['totals']['exports'][0] == -700, replay['totals']['nuclear'][:4])

# ---- regions ----------------------------------------------------------------

store = server.Store(tempfile.mkdtemp())
ingest = server.Ingest(store, server.Status())
grid = server.Grid(store, server.Status())
now_hh = (int(server.time.time()) // 1800) * 1800
payload = {'data': [{'from': server.iso(now_hh + k * 1800), 'to': server.iso(now_hh + (k + 1) * 1800), 'regions': [
    {'regionid': 2, 'shortname': 'South Scotland', 'intensity': {'forecast': 0, 'index': 'very low'},
     'generationmix': [{'fuel': 'wind', 'perc': 60.0}, {'fuel': 'nuclear', 'perc': 40.0}]},
    {'regionid': 13, 'shortname': 'London', 'intensity': {'forecast': 180 + k, 'index': 'Moderate'}, 'generationmix': []}]}
    for k in range(3)]}
check('regional rows stored', ingest._ingest_regions(payload) == 6)
store.write(server.UPSERT_HH, [('rpv12', now_hh - 1800, 250.0)], history=False)
data = grid.regions()
now = data['now']
check('now is the current half hour', data['t0'] + now * 1800 == now_hh, data['t0'])
check('values by region', data['ci']['2'][now] == 0 and data['ci']['13'][now + 2] == 182, data['ci'])
check('index levels', data['index']['13'][now] == 2 and data['index']['2'][now] == 0)
check('mix in fixed fuel order', data['mix']['2'][now].split(',')[data['fuels'].index('wind')] == '60')
check('regional solar mapped to the region', data['solar']['13'][now - 1] == 250, data['solar'])

# ---- balancing --------------------------------------------------------------

store = server.Store(tempfile.mkdtemp())
bal = server.Balancing(store)
day, period = server.settlement_period_at(server.time.time())
responses = {
    'cashflows/all/bid': [{'nationalGridBmUnit': 'WINDA-1', 'bmUnit': 'T_WINDA-1', 'totalCashflow': 7000.0}],
    'cashflows/all/offer': [{'nationalGridBmUnit': 'GASB-1', 'bmUnit': 'T_GASB-1', 'totalCashflow': 9000.0}],
    'volumes/all/bid': [{'nationalGridBmUnit': 'WINDA-1', 'totalVolumeAccepted': -60.0},
                        {'nationalGridBmUnit': 'WINDA-1', 'totalVolumeAccepted': -40.0}],
    'volumes/all/offer': [{'nationalGridBmUnit': 'GASB-1', 'totalVolumeAccepted': 75.0}],
}
original = server.get_json
server.get_json = lambda url, timeout=30: {'data': next(v for k, v in responses.items() if k in url)}
try:
    bal.fetch_period(day, 1)
finally:
    server.get_json = original
rows = {r[0]: r[1:] for r in store.query('SELECT unit, vol_bid, vol_offer, cash_bid, cash_offer FROM bal')}
check('bid volumes summed per unit', rows['WINDA-1'][0] == -100.0 and rows['WINDA-1'][2] == 7000.0, rows)
check('offers stored', rows['GASB-1'][1] == 75.0 and rows['GASB-1'][3] == 9000.0, rows)
check('half hour marked fetched', store.scalar('SELECT COUNT(*) FROM balp') == 1)
grid = server.Grid(store, server.Status(), balancing=bal)
data = grid.balancing()
first = data['series']['today'][0]
check('half hour totals', first[1] == -100.0 and first[2] == 75.0 and first[3] + first[4] == 16000.0, first)
check('days have the right length', len(data['series']['today']) == server.settlement_periods(day))
check('top payments', data['top'][0]['unit'] == 'GASB-1' and data['top'][0]['paid'] == 9000, data['top'])
check('periods in a clock-change day', server.settlement_periods(date(2026, 10, 25)) == 50 and server.settlement_periods(date(2026, 3, 29)) == 46)

print('feature tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
