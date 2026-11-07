#!/usr/bin/env python3
"""Mock of every upstream API the dashboard uses, with consistent synthetic data.

Response shapes follow the real services (as used by Kate Morley's production
code and the published API docs), so the dashboard's parsers are exercised
end to end. Values are deterministic functions of time.
"""
import csv
import hashlib
import json
import math
import os
import re
import sys
import time as _clock
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, sys.argv[2] if len(sys.argv) > 2 else '.')
from server import settlement_start, uk_midnight_utc  # noqa: E402  (reuse the code under test only for labels)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9100
INTER = ['INTFR', 'INTIRL', 'INTNED', 'INTEW', 'INTNEM', 'INTIFA2', 'INTNSL', 'INTELEC', 'INTVKL', 'INTGRNL']
FAIL = set()  # names of endpoints to fail, set via /control?fail=...
MODE = {'region': 'normal'}  # /control?region=normal|zero|low|empty|allregions


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def iso_min(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%MZ')


def parse(value):
    d = datetime.fromisoformat(value.strip())
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return int(d.timestamp())


def hour_of_day(t):
    return ((t % 86400) / 3600.0)


def solar_mw(t):
    h = hour_of_day(t)
    if h < 7.3 or h > 16.2:
        return 0.0
    return 3200.0 * math.sin(math.pi * (h - 7.3) / 8.9) ** 1.6 * (0.75 + 0.25 * math.sin(t / 7000.0))


def wind_total(t):
    return 9000 + 5500 * math.sin(t / 41000.0) + 1800 * math.sin(t / 9000.0 + 1.3)


def demand_mw(t):
    h = hour_of_day(t)
    base = 27000 + 6000 * math.sin(math.pi * (h - 6) / 14) if 6 <= h <= 20 else 23500
    return base + 2500 * math.exp(-((h - 17.5) ** 2) / 2.0)


def fuels(t):
    wind_tx = wind_total(t) * 0.82
    emb_wind = wind_total(t) * 0.18
    sol = solar_mw(t)
    nuclear = 4300 + 150 * math.sin(t / 200000.0)
    biomass = 1800 + 700 * math.sin(t / 30000.0)
    hydro = 380 + 120 * math.sin(t / 15000.0)
    other = 650
    inter = {
        'INTFR': 1400 + 500 * math.sin(t / 20000.0), 'INTIFA2': 700 + 300 * math.sin(t / 25000.0),
        'INTELEC': 800 + 200 * math.sin(t / 18000.0), 'INTNED': 300 * math.sin(t / 15000.0),
        'INTNEM': 600 + 350 * math.sin(t / 22000.0), 'INTNSL': 1000 + 400 * math.sin(t / 27000.0),
        'INTVKL': 500 * math.sin(t / 12000.0), 'INTIRL': -250 + 150 * math.sin(t / 9000.0),
        'INTEW': -300 + 200 * math.sin(t / 11000.0), 'INTGRNL': -200 + 250 * math.sin(t / 13000.0),
    }
    pumped = 900 * math.sin(math.pi * (hour_of_day(t) - 4) / 12)
    bess = max(0.0, 500 * math.sin(math.pi * (hour_of_day(t) - 15) / 6))
    known = wind_tx + emb_wind + sol + nuclear + biomass + hydro + other + sum(inter.values()) + pumped + bess
    gas = max(1200.0, demand_mw(t) - known)
    ocgt = 60 if 16 <= hour_of_day(t) <= 19 else 0
    rows = {
        'CCGT': gas - ocgt, 'OCGT': ocgt, 'COAL': 0, 'NUCLEAR': nuclear, 'OIL': 0, 'WIND': wind_tx,
        'NPSHYD': hydro, 'PS': pumped, 'BIOMASS': biomass, 'BESS': bess, 'OTHER': other,
    }
    rows.update(inter)
    return {k: int(round(v)) for k, v in rows.items()}, emb_wind, sol


# ---- BM units: a synthetic fleet built from the real stations.csv ---------

SRC = sys.argv[2] if len(sys.argv) > 2 else '.'


def h01(text):
    return int(hashlib.md5(text.encode()).hexdigest()[:8], 16) / 0xffffffff


def build_fleet():
    units = []
    with open(os.path.join(SRC, 'stations.csv'), encoding='utf-8') as handle:
        stations = list(csv.DictReader(handle))
    for st in stations:
        ids = st['units'].split()
        name = ''
        if not ids and st['unit_prefixes']:
            prefix = st['unit_prefixes'].split()[0]
            ids = [prefix + str(i) for i in range(1, 4)] if prefix.endswith('-') else [prefix]
        if not ids:
            ids = [re.sub('[^A-Z]', '', st['name'].upper())[:5] + 'B-1']
            name = st['name']
        cap = float(st['capacity_mw'] or 0) or 120.0
        for unit in ids:
            units.append({'ngc': unit, 'elexon': ('E_' if st['fuel'] == 'BATTERY' else 'T_') + unit,
                          'fuel': 'BATTERY' if st['fuel'] == 'BATTERY' else st['fuel'],
                          'cap': cap / len(ids), 'name': name, 'party': (name + ' Ltd') if name else 'Generator Co',
                          'type': 'E' if st['fuel'] == 'BATTERY' else 'T'})
    extras = [('NEWWO-1', 'T_NEWWO-1', 'WIND', 'Mystery Offshore Wind Ltd', 'T', 300),
              ('BATTX-1', 'E_BATTX-1', 'OTHER', 'Anon Storage Ltd', 'E', 49),
              ('UNKN-1', 'T_UNKN-1', '', 'Unknown Generation Ltd', 'T', 20),
              ('IED-FRAN1', 'I_IED-FRAN1', 'INTFR', 'Interconnector Co', 'I', 1000),
              ('AEDF000', '2__AEDF000', '', 'Supplier Co', 'S', -500),
              ('DEMND-1', 'E_DEMND-1', '', 'Factory Ltd', 'E', -30)]
    for ngc, elexon, fuel, party, kind, level in extras:
        units.append({'ngc': ngc, 'elexon': elexon, 'fuel': fuel, 'cap': abs(level), 'name': '',
                      'party': party, 'type': kind, 'fixed': level})
    return units


FLEET = build_fleet()
FUELINST_CODE = {'BATTERY': 'BESS'}


def unit_weight(unit, t):
    r = h01(unit['ngc'])
    fuel = unit['fuel']
    if fuel == 'WIND':
        return unit['cap'] * (0.25 + 0.75 * r) * (1 + 0.35 * math.sin(t / 5000.0 + 10 * r))
    if fuel in ('CCGT', 'OCGT'):
        return 0.0 if r < 0.35 else unit['cap'] * (0.6 + 0.4 * r)
    if fuel == 'NUCLEAR':
        return 0.0 if r < 0.15 else unit['cap']
    return unit['cap'] * (0.5 + 0.5 * r)


def unit_levels(t):
    totals, _, _ = fuels(t)
    weights = {}
    for unit in FLEET:
        if 'fixed' not in unit:
            weights.setdefault(unit['fuel'], []).append((unit, unit_weight(unit, t)))
    levels = {}
    for fuel, items in weights.items():
        total = totals.get(FUELINST_CODE.get(fuel, fuel), 0) * 1.06
        weight_sum = sum(w for _, w in items) or 1.0
        for unit, w in items:
            levels[unit['ngc']] = round(total * w / weight_sum, 1)
    for unit in FLEET:
        if 'fixed' in unit:
            levels[unit['ngc']] = float(unit['fixed'])
    return levels


CURTAILED = ['SGRWO-3', 'MOWEO-1', 'VKNGW-1']


def carbon(t):
    f, emb_wind, sol = fuels(t)
    supply = sum(max(0, v) for v in f.values()) + emb_wind + sol
    emitted = f['CCGT'] * 394 + f['OCGT'] * 651 + f['BIOMASS'] * 120 + f['OTHER'] * 300
    emitted += max(0, f['INTFR'] + f['INTIFA2'] + f['INTELEC']) * 53 + max(0, f['INTNED']) * 474
    return int(round(emitted / supply))


def index(v):
    return 'very low' if v < 40 else 'low' if v < 120 else 'moderate' if v < 200 else 'high' if v < 280 else 'very high'


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def send(self, code, body, ctype='application/json'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
        path = url.path
        if path == '/control':
            FAIL.clear()
            FAIL.update(x for x in q.get('fail', '').split(',') if x)
            MODE['region'] = q.get('region', 'normal')
            return self.send(200, {'fail': sorted(FAIL), 'mode': MODE})
        for name in FAIL:
            if name in path:
                return self.send(503, {'error': 'mock failure'})
        if path == '/bmrs/api/v1/datasets/FUELINST/stream':
            start, end = parse(q['publishDateTimeFrom']), parse(q['publishDateTimeTo'])
            out = []
            t = start - start % 300
            while t <= end:
                period_start = t - 300  # published five minutes after the period starts
                if period_start + 300 <= end:
                    f, _, _ = fuels(period_start)
                    day = datetime.fromtimestamp(period_start, timezone.utc).date()
                    for fuel, mw in f.items():
                        out.append({'dataset': 'FUELINST', 'publishTime': iso(t), 'startTime': iso(period_start),
                                    'settlementDate': day.isoformat(), 'settlementPeriod': 1, 'fuelType': fuel,
                                    'generation': mw})
                t += 300
            return self.send(200, out)
        if path == '/bmrs/api/v1/system/frequency/stream':
            start, end = parse(q['from']), parse(q['to'])
            out = []
            t = start - start % 15
            while t <= end - 60:
                hz = 50 + 0.06 * math.sin(t / 97.0) + 0.03 * math.sin(t / 13.0)
                out.append({'measurementTime': iso(t), 'frequency': round(hz, 3)})
                t += 15
            return self.send(200, out)
        if path == '/bmrs/api/v1/balancing/pricing/market-index':
            start, end = parse(q['from']), parse(q['to'])
            out = []
            t = start - start % 1800
            while t < end - 1800:
                price = round(70 + 35 * math.sin(math.pi * (hour_of_day(t) - 7) / 13) + 8 * math.sin(t / 50000.0), 2)
                out.append({'startTime': iso(t), 'dataProvider': 'APXMIDP', 'settlementDate': '2026-11-04',
                            'settlementPeriod': 1, 'price': price, 'volume': 900.5})
                out.append({'startTime': iso(t), 'dataProvider': 'N2EXMIDP', 'settlementDate': '2026-11-04',
                            'settlementPeriod': 1, 'price': 0, 'volume': 0})
                t += 1800
            return self.send(200, {'data': [x for x in out if x['dataProvider'] == q.get('dataProviders', 'APXMIDP')]})
        if path.startswith('/carbon/intensity/') or path.startswith('/carbon/regional/intensity/'):
            parts = path.split('/')
            regional = parts[2] == 'regional'
            frm = parse(parts[4] if regional else parts[3])
            kind = parts[5] if regional else parts[4]
            now = 1e18 if kind != 'fw48h' else None
            if kind == 'pt24h':
                start, end = frm - 86400, frm
            elif kind == 'fw48h':
                start, end = frm, frm + 48 * 3600
            else:
                start, end = frm, parse(kind)
            data = []
            t = start - start % 1800
            import time as _time
            real_now = _time.time()
            while t < end:
                v = carbon(t)
                if regional:
                    v = {'zero': 0, 'low': int(3 + 3 * math.sin(t / 9000.0))}.get(MODE['region'], max(5, int(v * 0.35)))
                entry = {'from': iso_min(t), 'to': iso_min(t + 1800),
                         'intensity': {'forecast': v if regional and MODE['region'] in ('zero', 'low') else v + 4, 'actual': (v if (t + 1800 <= real_now and not regional) else None),
                                       'index': index(v)}}
                if regional:
                    entry['intensity'].pop('actual')
                    mix = ({'wind': 61.8, 'nuclear': 33.4, 'hydro': 4.8} if MODE['region'] in ('zero', 'low')
                           else {'wind': 41.2, 'nuclear': 24.6, 'gas': 18.3, 'imports': 9.1, 'hydro': 6.8})
                    entry['generationmix'] = [{'fuel': fuel, 'perc': mix.get(fuel, 0)} for fuel in
                                              ('biomass', 'coal', 'imports', 'gas', 'nuclear', 'other', 'hydro', 'solar', 'wind')]
                data.append(entry)
                t += 1800
            if regional and MODE['region'] == 'empty':
                return self.send(200, {'data': {'regionid': 2, 'dnoregion': 'SP Distribution',
                                                'shortname': 'South Scotland', 'data': []}})
            if regional and MODE['region'] == 'allregions':
                return self.send(200, {'data': [{'from': e['from'], 'to': e['to'], 'regions': [
                    {'regionid': 1, 'shortname': 'North Scotland', 'intensity': {'forecast': 1, 'index': 'very low'}},
                    {'regionid': 2, 'shortname': 'South Scotland', 'intensity': e['intensity']}]} for e in data]})
            if regional:
                return self.send(200, {'data': {'regionid': 2, 'dnoregion': 'SP Distribution',
                                                'shortname': 'South Scotland', 'data': data}})
            return self.send(200, {'data': data})
        if path == '/bmrs/api/v1/reference/bmunits/all':
            return self.send(200, [{'nationalGridBmUnit': u['ngc'], 'elexonBmUnit': u['elexon'],
                                    'eic': '48W000' + u['ngc'], 'fuelType': u['fuel'] or None,
                                    'leadPartyName': u['party'], 'bmUnitType': u['type'], 'fpnFlag': True,
                                    'bmUnitName': u['name'] or None,
                                    'generationCapacity': str(round(u['cap'])) if u['cap'] > 0 else '0'}
                                   for u in FLEET])
        if path == '/bmrs/api/v1/datasets/PN':
            from datetime import date as _date
            day = _date.fromisoformat(q['settlementDate'])
            period = int(q['settlementPeriod'])
            start = settlement_start(day, period)
            if start > _clock.time() + 3 * 3600:
                return self.send(200, {'data': []})
            points = [start, start + 900, start + 1800]
            levels = [unit_levels(p) for p in points]
            rows = []
            for unit in FLEET:
                for i in range(2):
                    rows.append({'dataset': 'PN', 'settlementDate': day.isoformat(), 'settlementPeriod': period,
                                 'timeFrom': iso(points[i]), 'timeTo': iso(points[i + 1]),
                                 'levelFrom': levels[i][unit['ngc']], 'levelTo': levels[i + 1][unit['ngc']],
                                 'nationalGridBmUnit': unit['ngc'], 'bmUnit': unit['elexon']})
            return self.send(200, {'data': rows})
        if path == '/bmrs/api/v1/datasets/BOALF':
            start, end = parse(q['from']), parse(q['to'])
            now = _clock.time()
            block = int(now // 3600) * 3600
            rows = []
            for n, ngc in enumerate(CURTAILED):
                a = block + 600 * n
                pn = unit_levels(a)[ngc]
                low = round(pn * 0.2, 1)
                profile = [(a, pn), (a + 300, low), (a + 2700, low), (a + 3000, unit_levels(a + 3000)[ngc])]
                for (t0, l0), (t1, l1) in zip(profile, profile[1:]):
                    if start <= t0 <= end:
                        rows.append({'dataset': 'BOALF', 'settlementDate': '', 'settlementPeriodFrom': 1,
                                     'settlementPeriodTo': 2, 'timeFrom': iso(t0), 'timeTo': iso(t1),
                                     'levelFrom': l0, 'levelTo': l1, 'nationalGridBmUnit': ngc,
                                     'bmUnit': 'T_' + ngc, 'acceptanceNumber': 1000 + int(block // 3600) % 1000 * 10 + n,
                                     'acceptanceTime': iso(a - 120), 'deemedBoFlag': False, 'soFlag': True,
                                     'storFlag': False, 'rrFlag': False})
            return self.send(200, {'data': rows})
        if path == '/pvlive/api/v4/pes/0':
            start, end = parse(q['start']), parse(q['end'])
            import time as _time
            real_now = _time.time()
            rows = []
            t = start - start % 1800
            while t <= end:
                if t <= real_now - 360:
                    rows.append([0, iso(t), round(solar_mw(t - 900), 1)])
                t += 1800
            return self.send(200, {'data': rows, 'meta': ['pes_id', 'datetime_gmt', 'generation_mw']})
        if path == '/neso/api/3/action/resource_show':
            return self.send(200, {'success': True, 'result': {
                'id': q.get('id'), 'url': 'http://%s/neso/files/202611041425_embedded_forecast.csv' % self.headers.get('Host', '127.0.0.1:%d' % PORT)}})
        if path == '/neso/files/202611041425_embedded_forecast.csv':
            import time as _time
            now = _time.time()
            lines = ['DATE_GMT,TIME_GMT,SETTLEMENT_DATE,SETTLEMENT_PERIOD,EMBEDDED_WIND_FORECAST,'
                     'EMBEDDED_WIND_CAPACITY,EMBEDDED_SOLAR_FORECAST,EMBEDDED_SOLAR_CAPACITY']
            day = (datetime.fromtimestamp(now, timezone.utc) - timedelta(hours=2)).date()
            for d in range(0, 3):
                current = day + timedelta(days=d)
                for period in range(1, 51):
                    start = settlement_start(current, period)
                    if start >= uk_midnight_utc(current + timedelta(days=1)):
                        break
                    if start < now - 3600:
                        continue
                    _, emb_wind, sol = fuels(start + 900)
                    g = datetime.fromtimestamp(start, timezone.utc)
                    lines.append('%sT00:00:00,%s,%s,%d,%d,6600,%d,17000' % (
                        g.date().isoformat(), g.strftime('%H:%M'), current.isoformat(), period,
                        emb_wind * 1.03, sol * 1.05))
            return self.send(200, '\n'.join(lines) + '\n', 'text/csv')
        if path.endswith('/download/demanddataupdate.csv'):
            import time as _time
            now = _time.time()
            header = ('SETTLEMENT_DATE,SETTLEMENT_PERIOD,ND,FORECAST_ACTUAL_INDICATOR,TSD,ENGLAND_WALES_DEMAND,'
                      'EMBEDDED_WIND_GENERATION,EMBEDDED_WIND_CAPACITY,EMBEDDED_SOLAR_GENERATION,EMBEDDED_SOLAR_CAPACITY,'
                      'NON_BM_STOR,PUMP_STORAGE_PUMPING,SCOTTISH_TRANSFER,IFA_FLOW,IFA2_FLOW,BRITNED_FLOW,MOYLE_FLOW,'
                      'EAST_WEST_FLOW,NEMO_FLOW,NSL_FLOW,ELECLINK_FLOW,VIKING_FLOW,GREENLINK_FLOW')
            lines = [header]
            today = datetime.fromtimestamp(now, timezone.utc).date()
            for back in range(9, -3, -1):
                current = today - timedelta(days=back)
                for period in range(1, 51):
                    start = settlement_start(current, period)
                    if start >= uk_midnight_utc(current + timedelta(days=1)):
                        break
                    _, emb_wind, sol = fuels(start + 900)
                    flag = 'A' if start < now - 86400 else 'F'
                    date_text = current.strftime('%d-%b-%Y').upper() if back % 2 else current.isoformat()
                    lines.append('%s,%d,25000,%s,26000,23000,%d,6600,%d,17000,0,0,0,0,0,0,0,0,0,0,0,0,0' % (
                        date_text, period, flag, emb_wind, sol))
            return self.send(200, '\n'.join(lines) + '\n', 'text/csv')
        return self.send(404, {'error': 'not mocked: ' + path})


if __name__ == '__main__':
    ThreadingHTTPServer((sys.argv[3] if len(sys.argv) > 3 else '127.0.0.1', PORT), Handler).serve_forever()
