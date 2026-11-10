#!/usr/bin/env python3
"""Build app/station_wikidata.csv: each power station's Wikidata item, location and photo.

Usage: python3 -I tools/build_photos.py [DICTIONARY_DATA_DIR] [REPORT.md]

Photos come from Wikimedia Commons, found through Wikidata:

1. Every UK Wikidata item that is a kind of power station and has
   coordinates, with its classes, photo (P18) and Commons category (P373).
2. Each station in app/stations.csv is matched to an item: through the Power
   Station Dictionary's Wikidata IDs, or by name and distance (a distinctive
   name may be further away, as some of our coordinates are well out), or by
   hand (OVERRIDES below). Items whose classes or name say a different fuel
   (a coal station on the same site as a gas one, a battery beside a wind
   farm) are rejected.
3. A station whose item has no P18 photo can take the first suitable photo
   in its Commons category; those are marked "category" for review.
4. Commons supplies each photo's author and licence. Only licences that
   allow reuse with credit (CC BY, CC BY-SA, CC0, public domain) are kept.
5. Each match's Wikidata location is written out too, and
   tools/build_stations.py uses it in place of the dictionary's, some of
   which are tens of kilometres out.

Every photo has been looked at on the contact sheets (tools/photo_sheet.py);
NO_PHOTO and the other tables below record what that review decided.

Run it in CI (the "Build station photos" workflow), which can reach
Wikimedia. The report lists every match with its distance and method, and
coverage by fuel and capacity, for review before committing the CSV.
"""
import csv
import html
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIONS = os.path.join(ROOT, 'app', 'stations.csv')
OUT = os.path.join(ROOT, 'app', 'station_wikidata.csv')
UA = 'gb-grid-photo-builder/1.0 (https://github.com/rorymarland/gb-grid)'

