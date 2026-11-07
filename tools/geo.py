"""Shared geometry helpers for the build scripts (no third-party packages).

The projection must match project() in app/server.py exactly: a spherical
transverse Mercator centred on 2 degrees west (the same central meridian as
the Ordnance Survey grid), in kilometres, north up.
"""
import math

R = 6371.0
LON0 = math.radians(-2.0)
LAT0 = math.radians(49.0)


def project(lon, lat):
    """(lon, lat) in degrees -> (x, y) in km; y grows northwards."""
    lam = math.radians(lon) - LON0
    phi = math.radians(lat)
    b = math.cos(phi) * math.sin(lam)
    x = 0.5 * R * math.log((1 + b) / (1 - b))
    y = R * (math.atan2(math.tan(phi), math.cos(lam)) - LAT0)
    return x, y


def ring_area(ring):
    total = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i - 1]
        x2, y2 = ring[i]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def point_in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def point_in_polygons(x, y, polygons):
    """polygons: list of [outer, hole, hole...] rings."""
    for rings in polygons:
        if rings and point_in_ring(x, y, rings[0]) and not any(point_in_ring(x, y, h) for h in rings[1:]):
            return True
    return False


def clip_ring(ring, box):
    """Sutherland-Hodgman clip of a closed ring (list of (x, y, key)) to box."""
    x0, y0, x1, y1 = box
    edges = [
        (lambda p: p[0] >= x0, lambda p, q: _cross_x(p, q, x0)),
        (lambda p: p[0] <= x1, lambda p, q: _cross_x(p, q, x1)),
        (lambda p: p[1] >= y0, lambda p, q: _cross_y(p, q, y0)),
        (lambda p: p[1] <= y1, lambda p, q: _cross_y(p, q, y1)),
    ]
    output = ring
    for inside, cross in edges:
        if not output:
            break
        source, output = output, []
        prev = source[-1]
        for point in source:
            if inside(point):
                if not inside(prev):
                    output.append(cross(prev, point))
                output.append(point)
            elif inside(prev):
                output.append(cross(prev, point))
            prev = point
    return output


def _cross_x(p, q, x):
    t = (x - p[0]) / (q[0] - p[0])
    return (x, p[1] + t * (q[1] - p[1]), None)


def _cross_y(p, q, y):
    t = (y - p[1]) / (q[1] - p[1])
    return (p[0] + t * (q[0] - p[0]), y, None)


