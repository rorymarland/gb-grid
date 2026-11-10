"""OS National Grid eastings and northings to GPS (WGS84) latitude and longitude.

The reverse of app/static/js/osgrid.js: inverse Transverse Mercator on the
Airy 1830 ellipsoid, then the OS's Helmert transformation from OSGB36 to
WGS84. Good to about 5 m.
"""
import math

A, B, F0 = 6377563.396, 6356256.909, 0.9996012717
PHI0, LAM0, N0, E0 = math.radians(49), math.radians(-2), -100000.0, 400000.0


def _meridional(phi):
    n = (A - B) / (A + B)
    n2, n3 = n * n, n * n * n
    dp, sp = phi - PHI0, phi + PHI0
    return B * F0 * ((1 + n + 1.25 * n2 + 1.25 * n3) * dp - (3 * n + 3 * n2 + 21 / 8 * n3) * math.sin(dp) * math.cos(sp)
                     + (15 / 8 * n2 + 15 / 8 * n3) * math.sin(2 * dp) * math.cos(2 * sp)
                     - 35 / 24 * n3 * math.sin(3 * dp) * math.cos(3 * sp))


def grid_to_osgb36(easting, northing):
    e2 = 1 - (B * B) / (A * A)
    phi = PHI0 + (northing - N0) / (A * F0)
    for _ in range(50):
        m = _meridional(phi)
        if abs(northing - N0 - m) < 1e-5:
            break
        phi += (northing - N0 - m) / (A * F0)
    sin, cos, tan = math.sin(phi), math.cos(phi), math.tan(phi)
    nu = A * F0 / math.sqrt(1 - e2 * sin * sin)
    rho = A * F0 * (1 - e2) / (1 - e2 * sin * sin) ** 1.5
    eta2 = nu / rho - 1
    tan2, tan4, tan6 = tan ** 2, tan ** 4, tan ** 6
    vii = tan / (2 * rho * nu)
    viii = tan / (24 * rho * nu ** 3) * (5 + 3 * tan2 + eta2 - 9 * tan2 * eta2)
    ix = tan / (720 * rho * nu ** 5) * (61 + 90 * tan2 + 45 * tan4)
    x = 1 / (cos * nu)
    xi = 1 / (cos * 6 * nu ** 3) * (nu / rho + 2 * tan2)
    xii = 1 / (cos * 120 * nu ** 5) * (5 + 28 * tan2 + 24 * tan4)
    xiia = 1 / (cos * 5040 * nu ** 7) * (61 + 662 * tan2 + 1320 * tan4 + 720 * tan6)
    de = easting - E0
    lat = phi - vii * de ** 2 + viii * de ** 4 - ix * de ** 6
    lon = LAM0 + x * de - xi * de ** 3 + xii * de ** 5 - xiia * de ** 7
    return math.degrees(lat), math.degrees(lon)


def _cartesian(lat, lon, a, b):
    e2 = 1 - (b * b) / (a * a)
    phi, lam = math.radians(lat), math.radians(lon)
    nu = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    return nu * math.cos(phi) * math.cos(lam), nu * math.cos(phi) * math.sin(lam), (1 - e2) * nu * math.sin(phi)


def _geodetic(x, y, z, a, b):
    e2 = 1 - (b * b) / (a * a)
    r = math.hypot(x, y)
    phi = math.atan2(z, r * (1 - e2))
    for _ in range(12):
        nu = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
        phi = math.atan2(z + e2 * nu * math.sin(phi), r)
    return math.degrees(phi), math.degrees(math.atan2(y, x))


def osgb36_to_wgs84(lat, lon):
    x, y, z = _cartesian(lat, lon, A, B)
    tx, ty, tz, s = 446.448, -125.157, 542.060, -20.4894e-6
    rx, ry, rz = (math.radians(v / 3600) for v in (0.1502, 0.2470, 0.8421))
    return _geodetic(tx + (1 + s) * x - rz * y + ry * z, ty + rz * x + (1 + s) * y - rx * z,
                     tz - ry * x + rx * y + (1 + s) * z, 6378137.0, 6356752.314245)


def grid_to_wgs84(easting, northing):
    return osgb36_to_wgs84(*grid_to_osgb36(easting, northing))


if __name__ == '__main__':
    # The OS worked example, and round trips against the browser's forward conversion
    lat, lon = grid_to_osgb36(651409.903, 313177.270)
    assert abs(lat - (52 + 39 / 60 + 27.2531 / 3600)) < 1e-7 and abs(lon - (1 + 43 / 60 + 4.5177 / 3600)) < 1e-7, (lat, lon)
    lat, lon = grid_to_wgs84(374500, 675100)      # NT 745 751, Torness
    assert abs(lat - 55.968) < 0.002 and abs(lon + 2.409) < 0.002, (lat, lon)
    lat, lon = grid_to_wgs84(216600, 771200)      # NN 166 712, Ben Nevis
    assert abs(lat - 56.7969) < 0.002 and abs(lon + 5.0036) < 0.002, (lat, lon)   # the corner of a 100 m square
    print('osgb: ok')
