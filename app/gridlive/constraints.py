"""Grid constraints: where the transmission network runs out of room, and what it costs.

All from NESO's data portal, without a key:

  day-ahead-constraint-flows-and-limits  forecast flow and limit for each constraint
                                         group, every half hour, made the day before
  thermal-constraint-costs               what each constraint group cost, each day
                                         (a file per year, April to March)
  constraint-breakdown                   each day's constraint costs by reason
  wind-bmu-boa-volumes                   wind farms turned down, MW in each half hour
                                         (a file per year, April to March)

Half-hourly flows are kept for RAW_DAYS; older days are kept as one row per
group per day (bday), which is all the history pages need. NESO's SQL
interface does the adding up, so a year of history is a few requests.
"""

import json
import time
import urllib.parse
from datetime import date, timedelta

from . import fetch
from .config import NESO, log
from .fetch import FetchError
from .timeutil import local_epoch, settlement_start, uk_clock

RAW_DAYS = 21                 # half-hourly flows kept: three weeks
HISTORY_DAYS = 400            # daily summaries and costs fetched back this far
NEAR = 0.9                    # a boundary counts as near its limit at 90%
SQL_ROWS = 32000              # NESO's most rows per SQL request

# What NESO's constraint group codes mean, where it's clear. NESO doesn't publish
# descriptions; these follow its network diagrams and published order (north to
# south: SSE-SP, SCOTEX, SSHARN, FLOWSTH, SEIMP). 'boundary' is the ETYS boundary
# drawn on the map, only where the match is certain.
GROUPS = {
    'SSE_SP': ('North of Scotland to the Central Belt', 'B4'),
    'SSE_SP2': ('North of Scotland to the Central Belt (second limit)', 'B4'),
    'SCOTEX': ('Scotland to England', 'B6'),
    'SSHARN': ('South of Scotland and the north of England', None),
    'SSHARN3': ('South of Scotland and the north of England (third limit)', None),
    'FLOWSTH': ('Flow south through England', None),
    'SEIMP': ('Into the South East', None),
    'SWALEX': ('Out of South Wales', None),
    'GALLEX': ('Out of Galloway', None),
    'ERROEX': ('Out of Errochty, Perthshire', None),
}


def code(value):
    """NESO writes some codes two ways across its datasets (SSE-SP and SSE_SP)."""
    return str(value).strip().upper().replace('-', '_').replace(' ', '_')


def label(code):
    """(description, ETYS boundary) for a constraint group, or (None, None) where NESO's code is all there is."""
    return GROUPS.get(code, (None, None))


def sql(query, timeout=120):
    data = fetch.get_json(NESO + '/api/3/action/datastore_search_sql?sql=' + urllib.parse.quote(query), timeout=timeout)
    result = (data or {}).get('result') or {}
    if not result and (data or {}).get('error'):
        raise FetchError('NESO SQL: ' + json.dumps(data['error'])[:200])
    return result.get('records') or []


def resources(package, test=None):
    """The package's datastore resources, oldest first."""
    result = fetch.get_json(NESO + '/api/3/action/package_show?id=' + package, timeout=60).get('result') or {}
    found = [r for r in result.get('resources') or [] if r.get('datastore_active') and (test is None or test(r.get('name') or ''))]
    return sorted(found, key=lambda r: r.get('name') or '')


def as_float(value):
    try:
        return None if value is None or value == '' else float(value)
    except (TypeError, ValueError):
        return None


def parse_local(text):
    """'2026-11-13T23:30:00' in UK clock time to a UTC epoch."""
    text = str(text)
    d = date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    return local_epoch(d, int(text[11:13]), int(text[14:16]))


