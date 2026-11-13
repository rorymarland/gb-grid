"""What your last year would have cost on Octopus's other tariffs.

Prices come from Octopus's public product API (no key, so they don't count
towards the account's request allowance): the tariffs open to new customers
at each point in the last year, for the region of the tariff you're on.
Each half hour of your actual use is priced at that tariff's rate for that
half hour, plus its daily standing charge.

Compared: Flexible Octopus (electricity and gas), Agile Octopus and Octopus Go
(electricity), and today's Octopus 12M Fixed applied to the whole year, as a
guide to renewing. Tracker isn't open to new customers, and Cosy and Snug need
a heat pump or storage heaters, so they're left out.
"""

import urllib.parse
from bisect import bisect_right
from datetime import timedelta

from .config import log
from .timeutil import iso, parse_time, uk_clock, uk_day, uk_midnight_utc

DAY = 86400
WINDOW_DAYS = 365
LISTING_BACK = (0, 182, 364)   # product lists as they stood now, six months ago and a year ago
FAMILIES = [
    # key, Octopus's display name, fuels, label, note
    ('flexible', 'Flexible Octopus', ('elec', 'gas'), 'Flexible Octopus',
     'Octopus’s standard variable tariff, which follows the price cap.'),
    ('agile', 'Agile Octopus', ('elec',), 'Agile Octopus',
     'A new price every half hour, following the wholesale market.'),
    ('go', 'Octopus Go', ('elec',), 'Octopus Go',
     'A cheap rate for a few hours overnight, for people with an electric car.'),
    ('fixed', 'Octopus 12M Fixed', ('elec', 'gas'), 'Octopus 12M Fixed, at today’s prices',
     'Today’s 12-month fixed price applied to your last year: a guide to renewing.'),
]
UPSERT_RATE = ('INSERT INTO tariff_rate (tariff, kind, start, end, p) VALUES (?, ?, ?, ?, ?) '
               'ON CONFLICT (tariff, kind, start) DO UPDATE SET end = excluded.end, p = excluded.p '
               'WHERE tariff_rate.end IS NOT excluded.end OR tariff_rate.p IS NOT excluded.p')


def region_of(agreements):
    """The region letter at the end of the current tariff code (E-1R-...-N)."""
    for agreement in reversed(agreements or []):
        code = agreement.get('tariff') or ''
        letter = code.rsplit('-', 1)[-1]
        if len(letter) == 1 and letter.isalpha():
            return letter.upper()
    return None


def tariff_code(fuel, product, region):
    return ('E' if fuel == 'elec' else 'G') + '-1R-' + product + '-' + region


def discover(home, now):
    """{family: [(available_from, product code), ...]} from Octopus's product lists."""
    found = {key: {} for key, *_ in FAMILIES}
    for back in LISTING_BACK:
        params = {'is_business': 'false', 'brand': 'OCTOPUS_ENERGY', 'page_size': 100}
        if back:
            params['available_at'] = iso(now - back * DAY)
        for product in home._rest('/v1/products/', params, auth=False, pages=3):
            if product.get('direction') != 'IMPORT' or product.get('is_business') or product.get('is_prepay'):
                continue
            for key, name, *_ in FAMILIES:
                if product.get('display_name') == name and (key != 'fixed' or not back):
                    try:
                        start = parse_time(product['available_from']) if product.get('available_from') else 0
                    except (TypeError, ValueError):
                        start = 0
                    found[key][product['code']] = start
    return {key: sorted((start, code) for code, start in codes.items()) for key, codes in found.items() if codes}


def fetch_rates(home, products, region, now):
    """Unit rates and standing charges for every product found, added to since last time."""
    store = home.store
    window = now - (WINDOW_DAYS + 5) * DAY
    q = lambda value: urllib.parse.quote(value, safe='')  # noqa: E731
    changed = 0
    for key, name, fuels, *_ in FAMILIES:
        for _, product in products.get(key, []):
            for fuel in fuels:
                code = tariff_code(fuel, product, region)
                path = '/v1/products/%s/%s/%s/' % (q(product), 'electricity-tariffs' if fuel == 'elec' else 'gas-tariffs', q(code))
                for kind, leaf in (('unit', 'standard-unit-rates/'), ('standing', 'standing-charges/')):
                    if key == 'fixed':
                        lo = now - 2 * DAY    # only today's price matters
                    else:
                        have = store.scalar('SELECT MAX(start) FROM tariff_rate WHERE tariff = ? AND kind = ?', (code, kind))
                        lo = max(window, have - 2 * DAY) if have and kind == 'unit' else window
                    try:
                        results = home._rest(path + leaf, {'period_from': iso(lo), 'period_to': iso(now + 2 * DAY),
                                                           'page_size': 1500}, auth=False)
                    except Exception as error:   # a product without this fuel or region: skip it
                        if 'HTTP 404' in str(error):
                            continue
                        raise
                    direct = [r for r in results if r.get('payment_method') in (None, 'DIRECT_DEBIT')] or results
                    rows = []
                    for item in direct:
                        try:
                            start = parse_time(item['valid_from'])
                            end = parse_time(item['valid_to']) if item.get('valid_to') else None
                            rows.append((code, kind, start, end, float(item['value_inc_vat'])))
                        except (KeyError, TypeError, ValueError):
                            continue
                    if rows:
                        changed += store.write(UPSERT_RATE, rows, history=False)
    store.write('DELETE FROM tariff_rate WHERE end IS NOT NULL AND end < ?', [(window - 30 * DAY,)], history=False)
    return changed


