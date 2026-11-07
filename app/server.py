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
import time  # noqa: F401  (tests patch server.time.time)

from gridlive import fetch  # noqa: F401  (tests patch server.fetch.get_json)

_MODULES = ['config', 'reference', 'timeutil', 'fetch', 'store', 'status', 'ingest', 'units', 'jobs', 'balancing',
            'outages', 'longterm', 'notices', 'views_live', 'views_map', 'views_system', 'views_longterm', 'views_summary', 'grid', 'web', 'main']
for _name in _MODULES:
    _module = importlib.import_module('gridlive.' + _name)
    globals().update({key: value for key, value in vars(_module).items() if not key.startswith('__')})

if __name__ == '__main__':
    main()  # noqa: F821  (from gridlive.main)
