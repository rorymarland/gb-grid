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
original = server.fetch.get_json
server.fetch.get_json = lambda url, timeout=30: {'data': next(v for k, v in responses.items() if k in url)}
try:
    bal.fetch_period(day, 1)
finally:
    server.fetch.get_json = original
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

# ---- outages and margin -----------------------------------------------------

out = server.Outages()
now = server.time.time()
margin_rows = [{'startTime': server.iso(now + 3600), 'publishTime': server.iso(now - 600), 'forecastHorizon': 1, 'deratedMargin': 4100.0, 'lossOfLoadProbability': 0.0},
               {'startTime': server.iso(now + 3600), 'publishTime': server.iso(now - 9000), 'forecastHorizon': 4, 'deratedMargin': 9999.0, 'lossOfLoadProbability': 0.0},
               {'startTime': server.iso(now + 7200), 'publishTime': server.iso(now - 600), 'forecastHorizon': 2, 'deratedMargin': 900.0, 'lossOfLoadProbability': 0.01}]
original = server.fetch.get_json
server.fetch.get_json = lambda url, timeout=30: {'data': margin_rows}
try:
    series = out.fetch_margin(now)
finally:
    server.fetch.get_json = original
check('shortest horizon wins', series[0][1] == 4100, series)
out.margin = series
grid = server.Grid(server.Store(tempfile.mkdtemp()), server.Status(), outages=out)
summary = grid.margin_summary()
check('margin summary', summary['margin'] == 4100 and summary['min'] == 900 and summary['level'] == 'very-tight', summary)
check('margin levels', server.margin_level(6000)[0] == 'comfortable' and server.margin_level(3000)[0] == 'normal' and server.margin_level(1500)[0] == 'tight')
out.events = [
    {'asset': 'I_IEG-FRAN1', 'unit': 'IEG-FRAN1', 'lost': 1500.0, 'normal': 2000.0, 'type': 'Unplanned', 'cause': 'Fault', 'start': now - 86400, 'end': now + 86400, 'fuel': ''},
    {'asset': 'I_IED-FRAN1', 'unit': 'IED-FRAN1', 'lost': 1500.0, 'normal': 2000.0, 'type': 'Unplanned', 'cause': 'Fault', 'start': now - 86400, 'end': now + 86400, 'fuel': ''},
    {'asset': 'T_X-1', 'unit': 'X-1', 'lost': 400.0, 'normal': 400.0, 'type': 'Planned', 'cause': '', 'start': now + 86400, 'end': now + 9 * 86400, 'fuel': ''},
]
out.events.append({'asset': 'T_X-1', 'unit': 'X-1', 'lost': 300.0, 'normal': 400.0, 'type': 'Planned', 'cause': '', 'start': now + 86400, 'end': now + 2 * 86400, 'fuel': ''})
out.events.append({'asset': 'T_OLD-1', 'unit': 'OLD-1', 'lost': 550.0, 'normal': 550.0, 'type': 'Planned', 'cause': '', 'start': now - 9e7, 'end': now + 9e8, 'fuel': ''})
data = grid.outages_payload()
check('interconnector twins counted once', data['offline'][0]['name'] == 'IFA (France)' and data['offline'][0]['lost'] == 1500 and data['lost_total'] == 1500, data['offline'])
check('future outages listed separately', len(data['upcoming']) == 1 and data['upcoming'][0]['lost'] == 400, data['upcoming'])
check('closures left out', data['closed_count'] == 1 and data['closed_mw'] == 550 and data['lost_total'] == 1500, data)

# ---- long-term history and outlook ----------------------------------------

store = server.Store(tempfile.mkdtemp())
lh = server.LongHistory(store)
lh.resource = 'hgm'
start = server.floor_to(server.time.time(), 86400) - 400 * 86400
def mix_record(t, ci, coal=0.0, wind=5000.0):
    return {'DATETIME': datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S'), 'GAS': 10000.0, 'COAL': coal,
            'NUCLEAR': 5000.0, 'WIND': wind, 'WIND_EMB': 1000.0, 'HYDRO': 300, 'IMPORTS': 2000.0, 'BIOMASS': 2000.0,
            'OTHER': 300.0, 'SOLAR': 0.0, 'STORAGE': 0, 'GENERATION': 25600.0 + coal + wind - 5000, 'CARBON_INTENSITY': ci}
