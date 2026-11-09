"""Start-up: create the models, start the jobs and serve."""

import logging
import signal
import threading

from .config import BROWSER_REFRESH, DATA_DIR, GEO_PATH, HISTORY_DAYS, LOG_LEVEL, LONG_HISTORY, POLL, PORT, REGIONS_PATH, REGION_ID, STATIONS_PATH, VERSION, log
from .store import Store
from .status import Status
from .ingest import Ingest
from .units import Fleet, Units
from .jobs import run_backfill, run_job, run_maintenance
from .balancing import Balancing
from .outages import Outages
from .longterm import LongHistory, Outlook
from .grid import Grid
from .frequency import FrequencyData
from .events import Events
from .web import App, Handler, Server, UI, load_file


def main():
    logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                        format='%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    log.info('GB Grid Live %s starting on port %d (history %d days, browser refresh %ds)',
             VERSION, PORT, HISTORY_DAYS, BROWSER_REFRESH)
    store = Store(DATA_DIR)
    status = Status()
    ingest = Ingest(store, status)
    fleet = Fleet(STATIONS_PATH)
    units = Units(store, fleet)
    balancing = Balancing(store)
    outages = Outages()
    longhistory, outlook = LongHistory(store), Outlook()
    App.store, App.grid, App.ui = store, Grid(store, status, fleet, units, balancing, outages), UI()
    App.grid.outlook = outlook
    frequency = FrequencyData(store)
    events = Events(App.grid)
    App.geo = load_file(GEO_PATH, 'GEO_PATH', b'{"box":[0,0,1,1],"units":[],"labels":[]}')
    App.regions = load_file(REGIONS_PATH, 'the app folder', b'{"box":[0,0,1,1],"bounds":[0,0,1,1],"regions":[]}')
    stop = threading.Event()

    jobs = [
        ('generation', ingest.poll_generation),
        ('frequency', ingest.poll_frequency),
        ('carbon', ingest.poll_carbon),
        ('price', ingest.poll_price),
        ('solar', ingest.poll_solar),
        ('embedded', ingest.poll_embedded),
        ('ddu', ingest.poll_ddu),
        ('units', units.poll),
        ('warnings', ingest.poll_warnings),
        ('regions', ingest.poll_regions),
        ('balancing', balancing.poll),
        ('outages', outages.poll),
        ('outlook', outlook.poll),
        ('response', frequency.poll_neso),
        ('events', events.poll),
    ]
    if LONG_HISTORY:
        jobs += [('longhistory', longhistory.poll_mix), ('longprices', longhistory.poll_prices)]
    if REGION_ID:
        jobs.append(('region', ingest.poll_region))
    for index, (name, function) in enumerate(jobs):
        def start(name=name, function=function, offset=index * 0.4):
            stop.wait(offset)
            run_job(name, function, POLL[name], status, stop)
        threading.Thread(target=start, name=name, daemon=True).start()
    threading.Thread(target=run_backfill, args=(ingest, stop, frequency), name='backfill', daemon=True).start()
    threading.Thread(target=run_maintenance, args=(store, stop), name='maintenance', daemon=True).start()

    server = Server(('0.0.0.0', PORT), Handler)

    def shutdown(signum, frame):
        log.info('Shutting down')
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever(poll_interval=1)
    finally:
        server.server_close()
