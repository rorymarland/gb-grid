"""Polling the national feeds: generation, frequency, prices, carbon, solar, embedded generation, notices and regions."""

import json
import time
from datetime import datetime, timezone

from . import fetch
from .config import CARBON, DDU_PATH, ELEXON, EMBEDDED_RESOURCE_ID, HISTORY_DAYS, NESO, PVLIVE, REGION_ID, log
from .reference import FUELS, INTERCONNECTORS, MIX_FUELS, PES_REGIONS
from .timeutil import floor_to, iso, iso_minute, parse_settlement_date, parse_time, settlement_start
from .fetch import FetchError, as_float, http_get, rows_from_csv
from .store import UPSERT_CI, UPSERT_FREQ, UPSERT_GEN, UPSERT_HH, UPSERT_REG, UPSERT_WARN


# --------------------------------------------------------------------------
# Ingestion: one function per upstream source
# --------------------------------------------------------------------------

class Ingest:
    def __init__(self, store, status):
        self.store = store
        self.status = status
        self.unknown_fuels = set()
        self.embedded_url = None
        self.ddu_validators = {}
        self.region_summary = None
        self.regions_past_checked = 0
        self.regional_solar_checked = 0

    # ---- Elexon FUELINST (generation by fuel type, 5-minute) -------------

    def fuelinst(self, start, end, timeout=45):
        url = (ELEXON + '/datasets/FUELINST/stream?publishDateTimeFrom=' + iso(start)
               + '&publishDateTimeTo=' + iso(end))
        items = fetch.get_json(url, timeout=timeout)
        if isinstance(items, dict) and isinstance(items.get('data'), list):
            items = items['data']
        if not isinstance(items, list):
            raise FetchError('Unexpected FUELINST response shape')
        latest = {}
        skipped = 0
        for item in sorted((i for i in items if isinstance(i, dict)),
                           key=lambda i: str(i.get('publishTime', ''))):
            try:
                t = floor_to(parse_time(item['startTime']), 300)
                fuel = str(item['fuelType']).strip().upper()
                mw = float(item['generation'])
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
            if fuel not in FUELS and fuel not in INTERCONNECTORS and fuel not in self.unknown_fuels:
                self.unknown_fuels.add(fuel)
                log.info('[generation] new fuel type %r (counted as %s)', fuel,
                         'an interconnector' if fuel.startswith('INT') else 'Other')
            latest[(t, fuel)] = (t, fuel, mw)
        if skipped:
            log.debug('[generation] skipped %d malformed records', skipped)
        self.store.write(UPSERT_GEN, list(latest.values()))
        return len(latest)

    def poll_generation(self):
        now = time.time()
        self.fuelinst(now - 1800, now)

    def backfill_generation(self, days):
        end = time.time()
        for _ in range(days):
            start = end - 86400
            have = self.store.scalar('SELECT COUNT(DISTINCT t) FROM gen WHERE t >= ? AND t < ?', (start, end)) or 0
            if have < 270:
                count = self.fuelinst(start, end, timeout=120)
                log.info('[backfill] generation %s to %s: %d records', iso_minute(start), iso_minute(end), count)
                time.sleep(1)
            end = start

    # ---- Elexon system frequency (15-second readings) --------------------

    def poll_frequency(self):
        now = time.time()
        last = self.store.scalar('SELECT MAX(t) FROM freq')
        since = max(last - 120, now - 3600) if last else now - 3600
        url = ELEXON + '/system/frequency/stream?from=' + iso(since) + '&to=' + iso(now)
        items = fetch.get_json(url)
        if isinstance(items, dict) and isinstance(items.get('data'), list):
            items = items['data']
        if not isinstance(items, list):
            raise FetchError('Unexpected frequency response shape')
        rows = []
        for item in items:
            try:
                t = float(parse_time(item['measurementTime']))
                hz = float(item['frequency'])
            except (KeyError, TypeError, ValueError):
                continue
            if 45.0 < hz < 55.0:
                rows.append((t, hz))
        self.store.write(UPSERT_FREQ, rows, history=False)

    # ---- Elexon market index price (half-hourly) -------------------------

    def market_index(self, start, end, timeout=45):
        url = (ELEXON + '/balancing/pricing/market-index?from=' + iso(start) + '&to=' + iso(end)
               + '&dataProviders=APXMIDP')
        payload = fetch.get_json(url, timeout=timeout)
        items = payload.get('data') if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise FetchError('Unexpected market index response shape')
        rows = []
        for item in items:
            try:
                t = floor_to(parse_time(item['startTime']), 1800)
                price = as_float(item.get('price'))
                volume = as_float(item.get('volume'))
            except (KeyError, TypeError, ValueError):
                continue
            # A zero price with zero traded volume means no trades, not free power
            if price is None or (price == 0 and (volume or 0) == 0):
                continue
            rows.append(('price', t, price))
        self.store.write(UPSERT_HH, rows)
        return len(rows)

    def poll_price(self):
        now = time.time()
        self.market_index(now - 6 * 3600, now)

    def backfill_price(self, days):
        end = time.time()
        for _ in range(days):
            start = end - 86400
            have = self.store.scalar("SELECT COUNT(*) FROM hh WHERE k = 'price' AND t >= ? AND t < ?", (start, end)) or 0
            if have < 40:
                count = self.market_index(start, end, timeout=90)
                log.info('[backfill] prices %s to %s: %d periods', iso_minute(start), iso_minute(end), count)
                time.sleep(0.5)
            end = start

    # ---- NESO Carbon Intensity API ---------------------------------------

    @staticmethod
    def _ci_entries(payload, region=0):
        """Return (entries, region_name) for national or regional responses.

        Accepts every shape the API uses: a national list of half hours; a
        region object {regionid, shortname, data: [...]}; a list wrapping
        region objects; and the all-regions form, where each half hour
        carries a "regions" list (the matching region is picked out).
        """
        data = payload.get('data') if isinstance(payload, dict) else None
        name = None
        if isinstance(data, dict):
            name = data.get('shortname')
            data = data.get('data')
        elif isinstance(data, list) and data and isinstance(data[0], dict) \
                and 'intensity' not in data[0] and isinstance(data[0].get('data'), list):
            match = next((item for item in data if str(item.get('regionid')) == str(region)), data[0])
            name = match.get('shortname')
            data = match['data']
        if not isinstance(data, list):
            raise FetchError('Unexpected carbon intensity response shape')
        entries = []
        for entry in data:
            if isinstance(entry, dict) and isinstance(entry.get('regions'), list):
                match = next((item for item in entry['regions']
                              if isinstance(item, dict) and str(item.get('regionid')) == str(region)), None)
                if match:
                    name = name or match.get('shortname')
                    entries.append({'from': entry.get('from'), 'intensity': match.get('intensity'),
                                    'generationmix': match.get('generationmix')})
            else:
                entries.append(entry)
        return entries, name

    def _ingest_ci(self, region, payload, require=False):
        entries, name = self._ci_entries(payload, region)
        rows = []
        for entry in entries:
            try:
                t = floor_to(parse_time(entry['from']), 1800)
                intensity = entry.get('intensity') or {}
                forecast = as_float(intensity.get('forecast'))
                actual = as_float(intensity.get('actual'))
                index = intensity.get('index')
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if forecast is None and actual is None:
                continue
            rows.append((region, t, forecast, actual, str(index).lower() if index else None))
        if require and not rows:
            sample = json.dumps(payload)[:160] if payload is not None else 'nothing'
            raise FetchError('Carbon intensity response had no usable half hours (' + str(len(entries))
                             + ' entries). Start of response: ' + sample)
        self.store.write(UPSERT_CI, rows)
        return rows, name

    def poll_carbon(self):
        now = time.time()
        self._ingest_ci(0, fetch.get_json(CARBON + '/intensity/' + iso_minute(now) + '/pt24h'))
        self._ingest_ci(0, fetch.get_json(CARBON + '/intensity/' + iso_minute(now) + '/fw48h'), require=True)

    def backfill_carbon(self, days, region=0):
        end = time.time()
        for _ in range(days):
            start = end - 86400
            have = self.store.scalar('SELECT COUNT(*) FROM ci WHERE region = ? AND t >= ? AND t < ?',
                                     (region, start, end)) or 0
            if have < 44:
                span = iso_minute(start) + '/' + iso_minute(end)
                url = (CARBON + '/regional/intensity/' + span + '/regionid/' + str(region)) if region \
                    else (CARBON + '/intensity/' + span)
                rows, _ = self._ingest_ci(region, fetch.get_json(url, timeout=60))
                log.info('[backfill] %s %s to %s: %d periods', 'region' if region else 'carbon',
                         iso_minute(start), iso_minute(end), len(rows))
                time.sleep(0.5)
            end = start

    def poll_region(self):
        now = time.time()
        base = CARBON + '/regional/intensity/' + iso_minute(now)
        suffix = '/regionid/' + str(REGION_ID)
        payload = fetch.get_json(base + '/fw48h' + suffix)
        entries, name = self._ci_entries(payload, REGION_ID)
        if name:
            self.store.set_meta('region_name', name)
        self._remember_region_mix(entries, now)
        rows, _ = self._ingest_ci(REGION_ID, payload, require=True)
        values = [row[2] if row[2] is not None else row[3] for row in rows]
        summary = (len(rows), round(min(values)), round(max(values)))
        if summary != self.region_summary:
            log.info('[region] %s forecast: %d half hours, %d to %d g/kWh',
                     name or 'Region ' + str(REGION_ID), *summary)
            self.region_summary = summary
        try:
            self._ingest_ci(REGION_ID, fetch.get_json(base + '/pt24h' + suffix))
        except FetchError as error:
            # The past 24 hours are a nice-to-have; keep the forecast working
            log.debug('[region] past 24 hours unavailable: %s', error)

    def _remember_region_mix(self, entries, now):
        """Keep the regional generation mix for the current half hour."""
        best = None
        for entry in entries:
            try:
                t = floor_to(parse_time(entry['from']), 1800)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            mix = entry.get('generationmix')
            if isinstance(mix, list) and t <= now and (best is None or t > best[0]):
                best = (t, mix)
        if best is None:
            return
        parts = []
        for item in best[1]:
            try:
                parts.append([str(item['fuel']).lower(), round(float(item['perc']), 1)])
            except (KeyError, TypeError, ValueError):
                continue
        if parts:
            self.store.set_meta('region_mix', json.dumps({'t': best[0], 'mix': parts}, separators=(',', ':')))

    # ---- Sheffield Solar PV_Live (GB solar, half-hourly) -----------------

    def pvlive(self, start, end, timeout=45, pes=0, key='pv'):
        start = floor_to(start, 1800)
        end = floor_to(end, 1800) + 1800
        url = PVLIVE + '/pes/' + str(pes) + '?start=' + iso(start) + '&end=' + iso(end) + '&period=30'
        payload = fetch.get_json(url, timeout=timeout)
        if not isinstance(payload, dict) or 'data' not in payload or 'meta' not in payload:
            raise FetchError('Unexpected PV_Live response shape')
        meta = list(payload['meta'])
        try:
            time_col = meta.index('datetime_gmt')
            value_col = meta.index('generation_mw')
        except ValueError:
            raise FetchError('PV_Live response is missing expected fields')
        rows = []
        for record in payload['data'] or []:
            try:
                value = as_float(record[value_col])
                if value is None:
                    continue
                # PV_Live labels each half hour with its END time
                t = floor_to(parse_time(record[time_col]), 1800) - 1800
            except (IndexError, TypeError, ValueError):
                continue
            rows.append((key, t, max(0.0, value)))
        self.store.write(UPSERT_HH, rows, history=key == 'pv')
        return len(rows)

    def poll_solar(self):
        now = time.time()
        self.pvlive(now - 3 * 3600, now)

    def backfill_solar(self, days):
        now = time.time()
        have = self.store.scalar("SELECT COUNT(*) FROM hh WHERE k = 'pv' AND t >= ?", (now - days * 86400,)) or 0
        if have < days * 48 * 0.9:
            count = self.pvlive(now - days * 86400, now, timeout=120)
            log.info('[backfill] solar: %d half hours', count)

    # ---- NESO embedded wind and solar forecast (hourly) ------------------

    @staticmethod
    def _period_start(row, fields):
        """Start time of a row, preferring settlement date and period."""
        if 'SETTLEMENT_DATE' in fields and 'SETTLEMENT_PERIOD' in fields:
            day = parse_settlement_date(row[fields['SETTLEMENT_DATE']])
            period = int(float(row[fields['SETTLEMENT_PERIOD']]))
            if not 1 <= period <= 50:
                raise ValueError('settlement period out of range')
            return settlement_start(day, period)
        if 'DATE_GMT' in fields and 'TIME_GMT' in fields:
            day = parse_settlement_date(row[fields['DATE_GMT']])
            hours, minutes = str(row[fields['TIME_GMT']]).strip().split(':')[:2]
            base = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
            return floor_to(base + int(hours) * 3600 + int(minutes) * 60, 1800)
        raise ValueError('no usable time columns')

    def poll_embedded(self):
        meta = fetch.get_json(NESO + '/api/3/action/resource_show?id=' + EMBEDDED_RESOURCE_ID)
        try:
            url = meta['result']['url']
        except (KeyError, TypeError):
            raise FetchError('NESO resource_show response has no download URL')
        if url == self.embedded_url:
            return  # unchanged since the last successful ingest
        _, body, _ = http_get(url, accept='text/csv', timeout=60)
        fields, records = rows_from_csv(body)
        wind_col = fields.get('EMBEDDED_WIND_FORECAST')
        solar_col = fields.get('EMBEDDED_SOLAR_FORECAST')
        if not wind_col or not solar_col:
            raise FetchError('Embedded forecast CSV is missing forecast columns: ' + ', '.join(sorted(fields)))
        rows = []
        for record in records:
            try:
                t = self._period_start(record, fields)
                wind = as_float(record[wind_col])
                solar = as_float(record[solar_col])
            except (KeyError, TypeError, ValueError):
                continue
            if wind is not None:
                rows.append(('fc_wind', t, max(0.0, wind)))
            if solar is not None:
                rows.append(('fc_solar', t, max(0.0, solar)))
        if not rows:
            raise FetchError('Embedded forecast CSV contained no usable rows')
        self.store.write(UPSERT_HH, rows)
        self.embedded_url = url

    # ---- NESO Demand Data Update (daily; embedded history) ---------------

    def poll_ddu(self):
        headers = {}
        if self.ddu_validators.get('etag'):
            headers['If-None-Match'] = self.ddu_validators['etag']
        if self.ddu_validators.get('modified'):
            headers['If-Modified-Since'] = self.ddu_validators['modified']
        status, body, response_headers = http_get(NESO + DDU_PATH, accept='text/csv', timeout=120,
                                                  extra_headers=headers)
        if status == 304:
            return
        fields, records = rows_from_csv(body)
        needed = ['SETTLEMENT_DATE', 'SETTLEMENT_PERIOD', 'EMBEDDED_WIND_GENERATION', 'EMBEDDED_SOLAR_GENERATION']
        missing = [name for name in needed if name not in fields]
        if missing:
            raise FetchError('Demand Data Update CSV is missing ' + ', '.join(missing))
        indicator_col = fields.get('FORECAST_ACTUAL_INDICATOR')
        earliest = time.time() - (HISTORY_DAYS + 2) * 86400
        rows = []
        for record in records:
            try:
                t = self._period_start(record, fields)
                if t < earliest:
                    continue
                wind = as_float(record[fields['EMBEDDED_WIND_GENERATION']])
                solar = as_float(record[fields['EMBEDDED_SOLAR_GENERATION']])
            except (KeyError, TypeError, ValueError):
                continue
            actual = 1.0 if indicator_col and str(record[indicator_col]).strip().upper().startswith('A') else 0.0
            if wind is not None:
                rows.append(('ddu_wind', t, max(0.0, wind)))
            if solar is not None:
                rows.append(('ddu_solar', t, max(0.0, solar)))
            rows.append(('ddu_actual', t, actual))
        self.store.write(UPSERT_HH, rows)
        self.ddu_validators = {
            'etag': response_headers.get('ETag'),
            'modified': response_headers.get('Last-Modified'),
        }

    # ---- NESO system warnings, published through Elexon ------------------

    def system_warnings(self, start, end, timeout=60):
        url = (ELEXON + '/system/warnings?publishDateTimeFrom=' + iso_minute(start)
               + '&publishDateTimeTo=' + iso_minute(end))
        payload = fetch.get_json(url, timeout=timeout)
        items = payload.get('data') if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise FetchError('Unexpected system warnings response shape')
        rows = []
        for item in items:
            try:
                t = parse_time(item['publishTime'])
                kind = str(item['warningType']).strip().upper()
                body = str(item.get('warningText') or '').replace('\r', '').replace('\\n', '\n').strip()
            except (KeyError, TypeError, ValueError):
                continue
            rows.append((t, kind, body))
        self.store.write(UPSERT_WARN, rows, history=False)
        return len(rows)

    def poll_warnings(self):
        now = time.time()
        last = self.store.scalar('SELECT MAX(t) FROM warn')
        self.system_warnings(max(last - 6 * 3600, now - 30 * 86400) if last else now - 30 * 86400, now + 60)

    # ---- Regions: every region's carbon intensity and solar output -------

    def _ingest_regions(self, payload):
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise FetchError('Unexpected regional carbon intensity response shape')
        rows = []
        for entry in data:
            try:
                t = floor_to(parse_time(entry['from']), 1800)
                regions = entry['regions']
            except (KeyError, TypeError, ValueError):
                continue
            for region in regions if isinstance(regions, list) else []:
                try:
                    rid = int(region['regionid'])
                    intensity = region.get('intensity') or {}
                    value = as_float(intensity.get('forecast'))
                    if value is None:
                        value = as_float(intensity.get('actual'))
                except (KeyError, TypeError, ValueError):
                    continue
                index = intensity.get('index')
                mix = {}
                for item in region.get('generationmix') or []:
                    try:
                        mix[str(item['fuel']).lower()] = float(item['perc'])
                    except (KeyError, TypeError, ValueError):
                        continue
                text = ','.join('%g' % round(mix.get(fuel, 0.0), 1) for fuel in MIX_FUELS) if mix else None
                rows.append((t, rid, value, str(index).lower() if index else None, text))
        self.store.write(UPSERT_REG, rows, history=False)
        return len(rows)

    def poll_regions(self):
        now = time.time()
        base = CARBON + '/regional/intensity/' + iso_minute(now)
        if not self._ingest_regions(fetch.get_json(base + '/fw48h', timeout=90)):
            raise FetchError('The regional forecast had no usable half hours')
        if now - self.regions_past_checked > 3 * 3600:
            try:
                self._ingest_regions(fetch.get_json(base + '/pt24h', timeout=90))
                self.regions_past_checked = now
            except FetchError as error:
                log.debug('[regions] past 24 hours unavailable: %s', error)
        # Sheffield Solar's estimate for each region: the last day on the
        # first run, then the last few hours (revisions included)
        start = now - (26 * 3600 if now - self.regional_solar_checked > 26 * 3600 else 4 * 3600)
        failures = []
        for pes in sorted(PES_REGIONS):
            try:
                self.pvlive(start, now, pes=pes, key='rpv%d' % pes)
            except FetchError as error:
                failures.append(str(error))
            time.sleep(0.2)
        if len(failures) == len(PES_REGIONS):
            raise FetchError('Regional solar unavailable: ' + failures[0])
        self.regional_solar_checked = now
