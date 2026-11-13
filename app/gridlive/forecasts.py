"""The forecast scorecard: how good NESO's forecasts were.

From NESO's own records, downloaded once and then kept up to date:
  Historic day-ahead wind forecasts (2018-)       against transmission wind (the lh table)
  Day-ahead half-hourly demand forecast performance (2021-), with NESO's outturn
  National carbon intensity forecast (2018-)      the last forecast before each half hour, with the actual

And from the dashboard itself, from the day this version first runs: the carbon intensity and
embedded solar forecasts as they stood at noon the day before, scored once the outcome is in.
No keys; about 40 requests to NESO the first time, then a few a day."""

import time
import urllib.parse
from datetime import date, timedelta

from . import fetch
from .constraints import file_end, resources
from .config import NESO, log
from .fetch import FetchError, as_float
from .imbalance import temperatures
from .timeutil import local_epoch, parse_time, settlement_start, uk_clock, uk_day, uk_midnight_utc

WIND_RESOURCE = '7524ec65-f782-4258-aaf8-5b926c17b966'     # Historic Day Ahead Wind Forecasts
DEMAND_RESOURCE = '08e41551-80f8-4e28-a416-ea473a695db9'   # Day Ahead Half Hourly Demand Forecast Performance
CARBON_RESOURCE = '0e5fde43-2de7-4fb4-833d-c7bca3b658b0'   # National Carbon Intensity Forecast
PAGE = 10000                    # rows per request (NESO allows 32,000; smaller pages fail less)
FETCH_EVERY = 6 * 3600          # NESO's records are updated daily
SNAP_HOUR = 12                  # a day-ahead forecast is the one standing at noon (UK) the day before
SNAP_STALE = 6 * 3600           # ... taken no more than six hours before that
KEEP_SNAPS = 800                # days of our own snapshots kept


def sql(query, timeout=120):
    data = fetch.get_json(NESO + '/api/3/action/datastore_search_sql?sql=' + urllib.parse.quote(query), timeout=timeout)
    result = (data or {}).get('result') or {}
    if not result and (data or {}).get('error'):
        raise FetchError('NESO SQL: %s' % str(data['error'])[:200])
    return result.get('records') or []


def day_period(day_text, period):
    """UTC start of a settlement period from NESO's date and period columns."""
    return settlement_start(date.fromisoformat(str(day_text)[:10]), int(float(period)))


def cutoff(t):
    """When the day-ahead forecast for half hour t is taken: noon (UK) the day before its UK day."""
    return local_epoch(uk_day(t) - timedelta(days=1), SNAP_HOUR, 0)


