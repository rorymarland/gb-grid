#!/usr/bin/env python3
"""Look for better locations and photos for stations build_photos.py couldn't match.

Usage: python3 -I tools/explore_stations.py   (needs Wikimedia and gov.uk; run in CI)

Prints, for review:
  WD    Wikidata items found by searching for each unmatched station's name
  REPD  the Renewable Energy Planning Database's operational sites that match
        each renewable station by name, with their surveyed coordinates
  PH    Commons files found by searching for each station without a photo
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_photos as bp   # noqa: E402
import osgb                 # noqa: E402

ROOT = bp.ROOT
REPD_PAGE = 'https://www.gov.uk/api/content/government/publications/renewable-energy-planning-database-monthly-extract'
REPD_TECH = {
    'WIND': ('wind onshore', 'wind offshore'), 'SOLAR': ('solar photovoltaics',), 'BATTERY': ('battery',),
    'BIOMASS': ('biomass (dedicated)', 'biomass (co-firing)', 'efw incineration', 'advanced conversion technologies'),
    'NPSHYD': ('large hydro', 'small hydro'), 'PS': ('pumped storage hydroelectricity',),
}


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': bp.UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def repd_rows():
    pattern = r'https://assets\.publishing\.service\.gov\.uk/[^"\\ <>]+?\.csv'
    urls = []
    pages = [REPD_PAGE, REPD_PAGE.replace('monthly', 'quarterly'),
             'https://www.gov.uk/api/search.json?q=renewable+energy+planning+database&count=5&fields=link,title']
    for page in pages:
        try:
            text = fetch(page).decode('utf-8')
        except Exception as error:
            print('REPD page', page, 'failed:', type(error).__name__, error, file=sys.stderr)
            continue
        print('REPD page', page, len(text), text[:300].replace('\n', ' '), file=sys.stderr)
        if 'search.json' in page:
            for hit in json.loads(text).get('results', []):
                print('REPD search hit:', hit.get('title'), hit.get('link'), file=sys.stderr)
                if 'renewable-energy-planning-database' in (hit.get('link') or ''):
                    try:
                        urls += re.findall(pattern, fetch('https://www.gov.uk/api/content' + hit['link']).decode('utf-8'), re.I)
                    except Exception as error:
                        print('REPD', hit['link'], 'failed:', error, file=sys.stderr)
        else:
            urls += re.findall(pattern, text, re.I)
        if urls:
            break
    urls = sorted(set(urls), key=lambda u: ('repd' not in u.lower(), u))
    print('REPD links:', urls, file=sys.stderr)
    raw = fetch(urls[0])
    for encoding in ('utf-8-sig', 'cp1252'):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    rows = list(csv.DictReader(io.StringIO(text)))
    print('REPD: %d rows; columns: %s' % (len(rows), list(rows[0].keys())[:60]), file=sys.stderr)
    return rows


def col(row, *names):
    for key, value in row.items():
        k = (key or '').strip().lower()
        if any(k.startswith(n) for n in names):
            return (value or '').strip()
    return ''


def number(text):
    try:
        return float(text.replace(',', ''))
    except ValueError:
        return None


def main():
    stations = list(csv.DictReader(open(bp.STATIONS, encoding='utf-8')))
    wd = {r['id']: r for r in csv.DictReader(open(bp.OUT, encoding='utf-8'))}

    # ---- Wikidata by name ----
    print('=====WD=====')
    for st in stations:
        if st['id'] in wd:
            continue
        found = bp.get('https://www.wikidata.org/w/api.php?' + urllib.parse.urlencode({
            'action': 'wbsearchentities', 'search': re.sub(r'\s+(CCGT|OCGT|CHP|storage)$', '', st['name']), 'language': 'en',
            'type': 'item', 'limit': 6, 'format': 'json'})).get('search', [])
        ids = [f['id'] for f in found]
        details = bp.item_details(ids) if ids else {}
        shown = 0
        for f in found:
            it = details.get(f['id'])
            if not it:
                continue
            desc = (f.get('description') or '')[:60]
            sim = bp.similarity(st['name'], it['label'])
            d = bp.distance_km(float(st['lat']), float(st['lon']), it['lat'], it['lon']) if it.get('lat') is not None else None
            if sim < 0.5 or d is None or d > 150:
                continue
            print('WD|%s|%s|%s|%s|%s|%s|%.2f|%.1f|%.4f,%.4f|%s' % (st['id'], st['name'], st['fuel'], it['qid'], it['label'], desc, sim, d,
                                                                    it['lat'], it['lon'], ';'.join(it['images'])[:80]))
            shown += 1
        if not shown:
            print('WD|%s|%s|%s|none' % (st['id'], st['name'], st['fuel']))
        time.sleep(0.3)

    # ---- REPD ----
    print('=====REPD=====')
    try:
        repd = repd_rows()
    except Exception as error:
        print('REPD failed:', type(error).__name__, error)
        repd = []
    sites = []
    for r in repd:
        status = col(r, 'development status (short)', 'development status').lower()
        if status not in ('operational', 'under construction'):
            continue
        x, y = number(col(r, 'x-coordinate')), number(col(r, 'y-coordinate'))
        if not x or not y:
            continue
        lat, lon = osgb.grid_to_wgs84(x, y)
        sites.append({'name': col(r, 'site name'), 'tech': col(r, 'technology type').lower(), 'mw': number(col(r, 'installed capacity')),
                      'lat': lat, 'lon': lon, 'ref': col(r, 'ref id'), 'status': status, 'operator': col(r, 'operator')})
    print('REPD usable sites: %d' % len(sites), file=sys.stderr)
    for st in stations:
        techs = REPD_TECH.get(st['fuel'])
        if not techs:
            continue
        lat, lon = float(st['lat']), float(st['lon'])
        scored = []
        for s in sites:
            if s['tech'] not in techs:
                continue
            sim = bp.similarity(st['name'], s['name'])
            if sim >= 0.5:
                scored.append((sim, s['mw'] or 0, s))
        scored.sort(key=lambda t: (-t[0], -t[1]))
        located = 'wikidata' if (st['id'] in wd and wd[st['id']]['lat']) else 'dictionary'
        if not scored:
            print('REPD|%s|%s|%s|%s|none' % (st['id'], st['name'], st['fuel'], located))
        for sim, mw, s in scored[:3]:
            print('REPD|%s|%s|%s|%s|%s|%s|%s MW|%s|%.2f|%.1f km|%.4f,%.4f' % (
                st['id'], st['name'], st['fuel'], located, s['ref'], s['name'], s['mw'], s['tech'], sim,
                bp.distance_km(lat, lon, s['lat'], s['lon']), s['lat'], s['lon']))

    # ---- Commons photos by name ----
    print('=====PH=====')
    dull = re.compile(r'entrance|access|track|road|sign|substation|sub-station|construction|cable|office|\bbase\b|\bpit\b|foundation|plaque|'
                      r'gate|depot|pipeline|laying|lodge|compound|towards|nacelle|blades|\bport\b|pier|parking|\bsite\b|control building', re.I)
    candidates = []
    for st in stations:
        if st['id'] in wd and wd[st['id']]['file']:
            continue
        name = st['name']
        query = name if re.search(r'wind farm|solar|storage', name, re.I) else name + (
            ' wind farm' if st['fuel'] == 'WIND' else ' battery' if st['fuel'] == 'BATTERY' else ' power station')
        try:
            found = bp.get('https://commons.wikimedia.org/w/api.php?' + urllib.parse.urlencode({
                'action': 'query', 'list': 'search', 'srsearch': query + ' filetype:bitmap', 'srnamespace': 6, 'srlimit': 25, 'format': 'json'}))
        except Exception as error:
            print('PH|%s|%s|error %s' % (st['id'], name, error))
            continue
        titles = [h['title'].split(':', 1)[1] for h in (found.get('query') or {}).get('search', [])]
        want = bp.tokens(name)
        keep = [t for t in titles if want and want <= set(re.findall(r'[a-z0-9]+', t.lower())) and not bp.BAD_FILE.search(t) and not dull.search(t)]
        keep.sort(key=lambda t: 0 if re.search(r'from the air|aerial', t, re.I) else 1)
        for n, t in enumerate(keep[:5], 1):
            candidates.append((st['id'], name, n, t))
            print('CAND|%s|%s|%d|%s' % (st['id'], name, n, t))
        if not keep:
            print('PH|%s|%s|%s|none of %d' % (st['id'], name, st['fuel'], len(titles)))
        time.sleep(0.3)
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'w', newline='', encoding='utf-8') as fh:
            csv.writer(fh).writerows([('id', 'name', 'n', 'file')] + candidates)


if __name__ == '__main__':
    main()
