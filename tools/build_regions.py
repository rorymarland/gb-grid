#!/usr/bin/env python3
"""Build app/regions.json: the 14 regions NESO's carbon intensity and Sheffield
Solar's regional solar figures use, projected and simplified for the Regions page.

Usage: python3 -I tools/build_regions.py path/to/gb-dno-license-areas-20240503-as-geojson.geojson

Source: NESO, "GIS Boundaries for GB DNO Licence Areas" (NESO Open Data Licence),
in British National Grid coordinates. Shared borders are simplified once and
reused by both neighbours, so regions meet without slivers.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo import bng_to_wgs84, point_in_ring, project, ring_area  # noqa: E402
from build_geo import BOX, encode, simplify_ring  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'app', 'regions.json')
TOLERANCE = 0.45   # km
MIN_AREA = 4.0     # km2: smaller islands are dropped

# Licence area ID (also Sheffield Solar's PES ID) -> (NESO Carbon Intensity
# region ID, name as the Carbon Intensity API gives it)
REGIONS = {
    17: (1, 'North Scotland'), 18: (2, 'South Scotland'), 16: (3, 'North West England'),
    15: (4, 'North East England'), 23: (5, 'Yorkshire'), 13: (6, 'North Wales & Merseyside'),
    21: (7, 'South Wales'), 14: (8, 'West Midlands'), 11: (9, 'East Midlands'), 10: (10, 'East England'),
    22: (11, 'South West England'), 20: (12, 'South England'), 12: (13, 'London'), 19: (14, 'South East England'),
}


def polygons_of(geometry):
    if geometry['type'] == 'Polygon':
        return [geometry['coordinates']]
    if geometry['type'] == 'MultiPolygon':
        return geometry['coordinates']
    return []


def edge_distance(x, y, ring):
    best = float('inf')
    for i in range(len(ring)):
        ax, ay = ring[i - 1]
        bx, by = ring[i]
        dx, dy = bx - ax, by - ay
        t = 0.0 if dx == 0 and dy == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(x - (ax + t * dx), y - (ay + t * dy)))
    return best


def label_point(ring):
    """A point well inside the ring: the grid sample furthest from its edge."""
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    step = max(1.5, min(max(xs) - min(xs), max(ys) - min(ys)) / 36.0)
    best, where = -1.0, (sum(xs) / len(xs), sum(ys) / len(ys))
    y = min(ys) + step / 2
    while y < max(ys):
        x = min(xs) + step / 2
        while x < max(xs):
            if point_in_ring(x, y, ring):
                d = edge_distance(x, y, ring)
                if d > best:
                    best, where = d, (x, y)
            x += step
        y += step
    return where


def main():
    with open(sys.argv[1], encoding='utf-8') as handle:
        data = json.load(handle)
    crs = json.dumps(data.get('crs') or {})
    if '27700' not in crs:
        sys.exit('Expected British National Grid (EPSG:27700) coordinates, got ' + crs)
    shapes = {}
    for feature in data['features']:
        props = feature['properties']
        pes = int(props['ID'])
        if pes not in REGIONS:
            sys.exit('Unknown licence area ID %r (%s)' % (pes, props))
        polys = []
        for poly in polygons_of(feature['geometry']):
            rings = []
            for ring in poly:
                pts = []
                for easting, northing in (c[:2] for c in ring):
                    x, y = project(*bng_to_wgs84(easting, northing))
                    pts.append((x, y, (round(easting), round(northing))))
                if pts and pts[0][2] == pts[-1][2]:
                    pts = pts[:-1]
                rings.append(pts)
            polys.append(rings)
        shapes[pes] = (polys, props)
    missing = sorted(set(REGIONS) - set(shapes))
    if missing:
        sys.exit('Missing licence areas: %s' % missing)

    owners = {}
    for pes, (polys, _) in shapes.items():
        for rings in polys:
            for ring in rings:
                for p in ring:
                    owners.setdefault(p[2], set()).add(pes)

    result = []
    bounds = [float('inf'), float('inf'), float('-inf'), float('-inf')]
    for pes in sorted(shapes, key=lambda k: REGIONS[k][0]):
        polys, props = shapes[pes]
        shared = {}
        for rings in polys:
            for ring in rings:
                for p in ring:
                    others = len(owners[p[2]]) - 1
                    if others:
                        shared[p[2]] = others
        out = []
        for rings in polys:
            if abs(ring_area([(p[0], p[1]) for p in rings[0]])) < MIN_AREA:
                continue
            for ring in rings:
                simple = simplify_ring(ring, shared, TOLERANCE, lambda chain, own: own)
                if len(simple) >= 3 and abs(ring_area(simple)) >= 1.0:
                    out.append(simple)
        for ring in out:
            for x, y in ring:
                bounds = [min(bounds[0], x), min(bounds[1], -y), max(bounds[2], x), max(bounds[3], -y)]
        largest = max(out, key=lambda r: abs(ring_area(r)))
        lx, ly = label_point(largest)
        ci, name = REGIONS[pes]
        result.append({'id': ci, 'pes': pes, 'code': props.get('Name'), 'name': name, 'dno': props.get('DNO'),
                       'lx': round(lx, 1), 'ly': round(-ly, 1), 'd': encode(out)})
        print('%-26s rings %4d points %6d' % (name, len(out), sum(len(r) for r in out)))
    body = ('{"box":' + json.dumps([BOX[0], -BOX[3], BOX[2] - BOX[0], BOX[3] - BOX[1]]) +
            ',\n"bounds":' + json.dumps([round(v, 1) for v in bounds]) + ',\n"regions":[\n' +
            ',\n'.join(json.dumps(r, separators=(',', ':')) for r in result) + '\n]}')
    json.loads(body)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(body + '\n')
    print('wrote', OUT, len(body), 'bytes')


if __name__ == '__main__':
    main()
