"""System inertia: NESO's half-hourly figures since April 2017, and an estimate of it now.

NESO publishes "GB System Inertia" (NESO Open Data Licence) as one datastore resource per
financial year, each with Settlement Date, Settlement Period, Outturn Inertia and Market Provided
Inertia in GVA·s. Outturn is what the grid had; market provided is what it would have had without
NESO's own actions to keep enough spinning. The figures come after the day, and in July 2026 NESO
paused the dataset while it reviews it, so the latest can be months old.

Each resource is downloaded whole, once, then again only when NESO changes it (its last_modified
moves); while that happens, the newest year is read from a few days before the latest figure kept.
That's one request an hour to check, and about ten to fill in the history the first time.

The estimate: a straight-line fit of NESO's figures on the generation mix in its historic
generation mix (gas, nuclear, biomass, hydro, pumped storage, wind, solar and imports, in GW),
fitted on a year of figures, then tested on the 120 days of figures after it, which it hasn't
seen. It is shown only if it was within ESTIMATE_MAX_ERROR on average there; the figure
shown comes from the same fit on the latest year, applied to the mix now."""

import time
from datetime import date
from urllib.parse import quote

from . import fetch
from .config import NESO, log
from .fetch import FetchError, as_float
from .timeutil import settlement_start, uk_clock, uk_midnight_utc

PACKAGE = 'system-inertia'
PAGE = 32000                 # NESO's datastore gives at least this many rows a request; a year is 17,568 at most
REREAD_DAYS = 3              # how far back the newest year is read again when it changes
FEATURES = ['gas', 'nuclear', 'biomass', 'hydro', 'storage', 'wind', 'solar', 'imports']
TEST_DAYS = 120
TRAIN_DAYS = 365
ESTIMATE_MAX_ERROR = 10.0    # % mean absolute error on the test months, above which no estimate is shown
STALE = 7 * 86400            # NESO's latest figure older than this: say how old


def inertia_resources(store=None):
    """NESO's yearly inertia resources, oldest first. With a store, also notes whether NESO's
    description of the dataset says it is paused."""
    result = fetch.get_json(NESO + '/api/3/action/package_show?id=' + PACKAGE, timeout=60).get('result') or {}
    if store is not None:
        store.set_meta('inertia_paused', '1' if 'paused' in str(result.get('notes') or '').lower() else '0')
    resources = [r for r in result.get('resources', []) if r.get('datastore_active')
                 and 'inertia' in str(r.get('name') or '').lower() and 'cost' not in str(r.get('name') or '').lower()]
    if not resources:
        raise FetchError('NESO %s has no inertia resources' % PACKAGE)
    # Named like "GB System Inertia - 2026-2027"
    return sorted(resources, key=lambda r: (str(r.get('name') or ''), str(r.get('created') or '')))


def parse_records(records):
    """[(t, outturn, market)] from datastore records, whatever the key spelling or date format
    (2017 to 2021 have '2017-04-01T00:00:00', later years '2021-04-01')."""
    rows = []
    for record in records or []:
        keys = {str(k).lower().replace(' ', '').replace('_', ''): v for k, v in record.items()}
        day = str(keys.get('settlementdate') or '')[:10]
        outturn = as_float(keys.get('outturninertia'))
        try:
            period = int(float(keys.get('settlementperiod')))
            t = settlement_start(date.fromisoformat(day), period)
        except (TypeError, ValueError):
            continue
        if outturn is None or outturn <= 0 or not 1 <= period <= 50:
            continue
        rows.append((t, outturn, as_float(keys.get('marketprovidedinertia'))))
    return rows


def download(resource_id, since=None):
    """Rows of one resource: all of it, or from the UK day `since` (an ISO date) on."""
    if since:
        sql = 'SELECT * FROM "%s" WHERE "Settlement Date" >= \'%s\' ORDER BY "_id" LIMIT %d' % (resource_id, since, PAGE)
        result = fetch.get_json(NESO + '/api/3/action/datastore_search_sql?sql=' + quote(sql), timeout=120).get('result') or {}
        return parse_records(result.get('records'))
    rows, offset = [], 0
    while True:
        result = fetch.get_json(NESO + '/api/3/action/datastore_search?resource_id=%s&limit=%d&offset=%d&sort=_id'
                                % (resource_id, PAGE, offset), timeout=180).get('result') or {}
        records = result.get('records') or []
        rows += parse_records(records)
        offset += len(records)
        if not records or offset >= int(result.get('total') or 0):
            return rows
        time.sleep(1)


