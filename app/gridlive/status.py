"""Health of each upstream source, shown in the pages and at /api/status."""

import threading
import time

from .config import POLL, log


# --------------------------------------------------------------------------
# Source status tracking (shown in the UI and at /api/status)
# --------------------------------------------------------------------------

SOURCE_LABELS = {
    'generation': 'Generation (Elexon FUELINST)',
    'frequency': 'Frequency (Elexon)',
    'price': 'Market price (Elexon MID)',
    'carbon': 'Carbon intensity (NESO)',
    'region': 'Regional carbon intensity (NESO)',
    'solar': 'Solar (Sheffield Solar PV_Live)',
    'embedded': 'Embedded wind and solar (NESO forecast)',
    'ddu': 'Embedded history (NESO Demand Data Update)',
    'units': 'Power stations (Elexon PN and BOALF)',
    'warnings': 'Grid notices (Elexon system warnings)',
    'regions': 'Regions (NESO carbon intensity, Sheffield Solar)',
    'balancing': 'Balancing (Elexon cashflows and volumes, NESO constraint costs)',
    'outages': 'Outages and margin (Elexon REMIT and loss of load forecasts)',
    'longhistory': 'Long-term history (NESO historic generation mix)',
    'longprices': 'Long-term prices (Elexon market index since 2018)',
    'outlook': 'Outlook (Elexon demand and wind forecasts)',
    'response': 'Inertia and frequency response (NESO)',
    'events': 'Timeline (built from the other feeds)',
}


class Status:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = {}

    def _item(self, key):
        return self.items.setdefault(key, {
            'label': SOURCE_LABELS.get(key, key), 'interval': POLL.get(key),
            'last_attempt': None, 'last_success': None, 'last_error': None,
            'error_time': None, 'failures': 0,
        })

    def attempt(self, key):
        with self.lock:
            self._item(key)['last_attempt'] = time.time()

    def ok(self, key):
        with self.lock:
            item = self._item(key)
            if item['failures']:
                log.info('[%s] recovered after %d failed attempt(s)', key, item['failures'])
            item.update(last_success=time.time(), failures=0, last_error=None, error_time=None)

    def fail(self, key, message):
        with self.lock:
            item = self._item(key)
            item['failures'] += 1
            item['last_error'] = message
            item['error_time'] = time.time()
            failures = item['failures']
        # Log the first failure and then every tenth to keep logs readable
        if failures == 1 or failures % 10 == 0:
            log.warning('[%s] %s (failure %d)', key, message, failures)

    def snapshot(self):
        with self.lock:
            return {key: dict(value) for key, value in self.items.items()}