# Decisions from reviewing the contact sheets (tools/photo_sheet.py), by station name.
# Station -> Wikidata item, where the automatic match is wrong or missed
OVERRIDES = {
    'Lynemouth': 'Q11987128',          # converted from coal to biomass in 2018; Wikidata still says coal
    'Brigg': 'Q5566630',               # "Glanford Brigg Power Station"
    'Hornsea Three': 'Q138197905',     # has no country on Wikidata, so the main query misses it
    'Inch Cape': 'Q133305885',         # likewise
    'Dogger Bank C': 'Q134731299',     # its offshore substation, which stands inside the farm
}
# Station -> the Wikidata item (an ID, or a label to search for) of the site it
# stands on, for stations with no item of their own. Used for location only.
ANCHORS = {
    'West Burton B': 'West Burton power stations',
    'Fellside': 'Sellafield',                    # the CHP plant that supplies the Sellafield site
    'Fort Dunlop': 'Fort Dunlop',
    'Saltend': 'Q116913766',                     # Saltend Chemicals Park
    'Grangemouth CHP': 'Q5595715',               # Grangemouth Refinery
}
# Station -> where its photo was taken, for wind farms that REPD and the
# dictionary both put 17 km or more from their own photographs
PHOTO_LOCATIONS = {
    'Hill of Towie Wind Farm': (57.5067, -3.0795),
    'Tullo': (56.8390, -2.3820),
}
# Stations whose REPD location is wrong
REPD_SKIP = {
    'Dalquhandy Wind Farm',     # REPD's point is in South Ayrshire, 79 km from Coalburn, where it stands
}
# Station -> its site's name in the Renewable Energy Planning Database, where
# the names differ too much to match by themselves
REPD_NAMES = {
    'Ewe Hill II Wind Farm': 'Ewe Hill (extension)',
    'Minety': 'Minety South - Battery Storage',
}
# Station -> a Commons file chosen by eye, from the candidates that
# tools/explore_stations.py finds, or instead of the item's own photo
PHOTOS = {
    'Achlachan': 'Achlachan Wind Farm - geograph.org.uk - 7945361.jpg',
    'An Suidhe Wind Farm': 'An Suidhe wind farm - geograph.org.uk - 6719684.jpg',
    'Andershaw Wind Farm': 'Andershaw Wind Farm - geograph.org.uk - 7880782.jpg',
    'Auchrobert Wind Farm': 'Turbines in the Auchrobert Wind Farm - geograph.org.uk - 6709991.jpg',
    'Baillie Wind Farm': 'Baillie wind farm from the west - geograph.org.uk - 6919746.jpg',
    'Beinn Tharsuinn': 'Windfarm from NE slope of Beinn Tharsuinn - geograph.org.uk - 972149.jpg',
    'Berry Burn Wind Farm': 'Berry Burn Wind Farm - geograph.org.uk - 5884795.jpg',
    'Bhlaraidh Wind Farm': 'View of Bhlaraidh Wind Farm - geograph.org.uk - 7551541.jpg',
    'Brigg': 'Brigg Power Station (aerial) - geograph.org.uk - 3116976.jpg',
    'Carraig Gheal Wind Farm': 'Carraig Gheal wind farm - geograph.org.uk - 4038136.jpg',
    'Corriegarth Wind Farm': 'Corriegarth windfarm - geograph.org.uk - 5599698.jpg',
    'Cottam Development Centre': 'Cottam Development Centre Power Station - geograph.org.uk - 4949743.jpg',
    'Cowes': 'Kingston Power Station, East Cowes, Isle of Wight, England (3).jpg',
    'Creag Riabhach Wind Farm': 'Creag Riabhach Windfarm, Sutherland - geograph.org.uk - 7410699.jpg',
    'Crossdykes': 'Crossdykes wind farm - geograph.org.uk - 8014302.jpg',
    'Dalswinton Wind Farm': 'Dalswinton Wind Farm - geograph.org.uk - 3512151.jpg',
    'Dersalloch Wind Farm': 'Dersalloch Hill Wind Farm - geograph.org.uk - 5246342.jpg',
    'Edinbane Wind Farm': 'Edinbane wind farm - geograph.org.uk - 1730366.jpg',
    'Dorenell': 'Dorenell Wind Farm - geograph.org.uk - 6571426.jpg',
    'Kype Muir': 'Wind farm, Kype Muir - geograph.org.uk - 6623321.jpg',
    'Middle Muir': 'Middle Muir Wind Farm - geograph.org.uk - 8098836.jpg',
    'Pogbie': 'Pogbie Wind Farm - geograph.org.uk - 7676801.jpg',
    'Tralorg': 'In Tralorg Wind Farm - geograph.org.uk - 6812491.jpg',
    'Fallago Rig Wind Farm': 'Fallago Rig Wind Farm - geograph.org.uk - 6531554.jpg',
    'Galloper': 'Galloper windfarm.jpg',
    'Goole Fields 1 Wind Farm': 'Goole Fields 1 Wind Turbine Farm - geograph.org.uk - 5918981.jpg',
    'Gordonbush Wind Farm': 'View of Turbines at Gordonbush Wind Farm - geograph.org.uk - 3848723.jpg',
    'Greater Gabbard': 'Greater-gabbard-20140813-stenaline.jpg',
    'Harburnhead Wind Farm': 'Harburnhead Wind Farm from the air - geograph.org.uk - 8261703.jpg',
    'Hill of Towie Wind Farm': 'Hill of Towie Wind Farm - geograph.org.uk - 7119217.jpg',
    'Hornsea Two': 'Hornsea 2 wind farm from the air.jpg',
    'Keith Hill Wind Farm': 'Keith Hill wind farm - geograph.org.uk - 6304693.jpg',
    'Kilbraur Wind Farm': 'Kilbraur Wind Farm - geograph.org.uk - 6106652.jpg',
    'Mid Hill Wind Farm': 'Mid Hill windfarm - geograph.org.uk - 7092201.jpg',
    'Minsca Wind Farm': 'Minsca Wind Farm - geograph.org.uk - 1228044.jpg',
    'Moy Wind Farm': 'Moy Wind Farm - geograph.org.uk - 6299867.jpg',
    "Paul's Hill Wind Farm": "Wind Farm on Paul's Hill - geograph.org.uk - 1397264.jpg",
    'Rosehall': "Rosehall Windfarm from Cnoc a' Choire, Sutherland - geograph.org.uk - 7700796.jpg",
    'Rothes II Wind Farm': 'Rothes II Wind Farm - geograph.org.uk - 5209892.jpg',
    'Saltend': 'Saltend Power Station and Chemical Works - geograph.org.uk - 986127.jpg',
    'Strathy North Wind Farm': 'Strathy North wind farm - geograph.org.uk - 4623447.jpg',
    'Toddleburn Wind Farm': 'Toddleburn wind farm from the air - geograph.org.uk - 2725230.jpg',
    'Tullo': 'Tullo Wind Farm - geograph.org.uk - 5370939.jpg',
    'West Burton B': 'West Burton B Power Station.jpg',
    'Westermost Rough': 'Westermost Rough wind farm from Barmston beach.jpg',
}
# Matched, but the item's location isn't the station's (it covers a wider area, say), so keep ours
KEEP_LOCATION = {
    'East Anglia One', 'East Anglia Three', 'Hornsea One',
    'Hornsea Three',      # its item's coordinates have pointed at the onshore substation near Norwich
}
# Matched correctly, but the item's photo shows something else
NO_PHOTO = {
    'Didcot B',           # shows the demolished coal station, Didcot A
    'Fawley Cogen',       # shows the demolished oil station
    'Grain',              # shows the demolished oil station
    'Barry',              # shows the old coal station
    'Hornsea One',        # the zone's photo is of Hornsea Two
    'Dudgeon',            # taken in 2013, before Dudgeon was built; shows Sheringham Shoal
    'Greater Gabbard',    # cloud from an aircraft
    'Westermost Rough',   # haze, nothing visible
    'Humber Gateway',     # too dark to make anything out
    'Beatrice',           # a turbine at Nigg harbour
    'Triton Knoll',       # onshore cable trench
    'Seagreen',           # foundations on a barge in port
    'North Kyle Wind Farm',  # the entrance road sign
    'Beinn An Tuirc 2 Wind Farm',  # bare hillside, no turbines in view
    'East Anglia Three',  # the zone's photo is of East Anglia One
    'Inch Cape',          # foundations on a ship in Leith
    'Dogger Bank C',      # the item is its substation
}