def poll(store, spacing=1.0):
    """Bring the inertia table up to date with NESO. Returns the number of rows written."""
    resources = inertia_resources(store)
    written = 0
    for i, resource in enumerate(resources):
        stamp = str(resource.get('last_modified') or resource.get('metadata_modified') or '')
        key = 'inertia_res:' + resource['id']
        seen = store.get_meta(key)
        if seen == stamp and stamp:
            continue
        newest = i == len(resources) - 1
        since = None
        if newest and seen is not None:
            # Already have this year: just its last few days, again
            latest = store.scalar('SELECT MAX(t) FROM inertia')
            if latest:
                since = time.strftime('%Y-%m-%d', time.gmtime(latest - REREAD_DAYS * 86400))
        try:
            rows = download(resource['id'], since)
        except FetchError:
            if not since:
                raise
            rows = download(resource['id'])
        if rows:
            written += store.write('INSERT OR REPLACE INTO inertia (t, v, mkt) VALUES (?, ?, ?)', rows, history=False)
            log.info('[inertia] %s: %d half hours to %s', resource.get('name'), len(rows),
                     time.strftime('%Y-%m-%d', time.gmtime(max(r[0] for r in rows))))
        store.set_meta(key, stamp)
        time.sleep(spacing)
    return written


def inertia_at(store, t):
    v = store.scalar('SELECT v FROM inertia WHERE t = ?', (int(t // 1800) * 1800,))
    return None if v is None else round(v)


# ---- the estimate ---------------------------------------------------------------------

def lh_features(row):
    """Features (GW) from a historic generation mix row (gas, coal, nuclear, biomass, hydro,
    storage, wind, wind_emb, solar, imports in MW), or None if any is missing."""
    gas, coal, nuclear, biomass, hydro, storage, wind, wind_emb, solar, imports = row
    if any(v is None for v in (gas, nuclear, biomass, hydro, wind, solar, imports)):
        return None
    return [((gas or 0) + (coal or 0)) / 1000, nuclear / 1000, biomass / 1000, hydro / 1000, max(0, storage or 0) / 1000,
            (wind + (wind_emb or 0)) / 1000, solar / 1000, imports / 1000]


def mix_features(m):
    """The same features from one of Grid.mix's results (MW)."""
    return [(m['ccgt'] + m['ocgt'] + m['coal']) / 1000, m['nuclear'] / 1000, m['biomass'] / 1000, m['hydro'] / 1000,
            max(0.0, m['pumped']) / 1000, m['wind'] / 1000, m['solar'] / 1000, m['imports'] / 1000]


def solve(a, b):
    """x for a x = b, by Gaussian elimination with partial pivoting; None if singular."""
    n = len(b)
    m = [list(a[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            if f:
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


def fit(samples, ridge=1e-6):
    """Least squares coefficients [intercept, one per feature] for samples [(features, y)].
    A whisker of ridge keeps a feature that never moves (coal, lately) from making it singular."""
    if not samples:
        return None
    k = len(samples[0][0]) + 1
    xtx = [[0.0] * k for _ in range(k)]
    xty = [0.0] * k
    for features, y in samples:
        row = [1.0] + list(features)
        for i in range(k):
            ri = row[i]
            if not ri:
                continue
            xty[i] += ri * y
            xi = xtx[i]
            for j in range(i, k):
                xi[j] += ri * row[j]
    for i in range(k):
        for j in range(i):
            xtx[i][j] = xtx[j][i]
        if i:
            xtx[i][i] += ridge * len(samples)
    return solve(xtx, xty)


def predict(coef, features):
    return coef[0] + sum(c * f for c, f in zip(coef[1:], features))


def errors(coef, samples):
    """Mean absolute error, mean absolute % error, 90th percentile absolute error, mean error (bias)."""
    diffs = sorted(abs(predict(coef, f) - y) for f, y in samples)
    n = len(samples)
    return {'mae': sum(diffs) / n, 'mape': 100 * sum(abs(predict(coef, f) - y) / y for f, y in samples) / n,
            'p90': diffs[min(n - 1, int(0.9 * n))], 'bias': sum(predict(coef, f) - y for f, y in samples) / n, 'n': n}


def build_model(store):
    """The fit and how well it did on figures it hadn't seen, or {'ok': False, 'why': ...}. The
    test is NESO's last TEST_DAYS of half hours that have a generation mix beside them, and the
    trial fit the TRAIN_DAYS of them before that; counted in half hours, so a gap in NESO's
    figures doesn't shrink either."""
    test_n, train_n = TEST_DAYS * 48, TRAIN_DAYS * 48
    rows = store.query('SELECT i.t, i.v, l.gas, l.coal, l.nuclear, l.biomass, l.hydro, l.storage, l.wind, l.wind_emb, l.solar, l.imports '
                       'FROM inertia i JOIN lh l ON l.t = i.t ORDER BY i.t DESC LIMIT ?', (test_n + train_n + 2000,))
    samples = []
    for row in reversed(rows):
        features = lh_features(row[2:])
        if features is not None:
            samples.append((row[0], features, row[1]))
    if not samples:
        return {'ok': False, 'why': 'no overlap yet between NESO’s inertia figures and its historic generation mix'}
    samples = samples[-(test_n + train_n):]
    if len(samples) < test_n + 0.6 * train_n:
        return {'ok': False, 'why': 'not enough overlap yet between NESO’s inertia figures and its historic generation mix'}
    train, test = samples[:-test_n], samples[-test_n:]
    trial = fit([(f, y) for _, f, y in train])
    if trial is None:
        return {'ok': False, 'why': 'the fit failed'}
    tested = errors(trial, [(f, y) for _, f, y in test])
    # The one used: the same fit over the latest year, test months included
    coef = fit([(f, y) for _, f, y in samples[-train_n:]]) or trial
    ok = tested['mape'] <= ESTIMATE_MAX_ERROR
    return {'ok': ok, 'coef': coef, 'tested': tested, 'test_from': test[0][0], 'test_to': test[-1][0] + 1800,
            'fitted_to': samples[-1][0] + 1800,
            'why': None if ok else 'it was out by %.0f%% on average over the months it was tested on' % tested['mape']}


# ---- the payload ----------------------------------------------------------------------

def month_starts(first, last):
    """UTC epochs of the starts of the UK months from the one holding `first` to the one after `last`."""
    day = uk_clock(first)[0].replace(day=1)
    out = []
    while True:
        start = uk_midnight_utc(day)
        out.append(start)
        if start > last:
            return out
        day = day.replace(year=day.year + day.month // 12, month=day.month % 12 + 1)


def monthly(store):
    """[[start of UK month, mean, lowest, highest, market-provided mean]] for each month with at
    least 20 days of figures."""
    rows = store.query('SELECT t, v, mkt FROM inertia ORDER BY t')
    if not rows:
        return []
    starts = month_starts(rows[0][0], rows[-1][0])
    out, i, entry = [], 0, None
    for t, v, mkt in rows:
        while t >= starts[i + 1]:
            i += 1
        if entry is None or entry[0] != starts[i]:
            entry = [starts[i], 0.0, 0, v, v, 0.0, 0]
            out.append(entry)
        entry[1] += v
        entry[2] += 1
        entry[3] = min(entry[3], v)
        entry[4] = max(entry[4], v)
        if mkt is not None:
            entry[5] += mkt
            entry[6] += 1
    return [[e[0], round(e[1] / e[2], 1), round(e[3]), round(e[4]), round(e[5] / e[6], 1) if e[6] else None]
            for e in out if e[2] >= 48 * 20]


def published(store, now):
    """NESO's figures: the latest, the week to then, the monthly history and the lowest half hours."""
    latest = store.query('SELECT t, v, mkt FROM inertia ORDER BY t DESC LIMIT 1')
    if not latest:
        return None
    t, v, mkt = latest[0]
    week = store.query('SELECT t, v FROM inertia WHERE t > ? ORDER BY t', (t - 7 * 86400,))
    first = store.scalar('SELECT MIN(t) FROM inertia')
    # The five lowest, one a day: a low night is several low half hours running
    lowest, days = [], set()
    for low_t, low_v in store.query('SELECT t, v FROM inertia ORDER BY v, t LIMIT 1000'):
        day = uk_clock(low_t)[0]
        if day not in days:
            days.add(day)
            lowest.append((low_t, low_v))
            if len(lowest) == 5:
                break
    year = store.query('SELECT AVG(v), AVG(mkt), MIN(v) FROM inertia WHERE t > ?', (t - 365 * 86400,))[0]
    return {'t': t, 'gvas': round(v), 'market': round(mkt) if mkt is not None else None, 'first': first,
            'stale': now - t > STALE, 'age_days': int((now - t) // 86400), 'paused': store.get_meta('inertia_paused') == '1',
            'week': {'t': [r[0] for r in week], 'v': [round(r[1]) for r in week]},
            'low': min(round(r[1]) for r in week), 'high': max(round(r[1]) for r in week),
            'year': {'mean': round(year[0]), 'market_mean': round(year[1]) if year[1] is not None else None, 'min': round(year[2])},
            'monthly': monthly(store), 'lowest': [{'t': r[0], 'gvas': round(r[1])} for r in lowest]}


def estimate(model, mixes):
    """The estimate for each (t, Grid.mix result): {'t': [...], 'v': [...]}."""
    ts, vs = [], []
    for t, m in mixes:
        ts.append(t)
        vs.append(round(predict(model['coef'], mix_features(m))))
    return {'t': ts, 'v': vs}