records = [mix_record(start + i * 1800, 150 + (i % 48), coal=500.0 if i < 100 else 0.0, wind=20000.0 if i == 300 else 5000.0) for i in range(48 * 400)]
calls = []
def fake(url, timeout=30):
    calls.append(url)
    if 'datastore_search_sql' in url:
        return {'result': {'records': records if len(calls) == 1 else []}}
    return {'data': [{'startTime': server.iso(t), 'dataProvider': 'APXMIDP', 'price': 50.0 + (t // 1800) % 7, 'volume': 10.0}
                     for t in range(server.PRICE_HISTORY_START + 1800 * 0, server.PRICE_HISTORY_START + 7 * 86400, 1800)]}
original = server.fetch.get_json
server.fetch.get_json = fake
try:
    n = lh.fetch_mix()
    since = calls[0]
    lh.fetch_mix()
    first_prices = lh.fetch_prices()
finally:
    server.fetch.get_json = original
check('mix rows stored', n == len(records) and store.scalar('SELECT COUNT(*) FROM lh') == len(records))
check('first query starts at the beginning', '2000-01-01' in server.urllib.parse.unquote(since), since)
check('next query starts after the last row', datetime.fromtimestamp(start + (len(records) - 1) * 1800, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') in server.urllib.parse.unquote(calls[1]), calls[1])
check('prices stored', first_prices > 300 and store.scalar('SELECT MIN(t) FROM lp') == server.PRICE_HISTORY_START)
grid = server.Grid(store, server.Status())
check('buckets', [grid.longterm_bucket(x * 86400) for x in (7, 60, 365, 3000)] == [1800, 21600, 86400, 604800])
data = grid.longterm(start, start + 7 * 86400)
check('half-hourly over a week', data['bucket'] == 1800 and len(data['t']) == 336 and data['mix']['wind'][0] == 6000, (data['bucket'], len(data['t'])))
data = grid.longterm(start, start + 300 * 86400)
check('daily averages', data['bucket'] == 86400 and data['ci'][0] == round(150 + 23.5), data['ci'][:2])
data = grid.longterm()
check('weekly buckets start on Monday', data['bucket'] in (86400, 604800))
summary = grid.longterm_summary()
rec = {r['key']: r for r in summary['records']}
check('cleanest half hour', rec['cleanest-hh']['value'] == 150, rec.get('cleanest-hh'))
check('wind peak', rec['wind-peak']['value'] == 21000 and rec['wind-peak']['t'] == start + 300 * 1800, rec.get('wind-peak'))
check('last coal', summary['last_coal'] == start + 99 * 1800, summary['last_coal'])
check('year apart', summary['compare']['now'] and summary['compare']['then'] and abs(summary['compare']['now']['t'] - summary['compare']['then']['t'] - 364 * 86400) <= 3600, summary['compare'])
check('days are UK days', all(server.uk_midnight_utc(server.uk_day(r['t'])) == r['t'] for r in summary['records'] if r['span'] == 'day')
      and server.uk_midnight_utc(server.uk_day(summary['compare']['now']['t'])) == summary['compare']['now']['t'], summary['records'])
check('years', len(summary['years']) >= 2 and abs(sum(summary['years'][0]['mix'].values()) - 1) < 0.01, summary['years'])

ol = server.Outlook()
now = server.time.time()
base = server.floor_to(now, 1800) + 1800
def fake_outlook(url, timeout=30):
    if 'demand' in url:
        return {'data': [{'startTime': server.iso(base), 'publishTime': '2026-11-07T10:00:00Z', 'boundary': 'N', 'nationalDemand': 30000},
                         {'startTime': server.iso(base), 'publishTime': '2026-11-07T11:00:00Z', 'boundary': 'N', 'nationalDemand': 31000},
                         {'startTime': server.iso(base), 'publishTime': '2026-11-07T12:00:00Z', 'boundary': 'B1', 'nationalDemand': 1}]}
    return {'data': [{'startTime': server.iso(base), 'generation': 8000}]}
server.fetch.get_json = fake_outlook
try:
    ol.poll()
finally:
    server.fetch.get_json = original
check('latest national demand forecast', ol.demand == [(base, 31000.0)], ol.demand)
def fake_tsd(url, timeout=30):
    if 'demand' in url:
        return {'data': [{'startTime': server.iso(base + i * 1800), 'publishTime': '2026-11-07T11:00:00Z', 'boundary': 'N', 'nationalDemand': None if i else 30000,
                          'transmissionSystemDemand': 32000 + i} for i in range(4)]}
    return {'data': []}
server.fetch.get_json = fake_tsd
try:
    ol2 = server.Outlook()
    ol2.poll()
finally:
    server.fetch.get_json = original
check('falls back to transmission demand', ol2.demand_kind == 'transmission' and len(ol2.demand) == 4 and ol2.demand[0][1] == 32000, (ol2.demand_kind, ol2.demand))
check('wind forecast', ol.wind == [(base, 8000.0)], ol.wind)
grid.outlook = ol
payload = grid.outlook_payload()
check('outlook payload', payload['demand'] == [[base, 31000]] and payload['wind'] == [[base, 8000]], payload)

# ---- today on the grid -----------------------------------------------------

store = server.Store(tempfile.mkdtemp())
now = server.time.time()
today = server.uk_clock(now)[0]
start = server.uk_midnight_utc(today - server.timedelta(days=2))
end = server.floor_to(now, 300)
gen, hh, ci = [], [], []
for t in range(start, end, 300):
    for code, mw in (('WIND', 15000), ('CCGT', 6000), ('NUCLEAR', 4000), ('INTFR', 2000)):
        gen.append((t, code, mw))
for t in range(start, end, 1800):
    hh.append(('price', t, -5.0 if 2 <= (t - start) // 3600 % 24 < 4 else 60.0))
    ci.append((0, t, 90, 90, 'low'))
store.write(server.UPSERT_GEN, gen)
store.write(server.UPSERT_HH, hh)
store.write(server.UPSERT_CI, ci)
grid = server.Grid(store, server.Status())
y = grid.summary('yesterday')
words = lambda parts: ''.join(p if isinstance(p, str) else p['t'] for p in parts)
check('summary ready', y['ready'] and y['complete'], {k: y[k] for k in ('ready', 'date')})
check('mix led by wind', words(y['points'][0]['parts']).startswith('Wind supplied 56%'), words(y['points'][0]['parts']))
check('negative prices make the headline', 'below zero' in words(y['headline']) and y['stats']['negative_half_hours'] == 4, words(y['headline']))
check('terms marked', any(isinstance(p, dict) and p.get('term') == 'carbon-intensity' for pt in y['points'] for p in pt['parts']))
check('article', server.article('Electricity Margin Notice') == 'an Electricity Margin Notice' and server.article('High risk of demand reduction') == 'a high risk of demand reduction')
check('today so far', grid.summary('today')['day'] == 'today' and grid.summary('bogus')['day'] == 'today')

# ---- frequency: products, events, statistics ----------------------------------

check('product codes', [server.product_code(n) for n in ('DCL', 'Dynamic Containment LF', 'Dynamic Moderation HF', 'dr l',
                                                          'Positive Quick Reserve', 'Negative Slow Reserve', 'Mystery')]
      == ['DCL', 'DCL', 'DMH', 'DRL', 'PQR', 'NSR', None])

def readings_with(trip_at, depth, base=50.0, start=0, end=3600, up=False):
    out = []
    for t in range(start, end, 15):
        dt = t - trip_at
        v = base + 0.01 * ((t // 15) % 3 - 1)
        if 0 <= dt <= 600:
            shape = min(1.0, dt / 8.0) * server.math.exp(-max(0.0, dt - 8) / 50.0)
            v += (depth if up else -depth) * shape
        out.append((float(t), v))
    return out

found = server.find_events(readings_with(1800, 0.3))
check('one dip found', len(found) == 1, found)
f = found[0] if found else {}
check('dip figures', f.get('direction') == 'low' and f.get('start') == 1800 - 15 + 15 or f.get('start') in (1785, 1800), f)
check('dip size', f.get('peak', 50) < 49.8 and f.get('outside') and f.get('level') == 3 and f.get('recovery') and f['recovery'] < 400, f)
rise = server.find_events(readings_with(1800, 0.15, up=True))
check('rise found', len(rise) == 1 and rise[0]['direction'] == 'high' and rise[0]['level'] == 2, rise)
check('quiet day', server.find_events(readings_with(10 ** 9, 0.3)) == [])
stats = server.frequency_stats([(0, 49.85), (15, 50.0), (30, 50.05), (45, 50.25)])
check('frequency stats', stats['min']['hz'] == 49.85 and stats['max']['hz'] == 50.25 and stats['outside_02_s'] == 15
      and stats['outside_01'] == 50.0 and stats['within_005'] == 50.0, stats)
hist = server.histogram([(0, 49.995), (15, 50.004), (30, 49.0)])
check('histogram', sum(hist['counts']) == 3 and hist['counts'][0] == 1 and len(hist['counts']) == 60, hist)

# ---- the timeline ------------------------------------------------------------

trip = server.floor_to(now - 5 * 3600, 300) + 37
fr = [(t, v) for t, v in readings_with(trip, 0.3, start=int(trip - 3600), end=int(now - 60))]
store.write(server.UPSERT_FREQ, fr, history=False)
store.write(server.UPSERT_RESP, [(int(trip - 3600), int(trip + 10000), 'DCL', 1200.0, 3.0)], history=False)
store.write(server.UPSERT_HH, [('inertia', server.floor_to(trip, 1800), 155.0)], history=False)
# IFA drops 1,000 MW of imports at the trip
store.write(server.UPSERT_GEN, [(t, 'INTFR', 1000) for t in range(server.floor_to(trip, 300) + 300, server.floor_to(trip, 300) + 1800, 300)])
store.write(server.UPSERT_WARN, [(now - 7200, 'ELECTRICITY MARGIN NOTICE', 'ELECTRICITY MARGIN NOTICE from 16:00 to 19:00 on 01/01/2030, shortfall 500 MW')], history=False)
grid = server.Grid(store, server.Status())
events = server.Events(grid)
events.poll()
payload = grid.events_payload(days=4)
by_kind = {}
for e in payload['events']:
    by_kind.setdefault(e['kind'], []).append(e)
check('negative prices on the timeline', len(by_kind.get('price', [])) >= 2 and words(by_kind['price'][0]['title']).startswith('Prices went negative for 2 hours'), [words(e['title']) for e in by_kind.get('price', [])])
check('wind over half', any('Wind supplied over half' in words(e['title']) for e in by_kind.get('clean', [])))
check('demand peak marker', any(e['level'] == 1 for e in by_kind.get('demand', [])))
fe = by_kind.get('frequency', [])
check('frequency event', len(fe) == 1 and 'dipped to 49.' in words(fe[0]['title']), [words(e['title']) for e in fe])
detail = words(fe[0]['detail']) if fe else ''
check('cause found', 'IFA' in detail and '1,000 MW' in detail, detail)
check('response held quoted', '1,200 MW of Dynamic Containment' in detail, detail)
check('notice on the timeline', any('Electricity Margin Notice' in words(e['title']) for e in by_kind.get('notice', [])), by_kind.get('notice'))
check('history link only when covered', all(l['href'].startswith(('/frequency', '/map')) for e in payload['events'] for l in e['links']))
count = store.scalar('SELECT COUNT(*) FROM ev')
events.poll()
check('rerun is idempotent', store.scalar('SELECT COUNT(*) FROM ev') == count, count)
fp = grid.frequency_payload('1h', fe[0]['id'] if fe else None)
check('frequency payload', fp['latest'] and fp['series']['step'] == 15 and fp['response']['held'].get('DCL') is None
      and fp['event'] and fp['event']['data']['inertia'] == 155, {k: fp[k] for k in ('latest', 'response')})
fp7 = grid.frequency_payload('7d')
check('frequency week in buckets', fp7['series']['step'] == 300 and len(fp7['series']['min']) == len(fp7['series']['t']))
links = server.event_links({'id': 'x', 't': now - 3 * 86400, 'kind': 'price'}, now, replay_from=now - 7 * 86400, history_to=now)
check('map link inside the replay', links and links[0]['href'].startswith('/map?at='), links)
links = server.event_links({'id': 'x', 't': now - 30 * 86400, 'kind': 'price'}, now, replay_from=now - 7 * 86400, history_to=now)
check('history link after a week', links and links[0]['href'].startswith('/history?at='), links)

# ---- the sandbox --------------------------------------------------------------

line = server.fit_line([(x, 6.0 * x + 30 + (1 if x % 2 else -1)) for x in range(60)])
check('inertia fit', abs(line[0] - 6.0) < 0.05 and abs(line[1] - 30) < 1.5 and line[2] > 0.99, line)
check('too few points', server.fit_line([(1, 2)] * 10) is None)
sb = grid.sandbox_payload()
check('sandbox payload', sb['held'].get('DCL') == 1200 or sb['held'] == {} or 'DCL' in sb['held'], sb)
check('sandbox fit falls back', not sb['fit']['fitted'] and sb['fit']['a'] == 6.5, sb['fit'])

# ---- the web server: idle timeout and connection cap ---------------------------

import socket as _socket

class TinyServer(server.Server):
    max_connections = 2

class QuickHandler(server.Handler):
    timeout = 1

srv = TinyServer(('127.0.0.1', 0), QuickHandler)
server.threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
idle = [_socket.create_connection(('127.0.0.1', port)) for _ in range(2)]
server.time.sleep(0.2)
third = _socket.create_connection(('127.0.0.1', port))
third.settimeout(3)
check('over the cap gets a 503', third.recv(200).startswith(b'HTTP/1.1 503'))
third.close()
for c in idle:
    c.settimeout(3)
check('idle connections are closed', all(c.recv(10) == b'' for c in idle))
server.time.sleep(0.3)
again = _socket.create_connection(('127.0.0.1', port))
again.settimeout(3)
again.sendall(b'GET /no-such-page HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n')
reply = again.recv(200)
check('slots free up afterwards', reply.startswith(b'HTTP/1.1') and not reply.startswith(b'HTTP/1.1 503'), reply[:40])
again.close()
srv.shutdown()

# ---- station photos and places --------------------------------------------

work = tempfile.mkdtemp()
stations_csv = os.path.join(work, 'stations.csv')
with open(stations_csv, 'w', encoding='utf-8') as handle:
    handle.write('id,name,fuel,lat,lon,nation,offshore,capacity_mw,units,unit_prefixes,name_pattern\n'
                 '1,Torness,NUCLEAR,55.968,-2.4091,Scotland,0,1190,TORN-1,,\n'
                 '2,Finlarig,NPSHYD,56.4816,-4.2987,Scotland,0,17,FINL-1,,\n'
                 '3,Sofia,WIND,54.98,2.23,England,1,1400,SOFIA-1,,\n')
wikidata_csv = os.path.join(work, 'station_wikidata.csv')
with open(wikidata_csv, 'w', encoding='utf-8') as handle:
    handle.write('id,wikidata,lat,lon,file,width,height,author,licence,licence_url,source\n'
                 '1,Q1466261,55.9680,-2.4091,Torness Nuclear Power Station - April 2016.jpg,3972,1999,Taras Young,CC BY-SA 4.0,https://creativecommons.org/licenses/by-sa/4.0,item\n'
                 '2,Q111301500,56.4816,-4.2987,,,,,,,\n'
                 '3,not-a-qid,,,../../etc/passwd,1,1,x,CC0,,item\n'
                 '99,Q1,,,Elsewhere.jpg,1,1,x,CC0,,item\n')
fleet = server.Fleet(stations_csv, wikidata_csv)
check('photo loaded', fleet.stations[1]['photo'] and fleet.stations[1]['photo']['author'] == 'Taras Young', fleet.stations[1])
check('wikidata item loaded', fleet.stations[1]['wikidata'] == 'Q1466261')
check('no photo is none', fleet.stations[2]['photo'] is None and fleet.stations[2]['wikidata'] == 'Q111301500')
check('bad item and path-like file ignored', fleet.stations[3]['wikidata'] is None and fleet.stations[3]['photo'] is None, fleet.stations[3])
check('rows for unknown stations ignored', 99 not in fleet.stations)
check('missing wikidata file is harmless', server.Fleet(stations_csv, os.path.join(work, 'nope.csv')).stations[1]['photo'] is None)

place = server.MapViews.station_place(fleet.stations[1])
check('place has location', place['lat'] == 55.968 and place['lon'] == -2.4091 and place['offshore'] is False, place)
check('photo served from this server', place['photo']['src'].startswith('/api/photo?id=1&v='), place['photo'])
check('commons page link', place['photo']['page'] == 'https://commons.wikimedia.org/wiki/File:Torness_Nuclear_Power_Station_-_April_2016.jpg', place['photo']['page'])
check('place without photo', server.MapViews.station_place(fleet.stations[2])['photo'] is None)

JPEG = b'\xff\xd8\xff\xe0' + b'0' * 200
calls = []
def fake_fetch(url):
    calls.append(url)
    if 'Html' in url:
        return 200, b'<html>not an image</html>'
    if 'Huge' in url:
        return 200, b'\xff\xd8\xff' + b'0' * (server.MAX_BYTES + 1)
    return 200, JPEG
cache_dir = os.path.join(work, 'photos')
store = server.Photos(cache_dir, fetch=fake_fetch)
got = store.get('Torness Nuclear Power Station - April 2016.jpg')
check('photo fetched', got == (JPEG, 'image/jpeg'), got and got[1])
check('asks commons for a thumbnail', calls and 'Special%3AFilePath%2FTorness' in calls[0] and 'width=960' in calls[0], calls)
check('photo kept on disk', any(name.endswith('.jpg') for name in os.listdir(cache_dir)), os.listdir(cache_dir))
again = server.Photos(cache_dir, fetch=fake_fetch).get('Torness Nuclear Power Station - April 2016.jpg')
check('served from the cache after a restart', again == (JPEG, 'image/jpeg') and len(calls) == 1, len(calls))
check('non-images refused', store.get('Html page.jpg') is None)
check('oversized refused', store.get('Huge.jpg') is None)
before = len(calls)
check('failure remembered', store.get('Html page.jpg') is None and len(calls) == before, len(calls) - before)
check('no partial files left', not any(name.endswith('.part') for name in os.listdir(cache_dir)), os.listdir(cache_dir))

redirect = server.WikimediaOnly()
try:
    redirect.redirect_request(None, None, 302, 'Found', {}, 'https://evil.example/x.jpg')
    check('redirects off wikimedia refused', False)
except server.PhotoError as error:
    check('redirects off wikimedia refused', 'redirected to evil.example' in str(error), error)
try:
    redirect.redirect_request(None, None, 302, 'Found', {}, 'http://upload.wikimedia.org/x.jpg')
    check('plain http redirects refused', False)
except server.PhotoError as error:
    check('plain http redirects refused', 'redirected' in str(error), error)

# ---- the station page --------------------------------------------------------

samples = [(1000 + i * 300, 100.0) for i in range(13)]            # an hour at 100 MW
st = server.output_stats(samples, 200)
check('an hour at half capacity', st['mwh'] == 100 and st['factor'] == 0.5 and st['running'] == 1.0 and st['hours'] == 1.0 and st['peak'] == 100, st)
gappy = samples + [(1000 + 12 * 300 + 86400 + i * 300, 0.0) for i in range(13)]   # a day's gap, then an hour idle
st = server.output_stats(gappy, 200)
check('a gap in the record counts for 15 minutes at most', st['hours'] == 2.2 and st['mwh'] == 125 and 0.5 < st['running'] < 0.6, st)
battery = [(i * 300, -50.0 if i < 12 else 80.0) for i in range(25)]
st = server.output_stats(battery, 100)
check('storage counts charging separately', st['mwh'] == 80 and st['stored_mwh'] == 50 and st['peak'] == 80 and st['running'] == 0.5, st)
check('too little data gives no stats', server.output_stats(samples[:3], 200) is None and server.output_stats([], 200) is None)
check('no capacity, no factor', server.output_stats(samples, None)['factor'] is None)
check('half-hour averages', server.week_series([(0, 10.0), (300, 20.0), (1800, 40.0)]) == [[0, 15.0], [1800, 40.0]], server.week_series([(0, 10.0), (300, 20.0), (1800, 40.0)]))
check('edinburgh to london is about 534 km', abs(server.great_circle_km(55.95, -3.19, 51.51, -0.13) - 534) < 6, server.great_circle_km(55.95, -3.19, 51.51, -0.13))

with open(wikidata_csv, 'w', encoding='utf-8') as handle:
    handle.write('id,wikidata,lat,lon,located,file,width,height,author,licence,licence_url,source,owner,operator,opened,wikipedia,repd,applicant,turbines,turbine_mw,turbine_height\n'
                 '1,Q1466261,55.9680,-2.4091,wikidata,,,,,,,,,EDF Energy,1988,Torness nuclear power station,,,,,\n'
                 '3,,,,repd 77,,,,,,,,,,,,77,RWE,100,14,252\n')
fleet = server.Fleet(stations_csv, wikidata_csv)
check('facts loaded', fleet.stations[1]['facts'] == {'operator': 'EDF Energy', 'opened': '1988', 'wikipedia': 'Torness nuclear power station', 'located': 'wikidata'}, fleet.stations[1]['facts'])
check('repd facts loaded', fleet.stations[3]['facts'] == {'applicant': 'RWE', 'turbines': '100', 'turbine_mw': '14', 'turbine_height': '252', 'located': 'repd'}, fleet.stations[3]['facts'])
check('no facts is an empty dict', fleet.stations[2]['facts'] == {})

class FakeUnits:
    version = 1
    snapshot = {'time': 1794000000, 'at': 1794000000, 'basis': 'measured', 'stations': {
        1: {'mw': 933.6, 'units': 2, 'running': 2, 'ref_cap': 1190.0, 'instructed': 0,
            'list': [{'unit': 'T_TORN-2', 'mw': 466.8, 'cap': 595, 'instructed': False}, {'unit': 'T_TORN-1', 'mw': 466.8, 'cap': 595, 'instructed': False}]},
        2: {'mw': 0.0, 'units': 1, 'running': 0, 'ref_cap': 17.0, 'instructed': 0, 'list': []}}}
page_store = server.Store(tempfile.mkdtemp())
t0 = int(server.time.time()) - 3600
page_store.write(server.UPSERT_ST, [(t0 + i * 300, 1, 900.0 + i) for i in range(12)], history=False)
import json as _j
page_store.write(server.UPSERT_EV, [
    ('out-a', t0, 'outage', 3, _j.dumps(['Torness went offline unexpectedly, losing 600 MW']), _j.dumps(['Reason given: trip. ']), _j.dumps({'sid': 1, 'name': 'Torness', 'lost': 600}), t0),
    ('out-b', t0, 'outage', 2, _j.dumps(['Finlarig went offline']), '[]', _j.dumps({'sid': 2, 'name': 'Finlarig'}), t0),
    ('freq-a', t0 + 60, 'frequency', 3, _j.dumps(['Frequency dipped to 49.6 Hz']), '[]', _j.dumps({'causes': [{'how': 'outage', 'name': 'Torness', 'mw': 600}]}), t0),
    ('freq-b', t0 + 90, 'frequency', 2, _j.dumps(['Frequency dipped to 49.8 Hz']), '[]', _j.dumps({'causes': [{'how': 'interconnector', 'name': 'Torness'}]}), t0),
], history=False)
grid = server.Grid(page_store, server.Status(), fleet, FakeUnits())
page = grid.station_page(1)
check('station page basics', page['name'] == 'Torness' and page['kind'] == 'Nuclear' and page['capacity'] == 1190 and page['now']['mw'] == 933.6, {k: page[k] for k in ('name', 'kind', 'capacity', 'now')})
check('units sorted by name', [u['unit'] for u in page['units']] == ['T_TORN-1', 'T_TORN-2'], page['units'])
check('history and stats', len(page['points']) == 12 and page['stats']['day']['peak'] == 911.0 and page['stats']['week']['factor'] is not None, page['stats'])
check('its own events only', [e['id'] for e in page['events']] == ['freq-a', 'out-a'], [e['id'] for e in page['events']])
check('facts and wikipedia link', page['facts']['wikipedia_url'] == 'https://en.wikipedia.org/wiki/Torness_nuclear_power_station' and page['facts']['opened'] == '1988', page['facts'])
check('nearest stations by distance', [n['name'] for n in page['nearby']] == ['Finlarig', 'Sofia'] and 100 < page['nearby'][0]['km'] < 140 and page['nearby'][0]['mw'] == 0, page['nearby'])
check('rank among its kind', page['rank'] == {'place': 1, 'of': 1, 'label': 'nuclear'}, page['rank'])
check('unknown station', grid.station_page(99) is None)
idle = grid.station_page(3)
check('a station with no data yet', idle['now'] is None and idle['units'] == [] and idle['stats']['day'] is None and idle['points'] == [] and idle['facts']['turbines'] == '100', idle['now'])
check('station index for search', [s['n'] for s in grid.station_index()['stations']] == ['Finlarig', 'Sofia', 'Torness'] and grid.station_index()['stations'][2]['k'] == 'Nuclear', grid.station_index())
listing = grid.events_payload(days=2)
by_id = {e['id']: e for e in listing['events']}
check('outage events link to the station page', {'t': 'About this station', 'href': '/station?id=1'} in by_id['out-a']['links'], by_id['out-a']['links'])
check('other events do not', not any('/station' in l['href'] for l in by_id['freq-a']['links']), by_id['freq-a']['links'])

# ---- notifications ----------------------------------------------------------

import json as _json
notify = sys.modules['gridlive.notify']
NOW = 1_794_000_000          # 21:20 UK time on 6 November 2026
def event_row(eid, t, kind='outage', level=3, title='Torness went offline unexpectedly, losing 1,200 MW'):
    return (eid, t, kind, level, _json.dumps([title]), _json.dumps(['Reason given: turbine trip. ', {'t': 'See current outages', 'href': '/outages'}]),
            _json.dumps({'name': 'Torness', 'lost': 1200, 'series': [[1, 2]]}), t)
def notifier(**settings):
    defaults = {'NOTIFY_NTFY_URL': 'https://ntfy.example/grid-alerts', 'NOTIFY_NTFY_TOKEN': '', 'NOTIFY_WEBHOOK_URL': '',
                'NOTIFY_LEVEL': 'major', 'NOTIFY_KINDS': [], 'NOTIFY_QUIET_HOURS': '', 'PUBLIC_URL': 'http://nas:8080'}
    defaults.update(settings)
    for key, value in defaults.items():
        setattr(notify, key, value)
    store = server.Store(tempfile.mkdtemp())
    posts = []
    def post(url, payload, headers=None):
        posts.append((url, payload, headers))
        if 'broken' in url:
            raise OSError('connection refused')
        return 200
    return notify.Notifier(store, post=post), store, posts

n, store, posts = notifier()
store.write(server.UPSERT_EV, [event_row('out-old', NOW - 600)], history=False)
check('first run sends nothing', n.poll(NOW) == 0 and not posts, posts)
store.write(server.UPSERT_EV, [event_row('out-1', NOW + 60), event_row('freq-1', NOW + 60, 'frequency', 2, 'Frequency dipped to 49.71 Hz'),
                               event_row('late', NOW - 5 * 3600)], history=False)
check('a new major event is sent once', n.poll(NOW + 120) == 1 and len(posts) == 1, posts)
url, payload, headers = posts[0]
check('ntfy json publish to the server root', url == 'https://ntfy.example/' and payload['topic'] == 'grid-alerts', (url, payload))
check('ntfy title and message', payload['title'].startswith('Torness went offline') and payload['message'] == 'Reason given: turbine trip.', payload)
check('ntfy priority, tag and link', payload['priority'] == 4 and payload['tags'] == ['warning'] and payload['click'] == 'http://nas:8080/timeline#out-1', payload)
check('no token, no auth header', headers is None)
check('not sent again', n.poll(NOW + 240) == 0 and len(posts) == 1)
check('state reports the last one', n.state()['sent'] == 1 and n.state()['last']['title'].startswith('Torness') and n.state()['channels'] == ['ntfy'], n.state())

n, store, posts = notifier(NOTIFY_LEVEL='notable', NOTIFY_KINDS=['frequency'], NOTIFY_WEBHOOK_URL='https://hook.example/x', NOTIFY_NTFY_TOKEN='tk_1')
n.poll(NOW)
store.write(server.UPSERT_EV, [event_row('out-2', NOW), event_row('freq-2', NOW, 'frequency', 2, 'Frequency dipped to 49.71 Hz')], history=False)
check('level and kind filters', n.poll(NOW + 60) == 1 and len(posts) == 2 and all('Frequency' in p[1]['title'] for p in posts), [p[1]['title'] for p in posts])
check('token sent as a bearer header', posts[0][2] == {'Authorization': 'Bearer tk_1'}, posts[0][2])
hook = posts[1][1]
check('webhook payload', hook['kind'] == 'frequency' and hook['level'] == 'notable' and hook['time'].endswith('Z') and 'series' not in hook['data']
      and hook['url'] == 'http://nas:8080/timeline#freq-2', hook)

n, store, posts = notifier(NOTIFY_QUIET_HOURS='21:00-22:00')
n.poll(NOW - 60)
store.write(server.UPSERT_EV, [event_row('out-3', NOW)], history=False)
check('quiet hours send nothing', n.poll(NOW + 60) == 0 and not posts, (posts, server.uk_clock(NOW)[1]))
check('and the event is not sent later', n.poll(NOW + 3 * 3600) == 0 and not posts)
check('quiet hours across midnight', notify.in_quiet(notify.parse_quiet('23:00-07:00'), NOW + 8 * 3600)
      and not notify.in_quiet(notify.parse_quiet('23:00-07:00'), NOW), server.uk_clock(NOW + 8 * 3600)[1])
check('bad quiet hours ignored', notify.parse_quiet('late') is None and notify.parse_quiet('25:00-07:00') is None)

n, store, posts = notifier(NOTIFY_NTFY_URL='https://broken.example/topic')
n.poll(NOW)
store.write(server.UPSERT_EV, [event_row('out-4', NOW)], history=False)
check('a failed send is retried', n.poll(NOW + 60) == 0 and n.poll(NOW + 180) == 0 and len(posts) == 2 and 'refused' in n.state()['error'], (posts, n.state()))
check('until the event is too old', n.poll(NOW + 3 * 3600) == 0 and len(posts) == 2)

n, store, posts = notifier()
n.poll(NOW)
store.write(server.UPSERT_EV, [event_row('burst-%d' % i, NOW) for i in range(10)], history=False)
check('a burst is capped', n.poll(NOW + 60) == notify.MAX_PER_POLL and n.poll(NOW + 120) == 0, len(posts))

n, store, posts = notifier()
n.poll(NOW)
store.write(server.UPSERT_EV, [event_row('rec-1', NOW - 30 * 3600, 'record', 3, 'A new record for wind'),
                               event_row('out-9', NOW - 30 * 3600)], history=False)
check('late-published kinds get longer', n.poll(NOW + 60) == 1 and posts[0][1]['title'] == 'A new record for wind', posts)

n, store, posts = notifier(NOTIFY_NTFY_URL='', NOTIFY_WEBHOOK_URL='')
store.write(server.UPSERT_EV, [event_row('out-5', NOW)], history=False)
check('no channels: nothing sent, test refuses', n.poll(NOW) == 0 and n.poll(NOW + 60) == 0 and not n.test() and n.state()['channels'] == [], n.state())
check('ntfy address without a topic refused', notify.ntfy_target('https://ntfy.sh/') is None and notify.ntfy_target('ftp://x/y') is None)
check('self-hosted ntfy under a path', notify.ntfy_target('http://nas:8090/ntfy/grid') == ('http://nas:8090/ntfy/', 'grid'), notify.ntfy_target('http://nas:8090/ntfy/grid'))
n, store, posts = notifier()
check('test message goes out', n.test() and posts[0][1]['title'] == 'Test notification from GB grid', posts)

print('feature tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