GOOD_LICENCE = re.compile(r'^(cc[ -]by(-sa)?[ -]?[\d.]*|cc0|public domain|pd)', re.I)
BAD_FILE = re.compile(r'(map|diagram|logo|plan|locator|location|chart|schematic|\.svg$|\.gif$|\.tiff?$|\.pdf$)', re.I)
STOP = {'power', 'station', 'stations', 'plant', 'wind', 'farm', 'windfarm', 'offshore', 'onshore', 'energy', 'centre',
        'center', 'ccgt', 'ocgt', 'chp', 'generating', 'scheme', 'the', 'ltd', 'limited', 'pumped', 'storage', 'project',
        'hydro', 'hydroelectric', 'electric', 'nuclear', 'gas', 'fired', 'park', 'battery', 'bess', 'site', 'of', 'and',
        'extension', 'array', 'facility', 'solar', 'biomass', 'plc', 'uk'}


def get(url, data=None, tries=4):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as error:   # retry slow or rate-limited responses
            if attempt == tries - 1:
                raise
            print('  retrying after', type(error).__name__, error, file=sys.stderr)
            time.sleep(5 * (attempt + 1))


def sparql(query):
    body = urllib.parse.urlencode({'query': query, 'format': 'json'}).encode()
    return get('https://query.wikidata.org/sparql', data=body)['results']['bindings']


# What each kind of Wikidata class says about fuel; a class with none of these
# words ("power station") says nothing
TYPE_FUELS = [
    (('wind',), {'WIND'}),
    (('solar', 'photovoltaic'), {'SOLAR'}),
    (('battery', 'energy storage'), {'BATTERY'}),
    (('pumped',), {'PS'}),
    (('hydro',), {'NPSHYD', 'PS'}),
    (('nuclear',), {'NUCLEAR'}),
    (('coal', 'oil', 'diesel', 'tidal', 'geothermal'), {'OTHER'}),
    (('gas', 'combined cycle', 'combined-cycle', 'turbine'), {'CCGT', 'OCGT'}),
    (('biomass', 'waste', 'incinerat', 'biofuel', 'wood', 'straw'), {'BIOMASS'}),
    (('cogeneration', 'combined heat'), {'CCGT', 'OCGT', 'BIOMASS'}),
]


def fuel_problem(st, it):
    """Why an item can't be this station, judging by its classes and name, or None."""
    fuel, label = st['fuel'], it['label'].lower()
    implied = set()
    for t in it.get('types', ()):
        for words, fuels in TYPE_FUELS:
            if any(w in t for w in words):
                implied |= fuels
    if implied and fuel not in implied:
        return 'is a ' + ' / '.join(sorted(it['types']))
    storage = re.search(r'battery|bess|storage', label) and 'pumped' not in label
    if storage and fuel not in ('BATTERY', 'PS'):
        return 'is a battery'
    if fuel == 'BATTERY' and not storage:
        return 'is not a battery'
    if ('solar' in label) != (fuel == 'SOLAR') and fuel != 'BATTERY':
        return 'solar mismatch'
    if fuel == 'WIND' and 'wind' not in label and re.search(r'power station|hydro', label):
        return 'is not a wind farm'
    return None


def tokens(name):
    words = re.findall(r'[a-z0-9]+', name.lower().replace('&', ' and '))
    return {w for w in words if w not in STOP}


