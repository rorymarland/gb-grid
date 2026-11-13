#!/usr/bin/env python3
"""
GB Grid Live: a self-hosted dashboard for the Great Britain electricity grid.

Python standard library only. It polls the public data sources at roughly
their native publication cadence, keeps a SQLite history in DATA_DIR, and
serves the pages and a JSON API. The code lives in the gridlive package:

  config, reference, timeutil   settings, fixed data and time helpers
  fetch, store, status          HTTP client, SQLite storage, feed health
  ingest, units, balancing,     the feeds: national data, power stations,
  outages, longterm, notices    balancing, outages, long-term history, notices
  grid, views_*                 the API payloads
  web, jobs, main               the HTTP server, background jobs and start-up

This file starts the dashboard, and re-exports the package's names so tests
and tools can use them as server.<name>.

Inspired by Kate Morley's National Grid: Live (CC0): https://grid.iamkate.com/
"""

import importlib
import sys
import time  # noqa: F401  (tests patch server.time.time)

from gridlive import fetch  # noqa: F401  (tests patch server.fetch.get_json)

_MODULES = ['config', 'reference', 'timeutil', 'fetch', 'store', 'status', 'ingest', 'units', 'jobs', 'balancing',
            'outages', 'longterm', 'notices', 'photos', 'octopus', 'home_sessions', 'home_views', 'home', 'constraints', 'review', 'forecasts', 'imbalance', 'gas', 'entsoe', 'neighbours', 'weather', 'views_live', 'views_map', 'views_system', 'views_longterm', 'views_summary', 'frequency', 'events', 'notify', 'views_events', 'views_sandbox', 'grid', 'web', 'main']
for _name in _MODULES:
    _module = importlib.import_module('gridlive.' + _name)
    globals().update({key: value for key, value in vars(_module).items() if not key.startswith('__')})

if __name__ == '__main__':
    if '--test-notification' in sys.argv:
        # docker compose exec gb-grid python /app/server.py --test-notification
        notifier = Notifier(Store(DATA_DIR))  # noqa: F821
        if not notifier.channels():
            sys.exit('No notification channel is set: add NOTIFY_NTFY_URL or NOTIFY_WEBHOOK_URL.')
        sys.exit(0 if notifier.test() else 'The test notification could not be sent: %s' % notifier.error)
    if '--test-octopus' in sys.argv:
        # docker compose exec gb-grid python /app/server.py --test-octopus
        sys.exit(test_octopus())  # noqa: F821  (from gridlive.home)
    if '--test-entsoe' in sys.argv:
        # docker compose exec gb-grid python /app/server.py --test-entsoe
        sys.exit(test_entsoe())  # noqa: F821  (from gridlive.entsoe)
    main()  # noqa: F821  (from gridlive.main)
