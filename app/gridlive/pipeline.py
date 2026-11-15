"""What's being built: NESO's Transmission Entry Capacity (TEC) register.

Every project with an agreement to connect to the transmission network, or to use it from a
distribution network (an "embedded" agreement), one row each: its capacity connected now, the
change agreed (a new project's whole capacity), the date that takes effect, how far along it is
(Scoping, Awaiting Consents, Consents Approved, Under Construction/Commissioning, Built) and the
kind of plant, which NESO writes as one or more types joined by semicolons.

NESO updates it about twice a week (NESO Open Data Licence). The register is about 2,200 rows,
read whole in one request when its last_modified moves; the check is one request every six hours.
Each new version's totals are kept, so the page can say how the queue has moved."""

import json
import time
from datetime import date

from . import fetch
from .config import NESO, log
from .fetch import FetchError, as_float
from .timeutil import uk_clock

PACKAGE = 'transmission-entry-capacity-tec-register'
LIMIT = 32000
# How far along a project is, first to last; 'Built' is connected
STAGES = ['Scoping', 'Awaiting Consents', 'Consents Approved', 'Under Construction/Commissioning', 'Built']
STAGE_KEYS = {'scoping': 'scoping', 'awaiting consents': 'awaiting', 'consents approved': 'consented',
              'under construction/commissioning': 'building', 'built': 'built'}
PIPELINE = ['building', 'consented', 'awaiting', 'scoping']   # nearest to connecting first
KINDS = ['battery', 'solar', 'offshore', 'onshore', 'gas', 'nuclear', 'pumped', 'hydro', 'interconnector', 'other', 'mixed', 'demand']
KIND_NAMES = {'battery': 'Batteries and other storage', 'solar': 'Solar', 'offshore': 'Offshore wind', 'onshore': 'Onshore wind', 'gas': 'Gas',
              'nuclear': 'Nuclear', 'pumped': 'Pumped storage', 'hydro': 'Hydro', 'interconnector': 'Interconnectors',
              'other': 'Other generation', 'mixed': 'Mixed: two or more kinds', 'demand': 'Demand and network kit'}


def plant_kind(text):
    """One kind for a Plant Type such as 'PV Array (Photo Voltaic/solar);Energy Storage System'.
    Storage alongside solar or wind counts with them; two kinds of generation together is 'mixed';
    demand, reactive compensation and the like alone is 'demand'."""
    found = set()
    for part in str(text or '').split(';'):
        p = part.strip().lower()
        if not p:
            continue
        if 'pump' in p:
            found.add('pumped')
        elif 'storage' in p or 'battery' in p:
            found.add('storage')
        elif 'pv' in p or 'solar' in p or 'photo' in p:
            found.add('solar')
        elif 'wind' in p and 'off' in p:
            found.add('offshore')
        elif 'wind' in p:
            found.add('onshore')
        elif 'nuclear' in p:
            found.add('nuclear')
        elif 'hydrogen' in p:
            found.add('other')
        elif 'hydro' in p:
            found.add('hydro')
        elif 'interconnector' in p:
            found.add('interconnector')
        elif any(w in p for w in ('ccgt', 'ocgt', 'gas', 'reciprocating', 'chp', 'combined heat', 'agt', 'turbine')):
            found.add('gas')
        elif any(w in p for w in ('demand', 'reactive', 'compensat', 'synchronous', 'statcom', 'svc', 'condenser', 'substation')):
            found.add('demand')
        else:
            found.add('other')      # tidal, biomass, energy from waste, oil, fuel cells, thermal
    generation = found - {'storage', 'demand'}
    if not found:
        return 'other'
    if not generation:
        return 'battery' if 'storage' in found else 'demand'
    if len(generation) == 1:
        return generation.pop()
    return 'mixed'


