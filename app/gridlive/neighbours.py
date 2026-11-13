"""The carbon intensity of Britain's neighbours, for what comes through the interconnectors.

  France     RTE's éCO2mix real-time figures, through Open Data Réseaux Énergies (ODRÉ): the
             carbon intensity of French generation (taux_co2, g/kWh), every 15 minutes, in UTC.
             Licence Ouverte v2.0. Paged 100 records at a time; 50,000 requests a month.
  Denmark    Energinet's Energi Data Service (CO2Emis): the carbon intensity of electricity used
             in western Denmark (DK1, where Viking Link lands), every 5 minutes, in UTC. It
             refuses a second request within a minute or so, so it gets one request a poll.

No key for either. The other neighbours have no open live figure without an account, so they
keep NESO's typical factors (Norway, almost all hydro, an estimate)."""

import json
import time
import urllib.parse
from datetime import datetime, timezone

from . import fetch
from .config import ENERGINET, ODRE, log
from .fetch import FetchError, as_float
from .reference import IMPORT_FACTORS
from .timeutil import parse_time

# The countries in NESO's published table; the dashboard's figures for the rest are estimates
NESO_FACTORS = ('France', 'Netherlands', 'Belgium', 'Ireland')
KEEP = 8 * 86400
BACKFILL = 7 * 86400
STALE = 2 * 3600            # a live figure older than this gives way to the typical one
PAGE = 100                  # ODRÉ's most records a request
MAX_PAGES = 10

# Country -> (zone, who publishes it, what it measures)
LIVE = {
    'France': ('FR', 'RTE', 'French generation'),
    'Denmark': ('DK1', 'Energinet', 'electricity used in western Denmark'),
}


