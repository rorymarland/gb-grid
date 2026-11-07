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

print('feature tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
