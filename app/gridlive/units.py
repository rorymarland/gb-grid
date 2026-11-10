"""Power stations: modelling each station's output from Elexon unit data."""

import csv
import json
import math
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import fetch
from .config import ELEXON, log
from .timeutil import iso_minute, parse_time, uk_midnight_utc
from .fetch import FetchError, as_float
from .store import UPSERT_ST


# --------------------------------------------------------------------------
# Power stations: per-unit output for the map and stations views
#
# Elexon publishes no live metered output per power station, so each BM
# unit's output is modelled from what it told the system operator it would
# do (its Physical Notification) overridden by any instruction the operator
# has since given it (Bid-Offer Acceptances). Units are placed on the map
# through stations.csv, and each fuel's estimates are scaled so they add up
# to Elexon's measured FUELINST total for the same five minutes.
# --------------------------------------------------------------------------

# Unit fuel type -> display category (matches the overview's colours)
UNIT_CATEGORY = {
    'CCGT': 'gas', 'OCGT': 'gas', 'NUCLEAR': 'nuclear', 'WIND': 'wind', 'NPSHYD': 'hydro',
    'BIOMASS': 'biomass', 'PS': 'storage', 'BATTERY': 'storage', 'BESS': 'storage',
    'COAL': 'other', 'OIL': 'other', 'OTHER': 'other', 'SOLAR': 'solar',
}
# Elexon files batteries and directly connected solar farms under OTHER, so
# their names decide how they're shown.
NAME_CATEGORY = [(re.compile(r'\b(bess|batter(y|ies)|storage)\b', re.I), 'storage'),
                 (re.compile(r'\b(solar|pv|photovoltaic)\b', re.I), 'solar')]


def unit_category(fuel, station, ref):
    category = UNIT_CATEGORY.get(fuel, 'other')
    if fuel not in ('OTHER', ''):
        return category
    if station and station['cat'] in ('solar', 'storage'):
        return station['cat']
    text = ' '.join(part for part in ((ref or {}).get('n'), (ref or {}).get('p')) if part)
    for pattern, named in NAME_CATEGORY:
        if pattern.search(text):
            return named
    return category
STORAGE_FUELS = ('PS', 'BATTERY', 'BESS')
CATEGORY_ORDER = ['wind', 'solar', 'nuclear', 'gas', 'biomass', 'hydro', 'storage', 'other']
CATEGORY_LABELS = {
    'wind': 'Wind', 'solar': 'Solar', 'nuclear': 'Nuclear', 'gas': 'Gas', 'biomass': 'Biomass',
    'hydro': 'Hydro', 'storage': 'Storage', 'other': 'Other',
}

# Interconnector converter stations: (lat, lon, place) at the GB end, then the far end
LINK_ENDS = {
    'INTFR': ((51.106, 0.994, 'Sellindge'), (50.890, 1.790, 'Les Mandarins')),
    'INTIFA2': ((50.810, -1.210, 'Lee-on-the-Solent'), (49.100, -0.290, 'Tourbe')),
    'INTELEC': ((51.090, 1.140, 'Folkestone'), (50.920, 1.780, 'Coquelles')),
    'INTNED': ((51.445, 0.705, 'Isle of Grain'), (51.960, 4.030, 'Maasvlakte')),
    'INTNEM': ((51.300, 1.340, 'Richborough'), (51.300, 3.220, 'Zeebrugge')),
    'INTNSL': ((55.150, -1.530, 'Blyth'), (59.520, 6.650, 'Kvilldal')),
    'INTVKL': ((52.920, -0.200, 'Bicker Fen'), (55.480, 9.130, 'Revsing')),
    'INTIRL': ((55.070, -4.980, 'Auchencrosh'), (54.830, -5.730, 'Islandmagee')),
    'INTEW': ((53.230, -3.070, 'Deeside'), (53.470, -6.530, 'Woodland')),
    'INTGRNL': ((51.680, -4.990, 'Pembroke'), (52.270, -6.980, 'Great Island')),
}