def similarity(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def distance_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def candidates():
    # Two cheap queries rather than one expensive one: the classes that are
    # kinds of power station, then the UK items that are instances of them.
    classes = {r['c']['value'].rsplit('/', 1)[-1] for r in sparql('SELECT DISTINCT ?c WHERE { ?c wdt:P279* wd:Q159719 }')}
    print('  %d power station classes' % len(classes), file=sys.stderr)
    items = {}
    rows = []
    # Instances of those classes in the UK; geography is checked locally.
    # Batches of classes keep each query small.
    ordered = sorted(classes)
    for i in range(0, len(ordered), 40):
        query = """
SELECT ?item ?label ?coord ?image ?category ?typeLabel WHERE {
  VALUES ?type { %s }
  VALUES ?country { wd:Q145 wd:Q22 wd:Q21 wd:Q25 }
  ?item wdt:P31 ?type ; wdt:P17 ?country ; wdt:P625 ?coord .
  OPTIONAL { ?item rdfs:label ?label FILTER(LANG(?label) = "en") }
  OPTIONAL { ?type rdfs:label ?typeLabel FILTER(LANG(?typeLabel) = "en") }
  OPTIONAL { ?item wdt:P18 ?image }
  OPTIONAL { ?item wdt:P373 ?category }
}""" % ' '.join('wd:' + c for c in ordered[i:i + 40])
        rows += sparql(query)
        time.sleep(1)
    for row in rows:
        qid = row['item']['value'].rsplit('/', 1)[-1]
        m = re.match(r'Point\(([-\d.eE]+) ([-\d.eE]+)\)', row['coord']['value'])
        if not m:
            continue
        it = items.setdefault(qid, {'qid': qid, 'label': row.get('label', {}).get('value', qid),
                                    'lon': float(m.group(1)), 'lat': float(m.group(2)), 'images': [], 'category': None, 'types': set()})
        if 'typeLabel' in row:
            it['types'].add(row['typeLabel']['value'].lower())
        if 'image' in row:
            name = urllib.parse.unquote(row['image']['value'].rsplit('/', 1)[-1])
            if name not in it['images']:
                it['images'].append(name)
        if 'category' in row:
            it['category'] = row['category']['value']
    return items


def item_details(qids):
    """Label, coordinates, photo and category for specific items (the dictionary's links)."""
    out = {}
    qids = sorted(qids)
    for i in range(0, len(qids), 40):
        data = get('https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode({
            'action': 'wbgetentities', 'ids': '|'.join(qids[i:i + 40]), 'props': 'labels|claims', 'languages': 'en', 'format': 'json'}))
        for qid, ent in (data.get('entities') or {}).items():
            claims = ent.get('claims') or {}

            def first(prop):
                for c in claims.get(prop, []):
                    v = c.get('mainsnak', {}).get('datavalue', {}).get('value')
                    if v is not None:
                        return v
                return None
            coord = first('P625') or {}
            coords = [(c['mainsnak']['datavalue']['value'].get('latitude'), c['mainsnak']['datavalue']['value'].get('longitude'))
                      for c in claims.get('P625', []) if c.get('mainsnak', {}).get('datavalue')]
            out[qid] = {'qid': qid, 'label': ent.get('labels', {}).get('en', {}).get('value', qid),
                        'lat': coord.get('latitude'), 'lon': coord.get('longitude'), 'coords': coords,
                        'images': [c['mainsnak']['datavalue']['value'] for c in claims.get('P18', []) if c.get('mainsnak', {}).get('datavalue')],
                        'category': first('P373'), 'types': set()}
        time.sleep(0.5)
    return out


def nearest_coordinate(it, lat, lon):
    """An item with several coordinates (a wind farm and its onshore substation,
    say) takes the one nearest to where we already have the station."""
    coords = [c for c in it.get('coords') or [] if c[0] is not None]
    if len(coords) < 2:
        return it
    best = min(coords, key=lambda c: distance_km(lat, lon, c[0], c[1]))
    return dict(it, lat=best[0], lon=best[1])


def category_photo(category):
    data = get('https://commons.wikimedia.org/w/api.php?' + urllib.parse.urlencode({
        'action': 'query', 'list': 'categorymembers', 'cmtitle': 'Category:' + category, 'cmtype': 'file', 'cmlimit': '30', 'format': 'json'}))
    for m in (data.get('query') or {}).get('categorymembers', []):
        name = m['title'].split(':', 1)[1]
        if re.search(r'\.(jpe?g|png|webp)$', name, re.I) and not BAD_FILE.search(name):
            return name
    return None


