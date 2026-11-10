"""The timeline: notable moments, spotted in the stored data and kept for a year.

Each detector looks over the data it needs and upserts events with stable
ids, so running it again (after a restart, or once late data arrives) updates
an event rather than adding a second copy. Text comes as parts, like the daily
summary, so the pages can underline glossary terms.
"""

import json
import time
from datetime import date, timedelta

from .config import log
from .frequency import find_events, inertia_at, response_at
from .notices import describe_notice
from .outages import LINK_NAMES
from .reference import INTERCONNECTORS
from .store import UPSERT_EV
from .timeutil import uk_clock, uk_midnight_utc
from .units import short_unit_id
from .views_summary import FUEL_KEYS, L, T, gw, hm, money

KINDS = {
    'frequency': 'Frequency', 'outage': 'Unplanned outages', 'price': 'Prices', 'notice': 'Grid notices',
    'record': 'Records', 'clean': 'Clean power', 'demand': 'Demand', 'balancing': 'Balancing',
}
MAJOR, NOTABLE, DAILY = 3, 2, 1
REPLAY_DAYS = 7          # the map's time machine
SERVICE_NAMES = {'DCL': 'Dynamic Containment', 'DML': 'Dynamic Moderation', 'DRL': 'Dynamic Regulation',
                 'DCH': 'Dynamic Containment', 'DMH': 'Dynamic Moderation', 'DRH': 'Dynamic Regulation'}


def duration(seconds):
    if seconds is None:
        return None
    seconds = int(round(seconds / 15.0) * 15)
    if seconds < 60:
        return '%d seconds' % seconds
    minutes, rest = divmod(seconds, 60)
    if minutes >= 60:
        return '%d h %02d min' % divmod(minutes, 60)
    return '%d min' % minutes + (' %d s' % rest if rest else '')


def day_label(t):
    d = uk_clock(t)[0]
    return '%d %s %d' % (d.day, d.strftime('%b'), d.year)


def hz(value):
    return '%.2f Hz' % value