def project(lon, lat):
    """Spherical transverse Mercator on 2W, in km with y north (as tools/geo.py)."""
    lam = math.radians(lon) - math.radians(-2.0)
    phi = math.radians(lat)
    b = math.cos(phi) * math.sin(lam)
    x = 0.5 * 6371.0 * math.log((1 + b) / (1 - b))
    y = 6371.0 * (math.atan2(math.tan(phi), math.cos(lam)) - math.radians(49.0))
    return x, y


def map_point(lat, lon):
    """Map coordinates (km, y down) for the SVG."""
    x, y = project(lon, lat)
    return [round(x, 1), round(-y, 1)]


def settlement_period_at(t):
    """(UK settlement date, period number) containing UTC epoch t."""
    day = datetime.fromtimestamp(t, timezone.utc).date()
    for candidate in (day + timedelta(days=1), day, day - timedelta(days=1)):
        start = uk_midnight_utc(candidate)
        if start <= t:
            return candidate, int((t - start) // 1800) + 1
    return day, 1


def segment_level(segments, t):
    """Level at t from sorted (t0, l0, t1, l1) segments, or None if none covers t."""
    for t0, l0, t1, l1 in segments:
        if t0 <= t <= t1:
            return l0 if t1 <= t0 else l0 + (l1 - l0) * (t - t0) / (t1 - t0)
    return None


def held_level(segments, t):
    """Nearest level when no segment covers t (a gap or an older period)."""
    before = [s for s in segments if s[2] <= t]
    if before:
        return before[-1][3]
    return segments[0][1] if segments else 0.0


def short_unit_id(elexon_id):
    """T_DRAXX-1 -> DRAXX-1 (Elexon IDs carry a type prefix that NGC IDs lack)."""
    return re.sub(r'^[A-Z0-9]_+', '', (elexon_id or '').strip().upper())


class Fleet:
    """Power station locations (stations.csv) and BM unit to station matching."""

    def __init__(self, path, wikidata_path=None):
        self.stations = {}
        self.exact = {}
        self.prefixes = []
        self.patterns = []
        self.cache = {}
        self.error = None
        try:
            with open(path, encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
        except OSError as error:
            self.error = 'Could not read the station list (' + str(error) + ')'
            log.error('[units] %s. Check STATIONS_PATH points at stations.csv.', self.error)
            return
        for row in rows:
            try:
                sid = int(row['id'])
                lat, lon = float(row['lat']), float(row['lon'])
            except (KeyError, TypeError, ValueError):
                continue
            fuel = (row.get('fuel') or 'OTHER').strip().upper()
            try:
                capacity = as_float(row.get('capacity_mw'))
            except ValueError:
                capacity = None
            x, y = map_point(lat, lon)
            self.stations[sid] = {
                'id': sid, 'name': (row.get('name') or '').strip() or str(sid), 'fuel': fuel,
                'cat': UNIT_CATEGORY.get(fuel, 'other'), 'x': x, 'y': y,
                'nation': (row.get('nation') or '').strip(), 'offshore': (row.get('offshore') or '').strip() == '1',
                'capacity': capacity, 'lat': lat, 'lon': lon, 'wikidata': None, 'photo': None,
            }
            for unit in (row.get('units') or '').split():
                self.exact[unit.strip().upper()] = sid
            for prefix in (row.get('unit_prefixes') or '').split():
                self.prefixes.append((prefix.strip().upper(), sid))
            pattern = (row.get('name_pattern') or '').strip()
            if pattern:
                try:
                    self.patterns.append((re.compile(pattern, re.I), sid))
                except re.error as error:
                    log.warning('[units] ignoring the name pattern for %s: %s', self.stations[sid]['name'], error)
        self.prefixes.sort(key=lambda item: -len(item[0]))
        log.info('[units] %d power station locations loaded', len(self.stations))
        if wikidata_path:
            self.load_wikidata(wikidata_path)

    def load_wikidata(self, path):
        """Each station's Wikidata item and Commons photo (tools/build_photos.py)."""
        try:
            with open(path, encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
        except OSError as error:
            log.warning('[units] no station photos: %s', error)
            return
        photos = 0
        for row in rows:
            try:
                station = self.stations.get(int(row['id']))
            except (KeyError, TypeError, ValueError):
                continue
            if not station:
                continue
            qid = (row.get('wikidata') or '').strip()
            station['wikidata'] = qid if re.fullmatch(r'Q\d+', qid) else None
            name = (row.get('file') or '').strip()
            if name and '/' not in name:
                try:
                    width, height = int(row.get('width') or 0), int(row.get('height') or 0)
                except ValueError:
                    width = height = 0
                station['photo'] = {
                    'file': name, 'w': width, 'h': height, 'author': (row.get('author') or '').strip() or 'Unknown',
                    'licence': (row.get('licence') or '').strip(), 'licence_url': (row.get('licence_url') or '').strip(),
                }
                photos += 1
        log.info('[units] %d station photos available', photos)

    def resolve(self, unit, elexon, ref):
        """Station id for a BM unit, by exact ID, then ID prefix, then name."""
        key = (unit, elexon)
        if key in self.cache:
            return self.cache[key]
        ids = [i for i in (unit, short_unit_id(elexon)) if i]
        sid = next((self.exact[i] for i in ids if i in self.exact), None)
        if sid is None:
            sid = next((s for prefix, s in self.prefixes for i in ids if i.startswith(prefix)), None)
        if sid is None and ref:
            text = ' '.join(part for part in (ref.get('n'), ref.get('p')) if part)
            fuel = ref.get('f')
            for pattern, s in self.patterns:
                station = self.stations[s]
                if pattern.search(text) and (not fuel or fuel == 'OTHER' or UNIT_CATEGORY.get(fuel) == station['cat']):
                    sid = s
                    break
        self.cache[key] = sid
        return sid


class Units:
    """Polls Physical Notifications (PN), Bid-Offer Acceptances (BOALF) and
    the BM unit register, and models each station's output."""

    REFERENCE_KEY = 'bmu_reference_v1'

    def __init__(self, store, fleet):
        self.store = store
        self.fleet = fleet
        self.lock = threading.Lock()
        self.pn = {}
        self.boa = {}
        self.boa_checked = None
        self.reference = {}
        self.reference_time = 0
        self.reference_error = None
        self.snapshot = None
        self.version = 0
        self.history_t = None
        self.unplaced_logged = 0
        self.first_logged = False
        try:
            saved = json.loads(store.get_meta(self.REFERENCE_KEY, '') or '{}')
            if isinstance(saved.get('units'), dict):
                self.reference = saved['units']
                self.reference_time = float(saved.get('time') or 0)
        except (ValueError, TypeError, AttributeError):
            pass

    @staticmethod
    def _rows(payload, name):
        if isinstance(payload, dict) and isinstance(payload.get('data'), list):
            payload = payload['data']
        if not isinstance(payload, list):
            raise FetchError('Unexpected ' + name + ' response shape')
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _unit_of(item):
        unit = str(item.get('nationalGridBmUnit') or '').strip().upper()
        elexon = str(item.get('bmUnit') or item.get('elexonBmUnit') or '').strip().upper()
        return unit or short_unit_id(elexon), elexon

    # ---- Elexon BM unit register (names, fuel types; daily) --------------

    def refresh_reference(self, now):
        if now - self.reference_time < 86400:
            return
        try:
            items = self._rows(fetch.get_json(ELEXON + '/reference/bmunits/all', timeout=90), 'BM unit reference')
        except FetchError as error:
            self.reference_error = str(error)
            self.reference_time = now - 86400 + 3600  # try again in an hour
            log.warning('[units] BM unit register unavailable (%s); matching by ID only', error)
            return
        reference = {}
        for item in items:
            unit, elexon = self._unit_of(item)
            if not unit:
                continue
            capacity = None
            for field in ('generationCapacity', 'registeredCapacity'):
                try:
                    value = as_float(item.get(field))
                except ValueError:
                    value = None
                if value and value > 0:
                    capacity = value
                    break
            reference[unit] = {
                'e': elexon, 'f': str(item.get('fuelType') or '').strip().upper(),
                'p': str(item.get('leadPartyName') or '').strip(), 'n': str(item.get('bmUnitName') or '').strip(),
                't': str(item.get('bmUnitType') or '').strip().upper(), 'c': capacity,
            }
        if len(reference) < 50:
            self.reference_time = now - 86400 + 3600
            log.warning('[units] BM unit register looked incomplete (%d units); keeping the previous copy', len(reference))
            return
        self.reference, self.reference_time, self.reference_error = reference, now, None
        self.fleet.cache.clear()
        self.store.set_meta(self.REFERENCE_KEY, json.dumps({'time': now, 'units': reference}, separators=(',', ':')))
        log.info('[units] BM unit register: %d units', len(reference))

    # ---- Physical Notifications (one request per settlement period) -----

    def ensure_pn(self, day, period, now):
        key = (day.isoformat(), period)
        entry = self.pn.get(key)
        if entry and (len(entry['fetched']) >= 2 or now - entry['fetched'][-1] < 900):
            return entry
        url = ELEXON + '/datasets/PN?settlementDate=' + key[0] + '&settlementPeriod=' + str(period)
        units = {}
        for item in self._rows(fetch.get_json(url, timeout=90), 'PN'):
            unit, elexon = self._unit_of(item)
            try:
                segment = (parse_time(item['timeFrom']), float(item['levelFrom']),
                           parse_time(item['timeTo']), float(item['levelTo']))
            except (KeyError, TypeError, ValueError):
                continue
            if unit:
                units.setdefault(unit, [elexon, []])[1].append(segment)
        for value in units.values():
            value[1].sort()
        if not units and entry:
            entry['fetched'].append(now)
            return entry
        if not units:
            raise FetchError('Elexon has not published Physical Notifications for ' + key[0]
                             + ' period ' + str(period) + ' yet')
        entry = {'fetched': (entry['fetched'] if entry else []) + [now], 'units': units}
        self.pn[key] = entry
        for old in sorted(self.pn)[:-4]:
            del self.pn[old]
        log.debug('[units] PN %s period %d: %d units', key[0], period, len(units))
        return entry

    # ---- Bid-Offer Acceptances (incremental, every poll) -----------------

    def update_boalf(self, now):
        start = now - 3 * 3600 if self.boa_checked is None else max(now - 3 * 3600, self.boa_checked - 1200)
        url = ELEXON + '/datasets/BOALF?from=' + iso_minute(start) + '&to=' + iso_minute(now + 3 * 3600)
        count = 0
        for item in self._rows(fetch.get_json(url, timeout=90), 'BOALF'):
            unit, elexon = self._unit_of(item)
            try:
                number = int(item['acceptanceNumber'])
                segment = (parse_time(item['timeFrom']), float(item['levelFrom']),
                           parse_time(item['timeTo']), float(item['levelTo']))
            except (KeyError, TypeError, ValueError):
                continue
            if not unit:
                continue
            accepted = self.boa.setdefault((unit, number), {'elexon': elexon, 'segments': {}})
            if str(item.get('amendmentFlag') or '').strip().upper().startswith('DEL'):
                accepted['segments'].pop(segment[0], None)
                if not accepted['segments']:
                    del self.boa[(unit, number)]
                continue
            accepted['segments'][segment[0]] = segment
            count += 1
        self.boa_checked = now
        for key in [k for k, v in self.boa.items() if max(s[2] for s in v['segments'].values()) < now - 3 * 3600]:
            del self.boa[key]
        log.debug('[units] BOALF: %d segments, %d acceptances held', count, len(self.boa))

    def describe(self, unit, elexon=''):
        """(station id, category, display name) for a BM unit."""
        ref = self.reference.get(unit)
        sid = self.fleet.resolve(unit, elexon, ref)
        fuel = (ref or {}).get('f') or (self.fleet.stations[sid]['fuel'] if sid else '')
        fuel = fuel if fuel in UNIT_CATEGORY else 'OTHER'
        station = self.fleet.stations.get(sid)
        name = station['name'] if station else ((ref or {}).get('n') or (ref or {}).get('p') or elexon or unit)
        return sid, unit_category(fuel, station, ref), name

    def accepted_levels(self, t):
        """Latest acceptance covering t, per unit: {unit: (level, elexon)}."""
        best = {}
        for (unit, number), accepted in self.boa.items():
            segments = sorted(accepted['segments'].values())
            if segments[0][0] <= t <= max(s[2] for s in segments):
                level = segment_level(segments, t)
                if level is not None and (unit not in best or number > best[unit][0]):
                    best[unit] = (number, level, accepted['elexon'])
        return {unit: (level, elexon) for unit, (number, level, elexon) in best.items()}

    # ---- the job ---------------------------------------------------------

    def latest_fuelinst(self, now):
        t = self.store.scalar('SELECT t FROM gen WHERE t >= ? GROUP BY t HAVING COUNT(*) >= 8 '
                              'ORDER BY t DESC LIMIT 1', (now - 2400,))
        if not t:
            return None, {}
        return t, dict(self.store.query('SELECT fuel, mw FROM gen WHERE t = ?', (t,)))

    def poll(self):
        if not self.fleet.stations:
            raise FetchError(self.fleet.error or 'No power station locations loaded')
        now = time.time()
        self.refresh_reference(now)
        t, measured = self.latest_fuelinst(now)
        at = t + 150 if t else now
        day, period = settlement_period_at(at)
        errors = []
        pn = None
        try:
            pn = self.ensure_pn(day, period, now)
        except FetchError as error:
            errors.append(str(error))
            fallback = [k for k in sorted(self.pn) if k < (day.isoformat(), period)]
            pn = self.pn[fallback[-1]] if fallback else None
        try:
            self.update_boalf(now)
        except FetchError as error:
            errors.append('Bid-offer acceptances: ' + str(error))
        if pn is not None:
            self.compute(at, t, measured, pn['units'], day, period, now)
        if errors:
            raise FetchError('; '.join(errors))

    def compute(self, at, t, measured, pn_units, day, period, now):
        levels = {}
        for unit, (elexon, segments) in pn_units.items():
            level = segment_level(segments, at)
            levels[unit] = [held_level(segments, at) if level is None else level, 'pn', elexon]
        planned = {unit: values[0] for unit, values in levels.items()}
        for unit, (level, elexon) in self.accepted_levels(at).items():
            levels[unit] = [level, 'boa', levels.get(unit, [0, '', elexon])[2] or elexon]

        rows = []
        estimated = {}
        for unit, (level, source, elexon) in levels.items():
            ref = self.reference.get(unit)
            elexon = elexon or (ref or {}).get('e', '')
            if elexon.startswith(('I_', '2_', 'V_', 'C_')) or (ref and (ref['f'].startswith('INT') or ref['t'] in ('I', 'S'))):
                continue
            sid = self.fleet.resolve(unit, elexon, ref)
            fuel = (ref or {}).get('f') or (self.fleet.stations[sid]['fuel'] if sid else '')
            if fuel.startswith('INT'):
                continue
            if level < 0 and fuel not in STORAGE_FUELS and sid is None:
                continue  # demand-side units
            fuel = fuel if fuel in UNIT_CATEGORY else 'OTHER'
            rows.append((unit, elexon, sid, fuel, level, source, ref))
            if level > 0:
                estimated[fuel] = estimated.get(fuel, 0.0) + level

        scale = {}
        for fuel, total in estimated.items():
            target = measured.get('BESS' if fuel == 'BATTERY' else fuel)
            factor = 1.0
            if t and target is not None and total > 1:
                raw = max(0.0, float(target)) / total
                factor = raw if 0.4 <= raw <= 2.5 or target < 50 else 1.0
            scale[fuel] = factor

        stations, unplaced, instructions = {}, [], []
        categories = {cat: {'mapped': 0.0, 'unplaced': 0.0, 'estimated': 0.0} for cat in CATEGORY_ORDER}
        for unit, elexon, sid, fuel, level, source, ref in rows:
            if source == 'boa' and unit in planned and abs(level - planned[unit]) >= 1:
                instructions.append({'unit': unit, 'elexon': elexon, 'sid': sid,
                                     'cat': unit_category(fuel, self.fleet.stations.get(sid), ref),
                                     'delta': round(level - planned[unit], 1), 'level': round(level, 1)})
            mw = level * scale.get(fuel, 1.0) if level > 0 else level
            cat = unit_category(fuel, self.fleet.stations.get(sid), ref)
            if level > 0:
                categories[cat]['estimated'] += level
            if sid is not None:
                st = stations.setdefault(sid, {'mw': 0.0, 'units': 0, 'running': 0, 'ref_cap': 0.0, 'instructed': 0})
                st['mw'] += mw
                st['units'] += 1
                st['running'] += 1 if abs(mw) >= 1 else 0
                st['instructed'] += 1 if source == 'boa' else 0
                st['ref_cap'] += (ref or {}).get('c') or 0.0
                if mw > 0:
                    categories[cat]['mapped'] += mw
            elif mw >= 1 or mw <= -1:
                name = (ref or {}).get('n') or (ref or {}).get('p') or ''
                unplaced.append({'unit': elexon or unit, 'name': name, 'cat': cat, 'mw': round(mw, 1)})
                if mw > 0:
                    categories[cat]['unplaced'] += mw
        unplaced.sort(key=lambda item: -abs(item['mw']))

        snapshot = {
            'at': int(at), 'time': t, 'basis': 'measured' if t else 'scheduled',
            'period': [day.isoformat(), period], 'stations': stations,
            'categories': categories, 'scale': {k: round(v, 3) for k, v in scale.items()},
            'unplaced': unplaced[:80], 'unplaced_count': len(unplaced),
            'counts': {'units': len(rows), 'placed': sum(s['units'] for s in stations.values()),
                       'instructed': sum(s['instructed'] for s in stations.values())},
            'reference_units': len(self.reference), 'reference_error': self.reference_error,
            'instructions': instructions,
        }
        with self.lock:
            self.snapshot = snapshot
            self.version += 1
        if t and t != self.history_t:
            self.store.write(UPSERT_ST, [(t, sid, round(s['mw'], 1)) for sid, s in stations.items()], history=False)
            self.history_t = t
        self.log_progress(snapshot, now)

    def log_progress(self, snapshot, now):
        if not self.first_logged:
            self.first_logged = True
            log.info('[units] %d units reporting: %d placed at %d stations, %d without a known location',
                     snapshot['counts']['units'], snapshot['counts']['placed'], len(snapshot['stations']),
                     snapshot['unplaced_count'])
        big = [u for u in snapshot['unplaced'] if u['mw'] >= 30]
        if big and self.reference and now - self.unplaced_logged > 86400:
            self.unplaced_logged = now
            log.info('[units] Not on the map yet (no location known): %s',
                     '; '.join('%s %s(%s, %d MW)' % (u['unit'], (u['name'] + ' ') if u['name'] else '', u['cat'], u['mw'])
                               for u in big[:12]))