REPD_PAGES = ('https://www.gov.uk/api/content/government/publications/renewable-energy-planning-database-quarterly-extract',
              'https://www.gov.uk/api/content/government/publications/renewable-energy-planning-database-monthly-extract')
REPD_TECH = {
    'WIND': ('wind onshore',), 'SOLAR': ('solar photovoltaics',), 'BATTERY': ('battery',),
    'BIOMASS': ('biomass (dedicated)', 'biomass (co-firing)', 'efw incineration', 'advanced conversion technologies'),
    'NPSHYD': ('large hydro', 'small hydro'), 'PS': ('pumped storage hydroelectricity',),
}


def repd_sites():
    """Operational and under-construction sites in the government's Renewable
    Energy Planning Database, with their surveyed locations as GPS coordinates."""
    import io
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # for -I, which leaves the script's folder off the path
    import osgb
    link = None
    for page in REPD_PAGES:
        req = urllib.request.Request(page, headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=120) as r:
            text = r.read().decode('utf-8')
        links = sorted(set(re.findall(r'https://assets\.publishing\.service\.gov\.uk/[^"\\ <>]+?\.csv', text, re.I)))
        if links:
            link = links[-1]
            break
    if not link:
        raise RuntimeError('no REPD file found on gov.uk')
    print('  REPD: %s' % link, file=sys.stderr)
    with urllib.request.urlopen(urllib.request.Request(link, headers={'User-Agent': UA}), timeout=180) as r:
        raw = r.read()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('cp1252')

    def col(row, name):
        for key, value in row.items():
            if (key or '').strip().lower().startswith(name):
                return (value or '').strip()
        return ''

    def number(value):
        try:
            return float(value.replace(',', ''))
        except ValueError:
            return None
    sites = []
    for row in csv.DictReader(io.StringIO(text)):
        status = col(row, 'development status (short)').lower()
        x, y = number(col(row, 'x-coordinate')), number(col(row, 'y-coordinate'))
        if status not in ('operational', 'under construction') or not x or not y:
            continue
        lat, lon = osgb.grid_to_wgs84(x, y)
        sites.append({'name': col(row, 'site name'), 'tech': col(row, 'technology type').lower(), 'ref': col(row, 'ref id'),
                      'mw': number(col(row, 'installed capacity')) or 0, 'lat': lat, 'lon': lon})
    return sites, os.path.basename(link)


def repd_match(st, sites):
    """The REPD site for an onshore station: the same name and technology, the largest phase."""
    if st['name'] in REPD_SKIP:
        return None
    wanted = REPD_NAMES.get(st['name'])
    techs = REPD_TECH.get(st['fuel'], ())
    found = [s for s in sites if (s['name'] == wanted if wanted else s['tech'] in techs and similarity(st['name'], s['name']) >= 0.99)]
    return max(found, key=lambda s: s['mw']) if found else None


def anchor_item(value):
    """The item for an ANCHORS entry: an ID, or the first search result with that exact label and coordinates."""
    if re.fullmatch(r'Q\d+', value):
        return item_details([value]).get(value)
    found = get('https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode({
        'action': 'wbsearchentities', 'search': value, 'language': 'en', 'type': 'item', 'limit': 8, 'format': 'json'})).get('search', [])
    details = item_details([f['id'] for f in found]) if found else {}
    for f in found:
        it = details.get(f['id'])
        if it and it['label'].lower() == value.lower() and it.get('lat') is not None and 49 < it['lat'] < 61 and -9 < it['lon'] < 3:
            return it
    return None


def clean_author(text):
    """A name to credit, from Commons' Artist field."""
    m = re.match(r'No machine-readable author provided\. (.+?) assumed', text)
    if m:
        text = m.group(1)
    text = re.sub(r'^User:', '', text).strip()
    return text[:120] or 'Unknown'


def meta_coords(meta, into):
    try:
        into['lat'], into['lon'] = float(meta['GPSLatitude']['value']), float(meta['GPSLongitude']['value'])
        return True
    except (KeyError, TypeError, ValueError):
        return False


