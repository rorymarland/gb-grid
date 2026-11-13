"""API views: /api/longterm, its summary and /api/outlook."""

import time
from datetime import timedelta

from .config import LONG_HISTORY
from .timeutil import floor_to, uk_day, uk_midnight_utc
from .longterm import LH_START


class LongtermViews:
    """Mixed into Grid; uses its store, models and cache."""

    # ---- /api/longterm and /api/outlook -------------------------------------

    LT_FUELS = ['gas', 'coal', 'nuclear', 'wind', 'solar', 'biomass', 'hydro', 'imports', 'storage', 'other']
    LT_SQL = ('AVG(gas), AVG(coal), AVG(nuclear), AVG(COALESCE(wind, 0) + COALESCE(wind_emb, 0)), AVG(solar), '
              'AVG(biomass), AVG(hydro), AVG(imports), AVG(storage), AVG(other), AVG(generation), AVG(ci)')

    @staticmethod
    def longterm_bucket(span):
        if span <= 14 * 86400:
            return 1800
        if span <= 92 * 86400:
            return 6 * 3600
        if span <= 2 * 366 * 86400:
            return 86400
        return 7 * 86400

    def longterm_extent(self):
        first, last, count = self.store.query('SELECT MIN(t), MAX(t), COUNT(*) FROM lh')[0]
        price_first, price_last = self.store.query('SELECT MIN(t), MAX(t) FROM lp WHERE price IS NOT NULL')[0]
        complete = bool(last and last > time.time() - 10 * 86400)
        progress = 0.0 if not last else max(0.0, min(1.0, (last - LH_START) / max(1, time.time() - 2 * 86400 - LH_START)))
        return {'first': first, 'last': last, 'rows': count, 'price_first': price_first, 'price_last': price_last,
                'complete': complete, 'progress': round(progress, 3)}

    def longterm(self, start=None, end=None):
        extent = self.longterm_extent()
        if not extent['complete']:
            return {'ready': False, 'enabled': LONG_HISTORY, 'extent': extent}
        end = min(int(end or extent['last'] + 1800), extent['last'] + 1800)
        start = max(int(start or end - 365 * 86400), extent['first'])
        if end - start < 86400:
            start = end - 86400
        bucket = self.longterm_bucket(end - start)
        key = ('longterm', floor_to(start, bucket), floor_to(end, bucket))
        version = (extent['last'], extent['price_last'])
        with self.cache_lock:  # zooming makes many one-off ranges: keep only the recent ones
            ranges = [k for k in self.cache if k[0] == 'longterm']
            for stale in ranges[:-40]:
                del self.cache[stale]
        return self.cached(key, version, lambda: self._build_longterm(floor_to(start, bucket), end, bucket, extent))

    def _build_longterm(self, start, end, bucket, extent):
        # Weekly buckets start on Monday 00:00 UTC (the epoch was a Thursday)
        offset = 4 * 86400 if bucket == 7 * 86400 else 0
        if offset:
            start = floor_to(start - offset, bucket) + offset
        rows = self.store.query('SELECT ((t - ?) / ?) * ? + ? AS b, %s, COUNT(*) FROM lh WHERE t >= ? AND t < ? '
                                'GROUP BY b ORDER BY b' % self.LT_SQL, (offset, bucket, bucket, offset, start, end))
        prices = dict(self.store.query('SELECT ((t - ?) / ?) * ? + ? AS b, AVG(price) FROM lp WHERE t >= ? AND t < ? '
                                       'AND price IS NOT NULL GROUP BY b', (offset, bucket, bucket, offset, start, end)))
        times = [row[0] for row in rows]
        series = {fuel: [None if row[1 + i] is None else round(row[1 + i]) for row in rows] for i, fuel in enumerate(self.LT_FUELS)}
        def r(v, n=0):
            return None if v is None else round(v, n) if n else round(v)
        return {
            'ready': True, 'enabled': LONG_HISTORY, 'extent': extent, 'from': start, 'to': end, 'bucket': bucket,
            't': times, 'mix': series,
            'generation': [r(row[11]) for row in rows],
            'ci': [r(row[12]) for row in rows],
            'price': [r(prices.get(t), 2) for t in times],
        }

    def longterm_summary(self):
        extent = self.longterm_extent()
        if not extent['complete']:
            return {'ready': False, 'enabled': LONG_HISTORY, 'extent': extent}
        return self.cached(('longterm-summary',), (extent['last'], extent['price_last'], int(time.time() // 3600)),
                           lambda: self._build_summary(extent))

    def _build_summary(self, extent):
        q, now = self.store.query, time.time()
        records = []

        def add(key, title, unit, row, span, better):
            if row and row[0] is not None and row[1] is not None:
                records.append({'key': key, 'title': title, 'unit': unit, 't': row[0], 'value': round(row[1], 1),
                                'span': span, 'recent': now - row[0] < 31 * 86400, 'better': better})

        full_day = 'GROUP BY d HAVING COUNT(*) >= 46'
        add('cleanest-hh', 'Cleanest half hour', 'g/kWh',
            (q('SELECT t, ci FROM lh WHERE ci > 0 ORDER BY ci, t DESC LIMIT 1') or [None])[0], 'hh', 'low')
        add('cleanest-day', 'Cleanest day', 'g/kWh',
            (q('SELECT ukday(t) AS d, AVG(ci) a FROM lh WHERE ci > 0 %s ORDER BY a LIMIT 1' % full_day) or [None])[0], 'day', 'low')
        add('wind-peak', 'Most wind power', 'MW',
            (q('SELECT t, COALESCE(wind, 0) + COALESCE(wind_emb, 0) w FROM lh ORDER BY w DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('windiest-day', 'Windiest day', 'MW',
            (q('SELECT ukday(t) AS d, AVG(COALESCE(wind, 0) + COALESCE(wind_emb, 0)) a FROM lh %s ORDER BY a DESC LIMIT 1' % full_day) or [None])[0], 'day', 'high')
        add('wind-share', 'Highest wind share', '%',
            (q('SELECT t, 100.0 * (COALESCE(wind, 0) + COALESCE(wind_emb, 0)) / generation s FROM lh '
               'WHERE generation > 10000 ORDER BY s DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('solar-peak', 'Most solar power', 'MW',
            (q('SELECT t, solar FROM lh ORDER BY solar DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('zero-carbon', 'Highest zero-carbon share', '%',
            (q('SELECT t, 100.0 * (COALESCE(nuclear, 0) + COALESCE(wind, 0) + COALESCE(wind_emb, 0) + COALESCE(solar, 0) + COALESCE(hydro, 0)) / generation s '
               'FROM lh WHERE generation > 10000 ORDER BY s DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('demand-low', 'Least generation needed', 'MW',
            (q('SELECT t, generation FROM lh WHERE generation > 5000 ORDER BY generation LIMIT 1') or [None])[0], 'hh', 'low')
        add('demand-high', 'Most generation needed', 'MW',
            (q('SELECT t, generation FROM lh ORDER BY generation DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('price-high', 'Highest wholesale price', '£/MWh',
            (q('SELECT t, price FROM lp WHERE price IS NOT NULL ORDER BY price DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('price-low', 'Lowest wholesale price', '£/MWh',
            (q('SELECT t, price FROM lp WHERE price IS NOT NULL ORDER BY price LIMIT 1') or [None])[0], 'hh', 'low')
        last_coal = q('SELECT MAX(t) FROM lh WHERE coal > 20')[0][0]

        # The latest complete day against the same weekday a year earlier
        compare = None
        latest = q('SELECT ukday(t) AS d FROM lh %s ORDER BY d DESC LIMIT 1' % full_day)
        if latest:
            def day(d):
                end = uk_midnight_utc(uk_day(d + 43200) + timedelta(days=1))
                row = q('SELECT %s, COUNT(*) FROM lh WHERE t >= ? AND t < ?' % self.LT_SQL, (d, end))[0]
                if not row[-1]:
                    return None
                price = q('SELECT AVG(price) FROM lp WHERE t >= ? AND t < ? AND price IS NOT NULL', (d, end))[0][0]
                total = sum(v or 0 for v in row[:10]) or 1
                return {'t': d, 'mix': {f: round((row[i] or 0) / total, 4) for i, f in enumerate(self.LT_FUELS)},
                        'generation': round(row[10] or 0), 'ci': None if row[11] is None else round(row[11]),
                        'price': None if price is None else round(price, 2)}
            d = latest[0][0]
            then = uk_midnight_utc(uk_day(d + 43200) - timedelta(days=364))
            compare = {'now': day(d), 'then': day(then)}

        years = []
        prices = dict(q("SELECT ukyear(t) y, AVG(price) FROM lp WHERE price IS NOT NULL GROUP BY y"))
        for row in q("SELECT ukyear(t) y, %s, COUNT(*) FROM lh GROUP BY y ORDER BY y" % self.LT_SQL):
            total = sum(v or 0 for v in row[1:11]) or 1
            years.append({'year': row[0], 'mix': {f: round((row[1 + i] or 0) / total, 4) for i, f in enumerate(self.LT_FUELS)},
                          'ci': None if row[12] is None else round(row[12]), 'generation': round(row[11] or 0),
                          'price': None if prices.get(row[0]) is None else round(prices[row[0]], 2),
                          'complete': row[13] >= 17000})
        return {'ready': True, 'extent': extent, 'records': records, 'last_coal': last_coal,
                'compare': compare, 'years': years}

    def outlook_payload(self):
        version = (self.outlook.version if self.outlook else 0, self.store.version, int(time.time() // 300))
        return self.cached(('outlook',), version, self._build_outlook)

    def _build_outlook(self):
        now = time.time()
        start, end = floor_to(now, 1800), floor_to(now, 1800) + 48 * 3600
        def clip(pairs):
            return [[int(t), round(v)] for t, v in pairs if start <= t <= end and v is not None]
        solar = self.store.query("SELECT t, v FROM hh WHERE k = 'fc_solar' AND t >= ? AND t <= ? ORDER BY t", (start, end))
        carbon = [(t, v[0]) for t, v in sorted(self.carbon_table(0, start, end).items())]
        outlook = self.outlook
        return {
            'from': start, 'to': end,
            'demand': clip(outlook.demand if outlook else []),
            'wind': clip(outlook.wind if outlook else []),
            'solar': clip(solar),
            'carbon': clip(carbon),
            'raw': outlook.raw if outlook else {},
            'demand_kind': outlook.demand_kind if outlook else 'national',
        }