def iso_utc(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class Neighbours:
    def __init__(self, store):
        self.store = store

    def latest_t(self, zone):
        return self.store.scalar('SELECT MAX(t) FROM ext_ci WHERE zone = ?', (zone,))

    def _save(self, zone, rows):
        rows = [(zone, t, v) for t, v in rows]
        if rows:
            self.store.write('INSERT OR REPLACE INTO ext_ci (zone, t, v) VALUES (?, ?, ?)', rows, history=False)
        return len(rows)

    def fetch_france(self, since):
        """French generation's carbon intensity from `since` (UTC epoch). Returns records stored."""
        stored = 0
        for page in range(MAX_PAGES):
            query = {'select': 'date_heure,taux_co2', 'where': "taux_co2 is not null and date_heure >= '%s'" % iso_utc(since),
                     'order_by': 'date_heure', 'limit': PAGE, 'offset': page * PAGE}
            payload = fetch.get_json(ODRE + '/catalog/datasets/eco2mix-national-tr/records?' + urllib.parse.urlencode(query), timeout=60)
            results = payload.get('results') if isinstance(payload, dict) else None
            if not isinstance(results, list):
                raise FetchError('Unexpected response shape from ODRÉ')
            rows = []
            for r in results:
                v = as_float(r.get('taux_co2'))
                try:
                    t = parse_time(r.get('date_heure'))
                except (TypeError, ValueError):
                    continue
                if v is not None and 0 <= v < 2000:
                    rows.append((t, v))
            stored += self._save('FR', rows)
            if len(results) < PAGE:
                break
        return stored

    def fetch_denmark(self, hours):
        """Western Denmark's carbon intensity for the last `hours` hours, in one request."""
        query = {'start': 'now', 'startOffset': '-PT%dH' % hours, 'filter': json.dumps({'PriceArea': ['DK1']}),
                 'columns': 'Minutes5UTC,CO2Emission', 'sort': 'Minutes5UTC', 'limit': 0}
        payload = fetch.get_json(ENERGINET + '/dataset/CO2Emis?' + urllib.parse.urlencode(query), timeout=60)
        records = payload.get('records') if isinstance(payload, dict) else None
        if not isinstance(records, list):
            raise FetchError('Unexpected response shape from Energi Data Service')
        rows = []
        for r in records:
            v = as_float(r.get('CO2Emission'))
            try:
                t = parse_time(r.get('Minutes5UTC'))      # naive, and UTC
            except (TypeError, ValueError):
                continue
            if v is not None and 0 <= v < 2000:
                rows.append((t, v))
        return self._save('DK1', rows)

    def poll(self):
        now = time.time()
        errors = []
        last = self.latest_t('FR')
        try:
            # From an hour before the latest we have, in case a figure was revised, or a week back
            self.fetch_france(now - BACKFILL if last is None or last < now - BACKFILL else last - 3600)
        except FetchError as error:
            errors.append('France: %s' % error)
        last = self.latest_t('DK1')
        hours = 168 if last is None or last < now - BACKFILL else max(2, int((now - last) // 3600) + 2)
        try:
            self.fetch_denmark(hours)
        except FetchError as error:
            errors.append('Denmark: %s' % error)
        self.store.write('DELETE FROM ext_ci WHERE t < ?', [(now - KEEP,)], history=False)
        if errors:
            raise FetchError('; '.join(errors))


# ---- the payloads ------------------------------------------------------------------

def country_carbon(store, now):
    """Each neighbour's carbon intensity now: {country: {v, live, t, source, what}}. Live where
    there's an open figure from the last two hours; otherwise NESO's typical factor."""
    out = {}
    for country, factor in IMPORT_FACTORS.items():
        out[country] = {'v': factor, 'live': False, 'source': 'NESO' if country in NESO_FACTORS else 'estimate'}
        if country == 'Norway':
            out[country]['note'] = 'almost all hydro'
    for country, (zone, source, what) in LIVE.items():
        row = store.query('SELECT t, v FROM ext_ci WHERE zone = ? AND t <= ? ORDER BY t DESC LIMIT 1', (zone, now))
        if row and row[0][0] >= now - STALE:
            out[country] = {'v': round(row[0][1]), 'live': True, 't': row[0][0], 'source': source, 'what': what}
    return out


def imports_carbon(countries_mw, carbon):
    """The carbon intensity of what's coming in (g/kWh), weighted by each country's imports (MW),
    or None when nothing is."""
    flows = [(mw, carbon[c]['v']) for c, mw in countries_mw.items() if mw > 0 and c in carbon]
    total = sum(mw for mw, _ in flows)
    if total < 1:
        return None
    return sum(mw * v for mw, v in flows) / total


def half_hours(store, zone, start, end):
    """[[t, mean g/kWh]] for each half hour from start to end with a figure."""
    rows = store.query('SELECT t - t % 1800 AS h, AVG(v) FROM ext_ci WHERE zone = ? AND t >= ? AND t < ? GROUP BY h ORDER BY h',
                       (zone, start, end))
    return [[int(h), round(v, 1)] for h, v in rows]


def week(store, now):
    """What /api/neighbours returns: the last seven days, half hour by half hour, for Britain
    (NESO's figure) and each live neighbour, with each one's mean, and every neighbour now."""
    end = now - now % 1800 + 1800
    start = end - BACKFILL
    gb = [[int(t), round(a if a is not None else f, 1)] for t, f, a in store.query(
        'SELECT t, forecast, actual FROM ci WHERE region = 0 AND t >= ? AND t < ? ORDER BY t', (start, min(end, now)))
        if (a if a is not None else f) is not None]
    series = {'GB': gb}
    for country, (zone, _, _) in LIVE.items():
        series[zone] = half_hours(store, zone, start, end)

    def mean(rows):
        return round(sum(v for _, v in rows) / len(rows)) if rows else None
    # Means over the half hours every series has, so they compare like with like
    common = set(t for t, _ in gb)
    for rows in series.values():
        common &= set(t for t, _ in rows)
    return {
        'start': start, 'end': end, 'series': series,
        'means': {k: mean([r for r in rows if r[0] in common]) for k, rows in series.items()} if len(common) >= 48 else None,
        'common_hours': len(common) / 2,
        'now': country_carbon(store, now),
    }
