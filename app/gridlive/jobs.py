"""Background jobs: polling loops, the first-start backfill and maintenance."""

import random
import time

from .config import HISTORY_DAYS, REGION_ID, log
from .fetch import FetchError
from .store import FREQUENCY_DAYS


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------

def run_job(name, function, interval, status, stop):
    """Run `function` every `interval` seconds, backing off after failures."""
    failures = 0
    while not stop.is_set():
        status.attempt(name)
        started = time.time()
        try:
            function()
            status.ok(name)
            failures = 0
            delay = interval
        except Exception as error:  # keep the loop alive whatever happens
            failures += 1
            message = str(error) if isinstance(error, FetchError) else type(error).__name__ + ': ' + str(error)
            status.fail(name, message)
            if not isinstance(error, FetchError):
                log.debug('[%s] traceback', name, exc_info=True)
            delay = min(interval, 15 * 2 ** (failures - 1))
        log.debug('[%s] took %.2fs', name, time.time() - started)
        stop.wait(delay + random.uniform(0, min(3.0, delay * 0.1)))


def run_backfill(ingest, stop, frequency=None):
    """Fill the history on first start (newest first), politely and sequentially."""
    stop.wait(5)
    steps = [
        ('generation', lambda: ingest.backfill_generation(HISTORY_DAYS)),
        ('solar', lambda: ingest.backfill_solar(HISTORY_DAYS)),
        ('carbon', lambda: ingest.backfill_carbon(HISTORY_DAYS)),
        ('region', lambda: ingest.backfill_carbon(HISTORY_DAYS, REGION_ID) if REGION_ID else None),
        ('price', lambda: ingest.backfill_price(HISTORY_DAYS)),
        ('frequency', lambda: frequency.backfill(min(HISTORY_DAYS, FREQUENCY_DAYS - 1)) if frequency else None),
    ]
    for name, step in steps:
        if stop.is_set():
            return
        try:
            step()
        except Exception as error:
            log.warning('[backfill] %s: %s (history will fill in as data arrives)', name, error)
    log.info('[backfill] complete')


def run_maintenance(store, stop):
    while not stop.wait(3600):
        try:
            store.prune(HISTORY_DAYS + 2)
        except Exception as error:
            log.warning('[maintenance] %s', error)
