#!/usr/bin/env python3
"""Build app/boundaries.json: NESO's ETYS transmission system boundaries as map paths.

The boundaries (B4, B6, B7 and so on) come from NESO's Electricity Ten Year
Statement as a shapefile (in Web Mercator; British National Grid is handled too). This reads it
with the standard library only, converts each point to the dashboard's map
projection, simplifies the lines to about a kilometre, and writes paths at
twice the map's scale, as geo.json's outlines are.

    python3 tools/build_boundaries.py                 # download from NESO
    python3 tools/build_boundaries.py boundaries.zip  # or use a downloaded copy

Data: NESO, under the NESO Open Data Licence.
"""

import io
import json
import math
import os
import struct
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'app'))

from osgb import grid_to_wgs84  # noqa: E402
from gridlive.units import map_point  # noqa: E402

PACKAGE = 'https://api.neso.energy/api/3/action/package_show?id=etys-gb-transmission-system-boundaries'
OUT = os.path.join(HERE, '..', 'app', 'boundaries.json')
TOLERANCE = 2.0   # map units at twice the map's scale: about a kilometre
EARTH = 6378137.0


def web_mercator_to_wgs84(x, y):
    return math.degrees(2 * math.atan(math.exp(y / EARTH)) - math.pi / 2), math.degrees(x / EARTH)


def download():
    request = urllib.request.Request(PACKAGE, headers={'User-Agent': 'gb-grid build_boundaries'})
    with urllib.request.urlopen(request, timeout=60) as response:
        resources = json.load(response)['result']['resources']
    resource = sorted((r for r in resources if (r.get('format') or '').upper() == 'ZIP'),
                      key=lambda r: r.get('last_modified') or r.get('created') or '')[-1]
    print('Downloading', resource['name'], file=sys.stderr)
    with urllib.request.urlopen(urllib.request.Request(resource['url'], headers={'User-Agent': 'gb-grid'}), timeout=120) as response:
        return response.read(), resource['name']


def read_dbf(data):
    count, header, length = struct.unpack('<xxxxIHH', data[:12])
    fields, offset = [], 32
    while data[offset] != 0x0D:
        name = data[offset:offset + 11].split(b'\0')[0].decode('ascii', 'replace')
        kind, size = chr(data[offset + 11]), data[offset + 16]
        fields.append((name, kind, size))
        offset += 32
    rows = []
    for i in range(count):
        start = header + i * length + 1   # skip the deletion flag
        row, pos = {}, start
        for name, kind, size in fields:
            raw = data[pos:pos + size].decode('latin-1').strip()
            row[name] = raw
            pos += size
        rows.append(row)
    return fields, rows


def read_shp(data):
    shapes, offset = [], 100
    while offset < len(data):
        _, length = struct.unpack('>ii', data[offset:offset + 8])
        content = data[offset + 8:offset + 8 + length * 2]
        offset += 8 + length * 2
        kind = struct.unpack('<i', content[:4])[0]
        if kind == 0:
            shapes.append([])
            continue
        if kind not in (3, 5, 13, 15, 23, 25):   # polylines and polygons, with or without Z/M
            raise ValueError('unexpected shape type %d' % kind)
        parts_n, points_n = struct.unpack('<ii', content[36:44])
        parts = list(struct.unpack('<%di' % parts_n, content[44:44 + 4 * parts_n])) + [points_n]
        base = 44 + 4 * parts_n
        points = [struct.unpack('<dd', content[base + 16 * i:base + 16 * i + 16]) for i in range(points_n)]
        shapes.append([points[parts[i]:parts[i + 1]] for i in range(parts_n)])
    return shapes


def simplify(points, tolerance):
    """Douglas-Peucker."""
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** 0.5 or 1e-9
    worst, index = 0.0, 0
    for i in range(1, len(points) - 1):
        x, y = points[i]
        distance = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm
        if distance > worst:
            worst, index = distance, i
    if worst <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[:index + 1], tolerance)[:-1] + simplify(points[index:], tolerance)


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'rb') as handle:
            raw, source = handle.read(), os.path.basename(sys.argv[1])
    else:
        raw, source = download()
    archive = zipfile.ZipFile(io.BytesIO(raw))
    names = archive.namelist()
    shp = next(n for n in names if n.lower().endswith('.shp'))
    dbf = next(n for n in names if n.lower().endswith('.dbf'))
    prj = next((n for n in names if n.lower().endswith('.prj')), None)
    to_wgs84 = grid_to_wgs84
    if prj:
        projection = archive.read(prj).decode('latin-1')
        print('Projection:', projection[:90], file=sys.stderr)
        if 'Mercator' in projection and 'WGS' in projection:
            to_wgs84 = web_mercator_to_wgs84
        elif 'British_National_Grid' not in projection and 'OSGB' not in projection:
            raise SystemExit('Unexpected projection: ' + projection[:120])
    fields, rows = read_dbf(archive.read(dbf))
    shapes = read_shp(archive.read(shp))
    print('Fields:', fields, file=sys.stderr)
    name_field = next((f[0] for f in fields if f[0].lower() in ('boundary', 'name', 'bound_name', 'boundary_n', 'id')), fields[0][0])
    out = []
    for row, parts in zip(rows, shapes):
        name = row.get(name_field, '').strip()
        paths = []
        for part in parts:
            mapped = []
            for easting, northing in part:
                lat, lon = to_wgs84(easting, northing)
                x, y = map_point(lat, lon)
                mapped.append((x * 2, y * 2))
            mapped = simplify(mapped, TOLERANCE)
            if len(mapped) >= 2:
                paths.append('M' + 'L'.join('%d %d' % (round(x), round(y)) for x, y in mapped))
        if paths and name:
            out.append({'id': name, 'd': ''.join(paths), 'attrs': {k: v for k, v in row.items() if v and k != name_field}})
    out.sort(key=lambda b: b['id'])
    result = {'source': source, 'licence': 'NESO Open Data Licence', 'scale': 2, 'boundaries': out}
    text = json.dumps(result, separators=(',', ':'), ensure_ascii=False)
    with open(OUT, 'w', encoding='utf-8') as handle:
        handle.write(text + '\n')
    print('Wrote %d boundaries, %d bytes, to %s' % (len(out), len(text), OUT), file=sys.stderr)
    for b in out:
        print('  ', b['id'], len(b['d']), b['attrs'], file=sys.stderr)


if __name__ == '__main__':
    main()