class Events:
    def __init__(self, grid):
        self.grid = grid
        self.store = grid.store
        self.first = True
        self.records_seen = None
        self.full_scan = 0
        self.oldest_reading = None
        self.outages_seen = None

    # ---- storage -----------------------------------------------------------

    def save(self, events):
        now = int(time.time())
        rows = [(e['id'], int(e['t']), e['kind'], e['level'], json.dumps(e['title'], ensure_ascii=False),
                 json.dumps(e.get('detail') or [], ensure_ascii=False), json.dumps(e.get('data') or {}, ensure_ascii=False,
                                                                                   sort_keys=True), now)
                for e in events]
        return self.store.write(UPSERT_EV, rows, history=False)

    def stored(self, kind, since):
        rows = self.store.query('SELECT id, t, data FROM ev WHERE kind = ? AND t >= ?', (kind, since))
        return [(i, t, json.loads(d or '{}')) for i, t, d in rows]

    # ---- the job -----------------------------------------------------------

    def poll(self):
        found = []
        for name, detector in (('outages', self.outage_events), ('frequency', self.frequency_events),
                               ('prices', self.price_events), ('notices', self.notice_events),
                               ('daily', self.daily_events), ('records', self.record_events),
                               ('balancing', self.constraint_events)):
            try:
                events = detector()
                found += events
                # Outages first, so frequency events can find their cause
                if name == 'outages':
                    self.save(events)
            except Exception as error:   # one detector failing shouldn't stop the others
                log.warning('[events] %s: %s: %s', name, type(error).__name__, error)
                log.debug('[events] %s traceback', name, exc_info=True)
        changed = self.save(found)
        if self.first and found:
            log.info('[events] timeline has %d events', self.store.scalar('SELECT COUNT(*) FROM ev') or 0)
        self.first = False
        return changed

    # ---- unplanned outages (REMIT) -------------------------------------------

    def outage_name(self, e):
        asset = e['asset'] or e['unit']
        if asset.startswith('I_'):
            link = asset.split('-', 1)[-1]
            return next((v for k, v in LINK_NAMES.items() if link.upper().startswith(k)), asset[2:]), None, 'imports'
        unit = e['unit'] or short_unit_id(asset)
        units = self.grid.units
        sid, cat, name = units.describe(unit, asset) if units else (None, 'other', unit)
        return name, sid, cat

    def outage_events(self):
        outages = self.grid.outages
        if not outages:
            return []
        now = time.time()
        with outages.lock:
            items = list(outages.events)
        groups = {}
        for e in items:
            if e['type'] != 'Unplanned' or e['start'] > now or e['start'] < now - 4 * 86400:
                continue
            if e['end'] is not None and e['end'] > now + 2 * 365 * 86400:
                continue
            name, sid, cat = self.outage_name(e)
            # Import and export notices for one cable describe the same fault
            key = (name, int(e['start'] // 300))
            g = groups.setdefault(key, {'name': name, 'sid': sid, 'cat': cat, 'start': e['start'], 'lost': 0.0,
                                        'units': {}, 'end': e['end'], 'cause': e['cause'], 'normal': 0.0, 'mrids': []})
            g['mrids'].append(str(e.get('mrid') or ''))
            unit_key = 'cable' if cat == 'imports' else (e['unit'] or e['asset'])
            if e['lost'] > g['units'].get(unit_key, 0):
                g['units'][unit_key] = e['lost']
            g['end'] = None if (g['end'] is None or e['end'] is None) else max(g['end'], e['end'])
        events = []
        for (name, _), g in groups.items():
            lost = sum(g['units'].values())
            if lost < 300:
                continue
            start = int(g['start'])
            title = [g['name'] + ' went offline unexpectedly, losing ' + '{:,}'.format(round(lost)) + ' MW']
            detail = []
            if g['cause']:
                detail.append('Reason given: ' + g['cause'].rstrip('.') + '. ')
            if g['end']:
                detail.append('Expected back ' + ('by ' + hm(g['end']) if uk_clock(g['end'])[0] == uk_clock(start)[0]
                                                  else 'on ' + day_label(g['end']) + ' at ' + hm(g['end'])) + '. ')
            detail.append(L('See current outages', '/outages'))
            # Keyed by the notice, so a revised start time updates the event rather than adding another
            key = min(m for m in g['mrids']) or '%s-%d' % (name.lower().replace(' ', '-')[:40], start)
            events.append({'id': 'out-' + key[:60], 't': start, 'kind': 'outage',
                           'level': MAJOR if lost >= 1000 else NOTABLE, 'title': title, 'detail': detail,
                           'data': {'name': g['name'], 'sid': g['sid'], 'cat': g['cat'], 'lost': round(lost),
                                    'end': g['end'], 'cause': g['cause']}})
        return events

    # ---- frequency -------------------------------------------------------------

    def frequency_events(self):
        now = time.time()
        # Rescan everything hourly, and whenever older readings arrive (the backfill)
        oldest = self.store.scalar('SELECT MIN(t) FROM freq')
        outages = self.grid.outages.version if self.grid.outages else None
        full = now - self.full_scan > 3600 or oldest != self.oldest_reading or outages != self.outages_seen
        self.oldest_reading, self.outages_seen = oldest, outages
        since = now - (9 * 86400 if full else 6 * 3600)
        if full:
            self.full_scan = now
        readings = self.store.query('SELECT t, hz FROM freq WHERE t >= ? ORDER BY t', (since,))
        latest = readings[-1][0] if readings else now
        events = []
        for f in find_events(readings):
            if f['start'] > latest - 120:     # wait until the recovery has started to show
                continue
            events.append(self.frequency_event(f, readings))
        if full and readings:
            # Drop events the detector no longer finds in the same readings (after a rule change, say)
            keep = {e['id'] for e in events}
            lo, hi = readings[0][0] + 900, latest - 900
            stale = [(i,) for i, t, _ in self.stored('frequency', lo) if t <= hi and i not in keep]
            if stale:
                self.store.write('DELETE FROM ev WHERE id = ?', stale, history=False)
        return events

    def causes(self, f):
        """Unplanned outages and interconnector drops that line up with an excursion."""
        start, low = f['start'], f['direction'] == 'low'
        found = []
        if low:
            for _, t, data in self.stored('outage', start - 900):
                if start - 600 <= t <= start + 900:
                    found.append({'name': data.get('name'), 'mw': data.get('lost'), 't': t, 'how': 'outage'})
            outages = self.grid.outages
            if outages:
                with outages.lock:
                    items = list(outages.events)
                for e in items:
                    if e['type'] == 'Unplanned' and start - 600 <= e['start'] <= start + 900 and e['lost'] >= 100:
                        name = self.outage_name(e)[0]
                        if not any(c['name'] == name for c in found):
                            found.append({'name': name, 'mw': round(e['lost']), 't': int(e['start']), 'how': 'outage'})
        # Interconnectors: compare the five-minute readings either side of the start
        rows = self.store.query("SELECT t, fuel, mw FROM gen WHERE fuel LIKE 'INT%' AND t >= ? AND t <= ? ORDER BY t",
                                (start - 600, start + 900))
        flows = {}
        for t, fuel, mw in rows:
            flows.setdefault(fuel, []).append((t, mw))
        for code, series in flows.items():
            before = [mw for t, mw in series if t <= start]
            after = [mw for t, mw in series if t >= start + 60]
            if not before or not after:
                continue
            change = min(after[:2]) - before[-1] if low else max(after[:2]) - before[-1]
            if (low and change <= -300) or (not low and change >= 300):
                name = INTERCONNECTORS.get(code, (code,))[0]
                found.append({'name': name, 'mw': round(abs(change)), 't': start, 'how': 'interconnector'})
        found.sort(key=lambda c: -(c['mw'] or 0))
        return found[:3]

    def frequency_event(self, f, readings):
        start, low = f['start'], f['direction'] == 'low'
        series = [[int(t), round(v, 3)] for t, v in readings if start - 300 <= t <= start + 900]
        causes = self.causes(f)
        held = response_at(self.store, start)
        inertia = inertia_at(self.store, start)
        verb = 'dipped' if low else 'jumped'
        title = [T('Frequency', 'frequency'), ' %s to %s' % (verb, hz(f['peak']))]
        detail = ['It %s from %s to %s (the lowest 15-second reading) in about %s'
                  % ('fell' if low else 'rose', hz(f['pre']), hz(f['peak']), duration(max(f['to_peak'], 15)))
                  if low else
                  'It rose from %s to %s (the highest 15-second reading) in about %s'
                  % (hz(f['pre']), hz(f['peak']), duration(max(f['to_peak'], 15)))]
        if f['recovery'] is not None:
            detail.append(', and was back to normal after ' + duration(f['recovery']) + '. ')
        else:
            detail.append('. ')
        if f['outside']:
            detail += ['That is outside the ±0.2 Hz ', T('operating limits', 'frequency'), '. ']
        if causes:
            c = causes[0]
            if c['how'] == 'interconnector':
                detail += ['Likely cause: ', T('imports', 'interconnector') if low else 'exports',
                           ' on %s %s by %s MW. ' % (c['name'], 'fell' if low else 'stopped', '{:,}'.format(c['mw']))]
            else:
                detail += ['Likely cause: %s went offline (%s MW, ' % (c['name'], '{:,}'.format(c['mw'])),
                           T('unplanned outage notice', 'remit'), '). ']
        key = 'DCL' if low else 'DCH'
        if held.get(key):
            detail += ['NESO held ' + '{:,}'.format(held[key]) + ' MW of ', T(SERVICE_NAMES[key], 'dynamic-containment'),
                       ' (%s) at the time.' % ('low' if low else 'high')]
        data = dict(f)
        data.update({'series': series, 'causes': causes, 'held': held, 'inertia': inertia})
        return {'id': 'freq-%d' % start, 't': start, 'kind': 'frequency', 'level': f['level'],
                'title': title, 'detail': detail, 'data': data}

    # ---- prices ---------------------------------------------------------------

    def price_events(self):
        rows = self.store.query("SELECT t, v FROM hh WHERE k = 'price' AND v IS NOT NULL ORDER BY t")
        events = []

        def runs(test):
            run = []
            for t, v in rows:
                if run and t - run[-1][0] > 1800:
                    yield run
                    run = []
                if test(v):
                    run.append((t, v))
                elif run:
                    yield run
                    run = []
            if run:
                yield run

        for run in runs(lambda v: v < 0):
            start, end = run[0][0], run[-1][0] + 1800
            low_t, low_v = min(run, key=lambda p: p[1])
            hours = (end - start) / 3600.0
            span = 'half an hour' if hours < 1 else 'an hour' if hours == 1 else '%g hours' % hours
            title = [T('Prices went negative', 'negative-price'), ' for ' + span]
            detail = ['The lowest was £%.2f/MWh at %s. Generators were paying to keep running, usually because wind '
                      'and solar were more than Britain could use or export.' % (low_v, hm(low_t))]
            events.append({'id': 'neg-%d' % start, 't': start, 'kind': 'price', 'level': NOTABLE,
                           'title': title, 'detail': detail, 'data': {'end': end, 'low': low_v, 'low_t': low_t}})
        for run in runs(lambda v: v >= 200):
            start, end = run[0][0], run[-1][0] + 1800
            high_t, high_v = max(run, key=lambda p: p[1])
            title = [T('Wholesale price', 'wholesale-price'), ' reached £%d/MWh' % round(high_v)]
            detail = ['At %s, more than double a typical price. Spikes like this usually mean supply was tight.' % hm(high_t)]
            events.append({'id': 'spike-%d' % start, 't': high_t, 'kind': 'price', 'level': MAJOR if high_v >= 500 else NOTABLE,
                           'title': title, 'detail': detail, 'data': {'end': end, 'high': high_v}})
        return events

    # ---- grid notices ---------------------------------------------------------

    def notice_events(self):
        rows = self.store.query('SELECT t, kind, body FROM warn WHERE t >= ? ORDER BY t', (time.time() - 400 * 86400,))
        events = []
        for t, kind, body in rows:
            n = describe_notice(t, kind, body, now=t)
            if n['routine']:
                continue
            title = [('Cancelled: ' if n['cancelled'] else 'NESO issued '),
                     T(n['title'] if n['cancelled'] else article_lower(n['title']), 'dfs' if 'Flexibility' in n['title'] else 'margin-notice')]
            detail = [n['summary'] + ' ', L('Read the notices', '/outages')]
            events.append({'id': 'notice-%s' % n['id'], 't': int(t), 'kind': 'notice',
                           'level': MAJOR if n['level'] == 'strong' else NOTABLE if not n['cancelled'] else DAILY,
                           'title': title, 'detail': detail, 'data': {'kind': kind, 'start': n['start'], 'end': n['end']}})
        return events

    # ---- daily markers from the half-hourly history ----------------------------

    def daily_events(self):
        hist = self.grid.history('7d')
        times, series, demand, carbon = hist['t'], hist['series'], hist['demand'], hist['carbon']
        if not times:
            return []
        now = time.time()
        today = uk_clock(now)[0]
        events = []
        first_day = uk_clock(times[0])[0]
        day = first_day
        while day <= today:
            start, end = uk_midnight_utc(day), uk_midnight_utc(day + timedelta(days=1))
            idx = [i for i, t in enumerate(times) if start <= t < end]
            day = day + timedelta(days=1)
            if not idx:
                continue
            complete = end <= now and len(idx) >= 44
            # Peak demand, once the day is over
            peaks = [(demand[i], times[i]) for i in idx if demand[i] is not None]
            if complete and len(peaks) >= 40:
                value, t = max(peaks)
                events.append({'id': 'peak-%s' % uk_clock(start)[0].isoformat(), 't': t, 'kind': 'demand', 'level': DAILY,
                               'title': [T('Demand', 'demand'), ' peaked at %s GW' % gw(value)],
                               'detail': ['The highest half hour of the day.'], 'data': {'gw': value}})
            # Wind above half of supply, the first time each day
            for i in idx:
                total = sum(max(0.0, series[k][i] or 0.0) for k in FUEL_KEYS)
                wind = series['wind'][i] or 0.0
                if total > 5 and wind / total >= 0.5:
                    events.append({'id': 'wind50-%s' % uk_clock(start)[0].isoformat(), 't': times[i], 'kind': 'clean',
                                   'level': DAILY, 'title': ['Wind supplied over half of Britain’s electricity'],
                                   'detail': ['%s GW of %s GW, the first half hour above 50%% today.' % (gw(wind), gw(total))
                                              if uk_clock(start)[0] == today else
                                              '%s GW of %s GW, the first half hour above 50%% that day.' % (gw(wind), gw(total))],
                                   'data': {'share': round(wind / total, 3)}})
                    break
            # Carbon intensity under 50 g/kWh, the first time each day
            for i in idx:
                if carbon[i] is not None and carbon[i] < 50:
                    events.append({'id': 'ci50-%s' % uk_clock(start)[0].isoformat(), 't': times[i], 'kind': 'clean',
                                   'level': DAILY, 'title': [T('Carbon intensity', 'carbon-intensity'), ' fell below 50 g/kWh'],
                                   'detail': ['%d g/kWh at %s.' % (carbon[i], hm(times[i]))], 'data': {'ci': carbon[i]}})
                    break
        return events

    # ---- records in the long-term history --------------------------------------

    RECORDS = [
        ('wind', 'Most wind power on record', 'COALESCE(wind, 0) + COALESCE(wind_emb, 0)', 'high', 'GW'),
        ('solar', 'Most solar power on record', 'solar', 'high', 'GW'),
        ('ci', 'Cleanest half hour on record', 'ci', 'low', 'g/kWh'),
    ]

    def record_events(self):
        """New all-time records in NESO's history since 2009, set in the last year."""
        extent = self.grid.longterm_extent() if hasattr(self.grid, 'longterm_extent') else {}
        if not extent.get('complete'):
            return []
        last = self.store.scalar('SELECT MAX(t) FROM lh')
        if last is None or last == self.records_seen:
            return []
        cutoff = time.time() - 365 * 86400
        events = []
        for key, title, expr, better, unit in self.RECORDS:
            where = 'WHERE ci > 0' if key == 'ci' else ''
            best_row = None
            for t, v in self.store.query('SELECT t, %s FROM lh %s ORDER BY t' % (expr, where)):
                if v is None:
                    continue
                if best_row is None or (v > best_row[1] if better == 'high' else v < best_row[1]):
                    if best_row is not None and t >= cutoff:
                        value = '%s GW' % gw(v / 1000.0) if unit == 'GW' else '%d g/kWh' % round(v)
                        before = '%s GW' % gw(best_row[1] / 1000.0) if unit == 'GW' else '%d g/kWh' % round(best_row[1])
                        events.append({'id': 'rec-%s-%d' % (key, t), 't': t, 'kind': 'record', 'level': MAJOR,
                                       'title': [title + ': ' + value],
                                       'detail': ['Beating %s on %s, in NESO’s records since 2009. ' % (before, day_label(best_row[0])),
                                                  L('See the records', '/history')],
                                       'data': {'value': v, 'previous': best_row[1], 'previous_t': best_row[0]}})
                    best_row = (t, v)
        self.records_seen = last
        # A record broken again within the day: keep only the final one
        final = []
        for e in events:
            key = e['id'].split('-')[1]
            if not any(o['id'].split('-')[1] == key and e['t'] < o['t'] <= e['t'] + 86400 for o in events):
                final.append(e)
        stale = [e['id'] for e in events if e not in final]
        if stale:
            self.store.write('DELETE FROM ev WHERE id = ?', [(i,) for i in stale], history=False)
        return final

    # ---- balancing: expensive constraint days -----------------------------------

    def constraint_events(self):
        bal = self.grid.bal
        if not bal:
            return []
        with bal.lock:
            days = list(bal.constraints)
        if len(days) < 14:
            return []
        costs = sorted(d[1] for d in days)
        median = costs[len(costs) // 2]
        threshold = max(5e6, 2 * median)
        events = []
        for day, cost, volume, parts in days:
            if cost < threshold:
                continue
            try:
                t = uk_midnight_utc(date.fromisoformat(day)) + 12 * 3600
            except ValueError:
                continue
            top = max(parts.items(), key=lambda kv: kv[1])[0] if parts else None
            detail = ['About %s times a typical day. ' % ('%.0f' % (cost / median) if median else 'several')]
            if top:
                detail.append('Most of it was for %s. ' % top.lower())
            detail.append(L('See balancing costs', '/balancing'))
            events.append({'id': 'constraint-%s' % day, 't': t, 'kind': 'balancing', 'level': NOTABLE,
                           'title': [T('Constraint costs', 'constraint-costs'), ' reached %s' % money(cost)],
                           'detail': detail, 'data': {'cost': cost, 'volume': volume}})
        return events


def article_lower(title):
    if 'Flexibility' in title:
        return 'a Demand Flexibility Service event'
    text = title if title.istitle() else title[:1].lower() + title[1:]
    return ('an ' if text[:1].lower() in 'aeiou' else 'a ') + text


def event_links(e, now=None, replay_from=None, history_to=None, station_link=True):
    """Where to see an event: the map's time machine while it has frames for
    that moment (up to a week), else History."""
    now = time.time() if now is None else now
    t = e['t']
    links = []
    if e['kind'] == 'frequency':
        links.append({'t': 'Frequency detail', 'href': '/frequency?event=' + e['id']})
    earliest = max(now - REPLAY_DAYS * 86400 + 900, replay_from or now)
    if earliest <= t <= now:
        links.append({'t': 'See it on the map', 'href': '/map?at=%d' % t})
    elif e['kind'] != 'notice' and history_to and t <= history_to + 1800:
        links.append({'t': 'See it in History', 'href': '/history?at=%d' % t})
    if station_link and e.get('sid'):
        links.append({'t': 'About this station', 'href': '/station?id=%d' % e['sid']})
    return links
