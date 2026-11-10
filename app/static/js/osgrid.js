/* GPS (WGS84) coordinates to Ordnance Survey National Grid references.
   No dependencies, so the tests can run it in Node. */

var RAD = Math.PI / 180;

function toCartesian(lat, lon, a, b) {
  var e2 = 1 - (b * b) / (a * a);
  var phi = lat * RAD, lam = lon * RAD;
  var nu = a / Math.sqrt(1 - e2 * Math.sin(phi) * Math.sin(phi));
  return [nu * Math.cos(phi) * Math.cos(lam), nu * Math.cos(phi) * Math.sin(lam), (1 - e2) * nu * Math.sin(phi)];
}

function toLatLon(p, a, b) {
  var e2 = 1 - (b * b) / (a * a);
  var x = p[0], y = p[1], z = p[2];
  var r = Math.sqrt(x * x + y * y);
  var phi = Math.atan2(z, r * (1 - e2)), previous = 2 * Math.PI;
  for (var i = 0; i < 12 && Math.abs(phi - previous) > 1e-12; i++) {
    var nu = a / Math.sqrt(1 - e2 * Math.sin(phi) * Math.sin(phi));
    previous = phi;
    phi = Math.atan2(z + e2 * nu * Math.sin(phi), r);
  }
  return [phi / RAD, Math.atan2(y, x) / RAD];
}

/* WGS84 (GPS) latitude and longitude to OSGB36, by the OS's seven-parameter
   Helmert transformation: good to about 5 m, plenty for a grid reference. */
export function wgs84ToOsgb36(lat, lon) {
  var p = toCartesian(lat, lon, 6378137, 6356752.314245);
  var tx = -446.448, ty = 125.157, tz = -542.060, sc = 20.4894e-6;
  var rx = -0.1502 / 3600 * RAD, ry = -0.2470 / 3600 * RAD, rz = -0.8421 / 3600 * RAD;
  var q = [
    tx + (1 + sc) * p[0] - rz * p[1] + ry * p[2],
    ty + rz * p[0] + (1 + sc) * p[1] - rx * p[2],
    tz - ry * p[0] + rx * p[1] + (1 + sc) * p[2]
  ];
  return toLatLon(q, 6377563.396, 6356256.909);
}

/* OSGB36 latitude and longitude to National Grid easting and northing
   (Transverse Mercator on the Airy 1830 ellipsoid, OS formulae). */
export function osgb36ToGrid(lat, lon) {
  var a = 6377563.396, b = 6356256.909, F0 = 0.9996012717;
  var phi0 = 49 * RAD, lam0 = -2 * RAD, N0 = -100000, E0 = 400000;
  var e2 = 1 - (b * b) / (a * a), n = (a - b) / (a + b), n2 = n * n, n3 = n2 * n;
  var phi = lat * RAD, lam = lon * RAD;
  var sin = Math.sin(phi), cos = Math.cos(phi), tan = Math.tan(phi);
  var nu = a * F0 / Math.sqrt(1 - e2 * sin * sin);
  var rho = a * F0 * (1 - e2) / Math.pow(1 - e2 * sin * sin, 1.5);
  var eta2 = nu / rho - 1;
  var dp = phi - phi0, sp = phi + phi0;
  var M = b * F0 * ((1 + n + 1.25 * n2 + 1.25 * n3) * dp - (3 * n + 3 * n2 + 21 / 8 * n3) * Math.sin(dp) * Math.cos(sp) +
    (15 / 8 * n2 + 15 / 8 * n3) * Math.sin(2 * dp) * Math.cos(2 * sp) - 35 / 24 * n3 * Math.sin(3 * dp) * Math.cos(3 * sp));
  var cos3 = cos * cos * cos, cos5 = cos3 * cos * cos, tan2 = tan * tan, tan4 = tan2 * tan2;
  var I = M + N0;
  var II = nu / 2 * sin * cos;
  var III = nu / 24 * sin * cos3 * (5 - tan2 + 9 * eta2);
  var IIIA = nu / 720 * sin * cos5 * (61 - 58 * tan2 + tan4);
  var IV = nu * cos;
  var V = nu / 6 * cos3 * (nu / rho - tan2);
  var VI = nu / 120 * cos5 * (5 - 18 * tan2 + tan4 + 14 * eta2 - 58 * tan2 * eta2);
  var dl = lam - lam0, dl2 = dl * dl;
  return [E0 + IV * dl + V * dl * dl2 + VI * dl * dl2 * dl2, I + II * dl2 + III * dl2 * dl2 + IIIA * dl2 * dl2 * dl2];
}

/* "NT 745 751": the 100 km square and a 100 m (six-figure) reference, or null
   outside the grid (far offshore). */
export function gridRef(lat, lon, digits) {
  var o = wgs84ToOsgb36(lat, lon);
  var en = osgb36ToGrid(o[0], o[1]);
  var E = en[0], N = en[1];
  if (!(E >= 0 && E < 700000 && N >= 0 && N < 1300000)) return null;
  var e100 = Math.floor(E / 100000), n100 = Math.floor(N / 100000);
  var l1 = (19 - n100) - (19 - n100) % 5 + Math.floor((e100 + 10) / 5);
  var l2 = (19 - n100) * 5 % 25 + e100 % 5;
  if (l1 > 7) l1++;
  if (l2 > 7) l2++;
  var per = digits || 3, unit = Math.pow(10, 5 - per);
  var pad = function (v) { var t = String(Math.floor((v % 100000) / unit)); while (t.length < per) t = '0' + t; return t; };
  return String.fromCharCode(65 + l1) + String.fromCharCode(65 + l2) + ' ' + pad(E) + ' ' + pad(N);
}