def parse_day(value):
    """'2027-03-31' or '2027-03-31T00:00:00' to an ISO date, else None."""
    text = str(value or '')[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def parse_rows(records):
    """tec rows from datastore records."""
    out = {}
    for r in records or []:
        keys = {str(k).lower().replace(' ', '').replace('_', ''): v for k, v in r.items()}
        num = str(keys.get('projectnumber') or keys.get('projectid') or keys.get('id') or '').strip()
        status = STAGE_KEYS.get(str(keys.get('projectstatus') or '').strip().lower())
        if not num or not status:
            continue
        connected = as_float(keys.get('mwconnected')) or 0.0
        change = as_float(keys.get('mwincrease/decrease')) or 0.0
        total = as_float(keys.get('cumulativetotalcapacity(mw)'))
        plant = str(keys.get('planttype') or '').strip()
        out[num] = (num, str(keys.get('projectname') or '').strip(), str(keys.get('customername') or '').strip(),
                    str(keys.get('connectionsite') or '').strip(), str(keys.get('hostto') or '').strip(),
                    str(keys.get('agreementtype') or '').strip(), status, plant, plant_kind(plant),
                    connected, change, total if total is not None else connected + change,
                    parse_day(keys.get('mweffectivefrom')), str(keys.get('gate') or '').strip())
    return list(out.values())


class Pipeline:
    def __init__(self, store):
        self.store = store

    def resource(self):
        result = fetch.get_json(NESO + '/api/3/action/package_show?id=' + PACKAGE, timeout=60).get('result') or {}
        resources = [r for r in result.get('resources', []) if r.get('datastore_active')]
        named = [r for r in resources if 'tec register' in str(r.get('name') or '').lower()]
        resources = named or resources
        if not resources:
            raise FetchError('NESO has no TEC register in its datastore')
        return sorted(resources, key=lambda r: str(r.get('last_modified') or ''))[-1]

    def poll(self):
        res = self.resource()
        stamp = str(res.get('last_modified') or res.get('metadata_modified') or '')
        if stamp and self.store.get_meta('tec_version') == stamp:
            return 0
        result = fetch.get_json(NESO + '/api/3/action/datastore_search?resource_id=%s&limit=%d' % (res['id'], LIMIT),
                                timeout=180).get('result') or {}
        records = result.get('records') or []
        if int(result.get('total') or 0) > len(records):
            raise FetchError('The TEC register has more rows than one request gives')
        rows = parse_rows(records)
        if len(rows) < 100:
            raise FetchError('The TEC register looked too short (%d projects)' % len(rows))
        with self.store.lock:
            self.store.db.execute('BEGIN')
            try:
                self.store.db.execute('DELETE FROM tec')
                self.store.db.executemany('INSERT INTO tec (num, name, customer, site, host, agreement, status, plant, kind, connected, '
                                          'change, total, eff, gate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
                self.store.db.execute('COMMIT')
            except Exception:
                self.store.db.execute('ROLLBACK')
                raise
        # Each version's totals, by the day NESO published it, for the trend
        day = uk_clock(time.time())[0].isoformat()
        stamped = parse_day(stamp) or day
        snapshot = {}
        for row in rows:
            status, kind, connected, change = row[6], row[8], row[9], row[10]
            key = (stamped, status, kind)
            mw, n = snapshot.get(key, (0.0, 0))
            snapshot[key] = (mw + (connected if status == 'built' else change), n + 1)
        self.store.write('INSERT OR REPLACE INTO tec_hist (d, status, kind, mw, n) VALUES (?, ?, ?, ?, ?)',
                         [(d, s, k, round(mw, 1), n) for (d, s, k), (mw, n) in snapshot.items()], history=False)
        self.store.set_meta('tec_version', stamp)
        self.store.set_meta('tec_checked', json.dumps({'version': stamp, 'rows': len(rows), 'at': int(time.time())}))
        log.info('[pipeline] TEC register of %s: %d projects', stamped, len(rows))
        return len(rows)


# ---- the page ----------------------------------------------------------------------------

def view(store, now):
    """What /api/pipeline returns."""
    rows = store.query('SELECT num, name, customer, site, host, agreement, status, plant, kind, connected, change, total, eff, gate FROM tec')
    if not rows:
        return {'ready': False}
    today = uk_clock(now)[0]
    year_ahead = today.replace(year=today.year + 1).isoformat() if not (today.month == 2 and today.day == 29) else \
        today.replace(year=today.year + 1, day=28).isoformat()
    today_iso = today.isoformat()
    kinds = {k: {'kind': k, 'name': KIND_NAMES[k], 'built': 0.0, 'building': 0.0, 'consented': 0.0, 'awaiting': 0.0, 'scoping': 0.0, 'n': 0}
             for k in KINDS}
    years, late, undated, soon, gates = {}, {'mw': 0.0, 'n': 0}, {'mw': 0.0, 'n': 0}, [], {}
    totals = {'built': 0.0, 'pipeline': 0.0, 'n_pipeline': 0, 'year_ahead': 0.0, 'n_year_ahead': 0, 'embedded': 0.0, 'increase_built': 0.0}
    for num, name, customer, site, host, agreement, status, plant, kind, connected, change, total, eff, gate in rows:
        k = kinds[kind]
        if status == 'built':
            k['built'] += connected
            totals['built'] += connected
            # A built project can still have more agreed: an extension waiting to connect
            if change > 0:
                totals['increase_built'] += change
            continue
        k[status] += change
        k['n'] += 1
        totals['pipeline'] += change
        totals['n_pipeline'] += 1
        if 'embedded' in agreement.lower():
            totals['embedded'] += change
        gates[gate or ''] = gates.get(gate or '', 0.0) + change
        if not eff:
            undated['mw'] += change
            undated['n'] += 1
        else:
            if eff < today_iso:
                late['mw'] += change
                late['n'] += 1
            else:
                y = years.setdefault(int(eff[:4]), {s: 0.0 for s in PIPELINE})
                y[status] += change
            if today_iso <= eff < year_ahead:
                totals['year_ahead'] += change
                totals['n_year_ahead'] += 1
                if change > 0:
                    soon.append({'num': num, 'name': name, 'customer': customer, 'site': site, 'kind': kind, 'plant': plant,
                                 'mw': round(change), 'status': status, 'eff': eff, 'gate': gate, 'embedded': 'embedded' in agreement.lower()})
    soon.sort(key=lambda p: (-p['mw'], p['eff']))
    order = sorted((k for k in kinds.values() if k['built'] + k['building'] + k['consented'] + k['awaiting'] + k['scoping'] > 0),
                   key=lambda k: -(k['building'] + k['consented'] + k['awaiting'] + k['scoping']))
    for k in order:
        for s in ('built', 'building', 'consented', 'awaiting', 'scoping'):
            k[s] = round(k[s])
    first_year = today.year
    year_list = [{'year': y, **{s: round(v) for s, v in years[y].items()}} for y in sorted(years) if y >= first_year]
    # How the queue has moved: each snapshot's pipeline total
    hist = {}
    for d, status, kind, mw in store.query('SELECT d, status, kind, mw FROM tec_hist'):
        entry = hist.setdefault(d, {'d': d, 'built': 0.0, 'pipeline': 0.0})
        entry['built' if status == 'built' else 'pipeline'] += mw
    trend = [{'d': e['d'], 'built': round(e['built']), 'pipeline': round(e['pipeline'])} for _, e in sorted(hist.items())]
    checked = {}
    try:
        checked = json.loads(store.get_meta('tec_checked') or '{}')
    except ValueError:
        pass
    return {
        'ready': True, 'now': int(now), 'version': parse_day(checked.get('version')), 'projects': len(rows),
        'totals': {k: round(v) if isinstance(v, float) else v for k, v in totals.items()},
        'kinds': order, 'years': year_list, 'late': {'mw': round(late['mw']), 'n': late['n']},
        'undated': {'mw': round(undated['mw']), 'n': undated['n']},
        'soon': soon[:25], 'gates': {k: round(v) for k, v in gates.items()}, 'trend': trend[-60:],
        'stages': STAGES, 'names': KIND_NAMES,
    }