class RateTable:
    """Rates for one tariff code, looked up by time."""

    def __init__(self, rows):
        self.rows = sorted(rows)
        self.starts = [r[0] for r in self.rows]

    def at(self, t):
        i = bisect_right(self.starts, t) - 1
        if i < 0:
            return None
        start, end, p = self.rows[i]
        return p if end is None or t < end else None


def load_tables(store, codes):
    tables = {}
    for code in codes:
        for kind in ('unit', 'standing'):
            tables[(code, kind)] = RateTable(store.query(
                'SELECT start, end, p FROM tariff_rate WHERE tariff = ? AND kind = ?', (code, kind)))
    return tables


def compare(home, products, region, now):
    """The last 365 whole days of use, priced on each tariff and on yours."""
    if not products or not region:
        return None
    today = uk_clock(now)[0]
    end = uk_midnight_utc(today)
    start = uk_midnight_utc(today - timedelta(days=WINDOW_DAYS))
    rows = {fuel: home.store.query('SELECT t, kwh FROM home WHERE fuel = ? AND t >= ? AND t < ? ORDER BY t', (fuel, start, end))
            for fuel in ('elec', 'gas')}
    if not rows['elec']:
        return None
    yours = home._rates()
    yours_tables = {(fuel, kind): RateTable(yours.get((fuel, kind), [])) for fuel in ('elec', 'gas') for kind in ('unit', 'standing')}
    codes = {tariff_code(fuel, p, region) for key, _, fuels, *_ in FAMILIES for _, p in products.get(key, []) for fuel in fuels}
    tables = load_tables(home.store, codes)

    out = {'from': uk_clock(start)[0].isoformat(), 'to': (today - timedelta(days=1)).isoformat(), 'region': region, 'fuels': {}}
    for fuel in ('elec', 'gas'):
        slots = rows[fuel]
        if not slots:
            continue
        # Each half hour's local day and month, worked out once for every tariff
        slot_days = [uk_day(t) for t, _ in slots]
        months = sorted({d.strftime('%Y-%m') for d in set(slot_days)})
        month_index = {m: i for i, m in enumerate(months)}
        slot_month = [month_index['%04d-%02d' % (d.year, d.month)] for d in slot_days]
        day_list = sorted(set(slot_days))
        days = [uk_midnight_utc(d) for d in day_list]
        day_month = [month_index['%04d-%02d' % (d.year, d.month)] for d in day_list]
        kwh = sum(k for _, k in slots)
        options = [('yours', 'Your tariffs', 'What you paid for this use, at your tariffs’ unit rates and standing charges.',
                    lambda t, kind, f=fuel: yours_tables[(f, kind)].at(t))]
        for key, name, fuels, label, note in FAMILIES:
            if fuel not in fuels or key not in products:
                continue
            versions = products[key]
            fuel_codes = [(from_t, tariff_code(fuel, p, region)) for from_t, p in versions]
            if key == 'fixed':
                code = fuel_codes[-1][1]
                fixed = {kind: tables[(code, kind)].at(now) for kind in ('unit', 'standing')}
                if fixed['unit'] is None:
                    continue
                options.append((key, label, note, lambda t, kind, fx=fixed: fx[kind]))
            else:
                def lookup(t, kind, fc=fuel_codes):
                    # The version open to new customers at the time, else the nearest that has a price
                    ordered = sorted(fc, key=lambda v: (v[0] > t, -v[0] if v[0] <= t else v[0]))
                    for _, code in ordered:
                        p = tables[(code, kind)].at(t)
                        if p is not None:
                            return p
                    return None
                options.append((key, label, note, lookup))
        results = []
        for key, label, note, price in options:
            total, priced, by_month, unit_cost, standing_cost = 0.0, 0, [0.0] * len(months), 0.0, 0.0
            for (t, k), m in zip(slots, slot_month):
                p = price(t, 'unit')
                if p is None:
                    continue
                priced += 1
                unit_cost += k * p
                by_month[m] += k * p
            for d, m in zip(days, day_month):
                p = price(d, 'standing')
                if p is not None:
                    standing_cost += p
                    by_month[m] += p
            total = unit_cost + standing_cost
            complete = priced >= len(slots) * 0.99
            results.append({'key': key, 'name': label, 'note': note, 'complete': complete,
                            'cost': round(total, 1) if complete else None,
                            'unit': round(unit_cost / kwh, 2) if complete and kwh else None,
                            'standing': round(standing_cost, 1) if complete else None,
                            'months': [round(v, 1) for v in by_month] if complete else None})
        base = results[0]['cost']
        for r in results:
            r['diff'] = None if r['cost'] is None or base is None else round(r['cost'] - base, 1)
        out['fuels'][fuel] = {'kwh': round(kwh, 1), 'days': len(days), 'months': months, 'options': results}
    return out


def run(home, now):
    """The daily step: find the products, top up their prices, and price the year."""
    agreements = ((home.meters or {}).get('elec') or {}).get('agreements') or ((home.meters or {}).get('gas') or {}).get('agreements')
    region = region_of(agreements)
    if not region:
        log.info('[home] no region in the tariff code, so no tariff comparison')
        return
    products = discover(home, now)
    fetch_rates(home, products, region, now)
    home.products, home.region = products, region
    home.compare_key = None