def commons_info(files):
    """Author, licence and size of each Commons file."""
    out = {}
    files = sorted(set(files))
    for i in range(0, len(files), 40):
        data = get('https://commons.wikimedia.org/w/api.php?' + urllib.parse.urlencode({
            'action': 'query', 'titles': '|'.join('File:' + f for f in files[i:i + 40]), 'prop': 'imageinfo|coordinates',
            'iiprop': 'url|size|mime|extmetadata', 'colimit': 'max', 'format': 'json'}))
        q = data.get('query') or {}
        renames = {n['to']: n['from'] for n in q.get('normalized', [])}
        for page in (q.get('pages') or {}).values():
            info = (page.get('imageinfo') or [None])[0]
            if not info:
                continue
            title = renames.get(page['title'], page['title']).split(':', 1)[1]
            meta = info.get('extmetadata') or {}

            def text(key):
                raw = (meta.get(key) or {}).get('value') or ''
                return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', raw))).strip()
            out[title] = {'author': clean_author(text('Artist')), 'licence': text('LicenseShortName'),
                          'licence_url': text('LicenseUrl'), 'width': info.get('width'), 'height': info.get('height'),
                          'mime': info.get('mime'), 'page': info.get('descriptionurl')}
            # Where the photo was taken, if Commons knows: a check on the station's location
            if not meta_coords(meta, out[title]):
                for c in page.get('coordinates') or []:
                    out[title]['lat'], out[title]['lon'] = c.get('lat'), c.get('lon')
                    break
        time.sleep(0.5)
    return out


