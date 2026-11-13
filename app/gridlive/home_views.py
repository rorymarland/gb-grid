"""Your home: the figures /api/home returns."""

from bisect import bisect_right
from datetime import timedelta

from . import tariffs
from .config import GAS_KG_PER_KWH, OCTOPUS_LIVE_SECONDS, log
from .octopus import single_rate
from .timeutil import floor_to, settlement_periods, uk_clock, uk_midnight_utc

BACKFILL_DAYS = 400
LIVE_RANGES = {1: 10, 6: 60, 24: 240}   # hours shown: seconds per point (the raw readings, then averages)


class ViewsMixin:
    def _rates(self):
        rates = {}
        for fuel, kind, start, end, p in self.store.query(
                'SELECT fuel, kind, start, end, p FROM home_rate ORDER BY fuel, kind, start'):
            rates.setdefault((fuel, kind), []).append((start, end, p))
        return rates

    def view(self, now, hours=1, display=False):
        if display:
            self.displayed = now
        else:
            self.viewed = now
        if not self.enabled:
            return {'enabled': False}
        hours = hours if hours in LIVE_RANGES else 1
        day, _ = uk_clock(now)
        key = (self.version, day, int(now // 60))
        cached = self.cache
        if not cached or cached[0] != key:
            cached = self.cache = (key, self._build(now, day))
        payload = dict(cached[1])
        payload['live'] = self._live(now, hours)
        payload['compare'] = self._compare(now)
        payload['sessions'] = self._sessions(now)
        payload['extra_problems'] = dict(self.extra_problem) or None
        return payload

    def _compare(self, now):
        if not self.products or not self.region:
            return None
        key = (self.data_version, uk_clock(now)[0])
        cached = self.compare_cache
        if cached and cached[0] == key:
            return cached[1]
        try:
            result = tariffs.compare(self, self.products, self.region, now)
        except Exception:   # never let the comparison break the page
            log.exception('[home] tariff comparison failed')
            result = None
        self.compare_cache = (key, result)
        return result

    def _live(self, now, hours):
        """The latest reading, and the last `hours` of readings: raw for an hour,
        averaged into one- or four-minute points for longer."""
        with self.lock:
            latest = dict(self.live) if self.live else None
        if not latest:
            return None
        step = LIVE_RANGES[hours]
        start = now - hours * 3600
        if step == 10:
            points = [[t, round(w)] for t, w in self.store.query(
                'SELECT t, w FROM home_live WHERE t >= ? ORDER BY t', (start,))]
        else:
            points = [[int(b) * step, round(w)] for b, w in self.store.query(
                'SELECT t / ?, AVG(w) FROM home_live WHERE t >= ? GROUP BY t / ? ORDER BY 1', (step, start, step))]
        latest.update({'points': points, 'hours': hours, 'step': step})
        return latest

    def _day_summaries(self, since, until=None):
        """Each UK day's electricity and gas: kWh, kg of CO2 and cost (with standing charges),
        for the half hours from `since` (and before `until`). Keyed by date."""
        until = until if until is not None else 2 ** 40
        rate_lists = self._rates()
        starts = {k: [row[0] for row in v] for k, v in rate_lists.items()}

        def rate(fuel, kind, t):
            table = rate_lists.get((fuel, kind))
            if not table:
                return None
            i = bisect_right(starts[(fuel, kind)], t) - 1
            if i < 0:
                return None
            start, end, p = table[i]
            return p if end is None or t < end else None

        days = {}

        def bucket(t):
            d = uk_clock(t)[0]
            item = days.get(d)
            if item is None:
                item = days[d] = {'e': 0.0, 'e_n': 0, 'e_ci_kwh': 0.0, 'e_kg': 0.0, 'e_cost': 0.0, 'e_cost_ok': True,
                                  'g': 0.0, 'g_n': 0, 'g_cost': 0.0, 'g_cost_ok': True, 'live': False}
            return item

        for t, kwh, src, ci in self.store.query(
                "SELECT t, kwh, src, ci FROM home WHERE fuel = 'elec' AND t >= ? AND t < ? ORDER BY t", (since, until)):
            item = bucket(t)
            item['e'] += kwh
            item['e_n'] += 1
            item['live'] = item['live'] or src == 'live'
            if ci is not None:
                item['e_ci_kwh'] += kwh
                item['e_kg'] += kwh * ci / 1000
            unit = rate('elec', 'unit', t)
            if unit is None:
                item['e_cost_ok'] = False
            else:
                item['e_cost'] += kwh * unit
        for t, kwh in self.store.query("SELECT t, kwh FROM home WHERE fuel = 'gas' AND t >= ? AND t < ? ORDER BY t", (since, until)):
            item = bucket(t)
            item['g'] += kwh
            item['g_n'] += 1
            unit = rate('gas', 'unit', t)
            if unit is None:
                item['g_cost_ok'] = False
            else:
                item['g_cost'] += kwh * unit

        def summarise(d, item):
            start = uk_midnight_utc(d)
            periods = settlement_periods(d)
            e_kg = None
            if item['e_n']:
                # Half hours without a carbon figure (rare) are counted at the day's average
                e_kg = item['e_kg'] * item['e'] / item['e_ci_kwh'] if item['e_ci_kwh'] > 0 else (0.0 if item['e'] == 0 else None)
            costs = {}
            for fuel, n, ok, value in (('elec', item['e_n'], item['e_cost_ok'], item['e_cost']),
                                       ('gas', item['g_n'], item['g_cost_ok'], item['g_cost'])):
                standing = rate(fuel, 'standing', start)
                costs[fuel] = round(value + standing, 1) if n and ok and standing is not None else None
            known = [costs[f] for f, n in (('elec', item['e_n']), ('gas', item['g_n'])) if n]
            return {
                'd': d.isoformat(), 't': start,
                'e': round(item['e'], 3) if item['e_n'] else None,
                'g': round(item['g'], 2) if item['g_n'] else None,
                'e_kg': None if e_kg is None else round(e_kg, 3),
                'g_kg': round(item['g'] * GAS_KG_PER_KWH, 3) if item['g_n'] else None,
                'e_cost': costs['elec'], 'g_cost': costs['gas'],
                'cost': round(sum(known), 1) if known and all(c is not None for c in known) else None,
                'e_full': item['e_n'] >= periods,
                'g_full': item['g_n'] >= periods,
                'live': item['live'],
            }

        return {d: summarise(d, item) for d, item in days.items()}

    def _build(self, now, today):
        # From the first of the month twelve months ago, so every month in the table is whole
        oldest = today.replace(day=1)
        for _ in range(12):
            oldest = (oldest - timedelta(days=1)).replace(day=1)
        since = uk_midnight_utc(min(oldest, today - timedelta(days=365)))
        rate_lists = self._rates()

        def rate(fuel, kind, t):
            table = [row for row in rate_lists.get((fuel, kind), []) if row[0] <= t]
            if not table:
                return None
            start, end, p = table[-1]
            return p if end is None or t < end else None

        summaries = self._day_summaries(since)
        today_start = uk_midnight_utc(today)
        timing_from = uk_midnight_utc(today - timedelta(days=30))
        today_slots = [[t, round(kwh, 3), None if ci is None else round(ci), src] for t, kwh, src, ci in self.store.query(
            "SELECT t, kwh, src, ci FROM home WHERE fuel = 'elec' AND t >= ? ORDER BY t", (today_start,))]
        weighted, weight, flat, flat_n = 0.0, 0.0, 0.0, 0
        for kwh, ci in self.store.query("SELECT kwh, ci FROM home WHERE fuel = 'elec' AND t >= ? AND t < ? AND ci IS NOT NULL",
                                        (timing_from, today_start)):
            weighted += kwh * ci
            weight += kwh
            flat += ci
            flat_n += 1
        recent = []
        for back in range(30, -1, -1):
            d = today - timedelta(days=back)
            recent.append(summaries.get(d) or {'d': d.isoformat(), 't': uk_midnight_utc(d), 'e': None, 'g': None,
                                               'e_kg': None, 'g_kg': None, 'e_cost': None, 'g_cost': None, 'cost': None, 'e_full': False, 'g_full': False,
                                               'live': False})

        # Calendar months, newest last: the current one and the twelve before it
        months = []
        first = today.replace(day=1)
        for back in range(12, -1, -1):
            year, month = first.year, first.month - back
            while month < 1:
                month += 12
                year -= 1
            start = first.replace(year=year, month=month)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            items = [s for d, s in summaries.items() if start <= d < end]
            if not items:
                continue
            months.append(self._total(start, items, (end - start).days))

        year_items = [s for d, s in summaries.items() if d > today - timedelta(days=365)]
        current = int(floor_to(now, 1800))
        grid = self.store.query('SELECT forecast, actual, idx FROM ci WHERE region = 0 AND t = ?', (current,))
        grid_ci = None
        if grid:
            grid_ci = grid[0][1] if grid[0][1] is not None else grid[0][0]
        ahead = [[int(t), round(a if a is not None else f), idx] for t, f, a, idx in self.store.query(
            'SELECT t, forecast, actual, idx FROM ci WHERE region = 0 AND t >= ? AND t < ? ORDER BY t',
            (today_start, uk_midnight_utc(today + timedelta(days=1)))) if (a if a is not None else f) is not None]

        tariffs = {}
        for fuel in ('elec', 'gas'):
            info = (self.meters or {}).get(fuel)
            if not info:
                continue
            unit, standing = rate(fuel, 'unit', now), rate(fuel, 'standing', now)
            # One price at every time of day (a fixed or flexible tariff), rather than one that changes by the half hour
            nearby = {row[2] for row in rate_lists.get((fuel, 'unit'), [])
                      if row[0] < now + 86400 and (row[1] is None or row[1] > now - 2 * 86400)}
            current_agreement = next((a for a in reversed(info['agreements'])
                                      if a['start'] <= now and (a['end'] is None or now < a['end'])), None)
            tariffs[fuel] = {
                'unit': None if unit is None else round(unit, 3),
                'standing': None if standing is None else round(standing, 3),
                'flat': len(nearby) == 1,
                'costed': bool(current_agreement and single_rate(current_agreement['tariff'])),
            }
        first_t = self.store.scalar("SELECT MIN(t) FROM home WHERE fuel = 'elec'")
        return {
            'enabled': True,
            'now': now,
            'meters': {'elec': bool(self.meters and 'elec' in self.meters),
                       'gas': bool(self.meters and 'gas' in self.meters),
                       'live': bool(self.meters and (self.meters.get('elec') or {}).get('device'))} if self.meters else None,
            'live_seconds': OCTOPUS_LIVE_SECONDS,
            'grid': {'ci': None if grid_ci is None else round(grid_ci), 'idx': grid[0][2] if grid else None},
            'today': {'d': today.isoformat(), 'start': today_start, 'slots': today_slots, 'ahead': ahead,
                      'periods': settlement_periods(today), 'total': summaries.get(today)},
            'days': recent,
            'months': months,
            'year': self._total(today - timedelta(days=364), year_items, 365) if year_items else None,
            'timing': {'yours': round(weighted / weight, 1), 'flat': round(flat / flat_n, 1), 'kwh': round(weight, 1),
                       'slots': flat_n} if weight > 0 and flat_n >= 7 * 48 else None,
            'tariffs': tariffs,
            'gas_factor': GAS_KG_PER_KWH,
            'since': first_t,
            'problem': self.problem,
        }

    @staticmethod
    def _total(start, items, length):
        def add(key):
            values = [s[key] for s in items if s[key] is not None]
            return round(sum(values), 2) if values else None
        def complete(key, n_key):
            # A total only when every day with readings has a figure
            values = [s[key] for s in items if s[n_key] is not None]
            return round(sum(values), 1) if values and all(v is not None for v in values) else None
        e_cost, g_cost = complete('e_cost', 'e'), complete('g_cost', 'g')
        has_g = any(s['g'] is not None for s in items)
        return {'d': start.isoformat(), 't': uk_midnight_utc(start), 'length': length, 'days': len(items),
                'e_days': sum(1 for s in items if s['e'] is not None), 'g_days': sum(1 for s in items if s['g'] is not None),
                'e': add('e'), 'g': add('g'), 'e_kg': add('e_kg'), 'g_kg': add('g_kg'), 'e_cost': e_cost, 'g_cost': g_cost,
                'cost': None if e_cost is None or (has_g and g_cost is None) else round(e_cost + (g_cost or 0), 1)}
