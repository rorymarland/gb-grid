"""API views: /api/map, /api/station and /api/replay."""

import time
import urllib.parse

from .config import BROWSER_REFRESH, PHOTOS, VERSION
from .timeutil import floor_to
from .store import STATION_HISTORY_DAYS
from .photos import file_key
from .units import CATEGORY_LABELS, CATEGORY_ORDER, LINK_ENDS, map_point


class MapViews:
    """Mixed into Grid; uses its store, models and cache."""

    # ---- /api/map and /api/station -------------------------------------

    def map_data(self):
        version = (self.store.version, self.units.version if self.units else 0)
        return self.cached(('map',), version, self._build_map)

    def _build_map(self):
        live = self.live()
        snapshot = self.units.snapshot if self.units else None
        payload = {
            'version': VERSION, 'server_time': int(time.time()), 'refresh_seconds': BROWSER_REFRESH,
            'time': live.get('time'), 'status': live.get('status'), 'persistent': self.store.persistent,
            'demand_gw': live.get('demand_gw'), 'supply_gw': live.get('supply_gw'),
            'exports_gw': live.get('exports_gw'), 'storing_gw': live.get('storing_gw'),
            'carbon': live.get('carbon'), 'stations': [], 'links': [], 'categories': [],
            'unplaced': [], 'model': None,
        }
        for link in live.get('links', []):
            item = {'code': link['code'], 'name': link['name'], 'country': link['country'], 'note': link['note'],
                    'mw': None if link['gw'] is None else round(link['gw'] * 1000),
                    'cap': None if link['capacity'] is None else round(link['capacity'] * 1000)}
            ends = LINK_ENDS.get(link['code'])
            if ends:
                item['gb'] = map_point(ends[0][0], ends[0][1]) + [ends[0][2]]
                item['far'] = map_point(ends[1][0], ends[1][1]) + [ends[1][2]]
            payload['links'].append(item)

        sources = {item['key']: item for item in live.get('sources', [])}
        def mw(key, part=None):
            item = sources.get(key)
            if not item:
                return None
            value = item.get('parts', {}).get(part) if part else item.get('gw')
            return None if value is None else round(value * 1000)
        measured = {
            'wind': mw('wind', 'transmission'), 'solar': 0 if sources else None, 'nuclear': mw('nuclear'),
            'gas': mw('gas'), 'biomass': mw('biomass'), 'hydro': mw('hydro'), 'storage': mw('storage_out'),
            'other': None if not sources else sum(mw(k) or 0 for k in ('coal', 'oil', 'other')),
        }
        embedded = {'wind': mw('wind', 'embedded'), 'solar': mw('solar')}
        model = snapshot['categories'] if snapshot else {}
        counts = {}
        if snapshot:
            for sid, st in snapshot['stations'].items():
                station = self.fleet.stations[sid]
                if st['mw'] >= 1:
                    counts[station['cat']] = counts.get(station['cat'], 0) + 1
        for cat in CATEGORY_ORDER:
            part = model.get(cat, {})
            payload['categories'].append({
                'key': cat, 'label': CATEGORY_LABELS[cat], 'measured': measured.get(cat),
                'embedded': embedded.get(cat), 'mapped': round(part['mapped']) if part else None,
                'unplaced': round(part['unplaced']) if part else None, 'stations': counts.get(cat, 0),
            })
        if not snapshot:
            return payload
        for sid, st in sorted(snapshot['stations'].items(), key=lambda item: -abs(item[1]['mw'])):
            station = self.fleet.stations[sid]
            capacity = station['capacity'] or st['ref_cap']
            payload['stations'].append({
                'id': sid, 'n': station['name'], 'c': station['cat'], 'x': station['x'], 'y': station['y'],
                'na': station['nation'], 'off': 1 if station['offshore'] else 0,
                'cap': round(capacity) if capacity else None, 'mw': round(st['mw'], 1),
                'u': st['units'], 'on': st['running'], 'i': st['instructed'],
            })
        payload['unplaced'] = snapshot['unplaced']
        payload['model'] = {
            'basis': snapshot['basis'], 'at': snapshot['at'], 'time': snapshot['time'],
            'period': snapshot['period'], 'scale': snapshot['scale'], 'counts': snapshot['counts'],
            'unplaced_count': snapshot['unplaced_count'], 'reference_units': snapshot['reference_units'],
            'reference_error': snapshot['reference_error'], 'known_stations': len(self.fleet.stations),
        }
        return payload

    # ---- /api/replay: the map's time machine -----------------------------

    def replay(self):
        latest = self.store.scalar('SELECT MAX(t) FROM st')
        return self.cached(('replay',), latest, lambda: self._build_replay(latest))

    def _build_replay(self, latest, step=600):
        """Station output, interconnector flows and national totals every ten
        minutes over the stored station history (up to STATION_HISTORY_DAYS)."""
        if not latest:
            return {'step': step, 't': [], 'stations': {}, 'links': {}, 'totals': {}}
        end = floor_to(latest, step)
        first = self.store.scalar('SELECT MIN(t) FROM st') or end
        start = max(floor_to(first, step), end - STATION_HISTORY_DAYS * 86400 + step)
        times = list(range(start, end + 1, step))
        index = {t: i for i, t in enumerate(times)}
        stations = {}
        for sid, bucket, mw in self.store.query(
                'SELECT id, (t / ?) * ? AS b, AVG(mw) FROM st WHERE t >= ? AND t < ? GROUP BY id, b',
                (step, step, start, end + step)):
            series = stations.get(sid)
            if series is None:
                series = stations[sid] = [None] * len(times)
            i = index.get(bucket)
            if i is not None:
                series[i] = int(round(mw))
        fuels_by_bucket = {}
        for bucket, fuel, mw in self.store.query(
                'SELECT (t / ?) * ? AS b, fuel, AVG(mw) FROM gen WHERE t >= ? AND t < ? GROUP BY b, fuel',
                (step, step, start, end + step)):
            fuels_by_bucket.setdefault(bucket, {})[fuel] = mw
        wind_table, solar_table, _, _ = self.load_embedded(start, end + step)
        keys = ['wind', 'wind_embedded', 'solar', 'nuclear', 'gas', 'biomass', 'hydro', 'storage', 'other',
                'imports', 'exports', 'demand']
        totals = {key: [None] * len(times) for key in keys}
        links = {}
        for i, t in enumerate(times):
            fuels = fuels_by_bucket.get(t)
            if not fuels or len(fuels) < 8:
                continue
            middle = t + step // 2
            m = self.mix(fuels, self.interpolate(wind_table, middle), self.interpolate(solar_table, middle))
            values = {
                'wind': m['wind_transmission'], 'wind_embedded': m['wind_embedded'], 'solar': m['solar'],
                'nuclear': m['nuclear'], 'gas': m['gas'], 'biomass': m['biomass'], 'hydro': m['hydro'],
                'storage': m['storage_out'], 'other': m['coal'] + m['oil'] + m['other'],
                'imports': m['imports'], 'exports': m['exports'], 'demand': m['demand'],
            }
            for key in keys:
                totals[key][i] = int(round(values[key]))
            for code, mw in m['links'].items():
                links.setdefault(code, [None] * len(times))[i] = int(round(mw))
        return {'step': step, 't': times, 'stations': {str(k): v for k, v in stations.items()},
                'links': links, 'totals': totals, 'generated': int(time.time())}

    def station_history(self, sid):
        station = self.fleet.stations.get(sid) if self.fleet else None
        if station is None:
            return None
        rows = self.store.query('SELECT t, mw FROM st WHERE id = ? AND t >= ? ORDER BY t',
                                (sid, time.time() - 86400))
        return {'id': sid, 'name': station['name'], 'cat': station['cat'], 'step': 300,
                'since': self.store.scalar('SELECT MIN(t) FROM st'),
                'points': [[t, round(v, 1)] for t, v in rows], 'place': self.station_place(station)}

    @staticmethod
    def station_place(station):
        """Where a station is, and its photo and credit, for the station card."""
        photo = station.get('photo') if PHOTOS else None
        place = {'lat': station['lat'], 'lon': station['lon'], 'offshore': station['offshore'],
                 'wikidata': station.get('wikidata'), 'photo': None}
        if photo:
            place['photo'] = {
                'src': '/api/photo?id=%d&v=%s' % (station['id'], file_key(photo['file'])),
                'w': photo['w'], 'h': photo['h'], 'author': photo['author'], 'licence': photo['licence'],
                'licence_url': photo['licence_url'],
                'page': 'https://commons.wikimedia.org/wiki/File:' + urllib.parse.quote(photo['file'].replace(' ', '_')),
            }
        return place

    def status_payload(self, now):
        snapshot = self.status.snapshot()
        latest = self.latest_generation_time()
        units = self.units.snapshot if self.units else None
        freshness = {
            'generation': (latest + 300) if latest else None,
            'frequency': self.store.scalar('SELECT MAX(t) FROM freq'),
            'price': self.store.scalar("SELECT MAX(t) FROM hh WHERE k = 'price'"),
            'solar': self.store.scalar("SELECT MAX(t) FROM hh WHERE k = 'pv'"),
            'units': units['at'] if units else None,
        }
        result = {}
        for key, item in snapshot.items():
            item['data_time'] = int(freshness[key]) if freshness.get(key) else None
            result[key] = item
        return result
