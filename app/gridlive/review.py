"""The year in review: the grid's year and your home's year, from data the dashboard
already keeps. A finished year is saved as it stood, so it can be read again after the
timeline and NESO's data have moved on. No upstream requests of its own."""

import json
import threading
import time
from datetime import date, timedelta

from .config import GAS_KG_PER_KWH, log
from .events import MAJOR
from .timeutil import uk_clock, uk_day, uk_midnight_utc

FUELS = ['gas', 'coal', 'nuclear', 'wind', 'solar', 'biomass', 'hydro', 'imports', 'storage', 'other']
ZERO_CARBON = ('nuclear', 'wind', 'solar', 'hydro')
# Each fuel's half-hourly average MW; wind includes the smaller wind farms on local networks
SUMS = ('SUM(gas), SUM(coal), SUM(nuclear), SUM(COALESCE(wind, 0) + COALESCE(wind_emb, 0)), SUM(solar), SUM(biomass), '
        'SUM(hydro), SUM(imports), SUM(storage), SUM(other)')
TOTAL = ('(COALESCE(gas, 0) + COALESCE(coal, 0) + COALESCE(nuclear, 0) + COALESCE(wind, 0) + COALESCE(wind_emb, 0) + '
         'COALESCE(solar, 0) + COALESCE(biomass, 0) + COALESCE(hydro, 0) + COALESCE(imports, 0) + COALESCE(storage, 0) + '
         'COALESCE(other, 0))')
# Each fuel's carbon (g/kWh, from NESO's carbon intensity methodology), to check NESO's published
# intensity against its own mix: the two usually move together, within a few per cent
FACTORS = {'gas': 394, 'coal': 937, 'biomass': 120, 'other': 300}
ODD = 0.15                  # a month's intensity this far out of line with its mix is flagged
HOLD_UNTIL = (4, 1)         # a year with flagged months isn't saved before 1 April, giving NESO time to correct it
FREEZE_AFTER = 14 * 86400   # a year is saved this long after it ends: NESO's history and meter readings run late
FIRST_YEAR = 2009


def bounds(year):
    """UTC epochs of the UK midnights that start and end a calendar year."""
    return uk_midnight_utc(date(year, 1, 1)), uk_midnight_utc(date(year + 1, 1, 1))


