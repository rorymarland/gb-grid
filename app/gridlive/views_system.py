"""API views: regions, balancing, alerts and outages."""

import threading
import time
from datetime import timedelta

from .config import REGION_ID
from .reference import INDEX_LEVELS, MIX_FUELS, PES_REGIONS, REGION_NAMES
from .imbalance import day_rows as imbalance_rows, day_summary as imbalance_summary
from .timeutil import floor_to, settlement_periods, settlement_start
from .units import settlement_period_at, short_unit_id
from .outages import LINK_NAMES, margin_level
from .notices import describe_notice


class SystemViews:
    """Mixed into Grid; uses its store, models and cache."""

    # ---- /api/regions ----------------------------------------------------

    def regions(self):
        return self.cached(('regions',), (self.store.version, int(time.time() // 300)), self._build_regions)

    def _build_regions(self):
        now = time.time()
        current = floor_to(now, 1800)
        start, end = current - 86400, current + 48 * 3600
        times = list(range(start, end, 1800))
        index = {t: i for i, t in enumerate(times)}
        values, levels, mixes = {}, {}, {}
        for t, region, value, idx, mix in self.store.query(
                'SELECT t, region, v, idx, mix FROM reg WHERE t >= ? AND t < ? ORDER BY t', (start, end)):
            i = index.get(t)
            if i is None:
                continue
            key = str(region)
            if key not in values:
                values[key], levels[key], mixes[key] = [None] * len(times), [None] * len(times), [None] * len(times)
            values[key][i] = None if value is None else int(round(value))
            levels[key][i] = INDEX_LEVELS.index(idx) if idx in INDEX_LEVELS else None
            mixes[key][i] = mix
        solar = {}
        keys = ['rpv%d' % pes for pes in sorted(PES_REGIONS)]
        for key, t, value in self.store.query(
                'SELECT k, t, v FROM hh WHERE k IN (%s) AND t >= ? AND t < ?' % ','.join('?' * len(keys)),
                (*keys, start, end)):
            i = index.get(t)
            if i is not None:
                series = solar.setdefault(str(PES_REGIONS[int(key[3:])]), [None] * len(times))
                series[i] = int(round(value))
        return {
            't0': start, 'step': 1800, 'n': len(times), 'now': index.get(current), 'home': REGION_ID or None,
            'names': {str(k): v for k, v in REGION_NAMES.items()}, 'fuels': MIX_FUELS, 'levels': INDEX_LEVELS,
            'ci': values, 'index': levels, 'mix': mixes, 'solar': solar, 'generated': int(now),
        }

    # ---- /api/balancing ----------------------------------------------------

    def balancing(self):
        key = (self.store.version, self.units.version if self.units else 0, self.bal.version if self.bal else 0)
        return self.cached(('balancing',), key, self._build_balancing)

    def _build_balancing(self):
        now = time.time()
        day, period = settlement_period_at(now)
        names = {}

        def describe(unit, elexon=''):
            if unit not in names:
                names[unit] = self.units.describe(unit, elexon) if self.units else (None, 'other', unit)
            return names[unit]

        def place(sid):
            station = self.fleet.stations.get(sid) if (self.fleet and sid is not None) else None
            return (round(station['x'], 1), round(station['y'], 1)) if station else (None, None)

        # Now: units following instructions, priced at their bid or offer for this half hour
        snapshot = self.units.snapshot if self.units else None
        with self.bal.lock if self.bal else threading.Lock():
            prices = dict(self.bal.prices) if self.bal else {}
        live, cost, priced = [], 0.0, 0
        for item in (snapshot or {}).get('instructions', []):
            sid, cat, name = describe(item['unit'], item['elexon'])
            bid, offer = prices.get(item['unit'], (None, None))
            price = offer if item['delta'] > 0 else bid
            if price is not None:
                cost += item['delta'] * price
                priced += 1
            x, y = place(sid)
            live.append({'unit': item['unit'], 'sid': sid, 'name': name, 'cat': cat, 'delta': item['delta'],
                         'price': price, 'x': x, 'y': y})
        live.sort(key=lambda u: -abs(u['delta']))

        # Finished half hours, yesterday and today (UK days)
        days = [day - timedelta(days=1), day]
        fetched = {(d, sp) for d, sp in self.store.query('SELECT d, sp FROM balp WHERE d >= ?', (days[0].isoformat(),))}
        totals, top = {}, {}
        for d, sp, unit, elexon, vb, vo, cb, co in self.store.query(
                'SELECT d, sp, unit, elexon, vol_bid, vol_offer, cash_bid, cash_offer FROM bal WHERE d >= ?',
                (days[0].isoformat(),)):
            sid, cat, name = describe(unit, elexon or '')
            p = totals.setdefault((d, sp), [0.0] * 6)
            p[0] += vb or 0.0
            p[1] += vo or 0.0
            p[2] += cb or 0.0
            p[3] += co or 0.0
            if cat == 'wind':
                p[4] += vb or 0.0
                p[5] += cb or 0.0
            if d == day.isoformat():
                u = top.setdefault(unit, {'unit': unit, 'sid': sid, 'name': name, 'cat': cat, 'down': 0.0, 'up': 0.0, 'paid': 0.0})
                u['down'] += vb or 0.0
                u['up'] += vo or 0.0
                u['paid'] += (cb or 0.0) + (co or 0.0)
        series = {}
        for d in days:
            rows = []
            for sp in range(1, settlement_periods(d) + 1):
                p = totals.get((d.isoformat(), sp))
                if p is None and (d.isoformat(), sp) in fetched:
                    p = [0.0] * 6
                rows.append([settlement_start(d, sp)] + ([round(v, 1) for v in p] if p else [None] * 6))
            series['today' if d == day else 'yesterday'] = rows
        today = [r for r in series['today'] if r[1] is not None]
        summary = {
            'periods': len(today),
            'down_mwh': round(-sum(r[1] for r in today)), 'up_mwh': round(sum(r[2] for r in today)),
            'paid': round(sum(r[3] + r[4] for r in today)), 'wind_down_mwh': round(-sum(r[5] for r in today)),
            'wind_paid': round(sum(r[6] for r in today)),
        }
        ranked = sorted(top.values(), key=lambda u: -u['paid'])
        for u in ranked:
            u['down'], u['up'], u['paid'] = round(-u['down'], 1), round(u['up'], 1), round(u['paid'])
        return {
            'now': {'time': (snapshot or {}).get('at'), 'period': period, 'units': live[:200],
                    'down': round(-sum(u['delta'] for u in live if u['delta'] < 0)),
                    'up': round(sum(u['delta'] for u in live if u['delta'] > 0)),
                    'cost_hour': round(cost) if priced else None, 'priced': priced},
            'series': series, 'columns': ['t', 'bid_mwh', 'offer_mwh', 'bid_paid', 'offer_paid', 'wind_bid_mwh', 'wind_bid_paid'],
            'today': summary, 'top': ranked[:15], 'paid_units': len(ranked),
            'constraints': [{'d': c[0], 'cost': c[1], 'volume': c[2], 'parts': c[3]} for c in (self.bal.constraints if self.bal else [])],
            # Imbalance prices and NESO's trades outside the Balancing Mechanism, yesterday and today
            'imbalance': {('today' if d == day else 'yesterday'): {'rows': imbalance_rows(self.store, d), 'summary': imbalance_summary(self.store, d)}
                          for d in days},
            'generated': int(now),
        }

    # ---- /api/alerts: grid notices in force now --------------------------

    def notices(self, days=30):
        rows = self.store.query('SELECT t, kind, body FROM warn WHERE t >= ? ORDER BY t', (time.time() - days * 86400,))
        return [describe_notice(t, kind, body) for t, kind, body in rows]

    def alerts(self):
        now = time.time()
        latest = {}
        for notice in self.notices(7):
            if notice['routine'] or notice['kind'] == 'OTHER':
                continue
            latest[notice['kind']] = notice  # oldest first, so the latest message per kind wins
        active = []
        for notice in latest.values():
            if notice['cancelled']:
                continue
            hours = 24 if notice['kind'] == 'CAPACITY MARKET NOTICE' else 12
            end = notice['end'] or max(notice['start'] or 0, notice['published']) + hours * 3600
            if now <= end + 900:
                active.append(notice)
        active.sort(key=lambda n: (n['level'] != 'strong', -n['published']))
        return {'active': active, 'margin': self.margin_summary(), 'generated': int(now)}

    def margin_summary(self):
        if not self.outages:
            return None
        with self.outages.lock:
            series = list(self.outages.margin)
        now = time.time()
        series = [row for row in series if row[0] + 1800 > now]
        if not series:
            return None
        first, low = series[0], min(series, key=lambda row: row[1])
        key, label = margin_level(low[1])
        return {'t': first[0], 'margin': first[1], 'lolp': first[2], 'min': low[1], 'min_t': low[0],
                'lolp_max': max(row[2] for row in series), 'level': key, 'label': label,
                'series': [[t, m, round(p, 4)] for t, m, p in series]}

    # ---- /api/outages -------------------------------------------------------

    def outages_payload(self):
        key = (self.outages.version if self.outages else 0, self.units.version if self.units else 0, int(time.time() // 60))
        return self.cached(('outages',), key, self._build_outages)

    def _build_outages(self):
        now = time.time()
        with self.outages.lock if self.outages else threading.Lock():
            events = list(self.outages.events) if self.outages else []
            checked = self.outages.checked if self.outages else 0
        snapshot = self.units.snapshot if self.units else None
        live = (snapshot or {}).get('stations', {})
        groups, closed = {}, {}
        for e in events:
            asset = e['asset'] or e['unit']
            if e['end'] is not None and e['end'] > now + 2 * 365 * 86400 and e['start'] <= now:
                # Out for years: a closure or mothballing rather than an outage
                closed[e['unit'] or asset] = max(closed.get(e['unit'] or asset, 0), e['lost'])
                continue
            if asset.startswith('I_'):
                link = asset.split('-', 1)[-1]
                name = next((v for k, v in LINK_NAMES.items() if link.upper().startswith(k)), asset[2:])
                key, sid, cat = 'link:' + name, None, 'imports'
            else:
                unit = e['unit'] or short_unit_id(asset)
                sid, cat, name = self.units.describe(unit, asset) if self.units else (None, 'other', unit)
                key = 'st:%s' % sid if sid is not None else 'unit:' + unit
            current = e['start'] <= now
            g = groups.setdefault((key, current), {'name': name, 'sid': sid, 'cat': cat, 'per_unit': {}, 'units': set(),
                                                    'start': e['start'], 'end': e['end'], 'type': set(), 'cause': set()})
            # Import and export notices describe one cable; overlapping notices for one unit don't add up
            unit_key = 'cable' if key.startswith('link:') else (e['unit'] or asset)
            best = g['per_unit'].get(unit_key)
            if best is None or e['lost'] > best[0]:
                g['per_unit'][unit_key] = (e['lost'], e['normal'] or 0.0)
            g['units'].add(e['unit'] or asset)
            g['start'] = min(g['start'], e['start'])
            g['end'] = None if (g['end'] is None or e['end'] is None) else max(g['end'], e['end'])
            if e['type']:
                g['type'].add(e['type'])
            if e['cause']:
                g['cause'].add(e['cause'])
        offline, upcoming = [], []
        for (key, current), g in groups.items():
            lost = sum(v[0] for v in g['per_unit'].values())
            normal = sum(v[1] for v in g['per_unit'].values())
            item = {'name': g['name'], 'sid': g['sid'], 'cat': g['cat'], 'lost': round(lost), 'normal': round(normal) or None,
                    'units': len(g['per_unit']), 'start': g['start'], 'end': g['end'], 'planned': g['type'] == {'Planned'},
                    'cause': '; '.join(sorted(g['cause']))[:120], 'mw': round(live[g['sid']]['mw']) if g['sid'] in live else None}
            (offline if current else upcoming).append(item)
        offline.sort(key=lambda i: -i['lost'])
        upcoming.sort(key=lambda i: i['start'])
        lost_by_cat = {}
        for item in offline:
            lost_by_cat[item['cat']] = lost_by_cat.get(item['cat'], 0) + item['lost']
        nuclear = []
        if self.fleet:
            outage_by_sid = {i['sid']: i for i in offline if i['sid'] is not None}
            for sid, st in self.fleet.stations.items():
                if st['cat'] != 'nuclear' or not st.get('capacity'):
                    continue
                o = outage_by_sid.get(sid)
                nuclear.append({'sid': sid, 'name': st['name'], 'capacity': round(st['capacity']),
                                'mw': round(live[sid]['mw']) if sid in live else None,
                                'out': o['lost'] if o else 0, 'end': o['end'] if o else None, 'cause': o['cause'] if o else '',
                                'planned': o['planned'] if o else None})
            nuclear.sort(key=lambda n: -n['capacity'])
        notices = [n for n in self.notices(30) if not n['routine']]
        notices.sort(key=lambda n: -n['published'])
        return {'margin': self.margin_summary(), 'offline': offline[:60], 'offline_count': len(offline),
                'closed_count': len(closed), 'closed_mw': round(sum(closed.values())),
                'upcoming': upcoming[:40], 'lost_by_cat': lost_by_cat, 'lost_total': round(sum(lost_by_cat.values())),
                'nuclear': nuclear, 'notices': notices[:30], 'checked': int(checked) or None, 'generated': int(now)}