def _perp(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(p[0] - ax, p[1] - ay)
    return abs(dy * p[0] - dx * p[1] + bx * ay - by * ax) / math.hypot(dx, dy)


def douglas_peucker(points, tolerance):
    """Simplify an open chain, keeping both ends. points: list of (x, y)."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        best, index = 0.0, None
        for i in range(first + 1, last):
            d = _perp(points[i], points[first], points[last])
            if d > best:
                best, index = d, i
        if index is not None and best > tolerance:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, k in zip(points, keep) if k]


# ---- British National Grid (OSGB36, EPSG:27700) to WGS84 -------------------
# Ordnance Survey's transverse Mercator on the Airy 1830 ellipsoid, then the
# seven-parameter Helmert transformation to WGS84 (accurate to a few metres,
# far finer than these maps need).

AIRY_A, AIRY_B = 6377563.396, 6356256.909
WGS_A, WGS_B = 6378137.0, 6356752.314245
BNG_F0 = 0.9996012717
BNG_LAT0, BNG_LON0 = math.radians(49.0), math.radians(-2.0)
BNG_E0, BNG_N0 = 400000.0, -100000.0
# OSGB36 -> WGS84: translation (m), scale (ppm), rotations (arc seconds)
HELMERT = (446.448, -125.157, 542.060, -20.4894, 0.1502, 0.2470, 0.8421)


def _meridional_arc(lat):
    n = (AIRY_A - AIRY_B) / (AIRY_A + AIRY_B)
    d, s = lat - BNG_LAT0, lat + BNG_LAT0
    return AIRY_B * BNG_F0 * (
        (1 + n + 1.25 * n ** 2 + 1.25 * n ** 3) * d
        - (3 * n + 3 * n ** 2 + 21.0 / 8 * n ** 3) * math.sin(d) * math.cos(s)
        + (15.0 / 8 * n ** 2 + 15.0 / 8 * n ** 3) * math.sin(2 * d) * math.cos(2 * s)
        - 35.0 / 24 * n ** 3 * math.sin(3 * d) * math.cos(3 * s))


def bng_to_osgb36(easting, northing):
    """(E, N) metres -> (lat, lon) radians on OSGB36."""
    a, e2 = AIRY_A, 1 - (AIRY_B / AIRY_A) ** 2
    lat, m = BNG_LAT0, 0.0
    for _ in range(20):
        lat += (northing - BNG_N0 - m) / (a * BNG_F0)
        m = _meridional_arc(lat)
        if abs(northing - BNG_N0 - m) < 1e-5:
            break
    sin_lat, cos_lat, tan_lat = math.sin(lat), math.cos(lat), math.tan(lat)
    nu = a * BNG_F0 / math.sqrt(1 - e2 * sin_lat ** 2)
    rho = a * BNG_F0 * (1 - e2) / (1 - e2 * sin_lat ** 2) ** 1.5
    eta2 = nu / rho - 1
    t2 = tan_lat ** 2
    vii = tan_lat / (2 * rho * nu)
    viii = tan_lat / (24 * rho * nu ** 3) * (5 + 3 * t2 + eta2 - 9 * t2 * eta2)
    ix = tan_lat / (720 * rho * nu ** 5) * (61 + 90 * t2 + 45 * t2 ** 2)
    x = 1 / (cos_lat * nu)
    xi = 1 / (cos_lat * 6 * nu ** 3) * (nu / rho + 2 * t2)
    xii = 1 / (cos_lat * 120 * nu ** 5) * (5 + 28 * t2 + 24 * t2 ** 2)
    xiia = 1 / (cos_lat * 5040 * nu ** 7) * (61 + 662 * t2 + 1320 * t2 ** 2 + 720 * t2 ** 3)
    de = easting - BNG_E0
    return (lat - vii * de ** 2 + viii * de ** 4 - ix * de ** 6,
            BNG_LON0 + x * de - xi * de ** 3 + xii * de ** 5 - xiia * de ** 7)


def osgb36_to_wgs84(lat, lon):
    """(lat, lon) radians on OSGB36 -> (lat, lon) degrees on WGS84."""
    e2 = 1 - (AIRY_B / AIRY_A) ** 2
    nu = AIRY_A / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    x = nu * math.cos(lat) * math.cos(lon)
    y = nu * math.cos(lat) * math.sin(lon)
    z = (1 - e2) * nu * math.sin(lat)
    tx, ty, tz, s, rx, ry, rz = HELMERT
    s *= 1e-6
    rx, ry, rz = (math.radians(r / 3600.0) for r in (rx, ry, rz))
    x2 = tx + (1 + s) * x - rz * y + ry * z
    y2 = ty + rz * x + (1 + s) * y - rx * z
    z2 = tz - ry * x + rx * y + (1 + s) * z
    e2w = 1 - (WGS_B / WGS_A) ** 2
    p = math.hypot(x2, y2)
    phi = math.atan2(z2, p * (1 - e2w))
    for _ in range(10):
        nu = WGS_A / math.sqrt(1 - e2w * math.sin(phi) ** 2)
        phi = math.atan2(z2 + e2w * nu * math.sin(phi), p)
    return math.degrees(phi), math.degrees(math.atan2(y2, x2))


def bng_to_wgs84(easting, northing):
    """British National Grid metres -> (lon, lat) degrees on WGS84."""
    lat, lon = osgb36_to_wgs84(*bng_to_osgb36(easting, northing))
    return lon, lat