def main():
    dictionary = sys.argv[1] if len(sys.argv) > 1 else None
    report_path = sys.argv[2] if len(sys.argv) > 2 else None
    stations = list(csv.DictReader(open(STATIONS, encoding='utf-8')))
    names = {st['name']: st['id'] for st in stations}
    for table in (OVERRIDES, ANCHORS, PHOTO_LOCATIONS, REPD_NAMES, REPD_SKIP, PHOTOS, NO_PHOTO, KEEP_LOCATION):
        unknown = [n for n in table if n not in names]
        assert not unknown, 'not in stations.csv: %s' % unknown
    overrides = {names[n]: q for n, q in OVERRIDES.items()}
    file_overrides = {names[n]: f for n, f in PHOTOS.items()}
    no_photo = {names[n] for n in NO_PHOTO}
    linked = {}
    if dictionary:
        for row in csv.DictReader(open(os.path.join(dictionary, 'dictionary', 'ids.csv'), encoding='utf-8')):
            q = (row.get('wikidata_id') or '').strip()
            if q:
                linked[row['dictionary_id']] = q.split('/')[-1]
    print('Querying Wikidata for power stations around GB…', file=sys.stderr)
    items = candidates()
    print('  %d items' % len(items), file=sys.stderr)
    wanted = set(linked.values()) | {q for q in overrides.values() if q}
    missing = [q for q in wanted if q not in items]
    if missing:
        items.update(item_details(missing))

    matches, rejected, nearest = {}, [], {}
    for st in stations:
        sid, name = st['id'], st['name']
        lat, lon = float(st['lat']), float(st['lon'])
        offshore = st.get('offshore') == '1'
        if sid in overrides:
            it = items.get(overrides[sid])
            if it:
                it = nearest_coordinate(it, lat, lon)
                d = distance_km(lat, lon, it['lat'], it['lon']) if it.get('lat') is not None else None
                matches[sid] = (it, 'override', d)
            continue
        if sid in linked and linked[sid] in items:
            it = nearest_coordinate(items[linked[sid]], lat, lon)
            d = distance_km(lat, lon, it['lat'], it['lon']) if it.get('lat') is not None else None
            why = fuel_problem(st, it)
            if why:
                rejected.append((name, st['fuel'], it, d, why))
            else:
                matches[sid] = (it, 'dictionary', d)
                continue
        best, near = None, None
        for it in items.values():
            d = distance_km(lat, lon, it['lat'], it['lon'])
            sim = similarity(name, it['label'])
            if sim >= 0.5 and (near is None or (sim, -d) > (near[0], -near[2])):
                near = (sim, it, d)
            # A close name may be further away: some of our coordinates are well out
            ok = ((sim >= 0.99 and d <= (60 if offshore else 100)) or (sim >= 0.6 and d <= (40 if offshore else 30))
                  or (sim >= 0.5 and d <= (25 if offshore else 6)))
            if not ok:
                continue
            why = fuel_problem(st, it)
            if why:
                rejected.append((name, st['fuel'], it, d, why))
                continue
            score = sim - d / 1000.0
            if best is None or score > best[0]:
                best = (score, it, d)
        if best:
            matches[sid] = (best[1], 'name and distance', best[2])
        elif near:
            nearest[sid] = near

    # Stations without an item of their own: the site they stand on, else REPD
    located = {}
    for st in stations:
        if st['name'] in PHOTO_LOCATIONS and st['id'] not in matches:
            lat, lon = PHOTO_LOCATIONS[st['name']]
            located[st['id']] = (lat, lon, 'photo', 'where its photo was taken')
    for st in stations:
        if st['id'] in matches or st['id'] in located or st['name'] not in ANCHORS:
            continue
        it = anchor_item(ANCHORS[st['name']])
        time.sleep(0.3)
        if it and it.get('lat') is not None:
            located[st['id']] = (it['lat'], it['lon'], 'site ' + it['qid'], '%s (%s)' % (it['label'], it['qid']))
        else:
            print('  no anchor found for', st['name'], file=sys.stderr)
    try:
        sites, repd_file = repd_sites()
    except Exception as error:      # the photos and Wikidata locations are still worth having
        print('  REPD unavailable: %s %s' % (type(error).__name__, error), file=sys.stderr)
        sites, repd_file = [], 'unavailable'
    for st in stations:
        if st['id'] in matches or st['id'] in located or st.get('offshore') == '1':
            continue
        site = repd_match(st, sites)
        if site:
            located[st['id']] = (site['lat'], site['lon'], 'repd ' + site['ref'], '%s (REPD %s, %.0f MW)' % (site['name'], site['ref'], site['mw']))

    # Photos: one chosen by eye, else the item's own, else one from its Commons category
    chosen = {}
    for sid, f in file_overrides.items():
        it, method, d = matches.get(sid) or (None, 'none', None)
        chosen[sid] = (f, it, method, d, 'chosen')
    for sid, (it, method, d) in matches.items():
        if not it or sid in chosen:
            continue
        if sid in no_photo:
            continue
        good = [f for f in it.get('images', []) if not BAD_FILE.search(f)]
        if good:
            chosen[sid] = (good[0], it, method, d, 'item')
        elif it.get('category'):
            f = category_photo(it['category'])
            time.sleep(0.3)
            if f:
                chosen[sid] = (f, it, method, d, 'category')
    info = commons_info([c[0] for c in chosen.values()])

    rows, skipped = [], []
    for st in stations:
        m = matches.get(st['id'])
        it = m[0] if m else None
        c = chosen.get(st['id'])
        if not it and st['id'] not in located and not c:
            continue
        row = {'id': st['id'], 'wikidata': it['qid'] if it else '', 'lat': '', 'lon': '', 'located': ''}
        # A better location, which tools/build_stations.py prefers to the dictionary's
        if it and it.get('lat') is not None and st['name'] not in KEEP_LOCATION:
            row['lat'], row['lon'], row['located'] = '%.4f' % it['lat'], '%.4f' % it['lon'], 'wikidata'
        elif st['id'] in located:
            row['lat'], row['lon'], row['located'] = '%.4f' % located[st['id']][0], '%.4f' % located[st['id']][1], located[st['id']][2]
        meta = info.get(c[0]) if c else None
        if c and not meta:
            skipped.append((st['name'], c[0], 'not found on Commons'))
        elif c and not GOOD_LICENCE.match(meta['licence'] or ''):
            skipped.append((st['name'], c[0], 'licence ' + (meta['licence'] or 'unknown')))
        elif c:
            row.update({'file': c[0], 'width': meta['width'], 'height': meta['height'], 'author': meta['author'],
                        'licence': meta['licence'], 'licence_url': meta['licence_url'], 'source': c[4]})
        rows.append(row)
    with open(OUT, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=['id', 'wikidata', 'lat', 'lon', 'located', 'file', 'width', 'height', 'author', 'licence',
                                           'licence_url', 'source'], restval='')
        w.writeheader()
        w.writerows(rows)

    # Report: coverage and every match, for review
    have = {r['id'] for r in rows if r.get('file')}
    lines = ['# Station photos', '', '%d of %d stations have a photo.' % (len(have), len(stations)), '']
    by_fuel = {}
    for st in stations:
        f = by_fuel.setdefault(st['fuel'], [0, 0, 0.0, 0.0])
        cap = float(st['capacity_mw'] or 0)
        f[1] += 1
        f[3] += cap
        if st['id'] in have:
            f[0] += 1
            f[2] += cap
    total_cap = sum(v[3] for v in by_fuel.values())
    covered_cap = sum(v[2] for v in by_fuel.values())
    lines += ['Capacity covered (where known): %.0f%% of %.1f GW' % (100 * covered_cap / max(1, total_cap), total_cap / 1000), '',
              '| Fuel | Stations with a photo | Capacity covered |', '|---|---|---|']
    for fuel, (n, total, cap, tcap) in sorted(by_fuel.items(), key=lambda kv: -kv[1][3]):
        lines.append('| %s | %d of %d | %s |' % (fuel, n, total, '%.0f%%' % (100 * cap / tcap) if tcap else 'n/a'))
    lines += ['', '## Matches', '', '| Station | Fuel | Wikidata item | Distance | Method | Photo | Source | Licence |', '|---|---|---|---|---|---|---|---|']
    byid = {r['id']: r for r in rows}
    for st in stations:
        m = matches.get(st['id'])
        if not m or not m[0]:
            continue
        it, method, d = m
        r = byid.get(st['id'])
        lines.append('| %s | %s | %s (%s) | %s | %s | %s | %s | %s |' % (
            st['name'], st['fuel'], it['label'], it['qid'], '%.1f km' % d if d is not None else '?', method,
            r.get('file') or '(none)' if r else '(none)', r.get('source', '') if r else '', r.get('licence', '') if r else ''))
    lines += ['', '## Unmatched', '']
    for st in stations:
        if st['id'] in matches:
            continue
        line = '- %s (%s, %s)' % (st['name'], st['fuel'], 'offshore' if st.get('offshore') == '1' else 'onshore')
        if st['id'] in nearest:
            sim, it, d = nearest[st['id']]
            line += ': closest name %s (%s), %.1f km away, %s' % (it['label'], it['qid'], d, ' / '.join(sorted(it.get('types', ()))))
        lines.append(line)
    if rejected:
        lines += ['', '## Rejected on fuel', ''] + ['- %s (%s): %s (%s) %s, %s' % (
            n, f, it['label'], it['qid'], '%.1f km' % d if d is not None else '?', why) for n, f, it, d, why in rejected]
    if skipped:
        lines += ['', '## Skipped photos', ''] + ['- %s: %s (%s)' % s for s in skipped]
    lines += ['', '## Located by REPD or by the site they stand on', '', 'REPD file: %s' % repd_file, '',
              '| Station | Fuel | Source | Ours | Theirs | Apart |', '|---|---|---|---|---|---|']
    for st in stations:
        if st['id'] in located and st['id'] not in matches:
            lat, lon, _, detail = located[st['id']]
            lines.append('| %s | %s | %s | %s, %s | %.4f, %.4f | %.1f km |' % (
                st['name'], st['fuel'], detail, st['lat'], st['lon'], lat, lon, distance_km(float(st['lat']), float(st['lon']), lat, lon)))
    lines += ['', '## Photos taken far from the station', '', 'Onshore stations whose photo was taken more than 8 km from where we put them '
              '(photos of a wind farm are often taken from a few kilometres off).', '',
              '| Station | Located by | Our location | Photo taken at | Apart | Dictionary to photo |', '|---|---|---|---|---|---|']
    checked = 0
    for st in stations:
        r, c = byid.get(st['id']), chosen.get(st['id'])
        meta = info.get(c[0]) if c else None
        if not r or not meta or meta.get('lat') is None or st.get('offshore') == '1':
            continue
        checked += 1
        lat, lon = (float(r['lat']), float(r['lon'])) if r['lat'] else (float(st['lat']), float(st['lon']))
        d = distance_km(lat, lon, meta['lat'], meta['lon'])
        if d > 8:
            lines.append('| %s | %s | %.4f, %.4f | %.4f, %.4f | %.1f km | %.1f km |' % (
                st['name'], r['located'] or 'dictionary', lat, lon, meta['lat'], meta['lon'], d,
                distance_km(float(st['lat']), float(st['lon']), meta['lat'], meta['lon'])))
    lines += ['', '%d onshore stations have a photo with a known position.' % checked]
    lines += ['', '## Still on the dictionary\'s or a hand-entered location', '']
    lines += ['- %s (%s%s) %s, %s' % (st['name'], st['fuel'], ', offshore' if st.get('offshore') == '1' else '', st['lat'], st['lon'])
              for st in stations if not byid.get(st['id'], {}).get('lat')]
    lines += ['', '## Locations', '', 'Our coordinates against Wikidata\'s, where they differ by more than 2 km.', '',
              '| Station | Offshore | Ours | Wikidata | Apart |', '|---|---|---|---|---|']
    for st in stations:
        m = matches.get(st['id'])
        if m and m[0] and m[2] is not None and m[2] > 2:
            lines.append('| %s | %s | %s, %s | %.4f, %.4f | %.1f km |' % (
                st['name'], st.get('offshore') == '1' and 'yes' or '', st['lat'], st['lon'], m[0]['lat'], m[0]['lon'], m[2]))
    report = '\n'.join(lines) + '\n'
    if report_path:
        open(report_path, 'w', encoding='utf-8').write(report)
    print(report)


if __name__ == '__main__':
    main()
