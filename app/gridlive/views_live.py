"""API views: /api/history and /api/live (the Overview)."""

import json
import time

from .config import BROWSER_REFRESH, HISTORY_DAYS, REGION_ID, VERSION
from .reference import COUNTRY_ORDER, INTERCONNECTORS, NEGATIVE_SERIES, SUPPLY_SERIES
from .timeutil import floor_to
from .fetch import r2
from .neighbours import country_carbon, imports_carbon


class LiveViews:
    """Mixed into Grid; uses its store, models and cache."""

    # ---- /api/history ----------------------------------------------------

    RANGES = {'24h': (86400, 300), '7d': (7 * 86400, 1800), '30d': (30 * 86400, 3600)}

    def available_ranges(self):
        ranges = ['24h', '7d']
        if HISTORY_DAYS >= 30:
            ranges.append('30d')
        return ranges

    def history(self, range_key):
        if range_key not in self.RANGES or range_key not in self.available_ranges():
            range_key = '24h'
        return self.cached(('history', range_key), self.store.history_version,
                           lambda: self._build_history(range_key))

    def _build_history(self, range_key):
        span, step = self.RANGES[range_key]
        latest = self.latest_generation_time()
        end = floor_to(latest if latest else time.time(), step)
        start = end - span + step
        generation = self.load_generation(start, end + step)
        wind_table, solar_table, _, _ = self.load_embedded(start, end + step)
        carbon = self.carbon_table(0, floor_to(start, 1800), end + step)
        prices = self.price_table(floor_to(start, 1800), end + step)
        times = list(range(start, end + 1, step))
        keys = SUPPLY_SERIES + NEGATIVE_SERIES
        series = {key: [] for key in keys}
        countries = {name: [] for name in COUNTRY_ORDER}
        demand, carbon_values, carbon_kind, price_values = [], [], [], []
        for t in times:
            if step == 300:
                fuels = generation.get(t)
                embedded_wind = self.interpolate(wind_table, t)
                embedded_solar = self.interpolate(solar_table, t)
            else:
                samples = [generation[s] for s in range(t, t + step, 300) if s in generation]
                fuels = None
                if samples:
                    codes = set().union(*samples)
                    fuels = {code: sum(sample.get(code, 0.0) for sample in samples) / len(samples) for code in codes}
                embedded_wind = self.average_half_hours(wind_table, t, t + step)
                embedded_solar = self.average_half_hours(solar_table, t, t + step)
            if not fuels:
                for key in keys:
                    series[key].append(None)
                for name in countries:
                    countries[name].append(None)
                demand.append(None)
            else:
                m = self.mix(fuels, embedded_wind, embedded_solar)
                for key in keys:
                    series[key].append(r2(m[key] / 1000.0))
                for name in countries:
                    countries[name].append(r2(m['countries'].get(name, 0.0) / 1000.0))
                demand.append(r2(m['demand'] / 1000.0))
            # Carbon and price are half-hourly: average them into larger buckets
            half_hours = range(floor_to(t, 1800), max(t + step, floor_to(t, 1800) + 1800), 1800)
            official = [carbon[h] for h in half_hours if h in carbon and carbon[h][0] is not None]
            if official:
                carbon_values.append(round(sum(c[0] for c in official) / len(official)))
                carbon_kind.append('a' if all(c[2] == 'actual' for c in official) else 'f')
            elif fuels and m['carbon_estimate'] is not None:
                carbon_values.append(round(m['carbon_estimate']))
                carbon_kind.append('e')
            else:
                carbon_values.append(None)
                carbon_kind.append(None)
            price_points = [prices[h] for h in half_hours if prices.get(h) is not None]
            price_values.append(round(sum(price_points) / len(price_points), 2) if price_points else None)
        return {
            'range': range_key, 'step': step, 't': times,
            'series': series, 'countries': countries, 'demand': demand,
            'carbon': carbon_values, 'carbon_kind': carbon_kind, 'price': price_values,
            'generated': int(time.time()),
        }

    # ---- /api/live -------------------------------------------------------

    def live(self):
        return self.cached(('live',), self.store.version, self._build_live)

    def _rank(self, values, current, higher_is_better=False):
        """Share of past values that are worse than the current one (0..1)."""
        values = [v for v in values if v is not None]
        if current is None or len(values) < 12:
            return None
        if higher_is_better:
            return sum(1 for v in values if v < current) / len(values)
        return sum(1 for v in values if v > current) / len(values)

    def _build_live(self):
        now = time.time()
        payload = {
            'version': VERSION, 'server_time': int(now), 'refresh_seconds': BROWSER_REFRESH,
            'history_version': self.store.history_version, 'ranges': self.available_ranges(),
            'history_days': HISTORY_DAYS, 'persistent': self.store.persistent,
            'time': None, 'status': self.status_payload(now),
        }
        t = self.latest_generation_time()
        half_hour = floor_to(t if t else now, 1800)

        # Carbon intensity: official figure, 48 h forecast and 7-day context
        carbon_past = self.carbon_table(0, half_hour - 7 * 86400, half_hour - 1)
        carbon_future = self.carbon_table(0, half_hour, half_hour + 49 * 3600)
        def points(table):
            return [[k, round(v[0]), v[1]] for k, v in sorted(table.items()) if v[0] is not None]

        payload['forecast'] = points(carbon_future)
        payload['carbon_past'] = points({k: v for k, v in carbon_past.items() if k >= half_hour - 86400})
        if REGION_ID:
            region_now = floor_to(now, 1800)
            region_future = self.carbon_table(REGION_ID, region_now, region_now + 49 * 3600)
            region_past = self.carbon_table(REGION_ID, region_now - 86400, region_now - 1)
            current = region_future.get(region_now)
            payload['region'] = {
                'id': REGION_ID, 'name': self.store.get_meta('region_name', 'Region ' + str(REGION_ID)),
                'value': round(current[0]) if current and current[0] is not None else None,
                'index': current[1] if current else None,
                'forecast': points(region_future), 'past': points(region_past),
                'mix': None, 'recent': self.region_recent(region_now),
            }
            try:
                mix = json.loads(self.store.get_meta('region_mix', '') or 'null')
                if mix and region_now - mix['t'] <= 3 * 3600:
                    payload['region']['mix'] = mix
            except (ValueError, TypeError, KeyError):
                pass

        # Wholesale price
        price_rows = self.store.query("SELECT t, v FROM hh WHERE k = 'price' AND t >= ? ORDER BY t",
                                      (half_hour - 7 * 86400,))
        if price_rows:
            latest_t, latest_price = price_rows[-1]
            today = [v for k, v in price_rows if k > now - 86400]
            payload['price'] = {
                'value': round(latest_price, 2), 'from': latest_t,
                'rank': self._rank([v for k, v in price_rows[:-1]], latest_price),
                'day_min': round(min(today), 2) if today else None,
                'day_max': round(max(today), 2) if today else None,
                'day_avg': round(sum(today) / len(today), 2) if today else None,
            }

        # Frequency: latest reading plus the last hour
        freq_rows = self.store.query('SELECT t, hz FROM freq WHERE t >= ? ORDER BY t', (now - 3600,))
        if freq_rows:
            payload['frequency'] = {
                'hz': round(freq_rows[-1][1], 3), 't': int(freq_rows[-1][0]),
                'series': [[int(k), round(v, 3)] for k, v in freq_rows],
            }

        if not t:
            return payload

        # Generation mix at the latest 5-minute period
        fuels = self.load_generation(t, t)[t]
        wind_table, solar_table, wind_basis, solar_basis = self.load_embedded(t - 3600, t + 3600)
        m = self.mix(fuels, self.interpolate(wind_table, t), self.interpolate(solar_table, t))
        supply = m['supply'] or 1.0

        def source(key, label, gw_value, group, note=None):
            item = {'key': key, 'label': label, 'gw': r2(gw_value / 1000.0), 'group': group,
                    'pct': round(100.0 * max(0.0, gw_value) / supply, 1)}
            if note:
                item['note'] = note
            return item

        sources = [
            source('wind', 'Wind', m['wind'], 'renewable'),
            source('solar', 'Solar', m['solar'], 'renewable'),
            source('hydro', 'Hydro', m['hydro'], 'renewable'),
            source('nuclear', 'Nuclear', m['nuclear'], 'nuclear'),
            source('biomass', 'Biomass', m['biomass'], 'biomass'),
            source('gas', 'Gas', m['gas'], 'fossil'),
            source('coal', 'Coal', m['coal'], 'fossil'),
            source('oil', 'Oil', m['oil'], 'fossil'),
            source('other', 'Other', m['other'], 'other'),
            source('imports', 'Imports', m['imports'], 'transfer'),
            source('storage_out', 'Storage', m['storage_out'], 'transfer'),
        ]
        sources[0]['parts'] = {'transmission': r2(m['wind_transmission'] / 1000.0),
                               'embedded': r2(m['wind_embedded'] / 1000.0)}
        sources[5]['parts'] = {'ccgt': r2(m['ccgt'] / 1000.0), 'ocgt': r2(m['ocgt'] / 1000.0)}
        current_half_hour = floor_to(t, 1800)
        sources[0]['basis'] = wind_basis.get(current_half_hour)
        sources[1]['basis'] = solar_basis.get(current_half_hour)

        links = []
        for code, mw in sorted(m['links'].items()):
            name, country, capacity, note = INTERCONNECTORS.get(code, (code, 'Other', None, ''))
            links.append({'code': code, 'name': name, 'country': country, 'capacity': capacity,
                          'note': note, 'gw': r2(mw / 1000.0)})
        neighbour_ci = country_carbon(self.store, t)
        countries = [{'country': c, 'gw': r2(m['countries'][c] / 1000.0), 'ci': neighbour_ci.get(c)}
                     for c in COUNTRY_ORDER + sorted(set(m['countries']) - set(COUNTRY_ORDER))
                     if c in m['countries']]
        coming_in = imports_carbon(m['countries'], neighbour_ci)

        official = carbon_future.get(current_half_hour)
        if official and official[0] is not None:
            carbon = {'value': round(official[0]), 'index': official[1], 'kind': official[2],
                      'from': current_half_hour}
        else:
            carbon = {'value': round(m['carbon_estimate']) if m['carbon_estimate'] is not None else None,
                      'index': None, 'kind': 'estimate', 'from': t}
        carbon['estimate'] = round(m['carbon_estimate']) if m['carbon_estimate'] is not None else None
        carbon['rank'] = self._rank([v[0] for v in carbon_past.values()], carbon['value'])

        week = self.history('7d')
        demand_gw = m['demand'] / 1000.0
        payload.update({
            'time': t,
            'demand_gw': r2(demand_gw),
            'supply_gw': r2(m['supply'] / 1000.0),
            'exports_gw': r2(abs(m['exports']) / 1000.0),
            'storing_gw': r2(abs(m['storage_in']) / 1000.0),
            'demand_rank': self._rank(week['demand'], demand_gw, higher_is_better=True),
            'demand_week': {
                'min': min((v for v in week['demand'] if v is not None), default=None),
                'max': max((v for v in week['demand'] if v is not None), default=None),
            },
            'sources': sources,
            'groups': {
                'renewable': round(100.0 * (m['wind'] + m['solar'] + m['hydro']) / supply, 1),
                'low_carbon': round(100.0 * (m['wind'] + m['solar'] + m['hydro'] + m['nuclear']) / supply, 1),
                'fossil': round(100.0 * (m['gas'] + m['coal'] + m['oil']) / supply, 1),
            },
            'links': links,
            'countries': countries,
            'storage': {'pumped': r2(m['pumped'] / 1000.0), 'battery': r2(m['battery'] / 1000.0)},
            'carbon': carbon,
            'imports_ci': round(coming_in) if coming_in is not None else None,
            'generation_ci': round(m['generation_ci']) if m['generation_ci'] is not None else None,
        })
        return payload
