#!/usr/bin/env python3
"""Sanity-check app/stations.csv against the map's own coastline.

Usage: python3 tools/check_locations.py

Flags onshore stations that sit in the sea, offshore wind farms that sit on
land, stations sharing identical coordinates, and anything outside the map.
The coastline (app/geo.json) is simplified, so points within about 3 km of
it can land on the wrong side; the distance to the coast is shown.
"""
import csv
import json
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'app'))
from gridlive.units import map_point   # noqa: E402


def rings(d):
    """Polygons of an SVG path that uses only M, l and z, in map kilometres."""
    out = []
    for part in re.findall(r'M[^M]*', d):
        nums = [float(n) for n in re.findall(r'-?\d+(?:\.\d+)?', part)]
        x, y = nums[0] / 2, nums[1] / 2          # the outlines are stored at twice the map's scale
        ring = [(x, y)]
        for i in range(2, len(nums) - 1, 2):
            x, y = x + nums[i] / 2, y + nums[i + 1] / 2
            ring.append((x, y))
        if len(ring) > 2:
            out.append(ring)
    return out


def inside(point, ring):
    x, y = point
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def to_ring(point, ring):
    x, y = point
    best = float('inf')
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
        best = min(best, math.hypot(x - x1 - t * dx, y - y1 - t * dy))
    return best


def main():
    geo = json.load(open(os.path.join(ROOT, 'app', 'geo.json'), encoding='utf-8'))
    land = [ring for unit in geo['units'] if unit['kind'] == 'gb' for ring in rings(unit['d'])]
    stations = list(csv.DictReader(open(os.path.join(ROOT, 'app', 'stations.csv'), encoding='utf-8')))
    problems, seen = [], {}
    for st in stations:
        point = map_point(float(st['lat']), float(st['lon']))
        on_land = sum(inside(point, ring) for ring in land) % 2 == 1
        coast = min(to_ring(point, ring) for ring in land)
        offshore = st['offshore'] == '1'
        if offshore and on_land:
            problems.append('%s: offshore wind farm on land, %.1f km from the coast' % (st['name'], coast))
        elif not offshore and not on_land:
            problems.append('%s (%s): in the sea, %.1f km from the coast%s' % (
                st['name'], st['fuel'], coast, '' if coast > 3 else ' (within the outline\'s tolerance)'))
        key = (st['lat'], st['lon'])
        if key in seen:
            problems.append('%s and %s share the coordinates %s, %s' % (seen[key], st['name'], st['lat'], st['lon']))
        seen[key] = st['name']
        if not (49.5 < float(st['lat']) < 61.5 and -8.5 < float(st['lon']) < 3.5):
            problems.append('%s: outside Great Britain and its seas' % st['name'])
    print('%d stations checked against %d coastline polygons' % (len(stations), len(land)))
    for line in problems:
        print(' -', line)
    serious = [p for p in problems if 'tolerance' not in p]
    print('%d to look at, %d within tolerance' % (len(serious), len(problems) - len(serious)))
    return 1 if serious else 0


if __name__ == '__main__':
    sys.exit(main())