class Forecasts:
    def __init__(self, store):
        self.store = store
        self.fetched = 0
        self.version = 0
        self.cache = None

    # ---- NESO's records ----------------------------------------------------

    def _since(self, table, default):
        last = self.store.scalar('SELECT MAX(t) FROM %s' % table)
        return last if last is not None else default

    def fetch_wind(self, limit=PAGE):
        """The next page of day-ahead wind forecasts after the newest kept. Returns rows added."""
        last = self._since('fwind', 0)
        after = uk_day(last).isoformat() if last else '2000-01-01'
        rows = []
        for r in sql('SELECT "Date", "Settlement_period", "Capacity", "Incentive_forecast" FROM "%s" WHERE "Date" >= \'%s\' '
                     'ORDER BY "Date", "Settlement_period" LIMIT %d' % (WIND_RESOURCE, after, limit)):
            try:
                t = day_period(r['Date'], r['Settlement_period'])
            except (KeyError, TypeError, ValueError):
                continue
            fc, cap = as_float(r.get('Incentive_forecast')), as_float(r.get('Capacity'))
            if fc is not None and t > last:
                rows.append((t, round(fc), None if cap is None else round(cap)))
        self.store.write('INSERT OR REPLACE INTO fwind (t, fc, cap) VALUES (?, ?, ?)', rows, history=False)
        return len(rows)

    def fetch_demand(self, limit=PAGE):
        last = self._since('fdemand', 0)
        after = uk_day(last).isoformat() if last else '2000-01-01'
        rows = []
        for r in sql('SELECT "Date", "Settlement_Period", "Demand_Forecast", "Demand_Outturn" FROM "%s" WHERE "Date" >= \'%s\' '
                     'ORDER BY "Date", "Settlement_Period" LIMIT %d' % (DEMAND_RESOURCE, after, limit)):
            try:
                t = day_period(r['Date'], r['Settlement_Period'])
            except (KeyError, TypeError, ValueError):
                continue
            fc, out = as_float(r.get('Demand_Forecast')), as_float(r.get('Demand_Outturn'))
            if fc is not None and out is not None and out > 0 and t > last:
                rows.append((t, round(fc), round(out)))
        self.store.write('INSERT OR REPLACE INTO fdemand (t, fc, act) VALUES (?, ?, ?)', rows, history=False)
        return len(rows)

    def fetch_carbon(self, limit=PAGE):
        last = self._since('fci', 0)
        after = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(last)) if last else '2000-01-01T00:00:00'
        rows = []
        now = time.time()
        # NESO's firewall refuses queries with "IS NOT NULL" in them, so the empty future rows are skipped here
        for r in sql('SELECT "datetime", "forecast", "actual" FROM "%s" WHERE "datetime" > \'%s\' '
                     'ORDER BY "datetime" LIMIT %d' % (CARBON_RESOURCE, after, limit)):
            try:
                t = fetch_time(r['datetime'])
            except (KeyError, TypeError, ValueError):
                continue
            fc, act = as_float(r.get('forecast')), as_float(r.get('actual'))
            if fc is not None and act is not None and t < now:
                rows.append((t, round(fc), round(act)))
        self.store.write('INSERT OR REPLACE INTO fci (t, fc, act) VALUES (?, ?, ?)', rows, history=False)
        return len(rows)

    def fetch_turned_down(self):
        """Wind turned down each half hour back to 2018, one request per financial year's file, so
        wind forecasts are scored against what wind farms could have made. Each finished year is
        fetched once; the Constraints page keeps the current one up to date."""
        added = 0
        today = uk_clock(time.time())[0]
        for resource in resources('wind-bmu-boa-volumes', lambda n: 'BOA' in n):
            last = file_end(resource['name'])
            if last is None:
                continue
            done = 'fc_wcurt_' + resource['id']
            if self.store.scalar('SELECT v FROM meta WHERE k = ?', (done,)):
                continue
            first = date(last.year - 1, 4, 1)
            rows = []
            # BOA_Volume is MW in each half hour, so MWh is half of it (as on the Constraints page)
            for r in sql('SELECT "Date" d, "Settlement_Period" sp, SUM(CASE WHEN "BOA_Volume" < 0 THEN -"BOA_Volume" ELSE 0 END) / 2 down '
                         'FROM "%s" WHERE "Date" >= \'%s\' AND "Date" < \'%s\' GROUP BY 1, 2'
                         % (resource['id'], first.isoformat(), (last + timedelta(days=1)).isoformat()), timeout=180):
                try:
                    rows.append((day_period(r['d'], r['sp']), round(as_float(r['down']) or 0.0, 2)))
                except (KeyError, TypeError, ValueError):
                    continue
            self.store.write('INSERT OR IGNORE INTO wcurt_hh (t, mwh) VALUES (?, ?)', rows, history=False)
            added += len(rows)
            if last < today - timedelta(days=30):
                self.store.write('INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)', [(done, '1')], history=False)
            time.sleep(1)
        return added

    def fetch_all(self):
        """Page through each record until caught up (the first time, a few dozen requests)."""
        added = {}
        for name, step in (('wind', self.fetch_wind), ('demand', self.fetch_demand), ('carbon', self.fetch_carbon),
                           ('turned down', self.fetch_turned_down)):
            total, started = 0, time.time()
            try:
                while time.time() - started < 1800:
                    got = step()
                    total += got
                    # The turned-down files are read whole, once each; the records page by page until caught up
                    if got < 10 or step == self.fetch_turned_down:
                        break
                    time.sleep(1)
            except FetchError as error:
                added[name] = 'failed: %s' % error
                continue
            added[name] = total
        return added

    # ---- our own day-ahead snapshots ------------------------------------------

    def snapshot(self, now):
        """Keep the carbon and embedded solar forecasts for each half hour as they stand until noon
        the day before; once that's passed, they're left as they were. Then fill in what happened."""
        rows = []
        ahead = local_epoch(uk_clock(now)[0] + timedelta(days=3), 0, 0)    # the next two days are the ones still open
        for kind, query in (('ci', 'SELECT t, forecast FROM ci WHERE region = 0 AND t > ? AND t < ? AND forecast IS NOT NULL'),
                            ('solar', "SELECT t, v FROM hh WHERE k = 'fc_solar' AND t > ? AND t < ? AND v IS NOT NULL")):
            for t, v in self.store.query(query, (now, ahead)):
                if now < cutoff(t):
                    rows.append((kind, int(t), round(v, 1), int(now)))
        # When each was first kept, for the page
        self.store.write('INSERT OR IGNORE INTO meta (k, v) VALUES (?, ?)',
                         [('fsnap_since_' + kind, str(int(now))) for kind in sorted({r[0] for r in rows})], history=False)
        self.store.write('INSERT INTO fsnap (kind, t, fc, made) VALUES (?, ?, ?, ?) '
                         'ON CONFLICT (kind, t) DO UPDATE SET fc = excluded.fc, made = excluded.made '
                         'WHERE fsnap.fc IS NOT excluded.fc OR fsnap.made < excluded.made - 3600', rows, history=False)
        # Outcomes, from the carbon and PV_Live tables before they're pruned
        self.store.write("UPDATE fsnap SET act = (SELECT actual FROM ci WHERE region = 0 AND ci.t = fsnap.t) "
                         "WHERE kind = 'ci' AND act IS NULL AND t < ?", [(now - 1800,)], history=False)
        self.store.write("UPDATE fsnap SET act = (SELECT v FROM hh WHERE k = 'pv' AND hh.t = fsnap.t) "
                         "WHERE kind = 'solar' AND act IS NULL AND t < ?", [(now - 1800,)], history=False)
        self.store.write('DELETE FROM fsnap WHERE t < ?', [(now - KEEP_SNAPS * 86400,)], history=False)
        return len(rows)

    def page(self, now):
        """/api/forecasts, worked out again when the data changes or the hour does."""
        key = (self.store.scalar('SELECT MAX(t) FROM fwind'), self.store.scalar('SELECT MAX(t) FROM fdemand'),
               self.store.scalar('SELECT MAX(t) FROM fci'), self.store.scalar('SELECT COUNT(act) FROM fsnap'),
               self.store.scalar('SELECT MAX(t) FROM lh'), int(now // 3600))
        cached = self.cache
        if cached and cached[0] == key:
            return cached[1]
        body = view(self.store, now)
        self.cache = (key, body)
        return body

    def poll(self):
        now = time.time()
        self.snapshot(now)
        if now - self.fetched >= FETCH_EVERY:
            added = self.fetch_all()
            failed = {k: v for k, v in added.items() if isinstance(v, str)}
            if len(failed) == len(added):
                raise FetchError('; '.join('%s %s' % kv for kv in failed.items()))
            self.fetched = now
            if any(isinstance(v, int) and v for v in added.values()):
                log.info('[forecasts] new from NESO: %s', ', '.join('%s %s' % kv for kv in added.items()))
        self.version += 1


def fetch_time(text):
    """NESO's carbon forecast times: the start of the half hour, UTC."""
    return parse_time(str(text)[:19])


# ---- the page ----------------------------------------------------------------

# Each forecast: its rows as (t, forecast, outcome, capacity). Our own snapshots only count where
# they were taken within six hours of noon the day before (ukday(t) - 12 hours is that noon)
OWN_FRESH = 'AND made >= ukday(t) - 43200 - %d' % SNAP_STALE
KINDS = {
    # Wind is scored against what wind farms could have made: what they generated, plus what NESO
    # paid them not to (MWh in each half hour, so twice that in MW)
    'wind': {'title': 'Wind, the day before', 'unit': 'MW', 'measure': 'cap',
             'source': 'NESO’s day-ahead forecast for transmission-connected wind farms, against what they made plus what they were turned down',
             't': 'f.t', 'cols': 'f.t, f.fc, l.wind + COALESCE(c.mwh, 0) * 2, f.cap',
             'from': 'FROM fwind f JOIN lh l ON l.t = f.t LEFT JOIN wcurt_hh c ON c.t = f.t WHERE l.wind IS NOT NULL'},
    'demand': {'title': 'Demand, the day before', 'unit': 'MW', 'measure': 'pct',
               'source': 'NESO’s day-ahead forecast of national demand, against the outturn',
               't': 't', 'cols': 't, fc, act, NULL', 'from': 'FROM fdemand WHERE 1'},
    'ci': {'title': 'Carbon intensity, the day before', 'unit': 'g/kWh', 'measure': 'abs', 'own': True,
           'source': 'NESO’s carbon intensity forecast as it stood at noon the day before, kept by this dashboard',
           't': 't', 'cols': 't, fc, act, NULL', 'from': "FROM fsnap WHERE kind = 'ci' AND act IS NOT NULL " + OWN_FRESH},
    'solar': {'title': 'Solar, the day before', 'unit': 'MW', 'measure': 'abs', 'own': True,
              'source': 'NESO’s forecast for solar panels on local networks as it stood at noon the day before, kept by this dashboard, against Sheffield Solar’s PV_Live',
              't': 't', 'cols': 't, fc, act, NULL', 'from': "FROM fsnap WHERE kind = 'solar' AND act IS NOT NULL " + OWN_FRESH},
    'carbon': {'title': 'Carbon intensity, half an hour ahead', 'unit': 'g/kWh', 'measure': 'abs',
               'source': 'NESO’s last carbon intensity forecast before each half hour, against its later estimate',
               't': 't', 'cols': 't, fc, act, NULL', 'from': 'FROM fci WHERE 1'},
}
ORDER = ['wind', 'demand', 'ci', 'solar', 'carbon']
TEMPERATURE_KINDS = ('demand',)


def rows(store, key, start, end):
    k = KINDS[key]
    return store.query('SELECT %s %s AND %s >= ? AND %s < ? ORDER BY 1' % (k['cols'], k['from'], k['t'], k['t']), (start, end))


def extent(store, key):
    k = KINDS[key]
    return store.query('SELECT MIN(%s), COUNT(*), MAX(%s) %s' % (k['t'], k['t'], k['from']))[0]


def score(items):
    """The typical miss (mean absolute error), the bias (forecast minus outcome, on average), the miss
    as a share of the average outcome, the share of half hours within a tenth of it, and for
    capacity-limited forecasts (wind) the miss as a share of capacity. None for no rows."""
    if not items:
        return None
    n = len(items)
    errors = [fc - act for _, fc, act, _ in items]
    mae = sum(abs(e) for e in errors) / n
    mean_act = sum(act for _, _, act, _ in items) / n
    out = {'n': n, 'mae': round(mae, 1), 'bias': round(sum(errors) / n, 1), 'mean': round(mean_act, 1),
           'pct': round(mae / mean_act, 4) if mean_act > 0 else None,
           'within': round(sum(1 for e in errors if abs(e) <= 0.1 * mean_act) / n, 3) if mean_act > 0 else None}
    caps = [cap for _, _, _, cap in items if cap]
    if len(caps) == n:
        out['cap_pct'] = round(sum(abs(e) / cap for e, cap in zip(errors, caps)) / n, 4)
        out['cap'] = round(caps[-1])
    return out


def headline(s, measure):
    """The one figure each forecast is judged by, for months and comparisons."""
    if not s:
        return None
    return s.get('cap_pct') if measure == 'cap' else s['pct'] if measure == 'pct' else s['mae']


def view(store, now):
    """What /api/forecasts returns."""
    kinds = []
    for key in ORDER:
        k = KINDS[key]
        first, count, last = extent(store, key)
        item = {'key': key, 'title': k['title'], 'unit': k['unit'], 'measure': k['measure'], 'source': k['source'],
                'own': bool(k.get('own')), 'n': count or 0}
        if k.get('own'):
            since = store.scalar('SELECT v FROM meta WHERE k = ?', ('fsnap_since_' + key,))
            item['collecting'] = int(since) if since else None
        if not count:
            kinds.append(item)
            continue
        # The latest whole day with outcomes
        last_day = uk_day(last)
        if last + 1800 < uk_midnight_utc(last_day + timedelta(days=1)) - 3 * 3600 and last_day > uk_day(first):
            last_day -= timedelta(days=1)
        end = uk_midnight_utc(last_day + timedelta(days=1))
        day_rows = rows(store, key, uk_midnight_utc(last_day), end)
        recent = rows(store, key, uk_midnight_utc(last_day - timedelta(days=29)), end)
        then = rows(store, key, uk_midnight_utc(last_day - timedelta(days=29 + 364)), uk_midnight_utc(last_day + timedelta(days=1 - 364)))
        # Month by month since the record began, and the worst days of the last year
        by_month, by_day = {}, {}
        for row in rows(store, key, 0, end):
            day = uk_day(row[0])
            by_month.setdefault((day.year, day.month), []).append(row)
            if row[0] >= end - 365 * 86400:
                by_day.setdefault(day, []).append(row)
        months = []
        for (y, m) in sorted(by_month):
            s = score(by_month[(y, m)])
            if s['n'] >= 200:
                months.append({'m': '%d-%02d' % (y, m), 'v': headline(s, k['measure']), 'mae': s['mae'], 'bias': s['bias'], 'mean': s['mean'], 'n': s['n']})
        days = [(d, score(r)) for d, r in by_day.items() if len(r) >= 40]
        days.sort(key=lambda pair: -pair[1]['mae'])
        item.update({
            'since': first, 'to': last,
            'day': {'d': last_day.isoformat(), 'series': [[t, fc, act] for t, fc, act, _ in day_rows], 'score': score(day_rows)},
            'd30': score(recent),
            'year_ago': score(then) if recent and len(then) >= 0.8 * len(recent) else None,
            'months': months,
            'worst': [{'d': d.isoformat(), 'mae': s['mae'], 'bias': s['bias']} for d, s in days[:5]],
            'best': {'d': days[-1][0].isoformat(), 'mae': days[-1][1]['mae']} if days else None,
        })
        if key in TEMPERATURE_KINDS:
            # The day's temperature against normal, which explains most of demand's bigger misses
            temps = temperatures(store, last_day - timedelta(days=366), last_day)
            item['day']['temp'] = temps.get(item['day']['d'])
            for w in item['worst']:
                w['temp'] = temps.get(w['d'])
        kinds.append(item)
    return {'now': int(now), 'kinds': kinds}