class Constraints:
    def __init__(self, store, fleet=None):
        self.store, self.fleet = store, fleet
        self.version = 0
        self.checked = {}          # when each part last worked
        self.flow_resource = None

    # ---- day-ahead flows and limits ---------------------------------------

    def _flow_resource(self):
        if not self.flow_resource:
            found = resources('day-ahead-constraint-flows-and-limits')
            if not found:
                raise FetchError('NESO has no day-ahead constraint flows')
            self.flow_resource = found[-1]['id']
        return self.flow_resource

    def poll_flows(self, now):
        rid = self._flow_resource()
        table = '"%s"' % rid
        latest = self.store.scalar('SELECT MAX(t) FROM bflow')
        today = uk_clock(now)[0]
        since = (uk_clock(latest)[0] - timedelta(days=1)) if latest else today - timedelta(days=RAW_DAYS)
        rows, offset = [], 0
        while True:
            batch = sql('SELECT "Constraint Group" g, "Date_ Time GMT_BST" t, "Flow_MW" f, "Limit_MW" l FROM %s '
                        'WHERE "Date_ Time GMT_BST" >= \'%sT00:00:00\' ORDER BY "Date_ Time GMT_BST", "Constraint Group" '
                        'LIMIT %d OFFSET %d' % (table, since.isoformat(), SQL_ROWS, offset))
            rows += batch
            if len(batch) < SQL_ROWS:
                break
            offset += SQL_ROWS
        out = []
        for row in rows:
            try:
                t, flow, lim = parse_local(row['t']), as_float(row['f']), as_float(row['l'])
            except (KeyError, ValueError, TypeError):
                continue
            if flow is None or lim is None or not row.get('g'):
                continue
            out.append((code(row['g']), t, round(flow), round(lim)))
        if out:
            self.store.write('INSERT OR REPLACE INTO bflow (grp, t, flow, lim) VALUES (?, ?, ?, ?)', out, history=False)
        self.store.write('DELETE FROM bflow WHERE t < ?', [(now - (RAW_DAYS + 2) * 86400,)], history=False)
        self._summarise_raw()
        # Older days, added up by NESO: once, then never again
        first_raw = self.store.scalar('SELECT MIN(t) FROM bflow')
        have_from = self.store.scalar('SELECT MIN(d) FROM bday')
        target = (today - timedelta(days=HISTORY_DAYS)).isoformat()
        if first_raw and (have_from is None or have_from > target):
            # From HISTORY_DAYS ago up to the first day already summarised
            stop = have_from if have_from else uk_clock(first_raw)[0].isoformat()
            self._backfill_days(table, target, stop)
        return len(out)

    def _summarise_raw(self):
        """Each day's summary from the half-hourly rows (overwrites NESO-summed days as they come in)."""
        rows = {}
        for grp, t, flow, lim in self.store.query('SELECT grp, t, flow, lim FROM bflow ORDER BY t'):
            d = uk_clock(t)[0].isoformat()
            rows.setdefault((grp, d), []).append((flow, lim))
        out = []
        for (grp, d), values in rows.items():
            out.append((grp, d) + summary(values))
        if out:
            self.store.write('INSERT OR REPLACE INTO bday (grp, d, peak, mean, near, over, n, lim, maxflow) '
                             'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', out, history=False)

    def _backfill_days(self, table, begin, stop):
        """Older days' summaries, from the raw half hours in 20-day pieces (NESO's SQL allows no
        functions or casts, so it can't add these up itself). Once, on the first start."""
        day, end, days = date.fromisoformat(begin), date.fromisoformat(stop), set()
        while day < end:
            upto = min(day + timedelta(days=20), end)
            rows, offset = [], 0
            while True:
                batch = sql('SELECT "Constraint Group" g, "Date_ Time GMT_BST" t, "Flow_MW" f, "Limit_MW" l FROM %s '
                            'WHERE "Date_ Time GMT_BST" >= \'%sT00:00:00\' AND "Date_ Time GMT_BST" < \'%sT00:00:00\' '
                            'ORDER BY "Date_ Time GMT_BST", "Constraint Group" LIMIT %d OFFSET %d'
                            % (table, day.isoformat(), upto.isoformat(), SQL_ROWS, offset), timeout=180)
                rows += batch
                if len(batch) < SQL_ROWS:
                    break
                offset += SQL_ROWS
            groups = {}
            for row in rows:
                flow, lim = as_float(row.get('f')), as_float(row.get('l'))
                if flow is None or lim is None or not row.get('g') or not row.get('t'):
                    continue
                groups.setdefault((code(row['g']), str(row['t'])[:10]), []).append((flow, lim))   # the UK date
            out = [(g, d) + summary(values) for (g, d), values in groups.items()]
            if out:
                self.store.write('INSERT OR IGNORE INTO bday (grp, d, peak, mean, near, over, n, lim, maxflow) '
                                 'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)', out, history=False)
            days.update(d for _, d in groups)
            day = upto
            time.sleep(0.5)
        log.info('[constraints] %d days of boundary history from %s', len(days), begin)

    # ---- costs --------------------------------------------------------------

    def poll_costs(self, now):
        since = (uk_clock(now)[0] - timedelta(days=HISTORY_DAYS)).isoformat()
        rows = []
        for resource in resources('thermal-constraint-costs', lambda n: 'Data' in n)[-2:]:
            rows += sql('SELECT "Settlement Date" d, "Constraint Group" g, "Daily Cost GBP" c FROM "%s" '
                        'WHERE "Settlement Date" >= \'%s\'' % (resource['id'], since))
        out = [(code(r['g']), str(r['d'])[:10], as_float(r['c']) or 0.0) for r in rows if r.get('g') and r.get('d')]
        if out:
            self.store.write('INSERT OR REPLACE INTO bcost (grp, d, cost) VALUES (?, ?, ?)', out, history=False)
        days = []
        for resource in resources('constraint-breakdown')[-2:]:
            days += sql('SELECT * FROM "%s" WHERE "Date" >= \'%s\'' % (resource['id'], since))
        out = []
        for r in days:
            d = str(r.get('Date') or '')[:10]
            if not d:
                continue
            get = lambda key: as_float(r.get(key)) or 0.0  # noqa: E731
            out.append((d, get('Thermal constraints cost'), get('Voltage constraints cost'), get('Increasing system inertia cost'),
                        get('Reducing largest loss cost'), get('Thermal constraints volume')))
        if out:
            self.store.write('INSERT OR REPLACE INTO cday (d, thermal, voltage, inertia, loss, thermal_mwh) '
                             'VALUES (?, ?, ?, ?, ?, ?)', out, history=False)
        return len(out)

    # ---- wind turned down ---------------------------------------------------

    def poll_wind(self, now):
        today = uk_clock(now)[0]
        have = self.store.scalar('SELECT MAX(d) FROM wcurt')
        start = (date.fromisoformat(have) - timedelta(days=3)) if have else today - timedelta(days=HISTORY_DAYS)
        files = resources('wind-bmu-boa-volumes', lambda n: 'BOA' in n)
        # Each yearly file runs April to March; only the ones that overlap
        files = [f for f in files if file_end(f['name']) is None or file_end(f['name']) >= start]
        per_unit, per_hh = [], []
        for resource in files:
            table = '"%s"' % resource['id']
            chunk = start
            while chunk <= today:
                stop = min(chunk + timedelta(days=31), today + timedelta(days=1))
                where = '"Date" >= \'%s\' AND "Date" < \'%s\'' % (chunk.isoformat(), stop.isoformat())
                # BOA_Volume is the MW turned down in each half hour (NESO's data dictionary): MWh is half that
                per_unit += sql('SELECT "Date" d, "Generator_Name" u, MAX("Generator_Full_Name") n, '
                                'SUM(CASE WHEN "BOA_Volume" < 0 THEN -"BOA_Volume" ELSE 0 END) / 2 down, '
                                'SUM(CASE WHEN "BOA_Volume" > 0 THEN "BOA_Volume" ELSE 0 END) / 2 up '
                                'FROM %s WHERE %s GROUP BY 1, 2' % (table, where))
                per_hh += sql('SELECT "Date" d, "Settlement_Period" sp, '
                              'SUM(CASE WHEN "BOA_Volume" < 0 THEN -"BOA_Volume" ELSE 0 END) / 2 down '
                              'FROM %s WHERE %s GROUP BY 1, 2' % (table, where))
                chunk = stop
        units = []
        for r in per_unit:
            down = as_float(r.get('down')) or 0.0
            if down <= 0 or not r.get('u'):
                continue
            units.append((str(r['d'])[:10], str(r['u']).strip(), str(r.get('n') or '').strip(), round(down, 2)))
        halves = []
        for r in per_hh:
            try:
                halves.append((settlement_start(date.fromisoformat(str(r['d'])[:10]), int(r['sp'])), round(as_float(r['down']) or 0.0, 2)))
            except (KeyError, TypeError, ValueError):
                continue
        if units:
            self.store.write('INSERT OR REPLACE INTO wcurt (d, unit, name, mwh) VALUES (?, ?, ?, ?)', units, history=False)
        if halves:
            self.store.write('INSERT OR REPLACE INTO wcurt_hh (t, mwh) VALUES (?, ?)', halves, history=False)
        return len(units)

    # ---- the job ------------------------------------------------------------

    def poll(self):
        """Every few hours: NESO publishes these once a day or less."""
        now = time.time()
        errors = []
        for name, step in (('flows', self.poll_flows), ('costs', self.poll_costs), ('wind', self.poll_wind)):
            try:
                step(now)
                self.checked[name] = now
            except FetchError as error:
                errors.append(name + ': ' + str(error))
        self.version += 1
        if len(errors) == 3:
            raise FetchError('; '.join(errors))
        if errors:
            log.warning('[constraints] %s', '; '.join(errors))


