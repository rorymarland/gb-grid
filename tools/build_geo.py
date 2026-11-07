#!/usr/bin/env python3
"""Build app/geo.json: simplified, projected outlines for the map view.

Usage: python3 -I tools/build_geo.py path/to/ne_10m_admin_0_map_units.geojson

Source: Natural Earth 1:10m Admin 0 map units (public domain).
Shared borders are simplified once and reused by both neighbours (their
end points are pinned), so adjacent outlines meet without slivers.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geo import project, clip_ring, douglas_peucker, ring_area  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'app', 'geo.json')

# Projected extent in km (x east, y north of 49N on the 2W meridian). The
# page never shows anything outside it, so it reaches far enough east and
# south that land never stops at an edge on a wide screen. West and north
# west of Ireland is open sea, which needs no data.
BOX = (-1000.0, -480.0, 1460.0, 1620.0)

# id -> (Natural Earth GU_A3/SU_A3 codes, tolerance km, minimum area km2)
FEATURES = [
    ('SCT', ['SCT'], 0.55, 3.0),
    ('ENG', ['ENG'], 0.55, 3.0),
    ('WLS', ['WLS'], 0.55, 3.0),
    ('NIR', ['NIR'], 0.8, 8.0),
    ('IRL', ['IRL'], 0.8, 8.0),
    ('IMN', ['IMN'], 0.55, 8.0),
    ('JEY', ['JEY'], 1.2, 20.0),
    ('GGY', ['GGY'], 1.2, 20.0),
    ('FRA', ['FXX'], 1.2, 40.0),
    ('BEL', ['BFR', 'BWR', 'BCR'], 1.2, 40.0),
    ('NLD', ['NLX'], 1.2, 40.0),
    ('LUX', ['LUX'], 1.2, 40.0),
    ('DEU', ['DEU'], 1.2, 40.0),
    ('DNK', ['DNK'], 1.2, 40.0),
    ('NOR', ['NOW'], 2.2, 60.0),
    ('SWE', ['SWE'], 2.5, 100.0),
    ('FRO', ['FRO'], 1.5, 20.0),
    ('CHE', ['CHE'], 2.5, 100.0),
    ('LIE', ['LIE'], 2.5, 100.0),
    ('ITA', ['ITA'], 2.5, 100.0),
    ('AUT', ['AUT'], 2.5, 100.0),
    ('CZE', ['CZE'], 2.5, 100.0),
    ('POL', ['POL'], 2.5, 100.0),
    ('SVN', ['SVN'], 2.5, 100.0),
    ('HRV', ['HRV'], 2.5, 100.0),
    ('HUN', ['HUN'], 2.5, 100.0),
    ('SVK', ['SVK'], 2.5, 100.0),
    ('LTU', ['LTU'], 2.5, 100.0),
    ('LVA', ['LVA'], 2.5, 100.0),
    ('EST', ['EST'], 2.5, 100.0),
    ('FIN', ['FIN'], 2.5, 100.0),
    ('ALA', ['ALD'], 2.5, 100.0),
    ('RUS', ['RUS'], 2.5, 100.0),
]

LABELS = [
    ('Scotland', -3.75, 57.0, 'gb'), ('England', -1.45, 52.35, 'gb'), ('Wales', -3.7, 52.1, 'gb'),
    ('Ireland', -8.1, 53.05, 'near'), ('Northern Ireland', -6.85, 54.55, 'near'),
    ('France', 2.6, 49.45, 'near'), ('Belgium', 4.6, 50.6, 'near'), ('Netherlands', 5.7, 52.5, 'near'),
    ('Germany', 9.2, 52.2, 'near'), ('Denmark', 9.1, 56.25, 'near'), ('Norway', 8.6, 61.0, 'near'),
    ('Sweden', 13.6, 58.2, 'near'), ('Switzerland', 8.1, 46.85, 'near'), ('Poland', 16.4, 52.4, 'near'),
    ('North Sea', 2.6, 56.4, 'sea'), ('Irish Sea', -5.55, 54.0, 'sea'), ('English Channel', -2.4, 50.15, 'sea'),
    ('Atlantic Ocean', -10.5, 57.6, 'sea'), ('Norwegian Sea', 0.5, 62.4, 'sea'), ('Bay of Biscay', -4.6, 46.2, 'sea'),
]


def key_of(lon, lat):
    return (round(lon, 7), round(lat, 7))


def load(path):
    with open(path, encoding='utf-8') as handle:
        data = json.load(handle)
    found = {}
    for feature in data['features']:
        props = feature['properties']
        for code in (props.get('GU_A3'), props.get('SU_A3'), props.get('BRK_A3')):
            if code:
                found.setdefault(code, feature)
    return found


def polygons_of(geometry):
    if geometry['type'] == 'Polygon':
        return [geometry['coordinates']]
    if geometry['type'] == 'MultiPolygon':
        return geometry['coordinates']
    return []


def simplify_ring(ring, shared, tolerance, chain_tolerance):
    """ring: list of (x, y, key). Pins shared-run ends, simplifies each chain.

    A chain along a border shared with a neighbour uses the smaller of the two
    tolerances, so both outlines simplify it identically and still meet."""
    n = len(ring)
    if n < 4:
        return [(p[0], p[1]) for p in ring]
    counts = [shared.get(p[2], 0) if p[2] is not None else 0 for p in ring]
    fixed = []
    for i in range(n):
        c, before, after = counts[i], counts[i - 1], counts[(i + 1) % n]
        if c >= 2 or (c == 1 and (before == 0 or after == 0)):
            fixed.append(i)
    if not fixed:
        far = max(range(n), key=lambda i: math.hypot(ring[i][0] - ring[0][0], ring[i][1] - ring[0][1]))
        fixed = sorted({0, far})
    out = []
    for index, start in enumerate(fixed):
        end = fixed[(index + 1) % len(fixed)]
        chain = [ring[start]]
        i = start
        while True:
            i = (i + 1) % n
            chain.append(ring[i])
            if i == end:
                break
        simplified = douglas_peucker([(p[0], p[1]) for p in chain], chain_tolerance(chain, tolerance))
        out.extend(simplified[:-1])
    return out


def encode(rings):
    """Rings in km -> SVG path data in half-kilometre integer units."""
    parts = []
    for ring in rings:
        points = []
        for x, y in ring:
            q = (int(round(x * 2)), int(round(-y * 2)))
            if not points or q != points[-1]:
                points.append(q)
        if len(points) > 1 and points[0] == points[-1]:
            points.pop()
        if len(points) < 3:
            continue
        cmd = ['M%d %d' % points[0]]
        px, py = points[0]
        rel = []
        for x, y in points[1:]:
            rel.append((x - px, y - py))
            px, py = x, y
        text = 'l'
        for dx, dy in rel:
            text += ('%d' % dx) + ('%d' % dy if dy < 0 else ' %d' % dy) + ' '
        cmd.append(text.rstrip().replace(' -', '-'))
        parts.append(''.join(cmd) + 'z')
    return ''.join(parts)


def main():
    source = load(sys.argv[1])
    # Collect projected rings with source keys, per feature
    shapes = {}
    tolerances = {fid: tolerance for fid, _, tolerance, _ in FEATURES}
    for fid, codes, tolerance, min_area in FEATURES:
        polys = []
        for code in codes:
            feature = source.get(code)
            if not feature:
                sys.exit('missing feature ' + code)
            for poly in polygons_of(feature['geometry']):
                rings = []
                for ring in poly:
                    pts = [(*project(lon, lat), key_of(lon, lat)) for lon, lat in ring]
                    if pts and pts[0][2] == pts[-1][2]:
                        pts = pts[:-1]
                    rings.append(pts)
                polys.append(rings)
        shapes[fid] = (polys, tolerance, min_area)

    # Which features use each source vertex (shared borders)
    owners = {}
    for fid, (polys, _, _) in shapes.items():
        for rings in polys:
            for ring in rings:
                for p in ring:
                    owners.setdefault(p[2], set()).add(fid)

    def chain_tolerance(chain, own):
        inner = [p[2] for p in chain[1:-1] if p[2] is not None]
        if not inner or len(owners.get(inner[0], ())) < 2:
            return own
        return min(tolerances[f] for f in owners[inner[0]])

    result = {'box': [BOX[0], -BOX[3], BOX[2] - BOX[0], BOX[3] - BOX[1]], 'units': [], 'labels': []}
    stats = []
    for fid, (polys, tolerance, min_area) in shapes.items():
        shared = {}
        for rings in polys:
            for ring in rings:
                for p in ring:
                    others = len(owners[p[2]]) - 1
                    if others:
                        shared[p[2]] = others
        out_rings = []
        for rings in polys:
            outer = rings[0]
            if abs(ring_area([(p[0], p[1]) for p in outer])) < min_area:
                continue
            for ring in rings:
                clipped = clip_ring(ring, BOX)
                if len(clipped) < 3:
                    continue
                simple = simplify_ring(clipped, shared, tolerance, chain_tolerance)
                if len(simple) >= 3 and abs(ring_area(simple)) >= min(min_area, 1.0):
                    out_rings.append(simple)
        d = encode(out_rings)
        if d:
            result['units'].append({'id': fid, 'kind': 'gb' if fid in ('SCT', 'ENG', 'WLS') else 'near', 'd': d})
            stats.append((fid, len(out_rings), sum(len(r) for r in out_rings), len(d)))
    for text, lon, lat, kind in LABELS:
        x, y = project(lon, lat)
        result['labels'].append({'t': text, 'x': round(x, 1), 'y': round(-y, 1), 'k': kind})
    compact = lambda value: json.dumps(value, separators=(',', ':'))
    body = ('{"box":' + compact(result['box']) + ',\n"units":[\n'
            + ',\n'.join(compact(u) for u in result['units']) + '\n],\n"labels":[\n'
            + ',\n'.join(compact(l) for l in result['labels']) + '\n]}')
    json.loads(body)
    with open(OUT, 'w', encoding='utf-8', newline='\n') as handle:
        handle.write(body + '\n')
    for row in stats:
        print('%-4s rings %4d points %6d bytes %6d' % row)
    print('wrote', OUT, len(body), 'bytes')


if __name__ == '__main__':
    main()
