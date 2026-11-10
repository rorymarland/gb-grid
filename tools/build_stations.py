#!/usr/bin/env python3
"""Build app/stations.csv: where GB's transmission-connected power stations are.

Usage: python3 -I tools/build_stations.py DICTIONARY_DATA_DIR NATURAL_EARTH_MAP_UNITS_GEOJSON

Base data: the OSUKED Power Station Dictionary (MIT licence): station names,
BM unit IDs, locations, fuel types and, via its linked datasets, capacities.
Stations it doesn't cover (mostly built since 2023) are added by hand below
with approximate coordinates, BM unit ID prefixes and name patterns. The
server matches units to stations by exact ID, then prefix, then by name
against Elexon's BM unit reference data, and lists whatever it can't place.
"""
import csv
import io
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo import project, point_in_polygons  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'app', 'stations.csv')
WIKIDATA = os.path.join(ROOT, 'app', 'station_wikidata.csv')

FUEL_CODES = {
    'CCGT': 'CCGT', 'OCGT': 'OCGT', 'COAL': 'COAL', 'NUCLEAR': 'NUCLEAR', 'WIND': 'WIND',
    'NPSHYD': 'NPSHYD', 'PS': 'PS', 'BIOMASS': 'BIOMASS', 'BATTERY': 'BATTERY',
    'RECIPROCATING': 'OCGT', 'CHP': 'CCGT', 'TIDAL': 'OTHER', 'OIL': 'OIL', 'SOLAR': 'SOLAR',
}

# Closed, or never transmission-connected generation worth mapping
DROP = {
    10002, 10003, 10005, 10006, 10007, 10008, 10009, 10011, 10012, 10013, 10015, 10016,
    10017, 10018, 10019, 10020, 10022, 10050, 10060, 10082, 10127, 10128, 10133, 10134,
    10135, 10139, 10141, 10312, 10313, 10314, 10315, 10316, 10317,
}

# dictionary_id -> changes. 'prefixes' adds BM unit ID prefixes (space separated).
OVERRIDES = {
    10001: {'name': 'Didcot B', 'fuel': 'CCGT', 'units': 'DIDCB5 DIDCB6', 'capacity': 1430},
    10004: {'name': 'Drax', 'fuel': 'BIOMASS', 'capacity': 2580},
    10010: {'name': 'Lynemouth', 'capacity': 420},
    10014: {'name': 'West Burton B', 'fuel': 'CCGT', 'units': 'WBURB-1 WBURB-2 WBURB-3', 'capacity': 1332},
    10039: {'name': 'Keadby 1 and 2', 'fuel': 'CCGT', 'prefixes': 'KEAD-', 'capacity': 1627},
    10047: {'name': 'Pembroke', 'capacity': 2180},
    10067: {'name': 'Grain', 'fuel': 'CCGT', 'units': 'GRAI-6 GRAI-7 GRAI-8', 'capacity': 1275},
    10101: {'fuel': 'CCGT', 'capacity': 1180},
    10115: {'name': 'Errochty'}, 10116: {'name': 'Fasnakyle'}, 10125: {'name': 'Sloy'},
    10130: {'capacity': 1185}, 10131: {'name': 'Heysham 1 and 2', 'capacity': 2385},
    10136: {'name': 'Sizewell B', 'capacity': 1198}, 10138: {'name': 'Torness', 'capacity': 1190},
    10143: {'name': 'Cruachan', 'capacity': 440}, 10144: {'capacity': 1728},
    10145: {'capacity': 360}, 10146: {'name': 'Foyers', 'capacity': 305},
    10169: {'name': 'Burbo Bank and Burbo Bank Extension', 'capacity': 348},
    10185: {'name': 'Dudgeon', 'capacity': 402}, 10194: {'name': 'Galloper', 'capacity': 353},
    10201: {'name': 'Greater Gabbard', 'capacity': 504}, 10206: {'name': 'Gwynt y Mor', 'capacity': 576},
    10214: {'name': 'Humber Gateway', 'capacity': 219}, 10215: {'name': 'Hywind Scotland', 'capacity': 30},
    10219: {'name': 'Lincs', 'capacity': 270}, 10221: {'name': 'London Array', 'capacity': 630},
    10233: {'name': 'Race Bank', 'capacity': 573}, 10234: {'name': 'Rampion', 'capacity': 400},
    10239: {'name': 'Sheringham Shoal', 'capacity': 317},
    # the dictionary has Thanet's onshore substation
    10242: {'name': 'Thanet', 'capacity': 300, 'lat': 51.43, 'lon': 1.63},
    10246: {'name': 'Walney and Walney Extension', 'capacity': 1026},
    10248: {'name': 'Westermost Rough', 'capacity': 210}, 10250: {'name': 'West of Duddon Sands', 'capacity': 389},
    10290: {'name': 'Kincardine', 'capacity': 50}, 10294: {'name': 'Aberdeen Bay', 'capacity': 93},
    10297: {'name': 'Beatrice', 'capacity': 588}, 10300: {'name': 'East Anglia One', 'capacity': 714},
    10301: {'name': 'Hornsea One', 'capacity': 1218}, 10304: {'name': 'Moray East', 'capacity': 950},
    10306: {'name': 'Triton Knoll', 'capacity': 857},
    10318: {'name': 'Seagreen', 'prefixes': 'SGRWO-', 'capacity': 1075},
    10029: {'name': "Connah's Quay"}, 10042: {'name': "King's Lynn"}, 10052: {'name': 'Saltend'},
    10032: {'name': 'Cottam Development Centre'}, 10068: {'name': 'Grangemouth CHP'},
    10104: {'name': "Taylor's Lane"}, 10231: {'name': "Paul's Hill Wind Farm"},
    10187: {'name': 'Dunmaglass Wind Farm'}, 10147: {'name': "A'Chruach Wind Farm"},
    10177: {'name': 'Clyde Wind Farm'}, 10172: {'name': 'Rothes II Wind Farm'},
    10237: {'name': 'Rothes Wind Farm'}, 10319: {'name': 'Tilbury Green'},
    10111: {'fuel': 'NPSHYD'}, 10124: {'fuel': 'NPSHYD'}, 10168: {'fuel': 'WIND'},
    10072: {'fuel': 'CCGT'}, 10074: {'fuel': 'CCGT'}, 10080: {'fuel': 'OCGT'},
    10181: {'name': 'Crystal Rig Wind Farm', 'prefixes': 'CRYRW-'},
}