def summary(values):
    """(peak, mean, near, over, n, mean limit, highest flow) for one day's (flow, limit) pairs."""
    usable = [(f, l) for f, l in values if l and l > 0]
    if not usable:
        return (0.0, 0.0, 0, 0, len(values), 0, max((f for f, _ in values), default=0))
    ratios = [f / l for f, l in usable]
    return (round(max(ratios), 4), round(sum(max(0.0, r) for r in ratios) / len(ratios), 4),
            sum(1 for r in ratios if r >= NEAR), sum(1 for r in ratios if r >= 1.0), len(values),
            round(sum(l for _, l in usable) / len(usable)), max(f for f, _ in values))


def file_end(name):
    """The last day a yearly file ('Wind BOA Volumes 2025/26', 'Data 26-27') covers: 31 March of the second year."""
    digits = [part for part in name.replace('-', '/').split() if '/' in part]
    try:
        first = int(digits[-1].split('/')[0])
        if first < 100:          # 'Data 26-27'
            first += 2000
        return date(first + 1, 3, 31)
    except (IndexError, ValueError):
        return None


# ---- the page's figures ------------------------------------------------------

def _day_rows(store, d):
    rows = store.query('SELECT grp, peak, mean, near, over, n, lim, maxflow FROM bday WHERE d = ? AND lim > 0 ORDER BY peak DESC', (d,))
    out = []
    for grp, peak, mean, near, over, n, lim, maxflow in rows:
        text, boundary = label(grp)
        out.append({'code': grp, 'label': text, 'boundary': boundary, 'peak': round(peak, 3), 'mean': round(mean, 3),
                    'near': near, 'over': over, 'n': n, 'lim': lim, 'maxflow': maxflow})
    return out


