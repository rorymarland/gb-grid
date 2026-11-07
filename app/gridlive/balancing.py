"""Balancing: bids, offers, payments and constraint costs."""

import threading
import time
from datetime import timedelta

from . import fetch
from .config import ELEXON, NESO, log
from .timeutil import settlement_periods, settlement_start
from .fetch import FetchError, as_float
from .store import UPSERT_BAL
from .units import settlement_period_at


# --------------------------------------------------------------------------
# Balancing: what the grid operator pays units to change their output
# --------------------------------------------------------------------------

class Balancing:
    """Per-unit volumes and payments for each finished half hour (Elexon's
    indicative figures, about 20 minutes after it ends), bid and offer prices
    for the current half hour, and NESO's daily constraint costs."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.prices = {}            # unit -> (bid price, offer price) for the current half hour
        self.prices_period = None
        self.constraints = []       # [(date, cost, volume, {category: cost})]
        self.constraints_checked = 0
        self.version = 0

    def _rows(self, path):
        payload = fetch.get_json(ELEXON + path, timeout=90)
        items = payload.get('data') if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise FetchError('Unexpected response shape from ' + path.split('?')[0])
        return items

    def fetch_period(self, day, period):
        stamp = '%s/%d' % (day.isoformat(), period)
        units = {}
        for side in ('bid', 'offer'):
            for item in self._rows('/balancing/settlement/indicative/cashflows/all/%s/%s' % (side, stamp)):
                unit = item.get('nationalGridBmUnit') or item.get('bmUnit')
                cash = as_float(item.get('totalCashflow'))
                if unit and cash is not None:
                    entry = units.setdefault(unit, [item.get('bmUnit') or '', 0.0, 0.0, 0.0, 0.0])
                    entry[3 if side == 'bid' else 4] += cash
            for item in self._rows('/balancing/settlement/acceptance/volumes/all/%s/%s' % (side, stamp)):
                unit = item.get('nationalGridBmUnit') or item.get('bmUnit')
                volume = as_float(item.get('totalVolumeAccepted'))
                if unit and volume is not None:
                    entry = units.setdefault(unit, [item.get('bmUnit') or '', 0.0, 0.0, 0.0, 0.0])
                    entry[1 if side == 'bid' else 2] += volume
        rows = [(day.isoformat(), period, unit, e[0], round(e[1], 3), round(e[2], 3), round(e[3], 2), round(e[4], 2))
                for unit, e in units.items()]
        self.store.write(UPSERT_BAL, rows, history=False)
        self.store.write('INSERT OR REPLACE INTO balp (d, sp, t, fetched) VALUES (?, ?, ?, ?)',
                         [(day.isoformat(), period, settlement_start(day, period), int(time.time()))], history=False)
        return len(rows)

    def fetch_prices(self, day, period):
        prices = {}
        for item in self._rows('/balancing/bid-offer/all?settlementDate=%s&settlementPeriod=%d' % (day.isoformat(), period)):
            unit, pair = item.get('nationalGridBmUnit'), item.get('pairId')
            if not unit or pair not in (-1, 1):
                continue
            entry = prices.setdefault(unit, [None, None])
            if pair == -1:
                entry[0] = as_float(item.get('bid'))
            else:
                entry[1] = as_float(item.get('offer'))
        with self.lock:
            self.prices, self.prices_period = prices, (day, period)

    def fetch_constraints(self):
        result = fetch.get_json(NESO + '/api/3/action/package_show?id=constraint-breakdown', timeout=60).get('result') or {}
        resources = [r for r in result.get('resources', []) if r.get('datastore_active')]
        rows = []
        for resource in resources[-2:]:
            data = fetch.get_json(NESO + '/api/3/action/datastore_search?resource_id=%s&limit=400&sort=_id%%20desc' % resource['id'],
                            timeout=60).get('result') or {}
            rows += data.get('records') or []
        days = {}
        for row in rows:
            day = str(row.get('Date') or '')[:10]
            if not day:
                continue
            costs = {key[:-5].strip(): as_float(value) or 0.0 for key, value in row.items() if key.endswith(' cost')}
            volume = sum(as_float(value) or 0.0 for key, value in row.items() if key.endswith(' volume') and 'Thermal' in key)
            days[day] = (day, round(sum(costs.values())), round(volume), {k: round(v) for k, v in costs.items()})
        with self.lock:
            self.constraints = [days[k] for k in sorted(days)][-120:]
            self.version += 1

    def poll(self):
        now = time.time()
        errors = []
        day, period = settlement_period_at(now)
        if self.prices_period != (day, period):
            try:
                self.fetch_prices(day, period)
            except FetchError as error:
                errors.append('bid and offer prices: ' + str(error))
        # Finished half hours from yesterday (UK) onwards, oldest first, a few per poll
        done = {(d, sp) for d, sp in self.store.query('SELECT d, sp FROM balp WHERE d >= ?', ((day - timedelta(days=1)).isoformat(),))}
        todo = []
        for back in (1, 0):
            d = day - timedelta(days=back)
            for sp in range(1, settlement_periods(d) + 1):
                start = settlement_start(d, sp)
                if start + 1800 + 1200 <= now and (d.isoformat(), sp) not in done:
                    todo.append((d, sp))
        fetched = 0
        for d, sp in todo[-24:] if len(todo) > 24 and not done else todo[:24]:
            try:
                fetched += 1
                self.fetch_period(d, sp)
            except FetchError as error:
                if settlement_start(d, sp) > now - 3 * 3600:
                    break  # recent half hours may simply not be published yet
                errors.append('half hour %s %d: %s' % (d.isoformat(), sp, error))
                break
            time.sleep(0.25)
        if now - self.constraints_checked > 6 * 3600:
            try:
                self.fetch_constraints()
                self.constraints_checked = now
            except FetchError as error:
                errors.append('constraint costs: ' + str(error))
        with self.lock:
            self.version += 1
        if errors and not fetched and not self.prices:
            raise FetchError('; '.join(errors))
        if errors:
            log.debug('[balancing] %s', '; '.join(errors))