def year_earlier(d):
    """The same date a year before (28 February for 29 February)."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return date(d.year - 1, 2, 28)


def _pct(part, whole):
    return round(part / whole, 4) if whole else None


# ---- the grid's year -----------------------------------------------------------

def mix_figures(store, start, end):
    """Energy by fuel, its shares, carbon intensity and prices between two times."""
    row = store.query('SELECT %s, SUM(ci * %s), SUM(CASE WHEN ci > 0 THEN %s END), SUM(wind), COUNT(*) FROM lh WHERE t >= ? AND t < ?'
                      % (SUMS, TOTAL, TOTAL), (start, end))[0]
    if not row[-1]:
        return None
    energy = {f: (row[i] or 0) / 2 for i, f in enumerate(FUELS)}       # MWh: each half hour's MW for half an hour
    total = sum(energy.values())
    price = store.query('SELECT AVG(price), SUM(CASE WHEN price < 0 THEN 1 ELSE 0 END), COUNT(*) FROM lp '
                        'WHERE t >= ? AND t < ? AND price IS NOT NULL', (start, end))[0]
    return {
        'half_hours': row[-1],
        'twh': round(total / 1e6, 2),
        'fuel_twh': {f: round(v / 1e6, 2) for f, v in energy.items()},
        'mix': {f: _pct(v, total) for f, v in energy.items()},
        'zero': _pct(sum(energy[f] for f in ZERO_CARBON), total),
        # Weighted by what was generated, so busy winter evenings count for more than quiet summer nights
        'ci': round(row[10] / row[11]) if row[11] else None,
        'price': None if price[0] is None else round(price[0], 2),
        'negative_hours': (price[1] or 0) / 2 if price[2] else None,
        'wind_tx_twh': round((row[12] or 0) / 2 / 1e6, 2),    # transmission-connected wind farms only
    }


def implied(mix):
    """The intensity a mix implies at fixed factors (imports and zero-carbon sources count as none)."""
    return sum(FACTORS[f] * (mix.get(f) or 0) for f in FACTORS)


def month_bounds(year, m):
    return uk_midnight_utc(date(year, m, 1)), uk_midnight_utc(date(year + (m == 12), 1 if m == 12 else m + 1, 1))


def ci_check(store, year, months):
    """Months whose published intensity is out of line with their mix, against how the two
    related over the 24 months before the year. None without enough history to judge."""
    ratios = []
    for back in range(1, 25):
        y, m = year - (back + 11) // 12, (0 - back) % 12 + 1
        f = mix_figures(store, *month_bounds(y, m))
        if f and f['ci'] and implied(f['mix']) > 20 and f['half_hours'] > 1000:
            ratios.append(f['ci'] / implied(f['mix']))
    if len(ratios) < 12:
        return None
    ratios.sort()
    baseline = ratios[len(ratios) // 2]
    odd = []
    for m in months:
        guess = implied(m['mix'])
        if m['ci'] and guess > 20:
            m['ratio'] = round(m['ci'] / guess / baseline, 3)
            if abs(m['ratio'] - 1) > ODD:
                odd.append(m['m'])
    return {'baseline': round(baseline, 3), 'odd': odd,
            'excess': round(sum(m['ratio'] for m in months if m['m'] in odd) / len(odd) - 1, 3) if odd else None}


def covered(store, start, end):
    """The end of the last whole UK day of history inside [start, end), or None."""
    last = store.scalar('SELECT MAX(t) FROM lh WHERE t >= ? AND t < ?', (start, end))
    if last is None:
        return None
    day = uk_day(last)
    stop = uk_midnight_utc(day + timedelta(days=1))
    if last + 1800 < stop:         # the last day isn't complete yet
        stop = uk_midnight_utc(day)
    return stop if stop > start else None


def year_bests(store, start, end, records):
    """The year's cleanest, windiest and so on, each marked where it's also the all-time record."""
    q = store.query
    found = []
    record_at = {r['key']: r['t'] for r in records or []}

    def add(key, title, unit, row, span, better):
        if row and row[0] is not None and row[1] is not None:
            found.append({'key': key, 'title': title, 'unit': unit, 't': row[0], 'value': round(row[1], 1), 'span': span,
                          'better': better, 'record': record_at.get(key) == row[0]})

    where = 'WHERE t >= %d AND t < %d' % (start, end)
    full_day = 'GROUP BY d HAVING COUNT(*) >= 46'
    one = lambda sql: (q(sql) or [None])[0]  # noqa: E731
    add('cleanest-day', 'Cleanest day', 'g/kWh',
        one('SELECT ukday(t) d, AVG(ci) a FROM lh %s AND ci > 0 %s ORDER BY a LIMIT 1' % (where, full_day)), 'day', 'low')
    add('dirtiest-day', 'Dirtiest day', 'g/kWh',
        one('SELECT ukday(t) d, AVG(ci) a FROM lh %s AND ci > 0 %s ORDER BY a DESC LIMIT 1' % (where, full_day)), 'day', 'high')
    add('windiest-day', 'Windiest day', 'MW',
        one('SELECT ukday(t) d, AVG(COALESCE(wind, 0) + COALESCE(wind_emb, 0)) a FROM lh %s %s ORDER BY a DESC LIMIT 1' % (where, full_day)), 'day', 'high')
    add('stillest-day', 'Stillest day', 'MW',
        one('SELECT ukday(t) d, AVG(COALESCE(wind, 0) + COALESCE(wind_emb, 0)) a FROM lh %s %s ORDER BY a LIMIT 1' % (where, full_day)), 'day', 'low')
    add('cleanest-hh', 'Cleanest half hour', 'g/kWh', one('SELECT t, ci FROM lh %s AND ci > 0 ORDER BY ci, t DESC LIMIT 1' % where), 'hh', 'low')
    add('wind-peak', 'Most wind power', 'MW',
        one('SELECT t, COALESCE(wind, 0) + COALESCE(wind_emb, 0) w FROM lh %s ORDER BY w DESC LIMIT 1' % where), 'hh', 'high')
    add('solar-peak', 'Most solar power', 'MW', one('SELECT t, solar FROM lh %s AND solar IS NOT NULL ORDER BY solar DESC LIMIT 1' % where), 'hh', 'high')
    add('zero-carbon', 'Highest zero-carbon share', '%',
        one('SELECT t, 100.0 * (COALESCE(nuclear, 0) + COALESCE(wind, 0) + COALESCE(wind_emb, 0) + COALESCE(solar, 0) + COALESCE(hydro, 0)) / generation s '
            'FROM lh %s AND generation > 10000 ORDER BY s DESC LIMIT 1' % where), 'hh', 'high')
    add('demand-high', 'Most generation needed', 'MW', one('SELECT t, generation FROM lh %s ORDER BY generation DESC LIMIT 1' % where), 'hh', 'high')
    add('demand-low', 'Least generation needed', 'MW',
        one('SELECT t, generation FROM lh %s AND generation > 5000 ORDER BY generation LIMIT 1' % where), 'hh', 'low')
    add('price-high', 'Highest wholesale price', '£/MWh',
        one('SELECT t, price FROM lp %s AND price IS NOT NULL ORDER BY price DESC LIMIT 1' % where), 'hh', 'high')
    add('price-low', 'Lowest wholesale price', '£/MWh',
        one('SELECT t, price FROM lp %s AND price IS NOT NULL ORDER BY price LIMIT 1' % where), 'hh', 'low')
    return found


def grid_year(store, year, records=None):
    start, end = bounds(year)
    stop = covered(store, start, end)
    if stop is None:
        return None
    days = (uk_day(stop - 1) - date(year, 1, 1)).days + 1
    this = mix_figures(store, start, stop)
    # The year before over the same days, so a year so far compares like with like
    p_start = bounds(year - 1)[0]
    p_stop = min(uk_midnight_utc(date(year - 1, 1, 1) + timedelta(days=days)), bounds(year - 1)[1])
    before = mix_figures(store, p_start, p_stop) if year > FIRST_YEAR else None
    if before and before['half_hours'] < 0.95 * (p_stop - p_start) / 1800:
        before = None              # not enough of the year before to compare with

    months = []
    for m in range(1, 13):
        m_start = uk_midnight_utc(date(year, m, 1))
        m_end = uk_midnight_utc(date(year + (m == 12), 1 if m == 12 else m + 1, 1))
        if m_start >= stop:
            break
        f = mix_figures(store, m_start, min(m_end, stop))
        if f:
            months.append({'m': m, 't': m_start, 'whole': stop >= m_end, 'twh': f['twh'], 'mix': f['mix'], 'zero': f['zero'],
                           'ci': f['ci'], 'price': f['price'], 'wind_twh': f['fuel_twh']['wind']})

    check = ci_check(store, year, months)

    # Wind turned down and constraint costs (from the Constraints page's data, when it has the year)
    d0, d1 = date(year, 1, 1).isoformat(), uk_day(stop - 1).isoformat()
    down = store.query('SELECT SUM(mwh), MIN(d), COUNT(DISTINCT d) FROM wcurt WHERE d >= ? AND d <= ?', (d0, d1))[0]
    cost = store.query('SELECT SUM(thermal + voltage + inertia + loss), MIN(d), MAX(d), COUNT(*) FROM cday WHERE d >= ? AND d <= ?', (d0, d1))[0]
    constraints = None
    if down[0] or cost[0]:
        constraints = {
            'down_twh': None if not down[0] else round(down[0] / 1e6, 2), 'down_from': down[1],
            # Of what transmission-connected wind farms made plus what they were paid not to
            'down_share': _pct(down[0], down[0] + this['wind_tx_twh'] * 1e6) if down[0] and down[1] == d0 else None,
            'cost': None if not cost[0] else round(cost[0]), 'cost_from': cost[1], 'cost_to': cost[2],
        }

    # Every whole year before, for the long view (this one so far)
    history = []
    for y in range(FIRST_YEAR, year):
        y_start, y_end = bounds(y)
        f = mix_figures(store, y_start, y_end)
        if f and f['half_hours'] >= 0.95 * (y_end - y_start) / 1800:
            history.append({'year': y, 'ci': f['ci'], 'zero': f['zero'], 'wind': f['mix']['wind'], 'gas': f['mix']['gas'], 'coal': f['mix']['coal']})
    history.append({'year': year, 'ci': this['ci'], 'zero': this['zero'], 'wind': this['mix']['wind'], 'gas': this['mix']['gas'],
                    'coal': this['mix']['coal'], 'so_far': stop < end})

    last_coal = store.scalar('SELECT MAX(t) FROM lh WHERE coal > 20 AND t < ?', (stop,))
    # The timeline's major events. Frequency events can come every day in a windy spell,
    # so they're counted, with the biggest listed, and everything else is listed
    moments, frequency = [], []
    for t, kind, title, detail, data in store.query('SELECT t, kind, title, detail, data FROM ev WHERE t >= ? AND t < ? AND level >= ? ORDER BY t',
                                                    (start, end, MAJOR)):
        item = {'t': t, 'kind': kind, 'title': json.loads(title), 'detail': json.loads(detail or '[]')}
        if kind == 'frequency':
            try:
                change = abs(float(json.loads(data or '{}').get('change') or 0))
            except (TypeError, ValueError):
                change = 0.0
            frequency.append((change, item))
        else:
            moments.append(item)
    biggest = max(frequency, key=lambda pair: pair[0])[1] if frequency else None
    moments = sorted(moments[-15:] + ([biggest] if biggest else []), key=lambda m: m['t'])
    first_event = store.scalar('SELECT MIN(t) FROM ev')
    return {
        'from': start, 'to': stop, 'days': days, 'whole': stop >= end,
        'figures': this, 'before': before, 'months': months, 'history': history, 'ci_check': check,
        'bests': year_bests(store, start, stop, records),
        'constraints': constraints,
        'last_coal': last_coal,
        'moments': moments, 'frequency_events': len(frequency), 'biggest_frequency': biggest['t'] if biggest else None,
        'moments_from': first_event if first_event and first_event > start else None,
    }


# ---- your home's year ------------------------------------------------------------

def home_year(home, year, now):
    """Your electricity and gas for the year, by month, with carbon, cost and timing."""
    if not home or not home.enabled:
        return None
    start, end = bounds(year)
    summaries = home._day_summaries(start, end)
    if not summaries:
        return None
    items = sorted(summaries.items())
    first, last = items[0][0], items[-1][0]
    total = home._total(date(year, 1, 1), [s for _, s in items], (date(year + 1, 1, 1) - date(year, 1, 1)).days)
    months = []
    for m in range(1, 13):
        chunk = [s for d, s in items if d.month == m]
        if chunk:
            m_start = date(year, m, 1)
            length = ((date(year + (m == 12), 1 if m == 12 else m + 1, 1)) - m_start).days
            months.append(dict(home._total(m_start, chunk, length), m=m))
    busiest = max((s for _, s in items if s['e'] is not None and s['e_full']), key=lambda s: s['e'], default=None)
    # Timing: your electricity's carbon against the grid's average over the same half hours
    weighted, weight, flat, n = 0.0, 0.0, 0.0, 0
    for kwh, ci in home.store.query("SELECT kwh, ci FROM home WHERE fuel = 'elec' AND t >= ? AND t < ? AND ci IS NOT NULL", (start, end)):
        weighted += kwh * ci
        weight += kwh
        flat += ci
        n += 1
    timing = {'yours': round(weighted / weight, 1), 'flat': round(flat / n, 1)} if weight > 0 and n >= 30 * 48 else None
    # The same span of the year before, when the meter readings cover it
    before = None
    p_first, p_last = year_earlier(first), year_earlier(last)
    previous = home._day_summaries(uk_midnight_utc(p_first), uk_midnight_utc(p_last + timedelta(days=1)))
    span = (p_last - p_first).days + 1
    if previous and sum(1 for s in previous.values() if s['e_full']) >= 0.95 * span:
        before = home._total(p_first, list(previous.values()), span)
    sessions = None
    with home.lock:
        joined = dict((home.sessions or {}).get('joined') or {})
    year_sessions = [j for j in joined.values() if start <= j['start'] < end and j['type'] in ('TURN_DOWN', 'TURN_UP')]
    if year_sessions:
        given = [j['given'] for j in year_sessions if j['given'] is not None]
        sessions = {'joined': len(year_sessions), 'points': int(sum(given)) if given else None,
                    'waiting': sum(1 for j in year_sessions if j['given'] is None and j['end'] < now)}
    return {
        'from': first.isoformat(), 'to': last.isoformat(), 'partial_start': first > date(year, 1, 1),
        'total': total, 'months': months, 'before': before,
        'busiest': None if not busiest else {'d': busiest['d'], 'e': busiest['e']},
        'kg': None if total['e_kg'] is None and total['g_kg'] is None else round((total['e_kg'] or 0) + (total['g_kg'] or 0), 1),
        'timing': timing, 'sessions': sessions, 'gas_factor': GAS_KG_PER_KWH,
    }


# ---- the page ------------------------------------------------------------------

class Review:
    """Builds /api/review, caching the year in progress and saving finished years."""

    def __init__(self, store, grid, home=None):
        self.store, self.grid, self.home = store, grid, home
        self.lock = threading.Lock()
        self.cache = {}

    def years(self, now):
        """Years with any history, newest first, and any saved ones."""
        first = self.store.scalar('SELECT MIN(t) FROM lh')
        this_year = uk_clock(now)[0].year
        saved = {y for (y,) in self.store.query('SELECT year FROM review')}
        oldest = max(FIRST_YEAR, uk_day(first).year) if first else this_year
        return sorted(set(range(oldest, this_year + 1)) | saved, reverse=True)

    def build(self, year, now):
        summary = self.grid.longterm_summary() if self.grid else {}
        records = summary.get('records') if summary.get('ready') else None
        return {'year': year, 'made': int(now), 'grid': grid_year(self.store, year, records), 'home': home_year(self.home, year, now)}

    def view(self, year, now):
        this_year = uk_clock(now)[0].year
        if year is None:
            # Until the middle of January, the year just finished
            today = uk_clock(now)[0]
            year = this_year - 1 if today < date(this_year, 1, 15) else this_year
        years = self.years(now)
        if year not in years:
            return {'year': year, 'years': years, 'grid': None, 'home': None, 'saved': None}
        saved = self.store.query('SELECT made, body FROM review WHERE year = ?', (year,))
        if saved:
            body = json.loads(saved[0][1])
            body.update({'years': years, 'saved': saved[0][0]})
            return body
        last = self.store.scalar('SELECT MAX(t) FROM lh')
        version = (last, self.home.data_version if self.home and self.home.enabled else None,
                   self.store.scalar('SELECT MAX(d) FROM cday'), self.store.scalar('SELECT MAX(d) FROM wcurt'), int(now // 3600))
        with self.lock:
            hit = self.cache.get(year)
        if hit and hit[0] == version:
            body = hit[1]
        else:
            started = time.time()
            body = self.build(year, now)
            log.debug('[review] %d built in %.1f s', year, time.time() - started)
            with self.lock:
                self.cache = {year: (version, body)}
        # Saved once the late data is in: two weeks after the year ends, with the history complete
        end = bounds(year)[1]
        odd = bool(body['grid'] and (body['grid'].get('ci_check') or {}).get('odd'))
        held = odd and uk_clock(now)[0] < date(year + 1, *HOLD_UNTIL)
        if now >= end + FREEZE_AFTER and not held and body['grid'] and body['grid']['whole'] and self.grid and self.grid.longterm_extent().get('complete'):
            self.store.write('INSERT OR IGNORE INTO review (year, made, body) VALUES (?, ?, ?)',
                             [(year, int(now), json.dumps(body, separators=(',', ':')))], history=False)
            log.info('[review] saved %d as it stands', year)
            return dict(body, years=years, saved=int(now))
        return dict(body, years=years, saved=None)
