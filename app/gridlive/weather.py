"""The weather behind the grid, from Open-Meteo (no key; CC BY 4.0; free for non-commercial use,
10,000 calls a day, one call per location per request here).

Three British averages, hour by hour, from two days back to a week ahead (and 92 days back on
the first run):

  wind   wind speed 100 m up, about the height of a wind turbine's hub (m/s), at a dozen points
         among the wind farms, each weighted by the capacity of the farms nearest it
  temp   air temperature (°C) in each of Britain's nations and English regions, weighted by
         population (ONS mid-2022 estimates)
  sun    sunshine on the ground (global horizontal irradiance, W/m², the mean over the hour before
         the time given) in each of NESO's 14 regions, weighted by its share of solar output

Then the page sets them against what the grid did: wind farms' output against wind speed (a
power curve), demand against temperature, and solar output against sunshine, over the last 90
days."""

import math
import time
import urllib.parse

from . import fetch
from .config import OPEN_METEO, log
from .fetch import FetchError, as_float
from .reference import PES_REGIONS, REGION_NAMES
from .timeutil import uk_clock

FIRST_DAYS = 92             # history on the first run (Open-Meteo's most)
PAST_DAYS = 2
AHEAD_DAYS = 8              # today and a week ahead
KEEP = 100 * 86400

VARIABLES = {'wind': 'wind_speed_100m', 'temp': 'temperature_2m', 'sun': 'shortwave_radiation'}

# Wind: points among Britain's wind farms. Each farm in the station list counts towards the
# nearest point, by capacity.
WIND_POINTS = [
    ('Moray Firth', 58.2, -3.0), ('the Highlands', 57.5, -4.6), ('off Aberdeen and Angus', 56.8, -1.9),
    ('the Central Belt and Southern Uplands', 55.6, -3.9), ('Dogger Bank', 54.8, 1.9), ('Hornsea', 53.9, 1.8),
    ('off Lincolnshire', 53.3, 0.8), ('East Anglia', 52.4, 2.2), ('the Thames estuary', 51.6, 1.4),
    ('the Irish Sea', 54.0, -3.6), ('Wales', 52.3, -3.6), ('off Sussex', 50.7, -0.3),
]
# Temperature: Britain's nations and English regions, by population (ONS mid-2022, millions)
TEMP_POINTS = [
    ('London', 51.51, -0.13, 8.87), ('South East', 51.25, -0.6, 9.38), ('East of England', 52.2, 0.4, 6.40),
    ('South West', 51.0, -3.0, 5.76), ('West Midlands', 52.48, -1.9, 6.02), ('East Midlands', 52.95, -1.15, 4.93),
    ('North West', 53.48, -2.24, 7.52), ('Yorkshire', 53.8, -1.55, 5.54), ('North East', 54.97, -1.6, 2.68),
    ('Wales', 51.6, -3.4, 3.13), ('Scotland', 55.9, -3.9, 5.45),
]
# Sunshine: a point in each of NESO's regions, by the PES ID Sheffield Solar uses for it
SUN_POINTS = {
    10: (52.4, 0.8), 11: (52.9, -1.2), 12: (51.51, -0.12), 13: (53.2, -3.2), 14: (52.45, -2.2), 15: (54.8, -1.6),
    16: (53.9, -2.6), 17: (57.3, -4.0), 18: (55.7, -3.6), 19: (51.2, 0.6), 20: (51.0, -1.5), 21: (51.75, -3.6),
    22: (50.7, -3.8), 23: (53.8, -1.2),
}


def nearest(points, lat, lon):
    """Index of the nearest (name, lat, lon, ...) point, on a flat projection (fine at these distances)."""
    scale = math.cos(math.radians(lat))
    return min(range(len(points)), key=lambda i: (points[i][1] - lat) ** 2 + ((points[i][2] - lon) * scale) ** 2)


def wind_weights(stations):
    """[(name, lat, lon, MW)] for the wind points, from the wind farms in the station list."""
    totals = [0.0] * len(WIND_POINTS)
    for st in stations:
        if st.get('fuel') == 'WIND' and st.get('capacity') and st.get('lat') is not None:
            totals[nearest(WIND_POINTS, st['lat'], st['lon'])] += st['capacity']
    if not any(totals):
        totals = [1.0] * len(WIND_POINTS)
    return [(p[0], p[1], p[2], w) for p, w in zip(WIND_POINTS, totals) if w > 0]


def sun_weights(store, now):
    """[(name, lat, lon, weight)] for each region, by its solar output (MWh) over the last week."""
    totals = dict(store.query("SELECT CAST(SUBSTR(k, 4) AS INTEGER), SUM(v) FROM hh WHERE k LIKE 'rpv%' AND t >= ? GROUP BY k",
                              (now - 7 * 86400,)))
    out = [(REGION_NAMES.get(PES_REGIONS.get(pes), str(pes)), lat, lon, totals.get(pes) or 0.0) for pes, (lat, lon) in SUN_POINTS.items()]
    if not any(w for *_, w in out):
        out = [(n, lat, lon, 1.0) for n, lat, lon, _ in out]
    return [p for p in out if p[3] > 0]


