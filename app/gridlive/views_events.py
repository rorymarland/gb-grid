"""API views: /api/events (the timeline) and /api/frequency."""

import json
import time

from .events import KINDS, event_links
from .frequency import frequency_stats, histogram, inertia_at, response_at
from .timeutil import uk_clock, uk_midnight_utc

FREQ_RANGES = {'1h': 3600, '6h': 6 * 3600, '24h': 86400, '7d': 7 * 86400}


class EventViews:
    """Mixed into Grid."""

    def events_payload(self, days=30, kinds=None, level=1, limit=400):
        days = max(1, min(int(days), 400))
        now = time.time()
        rows = self.store.query('SELECT id, t, kind, level, title, detail, data FROM ev WHERE t >= ? AND t <= ? '
                                'ORDER BY t DESC', (now - days * 86400, now + 3600))
        counts = {k: 0 for k in KINDS}
        replay_from = self.store.scalar('SELECT MIN(t) FROM st')
        history_to = self.store.scalar('SELECT MAX(t) FROM lh')
        events = []
        for eid, t, kind, lvl, title, detail, data in rows:
            counts[kind] = counts.get(kind, 0) + 1
            if (kinds and kind not in kinds) or lvl < level or len(events) >= limit:
                continue
            e = {'id': eid, 't': t, 'kind': kind, 'level': lvl, 'title': json.loads(title), 'detail': json.loads(detail or '[]')}
            d = json.loads(data or '{}')
            if kind == 'frequency':
                e['freq'] = {k: d.get(k) for k in ('pre', 'peak', 'peak_t', 'change', 'recovery', 'direction', 'series')}
            e['links'] = event_links(e, now, replay_from, history_to)
            events.append(e)
        return {'days': days, 'kinds': KINDS, 'counts': counts, 'events': events, 'generated': int(now)}

    def frequency_payload(self, range_key='1h', event=None):
        range_key = range_key if range_key in FREQ_RANGES else '1h'
        version = (self.store.version, range_key, event, int(time.time() // 15))
        return self.cached(('frequency', range_key, event), version, lambda: self._build_frequency(range_key, event))

    def _build_frequency(self, range_key, event_id):
        now = time.time()
        span = FREQ_RANGES[range_key]
        latest_t = self.store.scalar('SELECT MAX(t) FROM freq')
        end = latest_t or now
        rows = self.store.query('SELECT t, hz FROM freq WHERE t >= ? ORDER BY t', (end - max(span, 86400),))
        window = [r for r in rows if r[0] > end - span]
        if span > 86400:
            # Five-minute buckets: lowest, average and highest
            buckets = {}
            for t, v in self.store.query('SELECT t, hz FROM freq WHERE t >= ? ORDER BY t', (end - span,)):
                buckets.setdefault(int(t // 300) * 300, []).append(v)
            series = {'t': sorted(buckets), 'step': 300}
            series['min'] = [round(min(buckets[t]), 3) for t in series['t']]
            series['mean'] = [round(sum(buckets[t]) / len(buckets[t]), 3) for t in series['t']]
            series['max'] = [round(max(buckets[t]), 3) for t in series['t']]
        else:
            series = {'t': [int(t) for t, _ in window], 'hz': [round(v, 3) for _, v in window], 'step': 15}
        today_start = uk_midnight_utc(uk_clock(now)[0])
        today = [r for r in rows if r[0] >= today_start]
        last24 = [r for r in rows if r[0] > end - 86400]
        latest = {'t': int(rows[-1][0]), 'hz': round(rows[-1][1], 3)} if rows else None
        # Inertia: the latest published half hour, and the last week
        inertia_rows = self.store.query("SELECT t, v FROM hh WHERE k = 'inertia' AND t >= ? ORDER BY t", (now - 8 * 86400,))
        market = dict(self.store.query("SELECT t, v FROM hh WHERE k = 'inertia_mkt' AND t >= ?", (now - 8 * 86400,)))
        inertia = None
        if inertia_rows:
            t, v = inertia_rows[-1]
            inertia = {'t': t, 'gvas': round(v), 'market': round(market[t]) if market.get(t) is not None else None,
                       'series': {'t': [r[0] for r in inertia_rows], 'v': [round(r[1]) for r in inertia_rows]},
                       'low': min(round(r[1]) for r in inertia_rows), 'high': max(round(r[1]) for r in inertia_rows)}
        held = response_at(self.store, now)
        window_row = self.store.query('SELECT MIN(start), MAX(end) FROM resp WHERE start <= ? AND end > ?', (now, now))
        response = {'held': held, 'from': window_row[0][0] if window_row else None, 'to': window_row[0][1] if window_row else None,
                    'checked': self.store.scalar('SELECT MAX(end) FROM resp')}
        events = self.events_payload(days=8, kinds=['frequency'], limit=30)['events']
        selected = None
        if event_id:
            row = self.store.query("SELECT id, t, kind, level, title, detail, data FROM ev WHERE id = ? AND kind = 'frequency'", (event_id,))
            if row:
                eid, t, kind, lvl, title, detail, data = row[0]
                d = json.loads(data or '{}')
                if d.get('inertia') is None:
                    d['inertia'] = inertia_at(self.store, t)
                if not d.get('held'):
                    d['held'] = response_at(self.store, t)
                selected = {'id': eid, 't': t, 'level': lvl, 'title': json.loads(title), 'detail': json.loads(detail or '[]'),
                            'data': d, 'links': event_links({'id': eid, 't': t, 'kind': kind}, now,
                                                                self.store.scalar('SELECT MIN(t) FROM st'),
                                                                self.store.scalar('SELECT MAX(t) FROM lh'))}
        return {
            'range': range_key, 'latest': latest, 'series': series,
            'today': frequency_stats(today), 'last24': frequency_stats(last24), 'histogram': histogram(last24),
            'inertia': inertia, 'response': response, 'events': events, 'event': selected, 'generated': int(now),
        }
