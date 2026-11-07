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