# Stations missing from the dictionary. BM unit prefixes for some are
# educated guesses; the name patterns are the fallback, and the server lists
# anything it can't place so gaps are visible rather than silent.
# (id, name, fuel, lat, lon, capacity MW, prefixes, name pattern)
MANUAL = [
    (20001, 'Hornsea Two', 'WIND', 53.94, 1.70, 1386, 'HOWBO-', r'hornsea (project )?(two|2)\b'),
    (20002, 'Hornsea Three', 'WIND', 53.85, 2.55, 2955, 'HOWCO-', r'hornsea (project )?(three|3)\b'),
    (20003, 'Dogger Bank A', 'WIND', 54.78, 1.95, 1235, 'DBAWO-', r'dogger ?bank a\b|dogger ?bank.*project 1\b'),
    (20004, 'Dogger Bank B', 'WIND', 55.02, 1.78, 1235, 'DBBWO-', r'dogger ?bank b\b|dogger ?bank.*project 2\b'),
    (20005, 'Dogger Bank C', 'WIND', 55.17, 2.58, 1218, 'DBCWO-', r'dogger ?bank c\b|dogger ?bank.*project 3\b'),
    (20006, 'Sofia', 'WIND', 54.98, 2.33, 1400, 'SOFWO-', r'\bsofia\b'),
    (20007, 'Moray West', 'WIND', 58.09, -3.10, 882, 'MOWWO-', r'moray west|moray offshore wind ?farm \(west\)'),
    (20008, 'Neart na Gaoithe', 'WIND', 56.27, -2.31, 450, 'NNGAO-', r'neart na gaoithe'),
    (20009, 'Inch Cape', 'WIND', 56.47, -2.25, 1080, 'INCPO- ICWWO-', r'inch ?cape'),
    (20010, 'East Anglia Three', 'WIND', 52.66, 2.85, 1372, 'EAAO-3 EA3WO- EATHO-', r'east anglia (three|3)\b'),
    (20011, 'Viking', 'WIND', 60.30, -1.30, 443, 'VKNGW-', r'viking energy|viking wind'),
    (20012, 'Markinch', 'BIOMASS', 56.198, -3.135, 65, '', r'markinch'),
    (20013, "Steven's Croft", 'BIOMASS', 55.155, -3.355, 44, '', r"steven'?s croft"),
    (20020, 'Pillswood', 'BATTERY', 53.79, -0.44, 98, '', r'pillswood'),
    (20021, 'Minety', 'BATTERY', 51.62, -1.97, 100, '', r'\bminety\b'),
    (20022, 'Thurrock storage', 'BATTERY', 51.48, 0.33, 300, '', r'thurrock (energy )?storage'),
    (20023, 'Coalburn storage', 'BATTERY', 55.58, -3.83, 500, '', r'coalburn'),
    (20024, 'Blackhillock storage', 'BATTERY', 57.53, -2.94, 200, '', r'blackhillock'),
    (20025, 'Capenhurst storage', 'BATTERY', 53.26, -2.95, 100, '', r'capenhurst'),
    (20026, 'Kilmarnock South storage', 'BATTERY', 55.58, -4.45, 200, '', r'kilmarnock south'),
    # Found running but unplaced in Elexon's data, November 2026 (unit IDs from the BM unit register)
    (20030, 'Teesside Renewable Energy Plant', 'BIOMASS', 54.588, -1.163, 299, 'TSREP-', r'teesrep|teesside renewable'),
    (20031, 'Wilton International', 'BIOMASS', 54.574, -1.104, '', 'WILCT-', r'wilton site'),
    (20032, 'Cleve Hill Solar Park', 'SOLAR', 51.330, 0.940, 373, 'CLVHS-', r'cleve hill'),
    (20033, 'Little Raith storage', 'BATTERY', 56.104, -3.330, '', 'LITRB-', r'little raith bess'),
    (20034, 'Wishaw storage', 'BATTERY', 55.770, -3.920, '', 'WISHB-', r'wishaw bess'),
    (20035, 'Jamesfield storage', 'BATTERY', 56.330, -3.280, '', 'JAMBB-', r'jamesfield'),
    (20036, 'Iron Acton storage', 'BATTERY', 51.556, -2.478, '', 'IRNAB-', r'iron acton'),
    (20037, 'Larks Green Solar', 'SOLAR', 51.545, -2.470, '', 'LARKS-', r'larks green solar'),
    (20038, 'Larks Green storage', 'BATTERY', 51.543, -2.466, '', 'LARKB-', r'larks green battery'),
    (20039, 'Neilston storage', 'BATTERY', 55.785, -4.430, '', 'NLSTB-', r'neilston battery'),
    (20040, 'Coventry storage', 'BATTERY', 52.390, -1.480, '', 'COVNB-', r'pivot power coventry'),
    (20041, 'Sundon storage', 'BATTERY', 51.930, -0.488, '', 'SUNDB-', r'sundon bess'),
    (20042, 'Warley Green Solar', 'SOLAR', 51.555, 0.360, '', 'BLPFS-', r'warley green|bulphan fen'),
    (20043, 'Tye Lane Solar', 'SOLAR', 52.070, 1.060, '', 'TYLNS-', r'tye lane solar'),
    (20044, 'Tye Lane storage', 'BATTERY', 52.068, 1.064, '', 'TYLNB-', r'tye lane bess'),
    (20045, 'Sutton Bridge Solar Farm', 'SOLAR', 52.770, 0.190, '', 'SUTBS-', r'sutton bridge solar'),
    (20046, 'Tebworth Solar', 'SOLAR', 51.935, -0.565, '', 'TEBWS-', r'tebworth'),
    (20047, 'Wolverhampton West storage', 'BATTERY', 52.600, -2.170, '', 'WOLVB-', r'wolverhampton west'),
    (20048, 'North Kyle Wind Farm', 'WIND', 55.365, -4.420, '', 'NOKYW-', r'north kyle'),
    (20049, 'Greengairs East Wind Farm', 'WIND', 55.905, -3.960, '', 'GRGRW-', r'greengairs'),
]

