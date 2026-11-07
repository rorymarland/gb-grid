import os, sys, time, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
os.environ['HISTORY_DAYS'] = '7'
import server as s

store = s.Store('/nonexistent/readonly/path')   # unwritable -> falls back to in-memory
status = s.Status()
grid = s.Grid(store, status)
print('persistent:', store.persistent)

# 1. empty database
live = grid.live(); hist = grid.history('24h'); grid.history('7d'); grid.history('30d')
print('empty live time:', live['time'], '| history points:', len(hist['t']), 'non-null demand:', sum(v is not None for v in hist['demand']))
json.dumps(live); json.dumps(hist)

# 2. generation only, including unknown fuel types
now = int(time.time()); t0 = s.floor_to(now - 600, 300)
rows = []
for t in range(t0 - 3600, t0 + 1, 300):
    for fuel, mw in {'CCGT': 9000, 'OCGT': 50, 'NUCLEAR': 4200, 'WIND': 8000, 'BIOMASS': 1500, 'NPSHYD': 300, 'PS': -400,
                     'BESS': 200, 'OTHER': 600, 'COAL': 0, 'OIL': 0, 'INTFR': 1500, 'INTNSL': -700, 'INTNEWLINK': 300, 'SOLARBM': 120}.items():
        rows.append((t, fuel, mw))
store.write(s.UPSERT_GEN, rows)
live = grid.live()
print('gen-only: demand', live['demand_gw'], 'supply', live['supply_gw'], 'carbon', live['carbon'])
print('  other gw (incl SOLARBM):', [x['gw'] for x in live['sources'] if x['key'] == 'other'], '| countries:', live['countries'])
print('  storing', live['storing_gw'], 'exports', live['exports_gw'], 'storage', live['storage'])

# 3. carbon official overrides estimate when present
hh = s.floor_to(t0, 1800)
store.write(s.UPSERT_CI, [(0, hh, 150.0, None, 'moderate')])
live = grid.live(); print('with official forecast:', live['carbon']['value'], live['carbon']['kind'], live['carbon']['index'])
store.write(s.UPSERT_CI, [(0, hh, None, 140.0, 'moderate')])
live = grid.live(); print('actual arrives (forecast kept):', live['carbon']['value'], live['carbon']['kind'], store.query('SELECT forecast, actual FROM ci'))

# 4. upsert change detection: re-writing identical rows does not bump versions
v = store.history_version; store.write(s.UPSERT_GEN, rows); print('no-op rewrite bumps version:', store.history_version != v)

# 5. status / job failure path
status.attempt('price'); status.fail('price', 'HTTP 503 from example'); status.fail('price', 'HTTP 503 from example')
print('status:', {k: v['failures'] for k, v in status.snapshot().items()})

# 6. ingest parsers against odd payloads
ing = s.Ingest(store, status)
for payload in ({'data': []}, {'data': {'regionid': 2, 'shortname': 'South Scotland', 'data': []}}, {'data': [{'regionid': 2, 'shortname': 'X', 'data': []}]}):
    print('ci entries ok:', ing._ci_entries(payload))
try:
    ing._ci_entries({'error': 'bad'})
except s.FetchError as e:
    print('ci bad payload ->', e)
fields, recs = s.rows_from_csv(b'\xef\xbb\xbfSETTLEMENT_DATE,SETTLEMENT_PERIOD,EMBEDDED_WIND_FORECAST,EMBEDDED_SOLAR_FORECAST\n2026-11-04,30,1500,200\n')
print('csv bom handled:', sorted(fields), s.Ingest._period_start(recs[0], fields))
