"""Long-term history since 2009 and the 48-hour outlook feeds."""

import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import fetch
from .config import ELEXON, NESO, log
from .timeutil import floor_to, iso_minute, parse_time
from .fetch import FetchError, as_float


# --------------------------------------------------------------------------
# Long-term history and the 48-hour outlook
# --------------------------------------------------------------------------

LH_COLUMNS = ['gas', 'coal', 'nuclear', 'wind', 'wind_emb', 'hydro', 'imports', 'biomass', 'other', 'solar', 'storage', 'generation', 'ci']
LH_SOURCE = ['GAS', 'COAL', 'NUCLEAR', 'WIND', 'WIND_EMB', 'HYDRO', 'IMPORTS', 'BIOMASS', 'OTHER', 'SOLAR', 'STORAGE', 'GENERATION', 'CARBON_INTENSITY']
LH_RESOURCE = 'f93d1835-75bc-43e5-84ad-12472b180a98'  # NESO "Historic generation mix", looked up by name first
# Only rows NESO has changed are written, so re-reading costs nothing when nothing changed
UPSERT_LH = ('INSERT INTO lh (t, %s) VALUES (%s) ON CONFLICT (t) DO UPDATE SET %s WHERE %s'
             % (', '.join(LH_COLUMNS), ', '.join('?' * 14), ', '.join('%s = excluded.%s' % (c, c) for c in LH_COLUMNS),
                ' OR '.join('lh.%s IS NOT excluded.%s' % (c, c) for c in LH_COLUMNS)))
REFRESH_DAYS = 400            # NESO revises recent figures: read this far back again ...
REFRESH_EVERY = 7 * 86400     # ... once a week
LH_START = int(datetime(2009, 1, 1, tzinfo=timezone.utc).timestamp())
PRICE_HISTORY_START = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp())