NAME_FIXES = [
    (r'\s+(Generator|Generation)( \d+)?$', ''), (r'\bWindfarm\b', 'Wind Farm'),
    (r'\bWind Farm Generation\b', 'Wind Farm'), (r'\bOffshore Wind Farm\b', ''),
    (r'\s+1$', ''), (r'\s+-$', ''), (r'\s{2,}', ' '),
]


def read_csv(path):
    with open(path, 'rb') as handle:
        raw = handle.read()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252', 'replace')
    return list(csv.DictReader(io.StringIO(text, newline='')))


def split_ids(text):
    return [x.strip() for x in (text or '').split(',') if x.strip()]


def clean_name(name):
    for pattern, repl in NAME_FIXES:
        name = re.sub(pattern, repl, name).strip()
    return name


def number(text):
    try:
        value = float(str(text).replace(',', '').strip())
        return value if value > 0 else None
    except ValueError:
        return None


def main():
    base, ne_path = sys.argv[1], sys.argv[2]
    attr = os.path.join(base, 'attribute_sources')
    ids = read_csv(os.path.join(base, 'dictionary', 'ids.csv'))
    locations = {r['dictionary_id']: r for r in read_csv(os.path.join(attr, 'plant-locations', 'plant-locations.csv'))}
    fuels = {r['ngc_bmu_id'].strip(): r['fuel_type'].strip().upper()
             for r in read_csv(os.path.join(attr, 'bmu-fuel-types', 'fuel_types.csv'))}
    gppd = {r['gppd_idnr']: number(r['capacity_mw'])
            for r in read_csv(os.path.join(attr, 'global-power-plant-database', 'global_power_plant_database.csv'))
            if r.get('country') == 'GBR'}
    repd = {}
    for r in read_csv(os.path.join(attr, 'renewable-energy-planning-database',
                                   'renewable-energy-planning-database-q2-june-2021.csv')):
        cap = number(r.get('Installed Capacity (MWelec)'))
        for key in ('Ref ID', 'Old Ref ID'):
            if r.get(key) and cap:
                repd[r[key].strip()] = cap

    # GB outlines (unsimplified) for nation and offshore classification
    with open(ne_path, encoding='utf-8') as handle:
        ne = json.load(handle)
    nations = {}
    for feature in ne['features']:
        code = feature['properties'].get('GU_A3')
        if code in ('ENG', 'SCT', 'WLS'):
            geometry = feature['geometry']
            polys = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
            nations[code] = [[[project(lon, lat) for lon, lat in ring] for ring in poly] for poly in polys]
    names = {'ENG': 'England', 'SCT': 'Scotland', 'WLS': 'Wales'}

    def classify(lat, lon, fuel):
        x, y = project(lon, lat)
        for code, polys in nations.items():
            if point_in_polygons(x, y, polys):
                return names[code], 0
        best = None
        for code, polys in nations.items():
            for poly in polys:
                for px, py in poly[0]:
                    d = (px - x) ** 2 + (py - y) ** 2
                    if best is None or d < best[0]:
                        best = (d, code)
        distance = math.sqrt(best[0])
        offshore = 1 if fuel == 'WIND' and distance > 1.5 else 0
        return names[best[1]], offshore

    # Locations from Wikidata, the Renewable Energy Planning Database and
    # so on (tools/build_photos.py), which beat the dictionary's: some of
    # those are tens of kilometres out
    wikidata = {}
    if os.path.exists(WIKIDATA):
        for r in read_csv(WIKIDATA):
            if r.get('lat'):
                wikidata[int(r['id'])] = (float(r['lat']), float(r['lon']))

    rows = []
    for r in ids:
        sid = int(r['dictionary_id'])
        if sid in DROP:
            continue
        loc = locations.get(r['dictionary_id'])
        units = split_ids(r['ngc_bmu_id'])
        override = OVERRIDES.get(sid, {})
        if 'units' in override:
            units = override['units'].split()
        if not loc or not units:
            continue
        lat = override.get('lat', float(loc['latitude']))
        lon = override.get('lon', float(loc['longitude']))
        lat, lon = wikidata.get(sid, (lat, lon))
        counts = {}
        for unit in units:
            code = FUEL_CODES.get(fuels.get(unit, ''))
            if code:
                counts[code] = counts.get(code, 0) + 1
        fuel = override.get('fuel') or (max(counts, key=counts.get) if counts else 'OTHER')
        capacity = override.get('capacity')
        if capacity is None:
            total = sum(gppd.get(g) or 0 for g in split_ids(r.get('gppd_idnr')))
            if not total:
                total = sum(repd.get(k) or 0 for k in split_ids(r.get('old_repd_id')) + split_ids(r.get('new_repd_id')))
            capacity = round(total) if total else ''
        name = override.get('name') or clean_name(r['name'] or '')
        if not name:
            continue
        nation, offshore = classify(lat, lon, fuel)
        rows.append([sid, name, fuel, '%.4f' % lat, '%.4f' % lon, nation, offshore, capacity,
                     ' '.join(units), override.get('prefixes', ''), ''])
    for sid, name, fuel, lat, lon, capacity, prefixes, pattern in MANUAL:
        re.compile(pattern, re.I)
        lat, lon = wikidata.get(sid, (lat, lon))
        nation, offshore = classify(lat, lon, fuel)
        rows.append([sid, name, fuel, '%.4f' % lat, '%.4f' % lon, nation, offshore, capacity, '', prefixes, pattern])

    rows.sort(key=lambda row: row[0])
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(['id', 'name', 'fuel', 'lat', 'lon', 'nation', 'offshore', 'capacity_mw',
                     'units', 'unit_prefixes', 'name_pattern'])
    writer.writerows(rows)
    text = buffer.getvalue()
    if '$' in text or '\t' in text:
        sys.exit('stations.csv must not contain $ or tabs')
    with open(OUT, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(text)
    by_fuel = {}
    for row in rows:
        by_fuel.setdefault(row[2], [0, 0])
        by_fuel[row[2]][0] += 1
        by_fuel[row[2]][1] += row[7] or 0
    print('wrote', OUT, len(rows), 'stations,', len(text), 'bytes')
    for fuel, (n, cap) in sorted(by_fuel.items()):
        print('  %-8s %3d stations %7.0f MW' % (fuel, n, cap))
    print('offshore:', sum(1 for r in rows if r[6]), ' no capacity:', sum(1 for r in rows if r[7] == ''))


if __name__ == '__main__':
    main()