def view(store, fleet, now, focus=None):
    """What /api/constraints returns."""
    today = uk_clock(now)[0]
    tomorrow = today + timedelta(days=1)
    days = {'today': {'d': today.isoformat(), 'groups': _day_rows(store, today.isoformat())},
            'tomorrow': {'d': tomorrow.isoformat(), 'groups': _day_rows(store, tomorrow.isoformat())}}
    # The group to chart: asked for, else tomorrow's (or today's) most stretched
    ranked = days['tomorrow']['groups'] or days['today']['groups']
    codes = {g for (g,) in store.query('SELECT DISTINCT grp FROM bflow')}
    if focus not in codes:
        focus = 'SCOTEX' if 'SCOTEX' in codes else (ranked[0]['code'] if ranked else None)
    series = []
    if focus:
        start = local_epoch(today - timedelta(days=6), 0, 0)
        series = [[t, flow, lim] for t, flow, lim in store.query(
            'SELECT t, flow, lim FROM bflow WHERE grp = ? AND t >= ? ORDER BY t', (focus, start))]

    # Each group over the last 30 and 365 days: half hours near and over the limit, and cost
    d30, d365 = (today - timedelta(days=30)).isoformat(), (today - timedelta(days=365)).isoformat()
    stats = {}
    for grp, near30, over30, n30 in store.query(
            'SELECT grp, SUM(near), SUM(over), SUM(n) FROM bday WHERE d >= ? AND d < ? GROUP BY grp', (d30, today.isoformat())):
        stats.setdefault(grp, {})['near30'], stats[grp]['over30'], stats[grp]['n30'] = near30, over30, n30
    for grp, over365, n365 in store.query(
            'SELECT grp, SUM(over), SUM(n) FROM bday WHERE d >= ? AND d < ? GROUP BY grp', (d365, today.isoformat())):
        stats.setdefault(grp, {})['over365'], stats[grp]['n365'] = over365, n365
    for grp, c30 in store.query('SELECT grp, SUM(cost) FROM bcost WHERE d >= ? GROUP BY grp', (d30,)):
        stats.setdefault(grp, {})['cost30'] = round(c30)
    for grp, c365 in store.query('SELECT grp, SUM(cost) FROM bcost WHERE d >= ? GROUP BY grp', (d365,)):
        stats.setdefault(grp, {})['cost365'] = round(c365)
    # The flow data splits some groups into variants (SSHARN3, SEIMPPR23); costs use the plain code.
    # One row per cost code, taking the variant most often over its limit.
    cost_codes = {g for g, s in stats.items() if 'cost365' in s}
    merged = {}
    for grp, s in stats.items():
        base = next((k for k in sorted(cost_codes, key=len, reverse=True) if grp.startswith(k)), grp.rstrip('0123456789'))
        item = merged.setdefault(base, {'code': base, 'variants': []})
        if 'cost365' in s:
            item['cost30'], item['cost365'] = s.get('cost30', 0), s['cost365']
        if 'n365' in s:
            item['variants'].append(grp)
            for key in ('near30', 'over30', 'n30', 'over365', 'n365'):
                if key in s and (key not in item or (s.get('over365') or 0) >= (item.get('over365') or 0)):
                    item[key] = s[key]
    groups = []
    for base, item in merged.items():
        text, boundary = label(base)
        item.update({'label': text, 'boundary': boundary, 'flows': bool(item['variants'])})
        groups.append(item)
    groups.sort(key=lambda g: (-(g.get('cost30') or 0), -(g.get('over30') or 0)))

    # Constraint costs by reason, each day
    cost_rows = store.query('SELECT d, thermal, voltage, inertia, loss FROM cday WHERE d >= ? ORDER BY d',
                            ((today - timedelta(days=60)).isoformat(),))
    def total(since):
        row = store.query('SELECT SUM(thermal + voltage + inertia + loss), COUNT(*) FROM cday WHERE d >= ?', (since,))
        return {'cost': round(row[0][0] or 0), 'days': row[0][1]} if row and row[0][1] else None
    # NESO's newest day is sometimes a stub (£82 on a £10m day) until the full day is published:
    # the latest day is the newest one costing at least a twentieth of the month's median
    recent = store.query('SELECT d, thermal + voltage + inertia + loss FROM cday ORDER BY d DESC LIMIT 31')
    typical = sorted(c for _, c in recent)[len(recent) // 2] if recent else 0
    latest_cost = [row for row in recent if row[1] >= typical / 20][:1]

    # Wind turned down: each day, and by wind farm (BM units grouped into stations)
    wind_days = [[d, round(mwh)] for d, mwh in store.query(
        'SELECT d, SUM(mwh) FROM wcurt WHERE d >= ? GROUP BY d ORDER BY d', ((today - timedelta(days=60)).isoformat(),))]
    def farms(since, limit=None):
        merged = {}
        for unit, name, mwh in store.query('SELECT unit, MAX(name), SUM(mwh) FROM wcurt WHERE d >= ? GROUP BY unit', (since,)):
            sid = fleet.resolve(unit, '', {'n': name, 'f': 'WIND'}) if fleet else None
            station = fleet.stations.get(sid) if fleet and sid is not None else None
            key = sid if station else 'u:' + (name or unit).rstrip(' 0123456789')
            item = merged.setdefault(key, {'sid': sid if station else None, 'n': station['name'] if station else (name or unit).rstrip(' 0123456789'),
                                           'x': station['x'] if station else None, 'y': station['y'] if station else None,
                                           'cap': round(station['capacity']) if station and station['capacity'] else None,
                                           'mwh': 0.0})
            item['mwh'] += mwh
        out = sorted(merged.values(), key=lambda f: -f['mwh'])
        for f in out:
            f['mwh'] = round(f['mwh'])
        return out[:limit] if limit else out
    def wind_total(since):
        row = store.query('SELECT SUM(mwh), COUNT(DISTINCT d) FROM wcurt WHERE d >= ?', (since,))
        return round(row[0][0] or 0) if row else 0
    # The share of wind turned down, over the last 30 days that have both figures (NESO's
    # generation history runs a few days behind): wind made by transmission-connected wind
    # farms, and the wind turned down on the same days
    made = {}
    for t, mw in store.query('SELECT t, wind FROM lh WHERE t >= ? AND t < ? AND wind IS NOT NULL',
                             (local_epoch(today - timedelta(days=45), 0, 0), local_epoch(today, 0, 0))):
        d = uk_clock(t)[0].isoformat()
        entry = made.setdefault(d, [0.0, 0])
        entry[0] += mw / 2
        entry[1] += 1
    full = sorted(d for d, (_, n) in made.items() if n >= 46)[-30:]
    share = None
    if len(full) >= 20:
        down = dict(store.query('SELECT d, SUM(mwh) FROM wcurt WHERE d >= ? GROUP BY d', (full[0],)))
        made_total = sum(made[d][0] for d in full)
        down_total = sum(down.get(d, 0.0) for d in full)
        if made_total > 0:
            share = {'from': full[0], 'to': full[-1], 'days': len(full), 'made': round(made_total), 'down': round(down_total),
                     'share': round(down_total / (made_total + down_total), 4)}
    latest_wind = store.query('SELECT MAX(d) FROM wcurt')[0][0]
    return {
        'now': int(now), 'days': days, 'focus': focus, 'series': series,
        'groups': groups,
        'costs': {'days': [[d, round(a), round(b), round(c), round(e)] for d, a, b, c, e in cost_rows],
                  'latest': {'d': latest_cost[0][0], 'cost': round(latest_cost[0][1])} if latest_cost else None,
                  'd30': total(d30), 'd365': total(d365)},
        'wind': {'days': wind_days, 'latest': latest_wind,
                 'latest_mwh': wind_total(latest_wind) - wind_total((date.fromisoformat(latest_wind) + timedelta(days=1)).isoformat()) if latest_wind else None,
                 'mwh30': wind_total(d30), 'mwh365': wind_total(d365),
                 'share': share,
                 'farms30': farms(d30, 12), 'map7': [f for f in farms((today - timedelta(days=7)).isoformat()) if f['x'] is not None][:60]},
    }