def weighted(series, weights):
    """{t: weighted mean} across locations. series: one {t: value} per location. Hours missing at
    some locations use the weights of those that have them."""
    sums, wsum = {}, {}
    for values, w in zip(series, weights):
        for t, v in values.items():
            if v is None:
                continue
            sums[t] = sums.get(t, 0.0) + v * w
            wsum[t] = wsum.get(t, 0.0) + w
    total = sum(weights)
    # Only hours where at least half the weight reported
    return {t: sums[t] / wsum[t] for t in sums if wsum[t] >= total / 2}


class Weather:
    def __init__(self, store, fleet=None):
        self.store = store
        self.fleet = fleet

    def points(self, kind, now):
        if kind == 'wind':
            return wind_weights(list(self.fleet.stations.values()) if self.fleet and self.fleet.stations else [])
        if kind == 'temp':
            return list(TEMP_POINTS)
        return sun_weights(self.store, now)

    def fetch(self, kind, points, past_days):
        """One request for every point; stores the weighted British value for each hour."""
        query = {'latitude': ','.join('%.3f' % p[1] for p in points), 'longitude': ','.join('%.3f' % p[2] for p in points),
                 'hourly': VARIABLES[kind], 'timeformat': 'unixtime', 'timezone': 'GMT',
                 'past_days': past_days, 'forecast_days': AHEAD_DAYS}
        if kind == 'wind':
            query['wind_speed_unit'] = 'ms'
        payload = fetch.get_json(OPEN_METEO + '/forecast?' + urllib.parse.urlencode(query), timeout=120)
        places = payload if isinstance(payload, list) else [payload]
        if len(places) != len(points) or not all(isinstance(p, dict) and isinstance(p.get('hourly'), dict) for p in places):
            raise FetchError('Unexpected response shape from Open-Meteo')
        series = []
        for place in places:
            hourly = place['hourly']
            times, values = hourly.get('time') or [], hourly.get(VARIABLES[kind]) or []
            series.append({int(t): as_float(v) for t, v in zip(times, values)})
        merged = weighted(series, [p[3] for p in points])
        digits = 1 if kind == 'sun' else 2
        rows = [(kind, t, round(v, digits)) for t, v in merged.items()]
        if rows:
            self.store.write('INSERT OR REPLACE INTO wx (k, t, v) VALUES (?, ?, ?)', rows, history=False)
        return len(rows)

    def poll(self):
        now = time.time()
        errors = []
        for kind in VARIABLES:
            first = self.store.scalar('SELECT MIN(t) FROM wx WHERE k = ?', (kind,))
            past = FIRST_DAYS if first is None or first > now - (FIRST_DAYS - 3) * 86400 else PAST_DAYS
            try:
                self.fetch(kind, self.points(kind, now), past)
            except FetchError as error:
                errors.append('%s: %s' % (kind, error))
        self.store.write('DELETE FROM wx WHERE t < ?', [(now - KEEP,)], history=False)
        if len(errors) == len(VARIABLES):
            raise FetchError('; '.join(errors))
        if errors:
            log.warning('[weather] %s', '; '.join(errors))


# ---- the page ---------------------------------------------------------------------

