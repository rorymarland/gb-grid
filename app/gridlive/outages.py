"""Outages (REMIT) and the de-rated margin forecast."""

import threading
import time

from . import fetch
from .config import ELEXON
from .timeutil import iso_minute, parse_time
from .fetch import FetchError, as_float


# --------------------------------------------------------------------------
# Outages: REMIT notices of unavailable capacity, and the margin forecast
# --------------------------------------------------------------------------

LINK_NAMES = {'FRAN1': 'IFA (France)', 'IFA2': 'IFA2 (France)', 'ELEC': 'ElecLink (France)', 'NETH': 'BritNed (Netherlands)',
              'BRIT': 'BritNed (Netherlands)', 'BELG': 'Nemo Link (Belgium)', 'NEMO': 'Nemo Link (Belgium)',
              'NORW': 'North Sea Link (Norway)', 'NSL': 'North Sea Link (Norway)', 'DENM': 'Viking Link (Denmark)',
              'VKL': 'Viking Link (Denmark)', 'IRE': 'East West (Ireland)', 'EWIC': 'East West (Ireland)',
              'MOYL': 'Moyle (Northern Ireland)', 'GREE': 'Greenlink (Ireland)'}
MARGIN_LEVELS = [(5000, 'comfortable', 'Comfortable'), (2500, 'normal', 'Normal'), (1000, 'tight', 'Tight'),
                 (float('-inf'), 'very-tight', 'Very tight')]


def margin_level(mw):
    for floor, key, label in MARGIN_LEVELS:
        if mw >= floor:
            return key, label
    return MARGIN_LEVELS[-1][1:]


class Outages:
    """Active outage notices covering now and the next 14 days, and Elexon's
    loss of load probability and de-rated margin forecast."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = []     # current and upcoming REMIT unavailabilities
        self.margin = []     # [(start, de-rated margin MW, loss of load probability)]
        self.checked = 0
        self.version = 0

    def fetch_margin(self, now):
        payload = fetch.get_json(ELEXON + '/forecast/system/loss-of-load?from=%s&to=%s' % (iso_minute(now - 1800), iso_minute(now + 13 * 3600)))
        items = payload.get('data') if isinstance(payload, dict) else payload
        best = {}
        for item in items if isinstance(items, list) else []:
            try:
                t = parse_time(item['startTime'])
                key = (int(item.get('forecastHorizon') or 99), -parse_time(item['publishTime']))
                margin, lolp = as_float(item.get('deratedMargin')), as_float(item.get('lossOfLoadProbability'))
            except (KeyError, TypeError, ValueError):
                continue
            if margin is not None and (t not in best or key < best[t][0]):
                best[t] = (key, margin, lolp or 0.0)
        if not best:
            raise FetchError('The margin forecast was empty')
        return [(t, round(v[1]), v[2]) for t, v in sorted(best.items())]

    def fetch_events(self, now):
        # One day per request: Elexon refuses long ranges on this endpoint
        ids, seen = [], set()
        for day in range(15):
            start = now - 600 + day * 86400
            listing = fetch.get_json(ELEXON + '/remit/list/by-event?from=%s&to=%s&latestRevisionOnly=true' % (iso_minute(start), iso_minute(start + 86400)), timeout=90)
            items = listing.get('data') if isinstance(listing, dict) else listing
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and 'id' in item and item['id'] not in seen:
                    seen.add(item['id'])
                    ids.append(item['id'])
            time.sleep(0.2)
        details = []
        for start in range(0, min(len(ids), 1500), 50):
            query = '&'.join('messageId=%d' % i for i in ids[start:start + 50])
            payload = fetch.get_json(ELEXON + '/remit?' + query, timeout=90)
            batch = payload.get('data') if isinstance(payload, dict) else payload
            details += batch if isinstance(batch, list) else []
            time.sleep(0.2)
        latest = {}
        for d in details:
            if d.get('messageType') != 'UnavailabilitiesOfElectricityFacilities' or d.get('eventStatus') != 'Active':
                continue
            try:
                start, end = parse_time(d['eventStartTime']), parse_time(d['eventEndTime']) if d.get('eventEndTime') else None
                lost = float(d.get('unavailableCapacity') or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if lost < 10 or (end is not None and end <= now):
                continue
            mrid = d.get('mrid') or str(d.get('id'))
            if mrid in latest and latest[mrid]['rev'] >= (d.get('revisionNumber') or 0):
                continue
            latest[mrid] = {'mrid': mrid, 'rev': d.get('revisionNumber') or 0, 'asset': d.get('assetId') or '', 'unit': d.get('affectedUnit') or '',
                            'lost': lost, 'normal': as_float(d.get('normalCapacity')), 'type': d.get('unavailabilityType') or '',
                            'cause': (d.get('cause') or '').strip(), 'start': start, 'end': end, 'fuel': d.get('fuelType') or '',
                            'published': parse_time(d['publishTime']) if d.get('publishTime') else None}
        return list(latest.values())

    def poll(self):
        now = time.time()
        errors = []
        try:
            margin = self.fetch_margin(now)
            with self.lock:
                self.margin = margin
        except FetchError as error:
            errors.append('margin forecast: ' + str(error))
        try:
            events = self.fetch_events(now)
            with self.lock:
                self.events = events
                self.checked = now
        except FetchError as error:
            errors.append('outage notices: ' + str(error))
        with self.lock:
            self.version += 1
        if errors:
            raise FetchError('; '.join(errors))