class LongHistory:
    """NESO's half-hourly generation mix and carbon intensity since 2009, and
    Elexon's market index price since 2018, downloaded once then kept current."""

    def __init__(self, store):
        self.store = store
        self.resource = None
        self.mix_checked = 0
        self.refreshed = 0         # when the last year was last read again
        self.price_span = 6 * 86400  # steps down to 3 days, then 1, if Elexon refuses the range
        self.version = 0

    def find_resource(self):
        try:
            result = fetch.get_json(NESO + '/api/3/action/package_show?id=historic-generation-mix', timeout=60).get('result') or {}
            for resource in result.get('resources', []):
                if resource.get('datastore_active'):
                    return resource['id']
        except FetchError as error:
            log.debug('[longhistory] dataset lookup failed: %s', error)
        return LH_RESOURCE

    def fetch_mix(self, limit=10000, after=None):
        """The next `limit` half hours after the newest kept (or after `after`)."""
        if not self.resource:
            self.resource = self.find_resource()
        last = after if after is not None else (self.store.scalar('SELECT MAX(t) FROM lh') or 0)
        since = datetime.fromtimestamp(last, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') if last else '2000-01-01T00:00:00'
        columns = ', '.join('"%s"' % c for c in ['DATETIME'] + LH_SOURCE)
        sql = 'SELECT %s FROM "%s" WHERE "DATETIME" > \'%s\' ORDER BY "DATETIME" LIMIT %d' % (columns, self.resource, since, limit)
        payload = fetch.get_json(NESO + '/api/3/action/datastore_search_sql?sql=' + urllib.parse.quote(sql), timeout=180)
        records = (payload.get('result') or {}).get('records') or []
        rows = []
        for record in records:
            try:
                t = int(datetime.fromisoformat(str(record['DATETIME'])[:19]).replace(tzinfo=timezone.utc).timestamp())
            except (KeyError, ValueError):
                continue
            values = [as_float(record.get(c)) for c in LH_SOURCE]
            rows.append((t,) + tuple(None if v is None else int(round(v)) for v in values))
        changed = self.store.write(UPSERT_LH, rows, history=False)
        if after is not None:
            return (rows[-1][0] if rows else None), changed
        return len(rows)

    def refresh_recent(self, days=REFRESH_DAYS):
        """Read the last `days` again: NESO cleans its figures after publishing them, sometimes
        months later, and only new half hours are fetched otherwise. Two or three requests."""
        after = time.time() - days * 86400
        changed = 0
        for _ in range(days * 48 // 10000 + 2):
            last, n = self.fetch_mix(after=after)
            changed += n
            if last is None or last <= after:
                break
            after = last
        return changed

    def fetch_prices(self):
        last = self.store.scalar('SELECT MAX(t) FROM lp') or (PRICE_HISTORY_START - 1800)
        start = last + 1800
        end = min(start + self.price_span, time.time())
        if end - start < 3600:
            return 0
        url = ELEXON + '/balancing/pricing/market-index?from=%s&to=%s&dataProviders=APXMIDP'
        try:
            payload = fetch.get_json(url % (iso_minute(start), iso_minute(end - 60)), timeout=90)
        except FetchError as error:
            if 'HTTP 400' not in str(error) or self.price_span <= 86400:
                raise
            log.info('[longprices] Elexon refused %d days at a time (%s); trying fewer', self.price_span // 86400, error)
            self.price_span = 3 * 86400 if self.price_span > 3 * 86400 else 86400
            return self.fetch_prices()
        items = payload.get('data') if isinstance(payload, dict) else payload
        rows = []
        for item in items if isinstance(items, list) else []:
            if item.get('dataProvider') not in (None, 'APXMIDP'):
                continue
            try:
                t, price, volume = parse_time(item['startTime']), as_float(item.get('price')), as_float(item.get('volume'))
            except (KeyError, ValueError):
                continue
            if price is not None and (volume or 0) > 0:
                rows.append((t, price))
        if not rows:
            rows = [(floor_to(end, 1800) - 1800, None)]  # a gap in the data: step past it
        self.store.write('INSERT OR REPLACE INTO lp (t, price) VALUES (?, ?)', rows, history=False)
        return len(rows)

    def poll_mix(self):
        """Page through the record (10,000 half hours per query) until caught up,
        for up to an hour per poll; then check every six hours."""
        now = time.time()
        last = self.store.scalar('SELECT MAX(t) FROM lh') or 0
        if now - last < 3 * 86400 and now - self.mix_checked < 6 * 3600:
            return
        while time.time() - now < 3600:
            got = self.fetch_mix()
            self.version += 1
            if not got:
                break
            time.sleep(1)
        self.mix_checked = now
        if now - last < 10 * 86400 and now - self.refreshed > REFRESH_EVERY:
            self.refreshed = now
            try:
                if self.refresh_recent():
                    self.version += 1
                    log.info('[longhistory] NESO has revised figures in the last %d days', REFRESH_DAYS)
            except FetchError as error:
                log.info('[longhistory] re-reading the last year failed: %s', error)

    def poll_prices(self):
        """A week of prices per request, one request at a time, until caught up."""
        now = time.time()
        while time.time() - now < 3600:
            got = self.fetch_prices()
            self.version += 1
            if not got:
                break
            time.sleep(0.5)


class Outlook:
    """Elexon's national demand and transmission wind forecasts."""

    def __init__(self):
        self.demand, self.wind, self.version, self.raw, self.demand_kind = [], [], 0, {}, 'national'

    def poll(self):
        now = time.time()
        window = 'from=%s&to=%s' % (iso_minute(floor_to(now, 1800)), iso_minute(now + 49 * 3600))
        errors = []
        try:
            # The current day-ahead forecast, filled out with the latest forecast for each later half hour
            best, tsd, raw = {}, {}, []
            for url in ('/forecast/demand/day-ahead/latest?boundary=N&' + window, '/forecast/demand/day-ahead?boundary=N'):
                payload = fetch.get_json(ELEXON + url)
                items = payload.get('data') if isinstance(payload, dict) else payload
                items = items if isinstance(items, list) else []
                raw.append(len(items))
                for item in items:
                    if item.get('boundary', 'N') != 'N':
                        continue
                    t, published = parse_time(item['startTime']), item.get('publishTime') or ''
                    for table, field in ((best, 'nationalDemand'), (tsd, 'transmissionSystemDemand')):
                        v = as_float(item.get(field))
                        if v is not None and (t not in table or published > table[t][0]):
                            table[t] = (published, v)
            self.raw['demand'] = raw
            # Recent forecasts sometimes leave national demand out; use transmission demand then
            if len(best) >= 0.5 * len(tsd):
                self.demand, self.demand_kind = [(t, best[t][1]) for t in sorted(best)], 'national'
            else:
                self.demand, self.demand_kind = [(t, tsd[t][1]) for t in sorted(tsd)], 'transmission'
        except (FetchError, KeyError, ValueError) as error:
            errors.append('demand: %s' % error)
        try:
            payload = fetch.get_json(ELEXON + '/forecast/generation/wind/latest?' + window)
            self.wind = sorted((parse_time(i['startTime']), as_float(i.get('generation'))) for i in payload.get('data') or []
                               if as_float(i.get('generation')) is not None)
        except (FetchError, KeyError, ValueError) as error:
            errors.append('wind: %s' % error)
        self.version += 1
        if errors:
            raise FetchError('; '.join(errors))