def median(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return None
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def binned(points, width, least=5):
    """[[middle of bin, median y, count]] for (x, y) points in bins `width` wide, where a bin has
    at least `least` points."""
    bins = {}
    for x, y in points:
        bins.setdefault(int(x // width), []).append(y)
    return [[round((b + 0.5) * width, 3), round(median(ys), 3), len(ys)] for b, ys in sorted(bins.items()) if len(ys) >= least]


def fit(points):
    """Least squares (slope, intercept) of y on x, or None with fewer than ten points."""
    n = len(points)
    if n < 10:
        return None
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    if sxx <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in points) / sxx
    return slope, my - slope * mx


def thin(points, most):
    """Every nth point, so a scatter stays light."""
    if len(points) <= most:
        return points
    step = len(points) / most
    return [points[int(i * step)] for i in range(most)]


def hourly(store, sql, args):
    """{hour start: mean} of half-hourly rows (t, v) from a query."""
    sums = {}
    for t, v in store.query(sql, args):
        if v is not None:
            h = t - t % 3600
            sums.setdefault(h, []).append(v)
    return {h: sum(vs) / len(vs) for h, vs in sums.items() if len(vs) == 2}


def relations(store, now):
    """The last 90 days: wind farms against wind, demand against temperature, solar against sun."""
    start = now - 90 * 86400
    wx = {k: dict(store.query('SELECT t, v FROM wx WHERE k = ? AND t >= ? AND t <= ?', (k, start, now))) for k in VARIABLES}
    # Wind: what transmission wind farms made plus what they were turned down, as a share of their
    # capacity, over each hour, against the mean of the wind speeds at its start and its end
    avail = hourly(store, 'SELECT f.t, (l.wind + COALESCE(c.mwh, 0) * 2) * 1.0 / f.cap FROM fwind f JOIN lh l ON l.t = f.t '
                   'LEFT JOIN wcurt_hh c ON c.t = f.t WHERE f.t >= ? AND f.cap > 0 AND l.wind IS NOT NULL', (start,))
    wind_pts = []
    for h, share in sorted(avail.items()):
        a, b = wx['wind'].get(h), wx['wind'].get(h + 3600)
        if a is not None and b is not None:
            wind_pts.append((round((a + b) / 2, 2), round(share * 100, 1)))
    # Solar: output over the hour against the sunshine Open-Meteo gives for that hour (stamped at its end)
    solar = hourly(store, 'SELECT t, solar FROM lh WHERE t >= ?', (start,))
    # (daylight hours only: the dark ones would pile up at nothing)
    sun_pts = [(wx['sun'][h + 3600], round(mw / 1000, 2)) for h, mw in sorted(solar.items())
               if wx['sun'].get(h + 3600) is not None and (wx['sun'][h + 3600] >= 1 or mw >= 50)]
    # Demand: each UK day's mean national demand against its mean temperature, with weekends apart
    days = {}
    for t, act in store.query('SELECT t, act FROM fdemand WHERE t >= ? AND t < ?', (start, now)):
        days.setdefault(uk_clock(t)[0], [[], []])[0].append(act)
    for t, v in wx['temp'].items():
        d = uk_clock(t)[0]
        if d in days:
            days[d][1].append(v)
    temp_pts = []
    for d, (demand, temps) in sorted(days.items()):
        if len(demand) >= 46 and len(temps) >= 22:
            temp_pts.append([d.isoformat(), round(sum(temps) / len(temps), 1), round(sum(demand) / len(demand) / 1000, 2), d.weekday() >= 5])
    # Below 15.5 °C, heating takes over: how much each degree colder adds on a working day
    heating = fit([(p[1], p[2]) for p in temp_pts if not p[3] and p[1] < 15.5])
    return {
        'wind': {'points': thin(wind_pts, 1500), 'curve': binned(wind_pts, 1.0), 'n': len(wind_pts)},
        'sun': {'points': thin(sun_pts, 1500), 'curve': binned(sun_pts, 50.0), 'n': len(sun_pts)},
        'temp': {'points': temp_pts, 'per_degree': round(-heating[0], 2) if heating and heating[0] < 0 else None,
                 'n_fit': sum(1 for p in temp_pts if not p[3] and p[1] < 15.5)},
    }


def view(store, now, live=None):
    """What /api/weather returns. live: the /api/live payload, for the grid now."""
    hour = int(now - now % 3600)
    start, end = hour - PAST_DAYS * 86400, hour + 7 * 86400
    rows = {}
    for k, t, v in store.query('SELECT k, t, v FROM wx WHERE t >= ? AND t <= ? ORDER BY t', (start, end)):
        rows.setdefault(t, {})[k] = v
    if not rows:
        return {'ready': False}
    week = [[t, r.get('wind'), r.get('temp'), r.get('sun')] for t, r in sorted(rows.items())]

    def at(kind, t):
        return rows.get(t, {}).get(kind)
    # Sunshine is the mean over the hour before its time, so the hour now ends at the next hour
    current = {'wind': at('wind', hour), 'temp': at('temp', hour), 'sun': at('sun', hour + 3600), 'hour': hour}
    grid = None
    if live and live.get('sources'):
        src = {s['key']: s for s in live['sources']}
        grid = {'wind_gw': src.get('wind', {}).get('gw'), 'wind_tx_gw': (src.get('wind', {}).get('parts') or {}).get('transmission'),
                'solar_gw': src.get('solar', {}).get('gw'), 'demand_gw': live.get('demand_gw'), 't': live.get('time')}
    return {'ready': True, 'now': int(now), 'hour': hour, 'week': week, 'current': current, 'grid': grid,
            'relations': cached_relations(store, now)}


_cache = {}


def cached_relations(store, now):
    """relations(), worked out again only when its inputs change or the hour does."""
    key = (int(now // 3600), store.scalar('SELECT MAX(t) FROM wx'), store.scalar('SELECT MAX(t) FROM lh'),
           store.scalar('SELECT MAX(t) FROM fdemand'), store.scalar('SELECT COUNT(*) FROM wx'))
    hit = _cache.get(id(store))
    if hit and hit[0] == key:
        return hit[1]
    value = relations(store, now)
    _cache[id(store)] = (key, value)
    return value
