"""Elexon's imbalance prices, NESO's trades outside the Balancing Mechanism, and Britain's
temperature against normal for the time of year. No keys.

  /balancing/settlement/system-prices/{date}   the price each half hour's imbalances settle at,
                                               and whether the system was short or long
  /datasets/DISBSAD?from&to                    NESO's trades and services outside the Balancing
                                               Mechanism (mostly across the interconnectors)
  /temperature?from&to                         the day's GB temperature, with Elexon's normal,
                                               high and low references (up to 367 days a request)
"""

import time
from datetime import date, timedelta

from . import fetch
from .config import ELEXON, log
from .fetch import FetchError, as_float
from .timeutil import settlement_periods, settlement_start, uk_clock

PRICE_DAYS = 7           # days of imbalance prices fetched on the first start
KEEP_DAYS = 40           # days of imbalance prices and trades kept
TEMP_FROM = 2009         # how far back to look for temperatures (Elexon's record is shorter)
TEMP_EVERY = 3600        # the day's temperature is published at about 16:45


def items(payload):
    data = payload.get('data') if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        raise FetchError('Unexpected response shape from Elexon')
    return data


class Imbalance:
    def __init__(self, store):
        self.store = store
        self.temps_checked = 0
        self.version = 0

    # ---- imbalance prices -----------------------------------------------------

    def fetch_prices(self, day):
        """One UK day's system prices. Returns the number of half hours."""
        rows = []
        for r in items(fetch.get_json(ELEXON + '/balancing/settlement/system-prices/%s?format=json' % day.isoformat(), timeout=60)):
            try:
                sp = int(r['settlementPeriod'])
                price = as_float(r.get('systemSellPrice'))
                buy = as_float(r.get('systemBuyPrice'))
            except (KeyError, TypeError, ValueError):
                continue
            if price is None or not 1 <= sp <= settlement_periods(day):
                continue
            rows.append((settlement_start(day, sp), day.isoformat(), sp, round(price, 2), None if buy is None else round(buy, 2),
                         None if as_float(r.get('netImbalanceVolume')) is None else round(as_float(r['netImbalanceVolume']), 1)))
        self.store.write('INSERT OR REPLACE INTO sysp (t, d, sp, sell, buy, niv) VALUES (?, ?, ?, ?, ?, ?)', rows, history=False)
        return len(rows)

    # ---- trades outside the Balancing Mechanism -----------------------------------

    def fetch_trades(self, start, end):
        """DISBSAD between two times, added up for each half hour: what NESO paid for energy it bought
        (volume above zero) and received for energy it sold (below zero). Returns half hours written."""
        frm = time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(start))
        to = time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(end))
        periods = {}
        for r in items(fetch.get_json(ELEXON + '/datasets/DISBSAD?from=%s&to=%s&format=json' % (frm, to), timeout=60)):
            try:
                key = (str(r['settlementDate'])[:10], int(r['settlementPeriod']))
            except (KeyError, TypeError, ValueError):
                continue
            cost, volume = as_float(r.get('cost')) or 0.0, as_float(r.get('volume')) or 0.0
            p = periods.setdefault(key, [0.0, 0.0, 0.0, 0.0, 0])
            if volume > 0:
                p[0] += cost
                p[2] += volume
            elif volume < 0:
                p[1] += cost
                p[3] += volume
            if volume:
                p[4] += 1
        rows = []
        for (d, sp), (buy, sell, buy_mwh, sell_mwh, n) in periods.items():
            try:
                t = settlement_start(date.fromisoformat(d), sp)
            except ValueError:
                continue
            rows.append((t, d, sp, round(buy, 2), round(sell, 2), round(buy_mwh, 2), round(sell_mwh, 2), n))
        # Each period's actions arrive together, so a period is replaced whole
        self.store.write('INSERT OR REPLACE INTO bsad (t, d, sp, buy, sell, buy_mwh, sell_mwh, n) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                         rows, history=False)
        return len(rows)

    # ---- temperature ---------------------------------------------------------------

    def fetch_temps(self, first, last):
        """Daily temperatures between two dates (at most 367 days). Returns days written."""
        rows = []
        for r in items(fetch.get_json(ELEXON + '/temperature?from=%s&to=%s&format=json' % (first.isoformat(), last.isoformat()), timeout=60)):
            try:
                d = date.fromisoformat(str(r['measurementDate'])[:10])
            except (KeyError, TypeError, ValueError):
                continue
            t = as_float(r.get('temperature'))
            if t is None:
                continue
            ref = [as_float(r.get(k)) for k in ('temperatureReferenceAverage', 'temperatureReferenceHigh', 'temperatureReferenceLow')]
            rows.append((d.isoformat(), round(t, 1), *(None if v is None else round(v, 1) for v in ref)))
        self.store.write('INSERT OR REPLACE INTO temp (d, temp, normal, high, low) VALUES (?, ?, ?, ?, ?)', rows, history=False)
        return len(rows)

    def backfill_temps(self, today):
        """Year by year back from today, until a year comes back empty (Elexon's record starts
        a few years ago). Once, then marked done."""
        if self.store.scalar("SELECT v FROM meta WHERE k = 'temp_backfilled'"):
            return 0
        total, last = 0, today
        while last.year >= TEMP_FROM:
            first = max(date(TEMP_FROM, 1, 1), last - timedelta(days=364))
            got = self.fetch_temps(first, last)
            total += got
            if not got:
                break
            last = first - timedelta(days=1)
            time.sleep(0.5)
        self.store.write("INSERT OR REPLACE INTO meta (k, v) VALUES ('temp_backfilled', ?)", [(str(total),)], history=False)
        log.info('[imbalance] %d days of temperatures since %s', total,
                 self.store.scalar('SELECT MIN(d) FROM temp') or 'none')
        return total

    # ---- the job ---------------------------------------------------------------------

    def poll(self):
        now = time.time()
        today = uk_clock(now)[0]
        errors = []
        # Imbalance prices: today as it fills in; earlier days until they have every half hour
        for back in range(PRICE_DAYS, -1, -1):
            day = today - timedelta(days=back)
            have = self.store.scalar('SELECT COUNT(*) FROM sysp WHERE d = ?', (day.isoformat(),)) or 0
            if back == 0 or have < settlement_periods(day):
                try:
                    self.fetch_prices(day)
                except FetchError as error:
                    errors.append('prices %s: %s' % (day, error))
                    break
        # Trades, a settlement day per request (Elexon refuses spans much over a day)
        for day in (today - timedelta(days=1), today):
            start, stop = settlement_start(day, 1), min(now, settlement_start(day + timedelta(days=1), 1))
            # The autumn clock change makes a 25-hour day: two requests for that one
            spans = [(start, stop)] if stop - start <= 86400 else [(start, start + 43200), (start + 43200, stop)]
            try:
                for a, b in spans:
                    self.fetch_trades(a, b)
            except FetchError as error:
                errors.append('trades %s: %s' % (day, error))
        if now - self.temps_checked >= TEMP_EVERY:
            try:
                self.fetch_temps(today - timedelta(days=10), today)
                self.backfill_temps(today)
                self.temps_checked = now
            except FetchError as error:
                errors.append('temperature: %s' % error)
        cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
        self.store.write('DELETE FROM sysp WHERE d < ?', [(cutoff,)], history=False)
        self.store.write('DELETE FROM bsad WHERE d < ?', [(cutoff,)], history=False)
        self.version += 1
        if len(errors) >= 3 or (errors and not self.store.scalar('SELECT COUNT(*) FROM sysp')):
            raise FetchError('; '.join(errors))
        for e in errors:
            log.info('[imbalance] %s', e)


# ---- for the pages ----------------------------------------------------------------

def day_rows(store, day):
    """[t, imbalance price, net imbalance MWh, market price, trades net cost] for each half hour of a UK day."""
    start, stop = settlement_start(day, 1), settlement_start(day + timedelta(days=1), 1)
    prices = {t: (sell, niv) for t, sell, niv in store.query('SELECT t, sell, niv FROM sysp WHERE t >= ? AND t < ?', (start, stop))}
    market = dict(store.query("SELECT t, v FROM hh WHERE k = 'price' AND t >= ? AND t < ?", (start, stop)))
    trades = {t: (buy or 0) + (sell or 0) for t, buy, sell in store.query('SELECT t, buy, sell FROM bsad WHERE t >= ? AND t < ?', (start, stop))}
    rows = []
    for sp in range(1, settlement_periods(day) + 1):
        t = settlement_start(day, sp)
        p = prices.get(t)
        # NIV is MWh over the half hour: above zero the system was short, below zero long
        rows.append([t, p[0] if p else None, None if not p or p[1] is None else round(p[1]), market.get(t),
                     None if t not in trades else round(trades[t])])
    return rows


def day_summary(store, day):
    rows = day_rows(store, day)
    priced = [r for r in rows if r[1] is not None]
    trades = store.query('SELECT SUM(buy), SUM(sell), SUM(buy_mwh), SUM(sell_mwh), SUM(n), COUNT(*) FROM bsad WHERE d = ?', (day.isoformat(),))[0]
    out = {'d': day.isoformat(), 'periods': len(priced)}
    if priced:
        hi = max(priced, key=lambda r: r[1])
        lo = min(priced, key=lambda r: r[1])
        out.update({'mean': round(sum(r[1] for r in priced) / len(priced), 2), 'high': [hi[0], hi[1]], 'low': [lo[0], lo[1]],
                    'short': sum(1 for r in priced if (r[2] or 0) > 0), 'long': sum(1 for r in priced if (r[2] or 0) < 0)})
        both = [r for r in priced if r[3] is not None]
        if both:
            out['market_mean'] = round(sum(r[3] for r in both) / len(both), 2)
            out['spread'] = round(sum(r[1] - r[3] for r in both) / len(both), 2)
    if trades[5]:
        out['trades'] = {'net': round((trades[0] or 0) + (trades[1] or 0)), 'bought': round(trades[0] or 0), 'sold': round(trades[1] or 0),
                         'bought_mwh': round(trades[2] or 0), 'sold_mwh': round(-(trades[3] or 0)), 'actions': trades[4] or 0}
    return out


def temperatures(store, first, last):
    """{iso date: (temperature, normal)} between two dates."""
    return {d: (t, n) for d, t, n in store.query('SELECT d, temp, normal FROM temp WHERE d >= ? AND d <= ?',
                                                   (first.isoformat(), last.isoformat()))}
