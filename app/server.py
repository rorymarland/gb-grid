#!/usr/bin/env python3
"""
GB Grid Live: a self-hosted dashboard for the Great Britain electricity grid.

Python standard library only. It polls the public data sources at roughly
their native publication cadence, keeps a small SQLite history in DATA_DIR,
and serves three pages (overview, map, stations) plus a JSON API.

Data sources
  Elexon Insights Solution (BMRS)  generation by fuel type (FUELINST, 5 min),
                                   system frequency (15 s), market index price
  NESO Carbon Intensity API        national and regional intensity, past and
                                   48 hour forecast (30 min)
  Sheffield Solar PV_Live          GB solar estimate (30 min, near real time)
  NESO Data Portal                 embedded wind and solar forecasts (hourly)
                                   and the Demand Data Update (daily, history)
  Elexon BM unit data              Physical Notifications, Bid-Offer
                                   Acceptances and the BM unit register, for
                                   the per-station map (see stations.csv)

Inspired by Kate Morley's National Grid: Live (CC0): https://grid.iamkate.com/
"""

import csv
import gzip
import hashlib
import io
import json
import logging
import math
import os
import random
import re
import signal
import socket
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = os.environ.get('GRID_VERSION', '').strip() or 'dev'
log = logging.getLogger('grid')


# --------------------------------------------------------------------------
# Configuration (all optional, see the .env file)
# --------------------------------------------------------------------------

def env_str(name, default):
    value = os.environ.get(name)
    return default if value is None or value.strip() == '' else value.strip()


def env_int(name, default, lo=None, hi=None):
    raw = os.environ.get(name, '').strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        print('Ignoring invalid ' + name + '=' + repr(raw), file=sys.stderr)
        value = default
    if lo is not None:
        value = max(lo, value)
    if hi is not None:
        value = min(hi, value)
    return value


PORT = env_int('GRID_INTERNAL_PORT', 8080, 1, 65535)
DATA_DIR = env_str('DATA_DIR', '/data')
APP_DIR = os.path.dirname(os.path.abspath(__file__))
UI_PATH = env_str('UI_PATH', os.path.join(APP_DIR, 'index.html'))
MAP_UI_PATH = env_str('MAP_UI_PATH', os.path.join(APP_DIR, 'map.html'))
GEO_PATH = env_str('GEO_PATH', os.path.join(APP_DIR, 'geo.json'))
STATIONS_PATH = env_str('STATIONS_PATH', os.path.join(APP_DIR, 'stations.csv'))
HISTORY_DAYS = env_int('HISTORY_DAYS', 7, 2, 60)
BROWSER_REFRESH = env_int('BROWSER_REFRESH_SECONDS', 10, 3, 600)
POLL = {
    'generation': env_int('POLL_GENERATION_SECONDS', 60, 15, 3600),
    'frequency': env_int('POLL_FREQUENCY_SECONDS', 20, 10, 3600),
    'carbon': env_int('POLL_CARBON_SECONDS', 300, 60, 7200),
    'price': env_int('POLL_PRICE_SECONDS', 300, 60, 7200),
    'solar': env_int('POLL_SOLAR_SECONDS', 300, 60, 7200),
    'embedded': env_int('POLL_EMBEDDED_SECONDS', 1800, 300, 21600),
    'ddu': env_int('POLL_DEMAND_UPDATE_SECONDS', 3600, 600, 86400),
    'region': env_int('POLL_CARBON_SECONDS', 300, 60, 7200),
    'units': env_int('POLL_UNITS_SECONDS', 60, 30, 3600),
    'warnings': env_int('POLL_WARNINGS_SECONDS', 300, 60, 3600),
    'regions': env_int('POLL_REGIONS_SECONDS', 1800, 300, 7200),
    'balancing': env_int('POLL_BALANCING_SECONDS', 300, 120, 3600),
    'outages': env_int('POLL_OUTAGES_SECONDS', 900, 300, 7200),
    'longhistory': env_int('POLL_LONG_HISTORY_SECONDS', 600, 300, 86400),
    'longprices': env_int('POLL_LONG_HISTORY_SECONDS', 600, 300, 86400),
    'outlook': env_int('POLL_OUTLOOK_SECONDS', 1800, 300, 7200),
}
LONG_HISTORY = env_str('LONG_HISTORY', 'true').lower() not in ('0', 'false', 'no', 'off')
REGION_ID = env_int('CARBON_REGION_ID', 0, 0, 17)  # 0 disables the regional card
LOG_LEVEL = env_str('LOG_LEVEL', 'INFO').upper()
ACCESS_LOG = env_str('ACCESS_LOG', 'false').lower() in ('1', 'true', 'yes', 'on')
UI_RELOAD = env_str('UI_RELOAD', 'false').lower() in ('1', 'true', 'yes', 'on')  # development: reread pages per request
STATIC_DIR = os.path.join(APP_DIR, 'static')
REGIONS_PATH = os.path.join(APP_DIR, 'regions.json')

ELEXON = env_str('ELEXON_BASE_URL', 'https://data.elexon.co.uk/bmrs/api/v1').rstrip('/')
CARBON = env_str('CARBON_BASE_URL', 'https://api.carbonintensity.org.uk').rstrip('/')
PVLIVE = env_str('PVLIVE_BASE_URL', 'https://api.pvlive.uk/pvlive/api/v4').rstrip('/')
NESO = env_str('NESO_BASE_URL', 'https://api.neso.energy').rstrip('/')

# NESO "Embedded Wind and Solar Forecasts" (live resource, updated hourly)
EMBEDDED_RESOURCE_ID = 'db6c038f-98af-4570-ab60-24d71ebd0ae5'
# NESO "Demand Data Update" (daily; includes embedded wind/solar history)
DDU_PATH = ('/dataset/7a12172a-939c-404c-b581-a6128b74f588/resource/'
            '177f6fa4-ae49-4182-81ea-0c6b35f26ca6/download/demanddataupdate.csv')

USER_AGENT = 'gb-grid-live/' + VERSION + ' (self-hosted dashboard; python-urllib)'


# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------

# Elexon FUELINST fuel type -> internal key
FUELS = {
    'CCGT': 'ccgt', 'OCGT': 'ocgt', 'COAL': 'coal', 'NUCLEAR': 'nuclear',
    'OIL': 'oil', 'WIND': 'wind', 'NPSHYD': 'hydro', 'PS': 'pumped',
    'BIOMASS': 'biomass', 'BESS': 'battery', 'OTHER': 'other',
}

# Interconnectors: FUELINST code -> (name, country, capacity GW, landing note)
INTERCONNECTORS = {
    'INTFR': ('IFA', 'France', 2.0, 'Sellindge to Calais'),
    'INTIFA2': ('IFA2', 'France', 1.0, 'Hampshire to Normandy'),
    'INTELEC': ('ElecLink', 'France', 1.0, 'Through the Channel Tunnel'),
    'INTNED': ('BritNed', 'Netherlands', 1.0, 'Isle of Grain to Maasvlakte'),
    'INTNEM': ('Nemo Link', 'Belgium', 1.0, 'Kent to Zeebrugge'),
    'INTNSL': ('North Sea Link', 'Norway', 1.4, 'Blyth to Kvilldal'),
    'INTVKL': ('Viking Link', 'Denmark', 1.4, 'Lincolnshire to Revsing'),
    'INTIRL': ('Moyle', 'Ireland', 0.5, 'Scotland to Northern Ireland'),
    'INTEW': ('East West', 'Ireland', 0.5, 'Wales to Dublin'),
    'INTGRNL': ('Greenlink', 'Ireland', 0.5, 'Pembrokeshire to Wexford'),
}
COUNTRY_ORDER = ['France', 'Norway', 'Netherlands', 'Belgium', 'Denmark', 'Ireland']

# The 14 distribution regions. Sheffield Solar's PES IDs (also NESO's licence
# area IDs) -> NESO Carbon Intensity API region IDs. 15 to 17 are England,
# Scotland and Wales, and 18 is Great Britain.
PES_REGIONS = {17: 1, 18: 2, 16: 3, 15: 4, 23: 5, 13: 6, 21: 7, 14: 8, 11: 9, 10: 10, 22: 11, 20: 12, 12: 13, 19: 14}
REGION_NAMES = {
    1: 'North Scotland', 2: 'South Scotland', 3: 'North West England', 4: 'North East England', 5: 'Yorkshire',
    6: 'North Wales & Merseyside', 7: 'South Wales', 8: 'West Midlands', 9: 'East Midlands', 10: 'East England',
    11: 'South West England', 12: 'South England', 13: 'London', 14: 'South East England', 15: 'England',
    16: 'Scotland', 17: 'Wales', 18: 'Great Britain',
}
MIX_FUELS = ['biomass', 'coal', 'imports', 'gas', 'nuclear', 'other', 'hydro', 'solar', 'wind']
INDEX_LEVELS = ['very low', 'low', 'moderate', 'high', 'very high']

# Emission factors (gCO2/kWh) from NESO's Carbon Intensity methodology
# (May 2024 issue). Norway and Denmark are not in NESO's published table;
# the values used here are approximations. Only used as a fallback when the
# official Carbon Intensity API figure is unavailable.
CARBON_FACTORS = {
    'ccgt': 394, 'ocgt': 651, 'coal': 937, 'oil': 935, 'biomass': 120,
    'other': 300, 'nuclear': 0, 'wind': 0, 'solar': 0, 'hydro': 0,
    'pumped': 0, 'battery': 0,
}
IMPORT_FACTORS = {
    'France': 53, 'Netherlands': 474, 'Belgium': 179, 'Ireland': 458,
    'Norway': 25, 'Denmark': 150,
}

# Series exposed by the history API, in stacking order (bottom to top)
SUPPLY_SERIES = ['nuclear', 'wind', 'solar', 'hydro', 'biomass', 'gas',
                 'coal', 'oil', 'other', 'imports', 'storage_out']
NEGATIVE_SERIES = ['exports', 'storage_in']


# --------------------------------------------------------------------------
# Time helpers (everything internal is UTC epoch seconds)
# --------------------------------------------------------------------------

def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def iso_minute(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%MZ')


def parse_time(value):
    """Parse an ISO 8601 timestamp (naive values are treated as UTC)."""
    text = str(value).strip()
    if not text:
        raise ValueError('empty timestamp')
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _last_sunday(year, month):
    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() + 1) % 7)


def uk_midnight_utc(day):
    """UTC epoch of local (Europe/London) midnight at the start of `day`.

    British Summer Time runs from 01:00 UTC on the last Sunday of March to
    01:00 UTC on the last Sunday of October, so local midnight is in BST
    for dates strictly after the March change, up to and including the
    October change day.
    """
    in_bst = _last_sunday(day.year, 3) < day <= _last_sunday(day.year, 10)
    base = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
    return base - (3600 if in_bst else 0)


def settlement_start(day, period):
    """Start (UTC epoch) of a GB settlement period (1-based, local days)."""
    return uk_midnight_utc(day) + (int(period) - 1) * 1800


def settlement_periods(day):
    """Number of settlement periods in a UK day: 46, 48 or 50."""
    return int((uk_midnight_utc(day + timedelta(days=1)) - uk_midnight_utc(day)) // 1800)


MONTHS = {m: i + 1 for i, m in enumerate(
    ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'])}


def parse_settlement_date(value):
    text = str(value).strip()
    if len(text) >= 10 and text[4] == '-' and text[7] == '-':
        return date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
    parts = text.replace('/', '-').split('-')
    if len(parts) == 3:
        day, month, year = parts
        month_number = int(month) if month.isdigit() else MONTHS[month[:3].upper()]
        return date(int(year[:4]), month_number, int(day))
    raise ValueError('unrecognised settlement date: ' + text)


def floor_to(t, step):
    return int(t) - int(t) % step


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------

class FetchError(Exception):
    pass


def http_get(url, accept='application/json', timeout=30, extra_headers=None):
    """GET a URL. Returns (status, body_bytes, headers). 304 is returned, not raised."""
    headers = {'User-Agent': USER_AGENT, 'Accept': accept, 'Accept-Encoding': 'gzip'}
    if extra_headers:
        headers.update(extra_headers)
    host = urllib.parse.urlsplit(url).netloc
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            if response.headers.get('Content-Encoding', '').lower() == 'gzip':
                body = gzip.decompress(body)
            return response.status, body, response.headers
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return 304, b'', error.headers
        detail = ''
        try:
            raw = error.read(65536)
            if (error.headers.get('Content-Encoding') or '').lower() == 'gzip' or raw[:2] == b'\x1f\x8b':
                raw = gzip.decompress(raw)
            detail = raw[:300].decode('utf-8', 'replace').strip().replace('\n', ' ')
        except Exception:
            pass
        raise FetchError('HTTP ' + str(error.code) + ' from ' + host + (': ' + detail if detail else ''))
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        reason = getattr(error, 'reason', error)
        message = type(error).__name__ + ' contacting ' + host + ': ' + str(reason)
        if isinstance(reason, ConnectionRefusedError):
            message = blocked_hint(host) or message
        raise FetchError(message)


def blocked_hint(netloc):
    """Explain a refused connection caused by a DNS blocklist answering 0.0.0.0."""
    hostname = netloc.rsplit('@', 1)[-1].split(':')[0]
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(hostname, 443, proto=socket.IPPROTO_TCP)}
    except OSError:
        return None
    sinkholed = [a for a in addresses if a in ('0.0.0.0', '::') or a.startswith('127.') or a == '::1']
    if addresses and len(sinkholed) == len(addresses) and hostname not in ('localhost', '127.0.0.1', '0.0.0.0', '::1'):
        return ('DNS returned ' + ', '.join(sorted(addresses)) + ' for ' + hostname
                + ', so a DNS blocklist (AdGuard Home, Pi-hole or similar) is blocking it. Allowlist it there.')
    return None


def get_json(url, timeout=30):
    _, body, _ = http_get(url, timeout=timeout)
    try:
        return json.loads(body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        host = urllib.parse.urlsplit(url).netloc
        raise FetchError('Invalid JSON from ' + host)


def rows_from_csv(body):
    text = body.decode('utf-8-sig', 'replace')
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise FetchError('CSV has no header row')
    fields = {name.strip().upper(): name for name in reader.fieldnames if name}
    return fields, list(reader)


def as_float(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == '' or text.upper() in ('NA', 'NAN', 'NULL', 'NONE'):
        return None
    return float(text)


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

UPSERT_GEN = ('INSERT INTO gen (t, fuel, mw) VALUES (?, ?, ?) '
              'ON CONFLICT (t, fuel) DO UPDATE SET mw = excluded.mw '
              'WHERE gen.mw IS NOT excluded.mw')
UPSERT_HH = ('INSERT INTO hh (k, t, v) VALUES (?, ?, ?) '
             'ON CONFLICT (k, t) DO UPDATE SET v = excluded.v '
             'WHERE hh.v IS NOT excluded.v')
UPSERT_CI = ('INSERT INTO ci (region, t, forecast, actual, idx) VALUES (?, ?, ?, ?, ?) '
             'ON CONFLICT (region, t) DO UPDATE SET '
             'forecast = COALESCE(excluded.forecast, ci.forecast), '
             'actual = COALESCE(excluded.actual, ci.actual), '
             'idx = COALESCE(excluded.idx, ci.idx) '
             'WHERE ci.forecast IS NOT COALESCE(excluded.forecast, ci.forecast) '
             'OR ci.actual IS NOT COALESCE(excluded.actual, ci.actual) '
             'OR ci.idx IS NOT COALESCE(excluded.idx, ci.idx)')
UPSERT_FREQ = ('INSERT INTO freq (t, hz) VALUES (?, ?) '
               'ON CONFLICT (t) DO UPDATE SET hz = excluded.hz WHERE freq.hz IS NOT excluded.hz')
UPSERT_ST = ('INSERT INTO st (t, id, mw) VALUES (?, ?, ?) '
             'ON CONFLICT (id, t) DO UPDATE SET mw = excluded.mw WHERE st.mw IS NOT excluded.mw')
UPSERT_REG = ('INSERT INTO reg (t, region, v, idx, mix) VALUES (?, ?, ?, ?, ?) '
              'ON CONFLICT (region, t) DO UPDATE SET v = excluded.v, idx = excluded.idx, mix = excluded.mix '
              'WHERE reg.v IS NOT excluded.v OR reg.idx IS NOT excluded.idx OR reg.mix IS NOT excluded.mix')
UPSERT_BAL = ('INSERT INTO bal (d, sp, unit, elexon, vol_bid, vol_offer, cash_bid, cash_offer) '
              'VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (d, sp, unit) DO UPDATE SET elexon = excluded.elexon, '
              'vol_bid = excluded.vol_bid, vol_offer = excluded.vol_offer, cash_bid = excluded.cash_bid, '
              'cash_offer = excluded.cash_offer')
UPSERT_WARN = ('INSERT INTO warn (t, kind, body) VALUES (?, ?, ?) '
               'ON CONFLICT (t, kind) DO UPDATE SET body = excluded.body WHERE warn.body IS NOT excluded.body')
STATION_HISTORY_DAYS = 7


class Store:
    def __init__(self, data_dir):
        self.lock = threading.RLock()
        self.version = 0          # bumps on any data change
        self.history_version = 0  # bumps on changes that affect the charts
        path = os.path.join(data_dir, 'grid.sqlite3')
        try:
            os.makedirs(data_dir, exist_ok=True)
            self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
            self.db.execute('PRAGMA journal_mode=WAL')
            self.persistent = True
            log.info('Database: %s', path)
        except (sqlite3.Error, OSError) as error:
            log.warning('Could not open %s (%s); using an in-memory database, '
                        'so history will not survive restarts', path, error)
            self.db = sqlite3.connect(':memory:', check_same_thread=False, isolation_level=None)
            self.persistent = False
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS gen (t INTEGER NOT NULL, fuel TEXT NOT NULL,
                mw REAL NOT NULL, PRIMARY KEY (t, fuel)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS hh (k TEXT NOT NULL, t INTEGER NOT NULL,
                v REAL, PRIMARY KEY (k, t)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS ci (region INTEGER NOT NULL, t INTEGER NOT NULL,
                forecast REAL, actual REAL, idx TEXT, PRIMARY KEY (region, t)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS freq (t REAL PRIMARY KEY, hz REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS st (t INTEGER NOT NULL, id INTEGER NOT NULL,
                mw REAL NOT NULL, PRIMARY KEY (id, t)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS warn (t INTEGER NOT NULL, kind TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY (t, kind)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS reg (t INTEGER NOT NULL, region INTEGER NOT NULL, v REAL,
                idx TEXT, mix TEXT, PRIMARY KEY (region, t)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS bal (d TEXT NOT NULL, sp INTEGER NOT NULL, unit TEXT NOT NULL, elexon TEXT,
                vol_bid REAL, vol_offer REAL, cash_bid REAL, cash_offer REAL, PRIMARY KEY (d, sp, unit)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS lh (t INTEGER PRIMARY KEY, gas INTEGER, coal INTEGER, nuclear INTEGER,
                wind INTEGER, wind_emb INTEGER, hydro INTEGER, imports INTEGER, biomass INTEGER, other INTEGER,
                solar INTEGER, storage INTEGER, generation INTEGER, ci INTEGER);
            CREATE TABLE IF NOT EXISTS lp (t INTEGER PRIMARY KEY, price REAL);
            CREATE TABLE IF NOT EXISTS balp (d TEXT NOT NULL, sp INTEGER NOT NULL, t INTEGER NOT NULL,
                fetched INTEGER NOT NULL, PRIMARY KEY (d, sp)) WITHOUT ROWID;
        ''')

    def write(self, sql, rows, history=True):
        """Upsert rows; returns the number of rows that actually changed."""
        if not rows:
            return 0
        with self.lock:
            before = self.db.total_changes
            self.db.execute('BEGIN')
            try:
                self.db.executemany(sql, rows)
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK')
                raise
            changed = self.db.total_changes - before
            if changed:
                self.version += 1
                if history:
                    self.history_version += 1
            return changed

    def query(self, sql, params=()):
        with self.lock:
            return self.db.execute(sql, params).fetchall()

    def scalar(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0][0] if rows else None

    def get_meta(self, key, default=None):
        value = self.scalar('SELECT v FROM meta WHERE k = ?', (key,))
        return default if value is None else value

    def set_meta(self, key, value):
        with self.lock:
            self.db.execute('INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v',
                            (key, str(value)))

    def prune(self, keep_days):
        cutoff = time.time() - keep_days * 86400
        with self.lock:
            before = self.db.total_changes
            self.db.execute('DELETE FROM gen WHERE t < ?', (cutoff,))
            self.db.execute('DELETE FROM hh WHERE t < ?', (cutoff,))
            self.db.execute('DELETE FROM ci WHERE t < ?', (cutoff,))
            self.db.execute('DELETE FROM freq WHERE t < ?', (time.time() - 26 * 3600,))
            self.db.execute('DELETE FROM st WHERE t < ?', (time.time() - STATION_HISTORY_DAYS * 86400,))
            self.db.execute('DELETE FROM warn WHERE t < ?', (time.time() - 400 * 86400,))
            self.db.execute('DELETE FROM reg WHERE t < ?', (time.time() - 3 * 86400,))
            old = (datetime.now(timezone.utc) - timedelta(days=9)).date().isoformat()
            self.db.execute('DELETE FROM bal WHERE d < ?', (old,))
            self.db.execute('DELETE FROM balp WHERE d < ?', (old,))
            removed = self.db.total_changes - before
        if removed:
            log.info('Pruned %d old rows', removed)


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


# --------------------------------------------------------------------------
# Ingestion: one function per upstream source
# --------------------------------------------------------------------------

class Ingest:
    def __init__(self, store, status):
        self.store = store
        self.status = status
        self.unknown_fuels = set()
        self.embedded_url = None
        self.ddu_validators = {}
        self.region_summary = None
        self.regions_past_checked = 0
        self.regional_solar_checked = 0

    # ---- Elexon FUELINST (generation by fuel type, 5-minute) -------------

    def fuelinst(self, start, end, timeout=45):
        url = (ELEXON + '/datasets/FUELINST/stream?publishDateTimeFrom=' + iso(start)
               + '&publishDateTimeTo=' + iso(end))
        items = get_json(url, timeout=timeout)
        if isinstance(items, dict) and isinstance(items.get('data'), list):
            items = items['data']
        if not isinstance(items, list):
            raise FetchError('Unexpected FUELINST response shape')
        latest = {}
        skipped = 0
        for item in sorted((i for i in items if isinstance(i, dict)),
                           key=lambda i: str(i.get('publishTime', ''))):
            try:
                t = floor_to(parse_time(item['startTime']), 300)
                fuel = str(item['fuelType']).strip().upper()
                mw = float(item['generation'])
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
            if fuel not in FUELS and fuel not in INTERCONNECTORS and fuel not in self.unknown_fuels:
                self.unknown_fuels.add(fuel)
                log.info('[generation] new fuel type %r (counted as %s)', fuel,
                         'an interconnector' if fuel.startswith('INT') else 'Other')
            latest[(t, fuel)] = (t, fuel, mw)
        if skipped:
            log.debug('[generation] skipped %d malformed records', skipped)
        self.store.write(UPSERT_GEN, list(latest.values()))
        return len(latest)

    def poll_generation(self):
        now = time.time()
        self.fuelinst(now - 1800, now)

    def backfill_generation(self, days):
        end = time.time()
        for _ in range(days):
            start = end - 86400
            have = self.store.scalar('SELECT COUNT(DISTINCT t) FROM gen WHERE t >= ? AND t < ?', (start, end)) or 0
            if have < 270:
                count = self.fuelinst(start, end, timeout=120)
                log.info('[backfill] generation %s to %s: %d records', iso_minute(start), iso_minute(end), count)
                time.sleep(1)
            end = start

    # ---- Elexon system frequency (15-second readings) --------------------

    def poll_frequency(self):
        now = time.time()
        last = self.store.scalar('SELECT MAX(t) FROM freq')
        since = max(last - 120, now - 3600) if last else now - 3600
        url = ELEXON + '/system/frequency/stream?from=' + iso(since) + '&to=' + iso(now)
        items = get_json(url)
        if isinstance(items, dict) and isinstance(items.get('data'), list):
            items = items['data']
        if not isinstance(items, list):
            raise FetchError('Unexpected frequency response shape')
        rows = []
        for item in items:
            try:
                t = float(parse_time(item['measurementTime']))
                hz = float(item['frequency'])
            except (KeyError, TypeError, ValueError):
                continue
            if 45.0 < hz < 55.0:
                rows.append((t, hz))
        self.store.write(UPSERT_FREQ, rows, history=False)

    # ---- Elexon market index price (half-hourly) -------------------------

    def market_index(self, start, end, timeout=45):
        url = (ELEXON + '/balancing/pricing/market-index?from=' + iso(start) + '&to=' + iso(end)
               + '&dataProviders=APXMIDP')
        payload = get_json(url, timeout=timeout)
        items = payload.get('data') if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise FetchError('Unexpected market index response shape')
        rows = []
        for item in items:
            try:
                t = floor_to(parse_time(item['startTime']), 1800)
                price = as_float(item.get('price'))
                volume = as_float(item.get('volume'))
            except (KeyError, TypeError, ValueError):
                continue
            # A zero price with zero traded volume means no trades, not free power
            if price is None or (price == 0 and (volume or 0) == 0):
                continue
            rows.append(('price', t, price))
        self.store.write(UPSERT_HH, rows)
        return len(rows)

    def poll_price(self):
        now = time.time()
        self.market_index(now - 6 * 3600, now)

    def backfill_price(self, days):
        end = time.time()
        for _ in range(days):
            start = end - 86400
            have = self.store.scalar("SELECT COUNT(*) FROM hh WHERE k = 'price' AND t >= ? AND t < ?", (start, end)) or 0
            if have < 40:
                count = self.market_index(start, end, timeout=90)
                log.info('[backfill] prices %s to %s: %d periods', iso_minute(start), iso_minute(end), count)
                time.sleep(0.5)
            end = start

    # ---- NESO Carbon Intensity API ---------------------------------------

    @staticmethod
    def _ci_entries(payload, region=0):
        """Return (entries, region_name) for national or regional responses.

        Accepts every shape the API uses: a national list of half hours; a
        region object {regionid, shortname, data: [...]}; a list wrapping
        region objects; and the all-regions form, where each half hour
        carries a "regions" list (the matching region is picked out).
        """
        data = payload.get('data') if isinstance(payload, dict) else None
        name = None
        if isinstance(data, dict):
            name = data.get('shortname')
            data = data.get('data')
        elif isinstance(data, list) and data and isinstance(data[0], dict) \
                and 'intensity' not in data[0] and isinstance(data[0].get('data'), list):
            match = next((item for item in data if str(item.get('regionid')) == str(region)), data[0])
            name = match.get('shortname')
            data = match['data']
        if not isinstance(data, list):
            raise FetchError('Unexpected carbon intensity response shape')
        entries = []
        for entry in data:
            if isinstance(entry, dict) and isinstance(entry.get('regions'), list):
                match = next((item for item in entry['regions']
                              if isinstance(item, dict) and str(item.get('regionid')) == str(region)), None)
                if match:
                    name = name or match.get('shortname')
                    entries.append({'from': entry.get('from'), 'intensity': match.get('intensity'),
                                    'generationmix': match.get('generationmix')})
            else:
                entries.append(entry)
        return entries, name

    def _ingest_ci(self, region, payload, require=False):
        entries, name = self._ci_entries(payload, region)
        rows = []
        for entry in entries:
            try:
                t = floor_to(parse_time(entry['from']), 1800)
                intensity = entry.get('intensity') or {}
                forecast = as_float(intensity.get('forecast'))
                actual = as_float(intensity.get('actual'))
                index = intensity.get('index')
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            if forecast is None and actual is None:
                continue
            rows.append((region, t, forecast, actual, str(index).lower() if index else None))
        if require and not rows:
            sample = json.dumps(payload)[:160] if payload is not None else 'nothing'
            raise FetchError('Carbon intensity response had no usable half hours (' + str(len(entries))
                             + ' entries). Start of response: ' + sample)
        self.store.write(UPSERT_CI, rows)
        return rows, name

    def poll_carbon(self):
        now = time.time()
        self._ingest_ci(0, get_json(CARBON + '/intensity/' + iso_minute(now) + '/pt24h'))
        self._ingest_ci(0, get_json(CARBON + '/intensity/' + iso_minute(now) + '/fw48h'), require=True)

    def backfill_carbon(self, days, region=0):
        end = time.time()
        for _ in range(days):
            start = end - 86400
            have = self.store.scalar('SELECT COUNT(*) FROM ci WHERE region = ? AND t >= ? AND t < ?',
                                     (region, start, end)) or 0
            if have < 44:
                span = iso_minute(start) + '/' + iso_minute(end)
                url = (CARBON + '/regional/intensity/' + span + '/regionid/' + str(region)) if region \
                    else (CARBON + '/intensity/' + span)
                rows, _ = self._ingest_ci(region, get_json(url, timeout=60))
                log.info('[backfill] %s %s to %s: %d periods', 'region' if region else 'carbon',
                         iso_minute(start), iso_minute(end), len(rows))
                time.sleep(0.5)
            end = start

    def poll_region(self):
        now = time.time()
        base = CARBON + '/regional/intensity/' + iso_minute(now)
        suffix = '/regionid/' + str(REGION_ID)
        payload = get_json(base + '/fw48h' + suffix)
        entries, name = self._ci_entries(payload, REGION_ID)
        if name:
            self.store.set_meta('region_name', name)
        self._remember_region_mix(entries, now)
        rows, _ = self._ingest_ci(REGION_ID, payload, require=True)
        values = [row[2] if row[2] is not None else row[3] for row in rows]
        summary = (len(rows), round(min(values)), round(max(values)))
        if summary != self.region_summary:
            log.info('[region] %s forecast: %d half hours, %d to %d g/kWh',
                     name or 'Region ' + str(REGION_ID), *summary)
            self.region_summary = summary
        try:
            self._ingest_ci(REGION_ID, get_json(base + '/pt24h' + suffix))
        except FetchError as error:
            # The past 24 hours are a nice-to-have; keep the forecast working
            log.debug('[region] past 24 hours unavailable: %s', error)

    def _remember_region_mix(self, entries, now):
        """Keep the regional generation mix for the current half hour."""
        best = None
        for entry in entries:
            try:
                t = floor_to(parse_time(entry['from']), 1800)
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
            mix = entry.get('generationmix')
            if isinstance(mix, list) and t <= now and (best is None or t > best[0]):
                best = (t, mix)
        if best is None:
            return
        parts = []
        for item in best[1]:
            try:
                parts.append([str(item['fuel']).lower(), round(float(item['perc']), 1)])
            except (KeyError, TypeError, ValueError):
                continue
        if parts:
            self.store.set_meta('region_mix', json.dumps({'t': best[0], 'mix': parts}, separators=(',', ':')))

    # ---- Sheffield Solar PV_Live (GB solar, half-hourly) -----------------

    def pvlive(self, start, end, timeout=45, pes=0, key='pv'):
        start = floor_to(start, 1800)
        end = floor_to(end, 1800) + 1800
        url = PVLIVE + '/pes/' + str(pes) + '?start=' + iso(start) + '&end=' + iso(end) + '&period=30'
        payload = get_json(url, timeout=timeout)
        if not isinstance(payload, dict) or 'data' not in payload or 'meta' not in payload:
            raise FetchError('Unexpected PV_Live response shape')
        meta = list(payload['meta'])
        try:
            time_col = meta.index('datetime_gmt')
            value_col = meta.index('generation_mw')
        except ValueError:
            raise FetchError('PV_Live response is missing expected fields')
        rows = []
        for record in payload['data'] or []:
            try:
                value = as_float(record[value_col])
                if value is None:
                    continue
                # PV_Live labels each half hour with its END time
                t = floor_to(parse_time(record[time_col]), 1800) - 1800
            except (IndexError, TypeError, ValueError):
                continue
            rows.append((key, t, max(0.0, value)))
        self.store.write(UPSERT_HH, rows, history=key == 'pv')
        return len(rows)

    def poll_solar(self):
        now = time.time()
        self.pvlive(now - 3 * 3600, now)

    def backfill_solar(self, days):
        now = time.time()
        have = self.store.scalar("SELECT COUNT(*) FROM hh WHERE k = 'pv' AND t >= ?", (now - days * 86400,)) or 0
        if have < days * 48 * 0.9:
            count = self.pvlive(now - days * 86400, now, timeout=120)
            log.info('[backfill] solar: %d half hours', count)

    # ---- NESO embedded wind and solar forecast (hourly) ------------------

    @staticmethod
    def _period_start(row, fields):
        """Start time of a row, preferring settlement date and period."""
        if 'SETTLEMENT_DATE' in fields and 'SETTLEMENT_PERIOD' in fields:
            day = parse_settlement_date(row[fields['SETTLEMENT_DATE']])
            period = int(float(row[fields['SETTLEMENT_PERIOD']]))
            if not 1 <= period <= 50:
                raise ValueError('settlement period out of range')
            return settlement_start(day, period)
        if 'DATE_GMT' in fields and 'TIME_GMT' in fields:
            day = parse_settlement_date(row[fields['DATE_GMT']])
            hours, minutes = str(row[fields['TIME_GMT']]).strip().split(':')[:2]
            base = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
            return floor_to(base + int(hours) * 3600 + int(minutes) * 60, 1800)
        raise ValueError('no usable time columns')

    def poll_embedded(self):
        meta = get_json(NESO + '/api/3/action/resource_show?id=' + EMBEDDED_RESOURCE_ID)
        try:
            url = meta['result']['url']
        except (KeyError, TypeError):
            raise FetchError('NESO resource_show response has no download URL')
        if url == self.embedded_url:
            return  # unchanged since the last successful ingest
        _, body, _ = http_get(url, accept='text/csv', timeout=60)
        fields, records = rows_from_csv(body)
        wind_col = fields.get('EMBEDDED_WIND_FORECAST')
        solar_col = fields.get('EMBEDDED_SOLAR_FORECAST')
        if not wind_col or not solar_col:
            raise FetchError('Embedded forecast CSV is missing forecast columns: ' + ', '.join(sorted(fields)))
        rows = []
        for record in records:
            try:
                t = self._period_start(record, fields)
                wind = as_float(record[wind_col])
                solar = as_float(record[solar_col])
            except (KeyError, TypeError, ValueError):
                continue
            if wind is not None:
                rows.append(('fc_wind', t, max(0.0, wind)))
            if solar is not None:
                rows.append(('fc_solar', t, max(0.0, solar)))
        if not rows:
            raise FetchError('Embedded forecast CSV contained no usable rows')
        self.store.write(UPSERT_HH, rows)
        self.embedded_url = url

    # ---- NESO Demand Data Update (daily; embedded history) ---------------

    def poll_ddu(self):
        headers = {}
        if self.ddu_validators.get('etag'):
            headers['If-None-Match'] = self.ddu_validators['etag']
        if self.ddu_validators.get('modified'):
            headers['If-Modified-Since'] = self.ddu_validators['modified']
        status, body, response_headers = http_get(NESO + DDU_PATH, accept='text/csv', timeout=120,
                                                  extra_headers=headers)
        if status == 304:
            return
        fields, records = rows_from_csv(body)
        needed = ['SETTLEMENT_DATE', 'SETTLEMENT_PERIOD', 'EMBEDDED_WIND_GENERATION', 'EMBEDDED_SOLAR_GENERATION']
        missing = [name for name in needed if name not in fields]
        if missing:
            raise FetchError('Demand Data Update CSV is missing ' + ', '.join(missing))
        indicator_col = fields.get('FORECAST_ACTUAL_INDICATOR')
        earliest = time.time() - (HISTORY_DAYS + 2) * 86400
        rows = []
        for record in records:
            try:
                t = self._period_start(record, fields)
                if t < earliest:
                    continue
                wind = as_float(record[fields['EMBEDDED_WIND_GENERATION']])
                solar = as_float(record[fields['EMBEDDED_SOLAR_GENERATION']])
            except (KeyError, TypeError, ValueError):
                continue
            actual = 1.0 if indicator_col and str(record[indicator_col]).strip().upper().startswith('A') else 0.0
            if wind is not None:
                rows.append(('ddu_wind', t, max(0.0, wind)))
            if solar is not None:
                rows.append(('ddu_solar', t, max(0.0, solar)))
            rows.append(('ddu_actual', t, actual))
        self.store.write(UPSERT_HH, rows)
        self.ddu_validators = {
            'etag': response_headers.get('ETag'),
            'modified': response_headers.get('Last-Modified'),
        }

    # ---- NESO system warnings, published through Elexon ------------------

    def system_warnings(self, start, end, timeout=60):
        url = (ELEXON + '/system/warnings?publishDateTimeFrom=' + iso_minute(start)
               + '&publishDateTimeTo=' + iso_minute(end))
        payload = get_json(url, timeout=timeout)
        items = payload.get('data') if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise FetchError('Unexpected system warnings response shape')
        rows = []
        for item in items:
            try:
                t = parse_time(item['publishTime'])
                kind = str(item['warningType']).strip().upper()
                body = str(item.get('warningText') or '').replace('\r', '').replace('\\n', '\n').strip()
            except (KeyError, TypeError, ValueError):
                continue
            rows.append((t, kind, body))
        self.store.write(UPSERT_WARN, rows, history=False)
        return len(rows)

    def poll_warnings(self):
        now = time.time()
        last = self.store.scalar('SELECT MAX(t) FROM warn')
        self.system_warnings(max(last - 6 * 3600, now - 30 * 86400) if last else now - 30 * 86400, now + 60)

    # ---- Regions: every region's carbon intensity and solar output -------

    def _ingest_regions(self, payload):
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise FetchError('Unexpected regional carbon intensity response shape')
        rows = []
        for entry in data:
            try:
                t = floor_to(parse_time(entry['from']), 1800)
                regions = entry['regions']
            except (KeyError, TypeError, ValueError):
                continue
            for region in regions if isinstance(regions, list) else []:
                try:
                    rid = int(region['regionid'])
                    intensity = region.get('intensity') or {}
                    value = as_float(intensity.get('forecast'))
                    if value is None:
                        value = as_float(intensity.get('actual'))
                except (KeyError, TypeError, ValueError):
                    continue
                index = intensity.get('index')
                mix = {}
                for item in region.get('generationmix') or []:
                    try:
                        mix[str(item['fuel']).lower()] = float(item['perc'])
                    except (KeyError, TypeError, ValueError):
                        continue
                text = ','.join('%g' % round(mix.get(fuel, 0.0), 1) for fuel in MIX_FUELS) if mix else None
                rows.append((t, rid, value, str(index).lower() if index else None, text))
        self.store.write(UPSERT_REG, rows, history=False)
        return len(rows)

    def poll_regions(self):
        now = time.time()
        base = CARBON + '/regional/intensity/' + iso_minute(now)
        if not self._ingest_regions(get_json(base + '/fw48h', timeout=90)):
            raise FetchError('The regional forecast had no usable half hours')
        if now - self.regions_past_checked > 3 * 3600:
            try:
                self._ingest_regions(get_json(base + '/pt24h', timeout=90))
                self.regions_past_checked = now
            except FetchError as error:
                log.debug('[regions] past 24 hours unavailable: %s', error)
        # Sheffield Solar's estimate for each region: the last day on the
        # first run, then the last few hours (revisions included)
        start = now - (26 * 3600 if now - self.regional_solar_checked > 26 * 3600 else 4 * 3600)
        failures = []
        for pes in sorted(PES_REGIONS):
            try:
                self.pvlive(start, now, pes=pes, key='rpv%d' % pes)
            except FetchError as error:
                failures.append(str(error))
            time.sleep(0.2)
        if len(failures) == len(PES_REGIONS):
            raise FetchError('Regional solar unavailable: ' + failures[0])
        self.regional_solar_checked = now


# --------------------------------------------------------------------------
# Power stations: per-unit output for the map and stations views
#
# Elexon publishes no live metered output per power station, so each BM
# unit's output is modelled from what it told the system operator it would
# do (its Physical Notification) overridden by any instruction the operator
# has since given it (Bid-Offer Acceptances). Units are placed on the map
# through stations.csv, and each fuel's estimates are scaled so they add up
# to Elexon's measured FUELINST total for the same five minutes.
# --------------------------------------------------------------------------

# Unit fuel type -> display category (matches the overview's colours)
UNIT_CATEGORY = {
    'CCGT': 'gas', 'OCGT': 'gas', 'NUCLEAR': 'nuclear', 'WIND': 'wind', 'NPSHYD': 'hydro',
    'BIOMASS': 'biomass', 'PS': 'storage', 'BATTERY': 'storage', 'BESS': 'storage',
    'COAL': 'other', 'OIL': 'other', 'OTHER': 'other', 'SOLAR': 'solar',
}
# Elexon files batteries and directly connected solar farms under OTHER, so
# their names decide how they're shown.
NAME_CATEGORY = [(re.compile(r'\b(bess|batter(y|ies)|storage)\b', re.I), 'storage'),
                 (re.compile(r'\b(solar|pv|photovoltaic)\b', re.I), 'solar')]


def unit_category(fuel, station, ref):
    category = UNIT_CATEGORY.get(fuel, 'other')
    if fuel not in ('OTHER', ''):
        return category
    if station and station['cat'] in ('solar', 'storage'):
        return station['cat']
    text = ' '.join(part for part in ((ref or {}).get('n'), (ref or {}).get('p')) if part)
    for pattern, named in NAME_CATEGORY:
        if pattern.search(text):
            return named
    return category
STORAGE_FUELS = ('PS', 'BATTERY', 'BESS')
CATEGORY_ORDER = ['wind', 'solar', 'nuclear', 'gas', 'biomass', 'hydro', 'storage', 'other']
CATEGORY_LABELS = {
    'wind': 'Wind', 'solar': 'Solar', 'nuclear': 'Nuclear', 'gas': 'Gas', 'biomass': 'Biomass',
    'hydro': 'Hydro', 'storage': 'Storage', 'other': 'Other',
}

# Interconnector converter stations: (lat, lon, place) at the GB end, then the far end
LINK_ENDS = {
    'INTFR': ((51.106, 0.994, 'Sellindge'), (50.890, 1.790, 'Les Mandarins')),
    'INTIFA2': ((50.810, -1.210, 'Lee-on-the-Solent'), (49.100, -0.290, 'Tourbe')),
    'INTELEC': ((51.090, 1.140, 'Folkestone'), (50.920, 1.780, 'Coquelles')),
    'INTNED': ((51.445, 0.705, 'Isle of Grain'), (51.960, 4.030, 'Maasvlakte')),
    'INTNEM': ((51.300, 1.340, 'Richborough'), (51.300, 3.220, 'Zeebrugge')),
    'INTNSL': ((55.150, -1.530, 'Blyth'), (59.520, 6.650, 'Kvilldal')),
    'INTVKL': ((52.920, -0.200, 'Bicker Fen'), (55.480, 9.130, 'Revsing')),
    'INTIRL': ((55.070, -4.980, 'Auchencrosh'), (54.830, -5.730, 'Islandmagee')),
    'INTEW': ((53.230, -3.070, 'Deeside'), (53.470, -6.530, 'Woodland')),
    'INTGRNL': ((51.680, -4.990, 'Pembroke'), (52.270, -6.980, 'Great Island')),
}


def project(lon, lat):
    """Spherical transverse Mercator on 2W, in km with y north (as tools/geo.py)."""
    lam = math.radians(lon) - math.radians(-2.0)
    phi = math.radians(lat)
    b = math.cos(phi) * math.sin(lam)
    x = 0.5 * 6371.0 * math.log((1 + b) / (1 - b))
    y = 6371.0 * (math.atan2(math.tan(phi), math.cos(lam)) - math.radians(49.0))
    return x, y


def map_point(lat, lon):
    """Map coordinates (km, y down) for the SVG."""
    x, y = project(lon, lat)
    return [round(x, 1), round(-y, 1)]


def settlement_period_at(t):
    """(UK settlement date, period number) containing UTC epoch t."""
    day = datetime.fromtimestamp(t, timezone.utc).date()
    for candidate in (day + timedelta(days=1), day, day - timedelta(days=1)):
        start = uk_midnight_utc(candidate)
        if start <= t:
            return candidate, int((t - start) // 1800) + 1
    return day, 1


def segment_level(segments, t):
    """Level at t from sorted (t0, l0, t1, l1) segments, or None if none covers t."""
    for t0, l0, t1, l1 in segments:
        if t0 <= t <= t1:
            return l0 if t1 <= t0 else l0 + (l1 - l0) * (t - t0) / (t1 - t0)
    return None


def held_level(segments, t):
    """Nearest level when no segment covers t (a gap or an older period)."""
    before = [s for s in segments if s[2] <= t]
    if before:
        return before[-1][3]
    return segments[0][1] if segments else 0.0


def short_unit_id(elexon_id):
    """T_DRAXX-1 -> DRAXX-1 (Elexon IDs carry a type prefix that NGC IDs lack)."""
    return re.sub(r'^[A-Z0-9]_+', '', (elexon_id or '').strip().upper())


class Fleet:
    """Power station locations (stations.csv) and BM unit to station matching."""

    def __init__(self, path):
        self.stations = {}
        self.exact = {}
        self.prefixes = []
        self.patterns = []
        self.cache = {}
        self.error = None
        try:
            with open(path, encoding='utf-8', newline='') as handle:
                rows = list(csv.DictReader(handle))
        except OSError as error:
            self.error = 'Could not read the station list (' + str(error) + ')'
            log.error('[units] %s. Check STATIONS_PATH points at stations.csv.', self.error)
            return
        for row in rows:
            try:
                sid = int(row['id'])
                lat, lon = float(row['lat']), float(row['lon'])
            except (KeyError, TypeError, ValueError):
                continue
            fuel = (row.get('fuel') or 'OTHER').strip().upper()
            try:
                capacity = as_float(row.get('capacity_mw'))
            except ValueError:
                capacity = None
            x, y = map_point(lat, lon)
            self.stations[sid] = {
                'id': sid, 'name': (row.get('name') or '').strip() or str(sid), 'fuel': fuel,
                'cat': UNIT_CATEGORY.get(fuel, 'other'), 'x': x, 'y': y,
                'nation': (row.get('nation') or '').strip(), 'offshore': (row.get('offshore') or '').strip() == '1',
                'capacity': capacity,
            }
            for unit in (row.get('units') or '').split():
                self.exact[unit.strip().upper()] = sid
            for prefix in (row.get('unit_prefixes') or '').split():
                self.prefixes.append((prefix.strip().upper(), sid))
            pattern = (row.get('name_pattern') or '').strip()
            if pattern:
                try:
                    self.patterns.append((re.compile(pattern, re.I), sid))
                except re.error as error:
                    log.warning('[units] ignoring the name pattern for %s: %s', self.stations[sid]['name'], error)
        self.prefixes.sort(key=lambda item: -len(item[0]))
        log.info('[units] %d power station locations loaded', len(self.stations))

    def resolve(self, unit, elexon, ref):
        """Station id for a BM unit, by exact ID, then ID prefix, then name."""
        key = (unit, elexon)
        if key in self.cache:
            return self.cache[key]
        ids = [i for i in (unit, short_unit_id(elexon)) if i]
        sid = next((self.exact[i] for i in ids if i in self.exact), None)
        if sid is None:
            sid = next((s for prefix, s in self.prefixes for i in ids if i.startswith(prefix)), None)
        if sid is None and ref:
            text = ' '.join(part for part in (ref.get('n'), ref.get('p')) if part)
            fuel = ref.get('f')
            for pattern, s in self.patterns:
                station = self.stations[s]
                if pattern.search(text) and (not fuel or fuel == 'OTHER' or UNIT_CATEGORY.get(fuel) == station['cat']):
                    sid = s
                    break
        self.cache[key] = sid
        return sid


class Units:
    """Polls Physical Notifications (PN), Bid-Offer Acceptances (BOALF) and
    the BM unit register, and models each station's output."""

    REFERENCE_KEY = 'bmu_reference_v1'

    def __init__(self, store, fleet):
        self.store = store
        self.fleet = fleet
        self.lock = threading.Lock()
        self.pn = {}
        self.boa = {}
        self.boa_checked = None
        self.reference = {}
        self.reference_time = 0
        self.reference_error = None
        self.snapshot = None
        self.version = 0
        self.history_t = None
        self.unplaced_logged = 0
        self.first_logged = False
        try:
            saved = json.loads(store.get_meta(self.REFERENCE_KEY, '') or '{}')
            if isinstance(saved.get('units'), dict):
                self.reference = saved['units']
                self.reference_time = float(saved.get('time') or 0)
        except (ValueError, TypeError, AttributeError):
            pass

    @staticmethod
    def _rows(payload, name):
        if isinstance(payload, dict) and isinstance(payload.get('data'), list):
            payload = payload['data']
        if not isinstance(payload, list):
            raise FetchError('Unexpected ' + name + ' response shape')
        return [item for item in payload if isinstance(item, dict)]

    @staticmethod
    def _unit_of(item):
        unit = str(item.get('nationalGridBmUnit') or '').strip().upper()
        elexon = str(item.get('bmUnit') or item.get('elexonBmUnit') or '').strip().upper()
        return unit or short_unit_id(elexon), elexon

    # ---- Elexon BM unit register (names, fuel types; daily) --------------

    def refresh_reference(self, now):
        if now - self.reference_time < 86400:
            return
        try:
            items = self._rows(get_json(ELEXON + '/reference/bmunits/all', timeout=90), 'BM unit reference')
        except FetchError as error:
            self.reference_error = str(error)
            self.reference_time = now - 86400 + 3600  # try again in an hour
            log.warning('[units] BM unit register unavailable (%s); matching by ID only', error)
            return
        reference = {}
        for item in items:
            unit, elexon = self._unit_of(item)
            if not unit:
                continue
            capacity = None
            for field in ('generationCapacity', 'registeredCapacity'):
                try:
                    value = as_float(item.get(field))
                except ValueError:
                    value = None
                if value and value > 0:
                    capacity = value
                    break
            reference[unit] = {
                'e': elexon, 'f': str(item.get('fuelType') or '').strip().upper(),
                'p': str(item.get('leadPartyName') or '').strip(), 'n': str(item.get('bmUnitName') or '').strip(),
                't': str(item.get('bmUnitType') or '').strip().upper(), 'c': capacity,
            }
        if len(reference) < 50:
            self.reference_time = now - 86400 + 3600
            log.warning('[units] BM unit register looked incomplete (%d units); keeping the previous copy', len(reference))
            return
        self.reference, self.reference_time, self.reference_error = reference, now, None
        self.fleet.cache.clear()
        self.store.set_meta(self.REFERENCE_KEY, json.dumps({'time': now, 'units': reference}, separators=(',', ':')))
        log.info('[units] BM unit register: %d units', len(reference))

    # ---- Physical Notifications (one request per settlement period) -----

    def ensure_pn(self, day, period, now):
        key = (day.isoformat(), period)
        entry = self.pn.get(key)
        if entry and (len(entry['fetched']) >= 2 or now - entry['fetched'][-1] < 900):
            return entry
        url = ELEXON + '/datasets/PN?settlementDate=' + key[0] + '&settlementPeriod=' + str(period)
        units = {}
        for item in self._rows(get_json(url, timeout=90), 'PN'):
            unit, elexon = self._unit_of(item)
            try:
                segment = (parse_time(item['timeFrom']), float(item['levelFrom']),
                           parse_time(item['timeTo']), float(item['levelTo']))
            except (KeyError, TypeError, ValueError):
                continue
            if unit:
                units.setdefault(unit, [elexon, []])[1].append(segment)
        for value in units.values():
            value[1].sort()
        if not units and entry:
            entry['fetched'].append(now)
            return entry
        if not units:
            raise FetchError('Elexon has not published Physical Notifications for ' + key[0]
                             + ' period ' + str(period) + ' yet')
        entry = {'fetched': (entry['fetched'] if entry else []) + [now], 'units': units}
        self.pn[key] = entry
        for old in sorted(self.pn)[:-4]:
            del self.pn[old]
        log.debug('[units] PN %s period %d: %d units', key[0], period, len(units))
        return entry

    # ---- Bid-Offer Acceptances (incremental, every poll) -----------------

    def update_boalf(self, now):
        start = now - 3 * 3600 if self.boa_checked is None else max(now - 3 * 3600, self.boa_checked - 1200)
        url = ELEXON + '/datasets/BOALF?from=' + iso_minute(start) + '&to=' + iso_minute(now + 3 * 3600)
        count = 0
        for item in self._rows(get_json(url, timeout=90), 'BOALF'):
            unit, elexon = self._unit_of(item)
            try:
                number = int(item['acceptanceNumber'])
                segment = (parse_time(item['timeFrom']), float(item['levelFrom']),
                           parse_time(item['timeTo']), float(item['levelTo']))
            except (KeyError, TypeError, ValueError):
                continue
            if not unit:
                continue
            accepted = self.boa.setdefault((unit, number), {'elexon': elexon, 'segments': {}})
            if str(item.get('amendmentFlag') or '').strip().upper().startswith('DEL'):
                accepted['segments'].pop(segment[0], None)
                if not accepted['segments']:
                    del self.boa[(unit, number)]
                continue
            accepted['segments'][segment[0]] = segment
            count += 1
        self.boa_checked = now
        for key in [k for k, v in self.boa.items() if max(s[2] for s in v['segments'].values()) < now - 3 * 3600]:
            del self.boa[key]
        log.debug('[units] BOALF: %d segments, %d acceptances held', count, len(self.boa))

    def describe(self, unit, elexon=''):
        """(station id, category, display name) for a BM unit."""
        ref = self.reference.get(unit)
        sid = self.fleet.resolve(unit, elexon, ref)
        fuel = (ref or {}).get('f') or (self.fleet.stations[sid]['fuel'] if sid else '')
        fuel = fuel if fuel in UNIT_CATEGORY else 'OTHER'
        station = self.fleet.stations.get(sid)
        name = station['name'] if station else ((ref or {}).get('n') or (ref or {}).get('p') or elexon or unit)
        return sid, unit_category(fuel, station, ref), name

    def accepted_levels(self, t):
        """Latest acceptance covering t, per unit: {unit: (level, elexon)}."""
        best = {}
        for (unit, number), accepted in self.boa.items():
            segments = sorted(accepted['segments'].values())
            if segments[0][0] <= t <= max(s[2] for s in segments):
                level = segment_level(segments, t)
                if level is not None and (unit not in best or number > best[unit][0]):
                    best[unit] = (number, level, accepted['elexon'])
        return {unit: (level, elexon) for unit, (number, level, elexon) in best.items()}

    # ---- the job ---------------------------------------------------------

    def latest_fuelinst(self, now):
        t = self.store.scalar('SELECT t FROM gen WHERE t >= ? GROUP BY t HAVING COUNT(*) >= 8 '
                              'ORDER BY t DESC LIMIT 1', (now - 2400,))
        if not t:
            return None, {}
        return t, dict(self.store.query('SELECT fuel, mw FROM gen WHERE t = ?', (t,)))

    def poll(self):
        if not self.fleet.stations:
            raise FetchError(self.fleet.error or 'No power station locations loaded')
        now = time.time()
        self.refresh_reference(now)
        t, measured = self.latest_fuelinst(now)
        at = t + 150 if t else now
        day, period = settlement_period_at(at)
        errors = []
        pn = None
        try:
            pn = self.ensure_pn(day, period, now)
        except FetchError as error:
            errors.append(str(error))
            fallback = [k for k in sorted(self.pn) if k < (day.isoformat(), period)]
            pn = self.pn[fallback[-1]] if fallback else None
        try:
            self.update_boalf(now)
        except FetchError as error:
            errors.append('Bid-offer acceptances: ' + str(error))
        if pn is not None:
            self.compute(at, t, measured, pn['units'], day, period, now)
        if errors:
            raise FetchError('; '.join(errors))

    def compute(self, at, t, measured, pn_units, day, period, now):
        levels = {}
        for unit, (elexon, segments) in pn_units.items():
            level = segment_level(segments, at)
            levels[unit] = [held_level(segments, at) if level is None else level, 'pn', elexon]
        planned = {unit: values[0] for unit, values in levels.items()}
        for unit, (level, elexon) in self.accepted_levels(at).items():
            levels[unit] = [level, 'boa', levels.get(unit, [0, '', elexon])[2] or elexon]

        rows = []
        estimated = {}
        for unit, (level, source, elexon) in levels.items():
            ref = self.reference.get(unit)
            elexon = elexon or (ref or {}).get('e', '')
            if elexon.startswith(('I_', '2_', 'V_', 'C_')) or (ref and (ref['f'].startswith('INT') or ref['t'] in ('I', 'S'))):
                continue
            sid = self.fleet.resolve(unit, elexon, ref)
            fuel = (ref or {}).get('f') or (self.fleet.stations[sid]['fuel'] if sid else '')
            if fuel.startswith('INT'):
                continue
            if level < 0 and fuel not in STORAGE_FUELS and sid is None:
                continue  # demand-side units
            fuel = fuel if fuel in UNIT_CATEGORY else 'OTHER'
            rows.append((unit, elexon, sid, fuel, level, source, ref))
            if level > 0:
                estimated[fuel] = estimated.get(fuel, 0.0) + level

        scale = {}
        for fuel, total in estimated.items():
            target = measured.get('BESS' if fuel == 'BATTERY' else fuel)
            factor = 1.0
            if t and target is not None and total > 1:
                raw = max(0.0, float(target)) / total
                factor = raw if 0.4 <= raw <= 2.5 or target < 50 else 1.0
            scale[fuel] = factor

        stations, unplaced, instructions = {}, [], []
        categories = {cat: {'mapped': 0.0, 'unplaced': 0.0, 'estimated': 0.0} for cat in CATEGORY_ORDER}
        for unit, elexon, sid, fuel, level, source, ref in rows:
            if source == 'boa' and unit in planned and abs(level - planned[unit]) >= 1:
                instructions.append({'unit': unit, 'elexon': elexon, 'sid': sid,
                                     'cat': unit_category(fuel, self.fleet.stations.get(sid), ref),
                                     'delta': round(level - planned[unit], 1), 'level': round(level, 1)})
            mw = level * scale.get(fuel, 1.0) if level > 0 else level
            cat = unit_category(fuel, self.fleet.stations.get(sid), ref)
            if level > 0:
                categories[cat]['estimated'] += level
            if sid is not None:
                st = stations.setdefault(sid, {'mw': 0.0, 'units': 0, 'running': 0, 'ref_cap': 0.0, 'instructed': 0})
                st['mw'] += mw
                st['units'] += 1
                st['running'] += 1 if abs(mw) >= 1 else 0
                st['instructed'] += 1 if source == 'boa' else 0
                st['ref_cap'] += (ref or {}).get('c') or 0.0
                if mw > 0:
                    categories[cat]['mapped'] += mw
            elif mw >= 1 or mw <= -1:
                name = (ref or {}).get('n') or (ref or {}).get('p') or ''
                unplaced.append({'unit': elexon or unit, 'name': name, 'cat': cat, 'mw': round(mw, 1)})
                if mw > 0:
                    categories[cat]['unplaced'] += mw
        unplaced.sort(key=lambda item: -abs(item['mw']))

        snapshot = {
            'at': int(at), 'time': t, 'basis': 'measured' if t else 'scheduled',
            'period': [day.isoformat(), period], 'stations': stations,
            'categories': categories, 'scale': {k: round(v, 3) for k, v in scale.items()},
            'unplaced': unplaced[:80], 'unplaced_count': len(unplaced),
            'counts': {'units': len(rows), 'placed': sum(s['units'] for s in stations.values()),
                       'instructed': sum(s['instructed'] for s in stations.values())},
            'reference_units': len(self.reference), 'reference_error': self.reference_error,
            'instructions': instructions,
        }
        with self.lock:
            self.snapshot = snapshot
            self.version += 1
        if t and t != self.history_t:
            self.store.write(UPSERT_ST, [(t, sid, round(s['mw'], 1)) for sid, s in stations.items()], history=False)
            self.history_t = t
        self.log_progress(snapshot, now)

    def log_progress(self, snapshot, now):
        if not self.first_logged:
            self.first_logged = True
            log.info('[units] %d units reporting: %d placed at %d stations, %d without a known location',
                     snapshot['counts']['units'], snapshot['counts']['placed'], len(snapshot['stations']),
                     snapshot['unplaced_count'])
        big = [u for u in snapshot['unplaced'] if u['mw'] >= 30]
        if big and self.reference and now - self.unplaced_logged > 86400:
            self.unplaced_logged = now
            log.info('[units] Not on the map yet (no location known): %s',
                     '; '.join('%s %s(%s, %d MW)' % (u['unit'], (u['name'] + ' ') if u['name'] else '', u['cat'], u['mw'])
                               for u in big[:12]))


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


def run_backfill(ingest, stop):
    """Fill the history on first start (newest first), politely and sequentially."""
    stop.wait(5)
    steps = [
        ('generation', lambda: ingest.backfill_generation(HISTORY_DAYS)),
        ('solar', lambda: ingest.backfill_solar(HISTORY_DAYS)),
        ('carbon', lambda: ingest.backfill_carbon(HISTORY_DAYS)),
        ('region', lambda: ingest.backfill_carbon(HISTORY_DAYS, REGION_ID) if REGION_ID else None),
        ('price', lambda: ingest.backfill_price(HISTORY_DAYS)),
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


# --------------------------------------------------------------------------
# Balancing: what the grid operator pays units to change their output
# --------------------------------------------------------------------------

class Balancing:
    """Per-unit volumes and payments for each finished half hour (Elexon's
    indicative figures, about 20 minutes after it ends), bid and offer prices
    for the current half hour, and NESO's daily constraint costs."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.prices = {}            # unit -> (bid price, offer price) for the current half hour
        self.prices_period = None
        self.constraints = []       # [(date, cost, volume, {category: cost})]
        self.constraints_checked = 0
        self.version = 0

    def _rows(self, path):
        payload = get_json(ELEXON + path, timeout=90)
        items = payload.get('data') if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise FetchError('Unexpected response shape from ' + path.split('?')[0])
        return items

    def fetch_period(self, day, period):
        stamp = '%s/%d' % (day.isoformat(), period)
        units = {}
        for side in ('bid', 'offer'):
            for item in self._rows('/balancing/settlement/indicative/cashflows/all/%s/%s' % (side, stamp)):
                unit = item.get('nationalGridBmUnit') or item.get('bmUnit')
                cash = as_float(item.get('totalCashflow'))
                if unit and cash is not None:
                    entry = units.setdefault(unit, [item.get('bmUnit') or '', 0.0, 0.0, 0.0, 0.0])
                    entry[3 if side == 'bid' else 4] += cash
            for item in self._rows('/balancing/settlement/acceptance/volumes/all/%s/%s' % (side, stamp)):
                unit = item.get('nationalGridBmUnit') or item.get('bmUnit')
                volume = as_float(item.get('totalVolumeAccepted'))
                if unit and volume is not None:
                    entry = units.setdefault(unit, [item.get('bmUnit') or '', 0.0, 0.0, 0.0, 0.0])
                    entry[1 if side == 'bid' else 2] += volume
        rows = [(day.isoformat(), period, unit, e[0], round(e[1], 3), round(e[2], 3), round(e[3], 2), round(e[4], 2))
                for unit, e in units.items()]
        self.store.write(UPSERT_BAL, rows, history=False)
        self.store.write('INSERT OR REPLACE INTO balp (d, sp, t, fetched) VALUES (?, ?, ?, ?)',
                         [(day.isoformat(), period, settlement_start(day, period), int(time.time()))], history=False)
        return len(rows)

    def fetch_prices(self, day, period):
        prices = {}
        for item in self._rows('/balancing/bid-offer/all?settlementDate=%s&settlementPeriod=%d' % (day.isoformat(), period)):
            unit, pair = item.get('nationalGridBmUnit'), item.get('pairId')
            if not unit or pair not in (-1, 1):
                continue
            entry = prices.setdefault(unit, [None, None])
            if pair == -1:
                entry[0] = as_float(item.get('bid'))
            else:
                entry[1] = as_float(item.get('offer'))
        with self.lock:
            self.prices, self.prices_period = prices, (day, period)

    def fetch_constraints(self):
        result = get_json(NESO + '/api/3/action/package_show?id=constraint-breakdown', timeout=60).get('result') or {}
        resources = [r for r in result.get('resources', []) if r.get('datastore_active')]
        rows = []
        for resource in resources[-2:]:
            data = get_json(NESO + '/api/3/action/datastore_search?resource_id=%s&limit=400&sort=_id%%20desc' % resource['id'],
                            timeout=60).get('result') or {}
            rows += data.get('records') or []
        days = {}
        for row in rows:
            day = str(row.get('Date') or '')[:10]
            if not day:
                continue
            costs = {key[:-5].strip(): as_float(value) or 0.0 for key, value in row.items() if key.endswith(' cost')}
            volume = sum(as_float(value) or 0.0 for key, value in row.items() if key.endswith(' volume') and 'Thermal' in key)
            days[day] = (day, round(sum(costs.values())), round(volume), {k: round(v) for k, v in costs.items()})
        with self.lock:
            self.constraints = [days[k] for k in sorted(days)][-120:]
            self.version += 1

    def poll(self):
        now = time.time()
        errors = []
        day, period = settlement_period_at(now)
        if self.prices_period != (day, period):
            try:
                self.fetch_prices(day, period)
            except FetchError as error:
                errors.append('bid and offer prices: ' + str(error))
        # Finished half hours from yesterday (UK) onwards, oldest first, a few per poll
        done = {(d, sp) for d, sp in self.store.query('SELECT d, sp FROM balp WHERE d >= ?', ((day - timedelta(days=1)).isoformat(),))}
        todo = []
        for back in (1, 0):
            d = day - timedelta(days=back)
            for sp in range(1, settlement_periods(d) + 1):
                start = settlement_start(d, sp)
                if start + 1800 + 1200 <= now and (d.isoformat(), sp) not in done:
                    todo.append((d, sp))
        fetched = 0
        for d, sp in todo[-24:] if len(todo) > 24 and not done else todo[:24]:
            try:
                fetched += 1
                self.fetch_period(d, sp)
            except FetchError as error:
                if settlement_start(d, sp) > now - 3 * 3600:
                    break  # recent half hours may simply not be published yet
                errors.append('half hour %s %d: %s' % (d.isoformat(), sp, error))
                break
            time.sleep(0.25)
        if now - self.constraints_checked > 6 * 3600:
            try:
                self.fetch_constraints()
                self.constraints_checked = now
            except FetchError as error:
                errors.append('constraint costs: ' + str(error))
        with self.lock:
            self.version += 1
        if errors and not fetched and not self.prices:
            raise FetchError('; '.join(errors))
        if errors:
            log.debug('[balancing] %s', '; '.join(errors))


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
        payload = get_json(ELEXON + '/forecast/system/loss-of-load?from=%s&to=%s' % (iso_minute(now - 1800), iso_minute(now + 13 * 3600)))
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
            listing = get_json(ELEXON + '/remit/list/by-event?from=%s&to=%s&latestRevisionOnly=true' % (iso_minute(start), iso_minute(start + 86400)), timeout=90)
            items = listing.get('data') if isinstance(listing, dict) else listing
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and 'id' in item and item['id'] not in seen:
                    seen.add(item['id'])
                    ids.append(item['id'])
            time.sleep(0.2)
        details = []
        for start in range(0, min(len(ids), 1500), 50):
            query = '&'.join('messageId=%d' % i for i in ids[start:start + 50])
            payload = get_json(ELEXON + '/remit?' + query, timeout=90)
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
            latest[mrid] = {'rev': d.get('revisionNumber') or 0, 'asset': d.get('assetId') or '', 'unit': d.get('affectedUnit') or '',
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


# --------------------------------------------------------------------------
# Long-term history and the 48-hour outlook
# --------------------------------------------------------------------------

LH_COLUMNS = ['gas', 'coal', 'nuclear', 'wind', 'wind_emb', 'hydro', 'imports', 'biomass', 'other', 'solar', 'storage', 'generation', 'ci']
LH_SOURCE = ['GAS', 'COAL', 'NUCLEAR', 'WIND', 'WIND_EMB', 'HYDRO', 'IMPORTS', 'BIOMASS', 'OTHER', 'SOLAR', 'STORAGE', 'GENERATION', 'CARBON_INTENSITY']
LH_RESOURCE = 'f93d1835-75bc-43e5-84ad-12472b180a98'  # NESO "Historic generation mix", looked up by name first
LH_START = int(datetime(2009, 1, 1, tzinfo=timezone.utc).timestamp())
PRICE_HISTORY_START = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp())


class LongHistory:
    """NESO's half-hourly generation mix and carbon intensity since 2009, and
    Elexon's market index price since 2018, downloaded once then kept current."""

    def __init__(self, store):
        self.store = store
        self.resource = None
        self.mix_checked = 0
        self.price_span = 6 * 86400  # steps down to 3 days, then 1, if Elexon refuses the range
        self.version = 0

    def find_resource(self):
        try:
            result = get_json(NESO + '/api/3/action/package_show?id=historic-generation-mix', timeout=60).get('result') or {}
            for resource in result.get('resources', []):
                if resource.get('datastore_active'):
                    return resource['id']
        except FetchError as error:
            log.debug('[longhistory] dataset lookup failed: %s', error)
        return LH_RESOURCE

    def fetch_mix(self, limit=10000):
        if not self.resource:
            self.resource = self.find_resource()
        last = self.store.scalar('SELECT MAX(t) FROM lh') or 0
        since = datetime.fromtimestamp(last, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') if last else '2000-01-01T00:00:00'
        columns = ', '.join('"%s"' % c for c in ['DATETIME'] + LH_SOURCE)
        sql = 'SELECT %s FROM "%s" WHERE "DATETIME" > \'%s\' ORDER BY "DATETIME" LIMIT %d' % (columns, self.resource, since, limit)
        payload = get_json(NESO + '/api/3/action/datastore_search_sql?sql=' + urllib.parse.quote(sql), timeout=180)
        records = (payload.get('result') or {}).get('records') or []
        rows = []
        for record in records:
            try:
                t = int(datetime.fromisoformat(str(record['DATETIME'])[:19]).replace(tzinfo=timezone.utc).timestamp())
            except (KeyError, ValueError):
                continue
            values = [as_float(record.get(c)) for c in LH_SOURCE]
            rows.append((t,) + tuple(None if v is None else int(round(v)) for v in values))
        self.store.write('INSERT OR REPLACE INTO lh (t, %s) VALUES (%s)' % (', '.join(LH_COLUMNS), ', '.join('?' * 14)), rows, history=False)
        return len(rows)

    def fetch_prices(self):
        last = self.store.scalar('SELECT MAX(t) FROM lp') or (PRICE_HISTORY_START - 1800)
        start = last + 1800
        end = min(start + self.price_span, time.time())
        if end - start < 3600:
            return 0
        url = ELEXON + '/balancing/pricing/market-index?from=%s&to=%s&dataProviders=APXMIDP'
        try:
            payload = get_json(url % (iso_minute(start), iso_minute(end - 60)), timeout=90)
        except FetchError as error:
            if 'HTTP 400' not in str(error) or self.price_span <= 86400:
                raise
            log.info('[longprices] Elexon refused %d days at a time (%s); trying fewer', self.price_span // 86400, error)
            self.price_span = 3 * 86400 if self.price_span > 3 * 86400 else 86400
            return self.fetch_prices()
        items = payload.get('data') if isinstance(payload, dict) else payload
        rows = []
        for item in items if isinstance(items, list) else []:
            if item.get('dataProvider') not in (None, 'APXMIDP'):
                continue
            try:
                t, price, volume = parse_time(item['startTime']), as_float(item.get('price')), as_float(item.get('volume'))
            except (KeyError, ValueError):
                continue
            if price is not None and (volume or 0) > 0:
                rows.append((t, price))
        if not rows:
            rows = [(floor_to(end, 1800) - 1800, None)]  # a gap in the data: step past it
        self.store.write('INSERT OR REPLACE INTO lp (t, price) VALUES (?, ?)', rows, history=False)
        return len(rows)

    def poll_mix(self):
        """Page through the record (10,000 half hours per query) until caught up,
        for up to an hour per poll; then check every six hours."""
        now = time.time()
        last = self.store.scalar('SELECT MAX(t) FROM lh') or 0
        if now - last < 3 * 86400 and now - self.mix_checked < 6 * 3600:
            return
        while time.time() - now < 3600:
            got = self.fetch_mix()
            self.version += 1
            if not got:
                break
            time.sleep(1)
        self.mix_checked = now

    def poll_prices(self):
        """A week of prices per request, one request at a time, until caught up."""
        now = time.time()
        while time.time() - now < 3600:
            got = self.fetch_prices()
            self.version += 1
            if not got:
                break
            time.sleep(0.5)



class Outlook:
    """Elexon's national demand and transmission wind forecasts."""

    def __init__(self):
        self.demand, self.wind, self.version, self.raw, self.demand_kind = [], [], 0, {}, 'national'

    def poll(self):
        now = time.time()
        window = 'from=%s&to=%s' % (iso_minute(floor_to(now, 1800)), iso_minute(now + 49 * 3600))
        errors = []
        try:
            # The current day-ahead forecast, filled out with the latest forecast for each later half hour
            best, tsd, raw = {}, {}, []
            for url in ('/forecast/demand/day-ahead/latest?boundary=N&' + window, '/forecast/demand/day-ahead?boundary=N'):
                payload = get_json(ELEXON + url)
                items = payload.get('data') if isinstance(payload, dict) else payload
                items = items if isinstance(items, list) else []
                raw.append(len(items))
                for item in items:
                    if item.get('boundary', 'N') != 'N':
                        continue
                    t, published = parse_time(item['startTime']), item.get('publishTime') or ''
                    for table, field in ((best, 'nationalDemand'), (tsd, 'transmissionSystemDemand')):
                        v = as_float(item.get(field))
                        if v is not None and (t not in table or published > table[t][0]):
                            table[t] = (published, v)
            self.raw['demand'] = raw
            # Recent forecasts sometimes leave national demand out; use transmission demand then
            if len(best) >= 0.5 * len(tsd):
                self.demand, self.demand_kind = [(t, best[t][1]) for t in sorted(best)], 'national'
            else:
                self.demand, self.demand_kind = [(t, tsd[t][1]) for t in sorted(tsd)], 'transmission'
        except (FetchError, KeyError, ValueError) as error:
            errors.append('demand: %s' % error)
        try:
            payload = get_json(ELEXON + '/forecast/generation/wind/latest?' + window)
            self.wind = sorted((parse_time(i['startTime']), as_float(i.get('generation'))) for i in payload.get('data') or []
                               if as_float(i.get('generation')) is not None)
        except (FetchError, KeyError, ValueError) as error:
            errors.append('wind: %s' % error)
        self.version += 1
        if errors:
            raise FetchError('; '.join(errors))


# --------------------------------------------------------------------------
# Grid notices: NESO's system warnings in plain English
# --------------------------------------------------------------------------

# Warning type -> (title, level, what it means). "strong" notices get a red banner.
NOTICE_KINDS = {
    'ELECTRICITY MARGIN NOTICE': ('Electricity Margin Notice', 'warning',
                                  'Spare generating capacity is forecast to fall below the margin NESO needs'),
    'CAPACITY MARKET NOTICE': ('Capacity Market Notice', 'warning',
                               'Spare capacity is forecast to fall below the Capacity Market threshold'),
    'HIGH RISK OF DEMAND REDUCTION': ('High risk of demand reduction', 'strong',
                                      'There is a high risk that some demand will have to be disconnected'),
    'DEMAND CONTROL IMMINENT': ('Demand control imminent', 'strong',
                                'NESO expects to order controlled disconnections of demand'),
    'NEGATIVE RESERVE ACTIVE POWER MARGIN': ('Negative reserve warning', 'warning',
                                             'NESO may not have enough room to turn generation down'),
    'DEMAND FLEXIBILITY SERVICE': ('Demand Flexibility Service', 'warning',
                                   'Households and businesses are being paid to use less electricity'),
}
# Routine messages that never need a banner
ROUTINE_NOTICES = ('SO-SO TRADES', 'IT SYSTEMS OUTAGE')
NOTICE_PERIOD = re.compile(r'from\s+(\d{1,2})[:.]?(\d{2})\s*(?:hrs?)?\s+to\s+(\d{1,2})[:.]?(\d{2})\s*(?:hrs?)?\s+on\s+'
                           r'(?:[A-Za-z]+\s+)?(\d{1,2})/(\d{1,2})/(\d{4})', re.I)
NOTICE_BETWEEN = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4})\s+between\s+(\d{1,2}):(\d{2})\s+and\s+(\d{1,2}):(\d{2})', re.I)
NOTICE_COMMENCE = re.compile(r'Commencement time of notice\s*:\s*(\d{1,2}):(\d{2})\s+on\s+(\d{1,2})/(\d{1,2})/(\d{4})', re.I)
NOTICE_SHORTFALL = re.compile(r'shortfall\s+(\d[\d,]*)\s*MW', re.I)
NOTICE_REGION = re.compile(r'(?:regions?:\s*|\bFor\s+)([A-Za-z][A-Za-z ]{2,40}?)\s+region', re.I)


def uk_offset(t):
    """Seconds Europe/London is ahead of UTC at epoch t (BST starts and ends at 01:00 UTC)."""
    year = datetime.fromtimestamp(t, timezone.utc).year
    start = _last_sunday(year, 3)
    end = _last_sunday(year, 10)
    starts = int(datetime(start.year, start.month, start.day, 1, tzinfo=timezone.utc).timestamp())
    ends = int(datetime(end.year, end.month, end.day, 1, tzinfo=timezone.utc).timestamp())
    return 3600 if starts <= t < ends else 0


def uk_clock(t):
    """(local date, 'HH:MM') in Europe/London for UTC epoch t."""
    local = datetime.fromtimestamp(t + uk_offset(t), timezone.utc)
    return local.date(), local.strftime('%H:%M')


def local_epoch(day, hours, minutes):
    """UTC epoch of a Europe/London wall-clock time (the earlier one when ambiguous)."""
    base = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) + int(hours) * 3600 + int(minutes) * 60
    return base - 3600 if uk_offset(base - 3600) else base


def describe_notice(t, kind, body, now=None):
    """Plain-English summary of one system warning, with its period if stated."""
    now = time.time() if now is None else now
    title, level, meaning = NOTICE_KINDS.get(kind, (kind.title(), 'warning', None))
    text = body or ''
    start = end = None
    match = NOTICE_PERIOD.search(text)
    if match:
        h0, m0, h1, m1, dd, mm, yyyy = (int(g) for g in match.groups())
        try:
            day = date(yyyy, mm, dd)
            start, end = local_epoch(day, h0, m0), local_epoch(day, h1, m1)
            if end <= start:
                end = local_epoch(day + timedelta(days=1), h1, m1)
        except ValueError:
            start = end = None
    else:
        match = NOTICE_BETWEEN.search(text)
        if match:
            dd, mm, yyyy, h0, m0, h1, m1 = (int(g) for g in match.groups())
            try:
                day = date(yyyy, mm, dd)
                start, end = local_epoch(day, h0, m0), local_epoch(day, h1, m1)
            except ValueError:
                start = end = None
        else:
            match = NOTICE_COMMENCE.search(text)
            if match:
                h0, m0, dd, mm, yyyy = (int(g) for g in match.groups())
                try:
                    start = local_epoch(date(yyyy, mm, dd), h0, m0)
                except ValueError:
                    start = None
    upper = text.upper()
    cancelled = 'CANCELLATION' in upper or 'HAS BEEN CANCELLED' in upper or 'NOTICE CANCELLED' in upper
    if meaning is None:
        first = next((line.strip() for line in text.split('\n')
                      if line.strip() and not line.strip().upper().startswith('FROM')), '')
        meaning = first[:160].rstrip('.') or title
    phrase = meaning
    region = NOTICE_REGION.search(text) if kind == 'NEGATIVE RESERVE ACTIVE POWER MARGIN' else None
    if region:
        phrase += ' in the ' + ' '.join(region.group(1).split()).title().replace(' To ', ' to ') + ' area'
    today = uk_clock(now)[0]
    if start is not None:
        start_day, start_clock = uk_clock(start)
        when = '' if start_day == today else ' tomorrow' if start_day == today + timedelta(days=1) \
            else ' on ' + start_day.strftime('%a ') + str(start_day.day) + start_day.strftime(' %b')
        phrase += (' from ' + start_clock + (' to ' + uk_clock(end)[1] if end else '') + when)
    shortfall = NOTICE_SHORTFALL.search(text)
    if shortfall:
        phrase += ', with a shortfall of ' + shortfall.group(1) + ' MW'
    return {
        'id': kind[:24] + '-' + str(int(t)), 'kind': kind, 'title': title, 'level': level,
        'summary': phrase + '.', 'text': text, 'published': int(t), 'start': start, 'end': end,
        'cancelled': cancelled, 'routine': kind in ROUTINE_NOTICES,
    }


# --------------------------------------------------------------------------
# Derived views for the API
# --------------------------------------------------------------------------

def r2(value):
    return None if value is None else round(value, 2)


class Grid:
    """Builds the /api/live and /api/history payloads from the store."""

    def __init__(self, store, status, fleet=None, units=None, balancing=None, outages=None):
        self.store = store
        self.outages = outages
        self.outlook = None
        self.status = status
        self.fleet = fleet
        self.units = units
        self.bal = balancing
        self.cache = {}
        self.cache_lock = threading.Lock()

    # ---- building blocks -------------------------------------------------

    def latest_generation_time(self):
        cutoff = time.time() - 3 * 86400
        return self.store.scalar('SELECT t FROM gen WHERE t >= ? GROUP BY t HAVING COUNT(*) >= 8 '
                                 'ORDER BY t DESC LIMIT 1', (cutoff,))

    def load_generation(self, start, end):
        result = {}
        for t, fuel, mw in self.store.query('SELECT t, fuel, mw FROM gen WHERE t >= ? AND t <= ?', (start, end)):
            result.setdefault(t, {})[fuel] = mw
        return result

    def load_embedded(self, start, end):
        """Best available embedded wind and solar per half hour (MW).

        Wind:  NESO Demand Data Update outturn, else NESO hourly forecast,
               else Demand Data Update forecast.
        Solar: Sheffield Solar PV_Live, else NESO hourly forecast, else
               Demand Data Update.
        """
        keys = ['pv', 'fc_wind', 'fc_solar', 'ddu_wind', 'ddu_solar', 'ddu_actual']
        values = {key: {} for key in keys}
        rows = self.store.query('SELECT k, t, v FROM hh WHERE k IN (?, ?, ?, ?, ?, ?) AND t >= ? AND t <= ?',
                                (*keys, floor_to(start, 1800) - 3600, end + 3600))
        for key, t, value in rows:
            values[key][t] = value
        wind, solar, wind_basis, solar_basis = {}, {}, {}, {}
        for t in set().union(*values.values()):
            if values['ddu_actual'].get(t) == 1 and values['ddu_wind'].get(t) is not None:
                wind[t], wind_basis[t] = values['ddu_wind'][t], 'neso-outturn'
            elif values['fc_wind'].get(t) is not None:
                wind[t], wind_basis[t] = values['fc_wind'][t], 'neso-forecast'
            elif values['ddu_wind'].get(t) is not None:
                wind[t], wind_basis[t] = values['ddu_wind'][t], 'neso-daily'
            if values['pv'].get(t) is not None:
                solar[t], solar_basis[t] = values['pv'][t], 'pvlive'
            elif values['fc_solar'].get(t) is not None:
                solar[t], solar_basis[t] = values['fc_solar'][t], 'neso-forecast'
            elif values['ddu_solar'].get(t) is not None:
                solar[t], solar_basis[t] = values['ddu_solar'][t], 'neso-daily'
        return wind, solar, wind_basis, solar_basis

    @staticmethod
    def interpolate(table, t):
        """Linear interpolation between half-hour centres; falls back to steps."""
        start = floor_to(t, 1800)
        centre = start + 900
        here = table.get(start)
        other = table.get(start - 1800 if t < centre else start + 1800)
        if here is None:
            return other
        if other is None:
            return here
        return here + (other - here) * (abs(t - centre) / 1800.0)

    @staticmethod
    def average_half_hours(table, start, end):
        values = [table[h] for h in range(floor_to(start, 1800), end, 1800) if table.get(h) is not None]
        return sum(values) / len(values) if values else None

    @staticmethod
    def mix(fuels, embedded_wind, embedded_solar):
        """Turn one instant of FUELINST data (MW) into display categories (MW)."""
        def g(code):
            return float(fuels.get(code) or 0.0)
        other = g('OTHER') + sum(mw for code, mw in fuels.items()
                                 if code not in FUELS and code not in INTERCONNECTORS and not code.startswith('INT'))
        links = {code: mw for code, mw in fuels.items() if code.startswith('INT')}
        countries = {}
        for code, mw in links.items():
            country = INTERCONNECTORS.get(code, (code, 'Other', None, ''))[1]
            countries[country] = countries.get(country, 0.0) + mw
        pumped, battery = g('PS'), g('BESS')
        m = {
            'wind_transmission': g('WIND'), 'wind_embedded': embedded_wind or 0.0,
            'solar': embedded_solar or 0.0, 'hydro': g('NPSHYD'), 'nuclear': g('NUCLEAR'),
            'biomass': g('BIOMASS'), 'ccgt': g('CCGT'), 'ocgt': g('OCGT'), 'coal': g('COAL'),
            'oil': g('OIL'), 'other': other, 'pumped': pumped, 'battery': battery,
        }
        m['wind'] = m['wind_transmission'] + m['wind_embedded']
        m['gas'] = m['ccgt'] + m['ocgt']
        m['imports'] = sum(mw for mw in links.values() if mw > 0)
        m['exports'] = sum(mw for mw in links.values() if mw < 0)
        m['storage_out'] = max(0.0, pumped) + max(0.0, battery)
        m['storage_in'] = min(0.0, pumped) + min(0.0, battery)
        generation = sum(max(0.0, m[k]) for k in ('nuclear', 'wind', 'solar', 'hydro', 'biomass', 'gas',
                                                  'coal', 'oil', 'other'))
        m['supply'] = generation + m['imports'] + m['storage_out']
        m['demand'] = m['supply'] + m['exports'] + m['storage_in'] + min(0.0, m['other'])
        emitted = sum(max(0.0, m[k]) * factor for k, factor in CARBON_FACTORS.items() if k in m)
        emitted += sum(mw * IMPORT_FACTORS.get(country, 300) for country, mw in countries.items() if mw > 0)
        m['carbon_estimate'] = emitted / m['supply'] if m['supply'] > 0 else None
        m['links'] = links
        m['countries'] = countries
        return m

    def region_recent(self, now):
        """How the region compared with Great Britain over the stored history."""
        start = now - min(HISTORY_DAYS, 30) * 86400
        region = self.carbon_table(REGION_ID, start, now - 1)
        values = [v[0] for v in region.values() if v[0] is not None]
        if len(values) < 48:
            return None
        national = [v[0] for t, v in self.carbon_table(0, min(region), now - 1).items() if v[0] is not None]
        return {
            'days': round((now - min(region)) / 86400, 1),
            'zero_share': round(sum(1 for v in values if v < 0.5) / len(values), 3),
            'average': round(sum(values) / len(values)),
            'max': round(max(values)),
            'gb_average': round(sum(national) / len(national)) if national else None,
        }

    def carbon_table(self, region, start, end):
        result = {}
        for t, forecast, actual, index in self.store.query(
                'SELECT t, forecast, actual, idx FROM ci WHERE region = ? AND t >= ? AND t <= ? ORDER BY t',
                (region, start, end)):
            value = actual if actual is not None else forecast
            result[t] = (value, index, 'actual' if actual is not None else 'forecast')
        return result

    def price_table(self, start, end):
        return dict(self.store.query("SELECT t, v FROM hh WHERE k = 'price' AND t >= ? AND t <= ?", (start, end)))

    def cached(self, key, version, builder):
        with self.cache_lock:
            hit = self.cache.get(key)
            if hit and hit[0] == version:
                return hit[1]
        value = builder()
        with self.cache_lock:
            self.cache[key] = (version, value)
        return value

    # ---- /api/history ----------------------------------------------------

    RANGES = {'24h': (86400, 300), '7d': (7 * 86400, 1800), '30d': (30 * 86400, 3600)}

    def available_ranges(self):
        ranges = ['24h', '7d']
        if HISTORY_DAYS >= 30:
            ranges.append('30d')
        return ranges

    def history(self, range_key):
        if range_key not in self.RANGES or range_key not in self.available_ranges():
            range_key = '24h'
        return self.cached(('history', range_key), self.store.history_version,
                           lambda: self._build_history(range_key))

    def _build_history(self, range_key):
        span, step = self.RANGES[range_key]
        latest = self.latest_generation_time()
        end = floor_to(latest if latest else time.time(), step)
        start = end - span + step
        generation = self.load_generation(start, end + step)
        wind_table, solar_table, _, _ = self.load_embedded(start, end + step)
        carbon = self.carbon_table(0, floor_to(start, 1800), end + step)
        prices = self.price_table(floor_to(start, 1800), end + step)
        times = list(range(start, end + 1, step))
        keys = SUPPLY_SERIES + NEGATIVE_SERIES
        series = {key: [] for key in keys}
        countries = {name: [] for name in COUNTRY_ORDER}
        demand, carbon_values, carbon_kind, price_values = [], [], [], []
        for t in times:
            if step == 300:
                fuels = generation.get(t)
                embedded_wind = self.interpolate(wind_table, t)
                embedded_solar = self.interpolate(solar_table, t)
            else:
                samples = [generation[s] for s in range(t, t + step, 300) if s in generation]
                fuels = None
                if samples:
                    codes = set().union(*samples)
                    fuels = {code: sum(sample.get(code, 0.0) for sample in samples) / len(samples) for code in codes}
                embedded_wind = self.average_half_hours(wind_table, t, t + step)
                embedded_solar = self.average_half_hours(solar_table, t, t + step)
            if not fuels:
                for key in keys:
                    series[key].append(None)
                for name in countries:
                    countries[name].append(None)
                demand.append(None)
            else:
                m = self.mix(fuels, embedded_wind, embedded_solar)
                for key in keys:
                    series[key].append(r2(m[key] / 1000.0))
                for name in countries:
                    countries[name].append(r2(m['countries'].get(name, 0.0) / 1000.0))
                demand.append(r2(m['demand'] / 1000.0))
            # Carbon and price are half-hourly: average them into larger buckets
            half_hours = range(floor_to(t, 1800), max(t + step, floor_to(t, 1800) + 1800), 1800)
            official = [carbon[h] for h in half_hours if h in carbon and carbon[h][0] is not None]
            if official:
                carbon_values.append(round(sum(c[0] for c in official) / len(official)))
                carbon_kind.append('a' if all(c[2] == 'actual' for c in official) else 'f')
            elif fuels and m['carbon_estimate'] is not None:
                carbon_values.append(round(m['carbon_estimate']))
                carbon_kind.append('e')
            else:
                carbon_values.append(None)
                carbon_kind.append(None)
            price_points = [prices[h] for h in half_hours if prices.get(h) is not None]
            price_values.append(round(sum(price_points) / len(price_points), 2) if price_points else None)
        return {
            'range': range_key, 'step': step, 't': times,
            'series': series, 'countries': countries, 'demand': demand,
            'carbon': carbon_values, 'carbon_kind': carbon_kind, 'price': price_values,
            'generated': int(time.time()),
        }

    # ---- /api/live -------------------------------------------------------

    def live(self):
        return self.cached(('live',), self.store.version, self._build_live)

    def _rank(self, values, current, higher_is_better=False):
        """Share of past values that are worse than the current one (0..1)."""
        values = [v for v in values if v is not None]
        if current is None or len(values) < 12:
            return None
        if higher_is_better:
            return sum(1 for v in values if v < current) / len(values)
        return sum(1 for v in values if v > current) / len(values)

    def _build_live(self):
        now = time.time()
        payload = {
            'version': VERSION, 'server_time': int(now), 'refresh_seconds': BROWSER_REFRESH,
            'history_version': self.store.history_version, 'ranges': self.available_ranges(),
            'history_days': HISTORY_DAYS, 'persistent': self.store.persistent,
            'time': None, 'status': self.status_payload(now),
        }
        t = self.latest_generation_time()
        half_hour = floor_to(t if t else now, 1800)

        # Carbon intensity: official figure, 48 h forecast and 7-day context
        carbon_past = self.carbon_table(0, half_hour - 7 * 86400, half_hour - 1)
        carbon_future = self.carbon_table(0, half_hour, half_hour + 49 * 3600)
        def points(table):
            return [[k, round(v[0]), v[1]] for k, v in sorted(table.items()) if v[0] is not None]

        payload['forecast'] = points(carbon_future)
        payload['carbon_past'] = points({k: v for k, v in carbon_past.items() if k >= half_hour - 86400})
        if REGION_ID:
            region_now = floor_to(now, 1800)
            region_future = self.carbon_table(REGION_ID, region_now, region_now + 49 * 3600)
            region_past = self.carbon_table(REGION_ID, region_now - 86400, region_now - 1)
            current = region_future.get(region_now)
            payload['region'] = {
                'id': REGION_ID, 'name': self.store.get_meta('region_name', 'Region ' + str(REGION_ID)),
                'value': round(current[0]) if current and current[0] is not None else None,
                'index': current[1] if current else None,
                'forecast': points(region_future), 'past': points(region_past),
                'mix': None, 'recent': self.region_recent(region_now),
            }
            try:
                mix = json.loads(self.store.get_meta('region_mix', '') or 'null')
                if mix and region_now - mix['t'] <= 3 * 3600:
                    payload['region']['mix'] = mix
            except (ValueError, TypeError, KeyError):
                pass

        # Wholesale price
        price_rows = self.store.query("SELECT t, v FROM hh WHERE k = 'price' AND t >= ? ORDER BY t",
                                      (half_hour - 7 * 86400,))
        if price_rows:
            latest_t, latest_price = price_rows[-1]
            today = [v for k, v in price_rows if k > now - 86400]
            payload['price'] = {
                'value': round(latest_price, 2), 'from': latest_t,
                'rank': self._rank([v for k, v in price_rows[:-1]], latest_price),
                'day_min': round(min(today), 2) if today else None,
                'day_max': round(max(today), 2) if today else None,
                'day_avg': round(sum(today) / len(today), 2) if today else None,
            }

        # Frequency: latest reading plus the last hour
        freq_rows = self.store.query('SELECT t, hz FROM freq WHERE t >= ? ORDER BY t', (now - 3600,))
        if freq_rows:
            payload['frequency'] = {
                'hz': round(freq_rows[-1][1], 3), 't': int(freq_rows[-1][0]),
                'series': [[int(k), round(v, 3)] for k, v in freq_rows],
            }

        if not t:
            return payload

        # Generation mix at the latest 5-minute period
        fuels = self.load_generation(t, t)[t]
        wind_table, solar_table, wind_basis, solar_basis = self.load_embedded(t - 3600, t + 3600)
        m = self.mix(fuels, self.interpolate(wind_table, t), self.interpolate(solar_table, t))
        supply = m['supply'] or 1.0

        def source(key, label, gw_value, group, note=None):
            item = {'key': key, 'label': label, 'gw': r2(gw_value / 1000.0), 'group': group,
                    'pct': round(100.0 * max(0.0, gw_value) / supply, 1)}
            if note:
                item['note'] = note
            return item

        sources = [
            source('wind', 'Wind', m['wind'], 'renewable'),
            source('solar', 'Solar', m['solar'], 'renewable'),
            source('hydro', 'Hydro', m['hydro'], 'renewable'),
            source('nuclear', 'Nuclear', m['nuclear'], 'nuclear'),
            source('biomass', 'Biomass', m['biomass'], 'biomass'),
            source('gas', 'Gas', m['gas'], 'fossil'),
            source('coal', 'Coal', m['coal'], 'fossil'),
            source('oil', 'Oil', m['oil'], 'fossil'),
            source('other', 'Other', m['other'], 'other'),
            source('imports', 'Imports', m['imports'], 'transfer'),
            source('storage_out', 'Storage', m['storage_out'], 'transfer'),
        ]
        sources[0]['parts'] = {'transmission': r2(m['wind_transmission'] / 1000.0),
                               'embedded': r2(m['wind_embedded'] / 1000.0)}
        sources[5]['parts'] = {'ccgt': r2(m['ccgt'] / 1000.0), 'ocgt': r2(m['ocgt'] / 1000.0)}
        current_half_hour = floor_to(t, 1800)
        sources[0]['basis'] = wind_basis.get(current_half_hour)
        sources[1]['basis'] = solar_basis.get(current_half_hour)

        links = []
        for code, mw in sorted(m['links'].items()):
            name, country, capacity, note = INTERCONNECTORS.get(code, (code, 'Other', None, ''))
            links.append({'code': code, 'name': name, 'country': country, 'capacity': capacity,
                          'note': note, 'gw': r2(mw / 1000.0)})
        countries = [{'country': c, 'gw': r2(m['countries'][c] / 1000.0)}
                     for c in COUNTRY_ORDER + sorted(set(m['countries']) - set(COUNTRY_ORDER))
                     if c in m['countries']]

        official = carbon_future.get(current_half_hour)
        if official and official[0] is not None:
            carbon = {'value': round(official[0]), 'index': official[1], 'kind': official[2],
                      'from': current_half_hour}
        else:
            carbon = {'value': round(m['carbon_estimate']) if m['carbon_estimate'] is not None else None,
                      'index': None, 'kind': 'estimate', 'from': t}
        carbon['estimate'] = round(m['carbon_estimate']) if m['carbon_estimate'] is not None else None
        carbon['rank'] = self._rank([v[0] for v in carbon_past.values()], carbon['value'])

        week = self.history('7d')
        demand_gw = m['demand'] / 1000.0
        payload.update({
            'time': t,
            'demand_gw': r2(demand_gw),
            'supply_gw': r2(m['supply'] / 1000.0),
            'exports_gw': r2(abs(m['exports']) / 1000.0),
            'storing_gw': r2(abs(m['storage_in']) / 1000.0),
            'demand_rank': self._rank(week['demand'], demand_gw, higher_is_better=True),
            'demand_week': {
                'min': min((v for v in week['demand'] if v is not None), default=None),
                'max': max((v for v in week['demand'] if v is not None), default=None),
            },
            'sources': sources,
            'groups': {
                'renewable': round(100.0 * (m['wind'] + m['solar'] + m['hydro']) / supply, 1),
                'low_carbon': round(100.0 * (m['wind'] + m['solar'] + m['hydro'] + m['nuclear']) / supply, 1),
                'fossil': round(100.0 * (m['gas'] + m['coal'] + m['oil']) / supply, 1),
            },
            'links': links,
            'countries': countries,
            'storage': {'pumped': r2(m['pumped'] / 1000.0), 'battery': r2(m['battery'] / 1000.0)},
            'carbon': carbon,
        })
        return payload

    # ---- /api/map and /api/station -------------------------------------

    def map_data(self):
        version = (self.store.version, self.units.version if self.units else 0)
        return self.cached(('map',), version, self._build_map)

    def _build_map(self):
        live = self.live()
        snapshot = self.units.snapshot if self.units else None
        payload = {
            'version': VERSION, 'server_time': int(time.time()), 'refresh_seconds': BROWSER_REFRESH,
            'time': live.get('time'), 'status': live.get('status'), 'persistent': self.store.persistent,
            'demand_gw': live.get('demand_gw'), 'supply_gw': live.get('supply_gw'),
            'exports_gw': live.get('exports_gw'), 'storing_gw': live.get('storing_gw'),
            'carbon': live.get('carbon'), 'stations': [], 'links': [], 'categories': [],
            'unplaced': [], 'model': None,
        }
        for link in live.get('links', []):
            item = {'code': link['code'], 'name': link['name'], 'country': link['country'], 'note': link['note'],
                    'mw': None if link['gw'] is None else round(link['gw'] * 1000),
                    'cap': None if link['capacity'] is None else round(link['capacity'] * 1000)}
            ends = LINK_ENDS.get(link['code'])
            if ends:
                item['gb'] = map_point(ends[0][0], ends[0][1]) + [ends[0][2]]
                item['far'] = map_point(ends[1][0], ends[1][1]) + [ends[1][2]]
            payload['links'].append(item)

        sources = {item['key']: item for item in live.get('sources', [])}
        def mw(key, part=None):
            item = sources.get(key)
            if not item:
                return None
            value = item.get('parts', {}).get(part) if part else item.get('gw')
            return None if value is None else round(value * 1000)
        measured = {
            'wind': mw('wind', 'transmission'), 'solar': 0 if sources else None, 'nuclear': mw('nuclear'),
            'gas': mw('gas'), 'biomass': mw('biomass'), 'hydro': mw('hydro'), 'storage': mw('storage_out'),
            'other': None if not sources else sum(mw(k) or 0 for k in ('coal', 'oil', 'other')),
        }
        embedded = {'wind': mw('wind', 'embedded'), 'solar': mw('solar')}
        model = snapshot['categories'] if snapshot else {}
        counts = {}
        if snapshot:
            for sid, st in snapshot['stations'].items():
                station = self.fleet.stations[sid]
                if st['mw'] >= 1:
                    counts[station['cat']] = counts.get(station['cat'], 0) + 1
        for cat in CATEGORY_ORDER:
            part = model.get(cat, {})
            payload['categories'].append({
                'key': cat, 'label': CATEGORY_LABELS[cat], 'measured': measured.get(cat),
                'embedded': embedded.get(cat), 'mapped': round(part['mapped']) if part else None,
                'unplaced': round(part['unplaced']) if part else None, 'stations': counts.get(cat, 0),
            })
        if not snapshot:
            return payload
        for sid, st in sorted(snapshot['stations'].items(), key=lambda item: -abs(item[1]['mw'])):
            station = self.fleet.stations[sid]
            capacity = station['capacity'] or st['ref_cap']
            payload['stations'].append({
                'id': sid, 'n': station['name'], 'c': station['cat'], 'x': station['x'], 'y': station['y'],
                'na': station['nation'], 'off': 1 if station['offshore'] else 0,
                'cap': round(capacity) if capacity else None, 'mw': round(st['mw'], 1),
                'u': st['units'], 'on': st['running'], 'i': st['instructed'],
            })
        payload['unplaced'] = snapshot['unplaced']
        payload['model'] = {
            'basis': snapshot['basis'], 'at': snapshot['at'], 'time': snapshot['time'],
            'period': snapshot['period'], 'scale': snapshot['scale'], 'counts': snapshot['counts'],
            'unplaced_count': snapshot['unplaced_count'], 'reference_units': snapshot['reference_units'],
            'reference_error': snapshot['reference_error'], 'known_stations': len(self.fleet.stations),
        }
        return payload

    # ---- /api/replay: the map's time machine -----------------------------

    def replay(self):
        latest = self.store.scalar('SELECT MAX(t) FROM st')
        return self.cached(('replay',), latest, lambda: self._build_replay(latest))

    def _build_replay(self, latest, step=600):
        """Station output, interconnector flows and national totals every ten
        minutes over the stored station history (up to STATION_HISTORY_DAYS)."""
        if not latest:
            return {'step': step, 't': [], 'stations': {}, 'links': {}, 'totals': {}}
        end = floor_to(latest, step)
        first = self.store.scalar('SELECT MIN(t) FROM st') or end
        start = max(floor_to(first, step), end - STATION_HISTORY_DAYS * 86400 + step)
        times = list(range(start, end + 1, step))
        index = {t: i for i, t in enumerate(times)}
        stations = {}
        for sid, bucket, mw in self.store.query(
                'SELECT id, (t / ?) * ? AS b, AVG(mw) FROM st WHERE t >= ? AND t < ? GROUP BY id, b',
                (step, step, start, end + step)):
            series = stations.get(sid)
            if series is None:
                series = stations[sid] = [None] * len(times)
            i = index.get(bucket)
            if i is not None:
                series[i] = int(round(mw))
        fuels_by_bucket = {}
        for bucket, fuel, mw in self.store.query(
                'SELECT (t / ?) * ? AS b, fuel, AVG(mw) FROM gen WHERE t >= ? AND t < ? GROUP BY b, fuel',
                (step, step, start, end + step)):
            fuels_by_bucket.setdefault(bucket, {})[fuel] = mw
        wind_table, solar_table, _, _ = self.load_embedded(start, end + step)
        keys = ['wind', 'wind_embedded', 'solar', 'nuclear', 'gas', 'biomass', 'hydro', 'storage', 'other',
                'imports', 'exports', 'demand']
        totals = {key: [None] * len(times) for key in keys}
        links = {}
        for i, t in enumerate(times):
            fuels = fuels_by_bucket.get(t)
            if not fuels or len(fuels) < 8:
                continue
            middle = t + step // 2
            m = self.mix(fuels, self.interpolate(wind_table, middle), self.interpolate(solar_table, middle))
            values = {
                'wind': m['wind_transmission'], 'wind_embedded': m['wind_embedded'], 'solar': m['solar'],
                'nuclear': m['nuclear'], 'gas': m['gas'], 'biomass': m['biomass'], 'hydro': m['hydro'],
                'storage': m['storage_out'], 'other': m['coal'] + m['oil'] + m['other'],
                'imports': m['imports'], 'exports': m['exports'], 'demand': m['demand'],
            }
            for key in keys:
                totals[key][i] = int(round(values[key]))
            for code, mw in m['links'].items():
                links.setdefault(code, [None] * len(times))[i] = int(round(mw))
        return {'step': step, 't': times, 'stations': {str(k): v for k, v in stations.items()},
                'links': links, 'totals': totals, 'generated': int(time.time())}

    def station_history(self, sid):
        station = self.fleet.stations.get(sid) if self.fleet else None
        if station is None:
            return None
        rows = self.store.query('SELECT t, mw FROM st WHERE id = ? AND t >= ? ORDER BY t',
                                (sid, time.time() - 86400))
        return {'id': sid, 'name': station['name'], 'cat': station['cat'], 'step': 300,
                'since': self.store.scalar('SELECT MIN(t) FROM st'),
                'points': [[t, round(v, 1)] for t, v in rows]}

    def status_payload(self, now):
        snapshot = self.status.snapshot()
        latest = self.latest_generation_time()
        units = self.units.snapshot if self.units else None
        freshness = {
            'generation': (latest + 300) if latest else None,
            'frequency': self.store.scalar('SELECT MAX(t) FROM freq'),
            'price': self.store.scalar("SELECT MAX(t) FROM hh WHERE k = 'price'"),
            'solar': self.store.scalar("SELECT MAX(t) FROM hh WHERE k = 'pv'"),
            'units': units['at'] if units else None,
        }
        result = {}
        for key, item in snapshot.items():
            item['data_time'] = int(freshness[key]) if freshness.get(key) else None
            result[key] = item
        return result

    # ---- /api/regions ----------------------------------------------------

    def regions(self):
        return self.cached(('regions',), (self.store.version, int(time.time() // 300)), self._build_regions)

    def _build_regions(self):
        now = time.time()
        current = floor_to(now, 1800)
        start, end = current - 86400, current + 48 * 3600
        times = list(range(start, end, 1800))
        index = {t: i for i, t in enumerate(times)}
        values, levels, mixes = {}, {}, {}
        for t, region, value, idx, mix in self.store.query(
                'SELECT t, region, v, idx, mix FROM reg WHERE t >= ? AND t < ? ORDER BY t', (start, end)):
            i = index.get(t)
            if i is None:
                continue
            key = str(region)
            if key not in values:
                values[key], levels[key], mixes[key] = [None] * len(times), [None] * len(times), [None] * len(times)
            values[key][i] = None if value is None else int(round(value))
            levels[key][i] = INDEX_LEVELS.index(idx) if idx in INDEX_LEVELS else None
            mixes[key][i] = mix
        solar = {}
        keys = ['rpv%d' % pes for pes in sorted(PES_REGIONS)]
        for key, t, value in self.store.query(
                'SELECT k, t, v FROM hh WHERE k IN (%s) AND t >= ? AND t < ?' % ','.join('?' * len(keys)),
                (*keys, start, end)):
            i = index.get(t)
            if i is not None:
                series = solar.setdefault(str(PES_REGIONS[int(key[3:])]), [None] * len(times))
                series[i] = int(round(value))
        return {
            't0': start, 'step': 1800, 'n': len(times), 'now': index.get(current), 'home': REGION_ID or None,
            'names': {str(k): v for k, v in REGION_NAMES.items()}, 'fuels': MIX_FUELS, 'levels': INDEX_LEVELS,
            'ci': values, 'index': levels, 'mix': mixes, 'solar': solar, 'generated': int(now),
        }

    # ---- /api/balancing ----------------------------------------------------

    def balancing(self):
        key = (self.store.version, self.units.version if self.units else 0, self.bal.version if self.bal else 0)
        return self.cached(('balancing',), key, self._build_balancing)

    def _build_balancing(self):
        now = time.time()
        day, period = settlement_period_at(now)
        names = {}

        def describe(unit, elexon=''):
            if unit not in names:
                names[unit] = self.units.describe(unit, elexon) if self.units else (None, 'other', unit)
            return names[unit]

        def place(sid):
            station = self.fleet.stations.get(sid) if (self.fleet and sid is not None) else None
            return (round(station['x'], 1), round(station['y'], 1)) if station else (None, None)

        # Now: units following instructions, priced at their bid or offer for this half hour
        snapshot = self.units.snapshot if self.units else None
        with self.bal.lock if self.bal else threading.Lock():
            prices = dict(self.bal.prices) if self.bal else {}
        live, cost, priced = [], 0.0, 0
        for item in (snapshot or {}).get('instructions', []):
            sid, cat, name = describe(item['unit'], item['elexon'])
            bid, offer = prices.get(item['unit'], (None, None))
            price = offer if item['delta'] > 0 else bid
            if price is not None:
                cost += item['delta'] * price
                priced += 1
            x, y = place(sid)
            live.append({'unit': item['unit'], 'sid': sid, 'name': name, 'cat': cat, 'delta': item['delta'],
                         'price': price, 'x': x, 'y': y})
        live.sort(key=lambda u: -abs(u['delta']))

        # Finished half hours, yesterday and today (UK days)
        days = [day - timedelta(days=1), day]
        fetched = {(d, sp) for d, sp in self.store.query('SELECT d, sp FROM balp WHERE d >= ?', (days[0].isoformat(),))}
        totals, top = {}, {}
        for d, sp, unit, elexon, vb, vo, cb, co in self.store.query(
                'SELECT d, sp, unit, elexon, vol_bid, vol_offer, cash_bid, cash_offer FROM bal WHERE d >= ?',
                (days[0].isoformat(),)):
            sid, cat, name = describe(unit, elexon or '')
            p = totals.setdefault((d, sp), [0.0] * 6)
            p[0] += vb or 0.0
            p[1] += vo or 0.0
            p[2] += cb or 0.0
            p[3] += co or 0.0
            if cat == 'wind':
                p[4] += vb or 0.0
                p[5] += cb or 0.0
            if d == day.isoformat():
                u = top.setdefault(unit, {'unit': unit, 'sid': sid, 'name': name, 'cat': cat, 'down': 0.0, 'up': 0.0, 'paid': 0.0})
                u['down'] += vb or 0.0
                u['up'] += vo or 0.0
                u['paid'] += (cb or 0.0) + (co or 0.0)
        series = {}
        for d in days:
            rows = []
            for sp in range(1, settlement_periods(d) + 1):
                p = totals.get((d.isoformat(), sp))
                if p is None and (d.isoformat(), sp) in fetched:
                    p = [0.0] * 6
                rows.append([settlement_start(d, sp)] + ([round(v, 1) for v in p] if p else [None] * 6))
            series['today' if d == day else 'yesterday'] = rows
        today = [r for r in series['today'] if r[1] is not None]
        summary = {
            'periods': len(today),
            'down_mwh': round(-sum(r[1] for r in today)), 'up_mwh': round(sum(r[2] for r in today)),
            'paid': round(sum(r[3] + r[4] for r in today)), 'wind_down_mwh': round(-sum(r[5] for r in today)),
            'wind_paid': round(sum(r[6] for r in today)),
        }
        ranked = sorted(top.values(), key=lambda u: -u['paid'])
        for u in ranked:
            u['down'], u['up'], u['paid'] = round(-u['down'], 1), round(u['up'], 1), round(u['paid'])
        return {
            'now': {'time': (snapshot or {}).get('at'), 'period': period, 'units': live[:200],
                    'down': round(-sum(u['delta'] for u in live if u['delta'] < 0)),
                    'up': round(sum(u['delta'] for u in live if u['delta'] > 0)),
                    'cost_hour': round(cost) if priced else None, 'priced': priced},
            'series': series, 'columns': ['t', 'bid_mwh', 'offer_mwh', 'bid_paid', 'offer_paid', 'wind_bid_mwh', 'wind_bid_paid'],
            'today': summary, 'top': ranked[:15], 'paid_units': len(ranked),
            'constraints': [{'d': c[0], 'cost': c[1], 'volume': c[2], 'parts': c[3]} for c in (self.bal.constraints if self.bal else [])],
            'generated': int(now),
        }

    # ---- /api/alerts: grid notices in force now --------------------------

    def notices(self, days=30):
        rows = self.store.query('SELECT t, kind, body FROM warn WHERE t >= ? ORDER BY t', (time.time() - days * 86400,))
        return [describe_notice(t, kind, body) for t, kind, body in rows]

    def alerts(self):
        now = time.time()
        latest = {}
        for notice in self.notices(7):
            if notice['routine'] or notice['kind'] == 'OTHER':
                continue
            latest[notice['kind']] = notice  # oldest first, so the latest message per kind wins
        active = []
        for notice in latest.values():
            if notice['cancelled']:
                continue
            hours = 24 if notice['kind'] == 'CAPACITY MARKET NOTICE' else 12
            end = notice['end'] or max(notice['start'] or 0, notice['published']) + hours * 3600
            if now <= end + 900:
                active.append(notice)
        active.sort(key=lambda n: (n['level'] != 'strong', -n['published']))
        return {'active': active, 'margin': self.margin_summary(), 'generated': int(now)}

    def margin_summary(self):
        if not self.outages:
            return None
        with self.outages.lock:
            series = list(self.outages.margin)
        now = time.time()
        series = [row for row in series if row[0] + 1800 > now]
        if not series:
            return None
        first, low = series[0], min(series, key=lambda row: row[1])
        key, label = margin_level(low[1])
        return {'t': first[0], 'margin': first[1], 'lolp': first[2], 'min': low[1], 'min_t': low[0],
                'lolp_max': max(row[2] for row in series), 'level': key, 'label': label,
                'series': [[t, m, round(p, 4)] for t, m, p in series]}

    # ---- /api/longterm and /api/outlook -------------------------------------

    LT_FUELS = ['gas', 'coal', 'nuclear', 'wind', 'solar', 'biomass', 'hydro', 'imports', 'storage', 'other']
    LT_SQL = ('AVG(gas), AVG(coal), AVG(nuclear), AVG(COALESCE(wind, 0) + COALESCE(wind_emb, 0)), AVG(solar), '
              'AVG(biomass), AVG(hydro), AVG(imports), AVG(storage), AVG(other), AVG(generation), AVG(ci)')

    @staticmethod
    def longterm_bucket(span):
        if span <= 14 * 86400:
            return 1800
        if span <= 92 * 86400:
            return 6 * 3600
        if span <= 2 * 366 * 86400:
            return 86400
        return 7 * 86400

    def longterm_extent(self):
        first, last, count = self.store.query('SELECT MIN(t), MAX(t), COUNT(*) FROM lh')[0]
        price_first, price_last = self.store.query('SELECT MIN(t), MAX(t) FROM lp WHERE price IS NOT NULL')[0]
        complete = bool(last and last > time.time() - 10 * 86400)
        progress = 0.0 if not last else max(0.0, min(1.0, (last - LH_START) / max(1, time.time() - 2 * 86400 - LH_START)))
        return {'first': first, 'last': last, 'rows': count, 'price_first': price_first, 'price_last': price_last,
                'complete': complete, 'progress': round(progress, 3)}

    def longterm(self, start=None, end=None):
        extent = self.longterm_extent()
        if not extent['complete']:
            return {'ready': False, 'enabled': LONG_HISTORY, 'extent': extent}
        end = min(int(end or extent['last'] + 1800), extent['last'] + 1800)
        start = max(int(start or end - 365 * 86400), extent['first'])
        if end - start < 86400:
            start = end - 86400
        bucket = self.longterm_bucket(end - start)
        key = ('longterm', floor_to(start, bucket), floor_to(end, bucket))
        version = (extent['last'], extent['price_last'])
        with self.cache_lock:  # zooming makes many one-off ranges: keep only the recent ones
            ranges = [k for k in self.cache if k[0] == 'longterm']
            for stale in ranges[:-40]:
                del self.cache[stale]
        return self.cached(key, version, lambda: self._build_longterm(floor_to(start, bucket), end, bucket, extent))

    def _build_longterm(self, start, end, bucket, extent):
        # Weekly buckets start on Monday 00:00 UTC (the epoch was a Thursday)
        offset = 4 * 86400 if bucket == 7 * 86400 else 0
        if offset:
            start = floor_to(start - offset, bucket) + offset
        rows = self.store.query('SELECT ((t - ?) / ?) * ? + ? AS b, %s, COUNT(*) FROM lh WHERE t >= ? AND t < ? '
                                'GROUP BY b ORDER BY b' % self.LT_SQL, (offset, bucket, bucket, offset, start, end))
        prices = dict(self.store.query('SELECT ((t - ?) / ?) * ? + ? AS b, AVG(price) FROM lp WHERE t >= ? AND t < ? '
                                       'AND price IS NOT NULL GROUP BY b', (offset, bucket, bucket, offset, start, end)))
        times = [row[0] for row in rows]
        series = {fuel: [None if row[1 + i] is None else round(row[1 + i]) for row in rows] for i, fuel in enumerate(self.LT_FUELS)}
        def r(v, n=0):
            return None if v is None else round(v, n) if n else round(v)
        return {
            'ready': True, 'enabled': LONG_HISTORY, 'extent': extent, 'from': start, 'to': end, 'bucket': bucket,
            't': times, 'mix': series,
            'generation': [r(row[11]) for row in rows],
            'ci': [r(row[12]) for row in rows],
            'price': [r(prices.get(t), 2) for t in times],
        }

    def longterm_summary(self):
        extent = self.longterm_extent()
        if not extent['complete']:
            return {'ready': False, 'enabled': LONG_HISTORY, 'extent': extent}
        return self.cached(('longterm-summary',), (extent['last'], extent['price_last'], int(time.time() // 3600)),
                           lambda: self._build_summary(extent))

    def _build_summary(self, extent):
        q, now = self.store.query, time.time()
        records = []

        def add(key, title, unit, row, span, better):
            if row and row[0] is not None and row[1] is not None:
                records.append({'key': key, 'title': title, 'unit': unit, 't': row[0], 'value': round(row[1], 1),
                                'span': span, 'recent': now - row[0] < 31 * 86400, 'better': better})

        full_day = 'GROUP BY d HAVING COUNT(*) >= 46'
        add('cleanest-hh', 'Cleanest half hour', 'g/kWh',
            (q('SELECT t, ci FROM lh WHERE ci > 0 ORDER BY ci, t DESC LIMIT 1') or [None])[0], 'hh', 'low')
        add('cleanest-day', 'Cleanest day', 'g/kWh',
            (q('SELECT (t / 86400) * 86400 AS d, AVG(ci) a FROM lh WHERE ci > 0 %s ORDER BY a LIMIT 1' % full_day) or [None])[0], 'day', 'low')
        add('wind-peak', 'Most wind power', 'MW',
            (q('SELECT t, COALESCE(wind, 0) + COALESCE(wind_emb, 0) w FROM lh ORDER BY w DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('windiest-day', 'Windiest day', 'MW',
            (q('SELECT (t / 86400) * 86400 AS d, AVG(COALESCE(wind, 0) + COALESCE(wind_emb, 0)) a FROM lh %s ORDER BY a DESC LIMIT 1' % full_day) or [None])[0], 'day', 'high')
        add('wind-share', 'Highest wind share', '%',
            (q('SELECT t, 100.0 * (COALESCE(wind, 0) + COALESCE(wind_emb, 0)) / generation s FROM lh '
               'WHERE generation > 10000 ORDER BY s DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('solar-peak', 'Most solar power', 'MW',
            (q('SELECT t, solar FROM lh ORDER BY solar DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('zero-carbon', 'Highest zero-carbon share', '%',
            (q('SELECT t, 100.0 * (COALESCE(nuclear, 0) + COALESCE(wind, 0) + COALESCE(wind_emb, 0) + COALESCE(solar, 0) + COALESCE(hydro, 0)) / generation s '
               'FROM lh WHERE generation > 10000 ORDER BY s DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('demand-low', 'Least generation needed', 'MW',
            (q('SELECT t, generation FROM lh WHERE generation > 5000 ORDER BY generation LIMIT 1') or [None])[0], 'hh', 'low')
        add('demand-high', 'Most generation needed', 'MW',
            (q('SELECT t, generation FROM lh ORDER BY generation DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('price-high', 'Highest wholesale price', '£/MWh',
            (q('SELECT t, price FROM lp WHERE price IS NOT NULL ORDER BY price DESC LIMIT 1') or [None])[0], 'hh', 'high')
        add('price-low', 'Lowest wholesale price', '£/MWh',
            (q('SELECT t, price FROM lp WHERE price IS NOT NULL ORDER BY price LIMIT 1') or [None])[0], 'hh', 'low')
        last_coal = q('SELECT MAX(t) FROM lh WHERE coal > 20')[0][0]

        # The latest complete day against the same weekday a year earlier
        compare = None
        latest = q('SELECT (t / 86400) * 86400 AS d FROM lh %s ORDER BY d DESC LIMIT 1' % full_day)
        if latest:
            def day(d):
                row = q('SELECT %s, COUNT(*) FROM lh WHERE t >= ? AND t < ?' % self.LT_SQL, (d, d + 86400))[0]
                if not row[-1]:
                    return None
                price = q('SELECT AVG(price) FROM lp WHERE t >= ? AND t < ? AND price IS NOT NULL', (d, d + 86400))[0][0]
                total = sum(v or 0 for v in row[:10]) or 1
                return {'t': d, 'mix': {f: round((row[i] or 0) / total, 4) for i, f in enumerate(self.LT_FUELS)},
                        'generation': round(row[10] or 0), 'ci': None if row[11] is None else round(row[11]),
                        'price': None if price is None else round(price, 2)}
            d = latest[0][0]
            compare = {'now': day(d), 'then': day(d - 364 * 86400)}

        years = []
        prices = dict(q("SELECT CAST(strftime('%Y', t, 'unixepoch') AS INTEGER) y, AVG(price) FROM lp WHERE price IS NOT NULL GROUP BY y"))
        for row in q("SELECT CAST(strftime('%%Y', t, 'unixepoch') AS INTEGER) y, %s, COUNT(*) FROM lh GROUP BY y ORDER BY y" % self.LT_SQL):
            total = sum(v or 0 for v in row[1:11]) or 1
            years.append({'year': row[0], 'mix': {f: round((row[1 + i] or 0) / total, 4) for i, f in enumerate(self.LT_FUELS)},
                          'ci': None if row[12] is None else round(row[12]), 'generation': round(row[11] or 0),
                          'price': None if prices.get(row[0]) is None else round(prices[row[0]], 2),
                          'complete': row[13] >= 17000})
        return {'ready': True, 'extent': extent, 'records': records, 'last_coal': last_coal,
                'compare': compare, 'years': years}

    def outlook_payload(self):
        version = (self.outlook.version if self.outlook else 0, self.store.version, int(time.time() // 300))
        return self.cached(('outlook',), version, self._build_outlook)

    def _build_outlook(self):
        now = time.time()
        start, end = floor_to(now, 1800), floor_to(now, 1800) + 48 * 3600
        def clip(pairs):
            return [[int(t), round(v)] for t, v in pairs if start <= t <= end and v is not None]
        solar = self.store.query("SELECT t, v FROM hh WHERE k = 'fc_solar' AND t >= ? AND t <= ? ORDER BY t", (start, end))
        carbon = [(t, v[0]) for t, v in sorted(self.carbon_table(0, start, end).items())]
        outlook = self.outlook
        return {
            'from': start, 'to': end,
            'demand': clip(outlook.demand if outlook else []),
            'wind': clip(outlook.wind if outlook else []),
            'solar': clip(solar),
            'carbon': clip(carbon),
            'raw': outlook.raw if outlook else {},
            'demand_kind': outlook.demand_kind if outlook else 'national',
        }

    # ---- /api/outages -------------------------------------------------------

    def outages_payload(self):
        key = (self.outages.version if self.outages else 0, self.units.version if self.units else 0, int(time.time() // 60))
        return self.cached(('outages',), key, self._build_outages)

    def _build_outages(self):
        now = time.time()
        with self.outages.lock if self.outages else threading.Lock():
            events = list(self.outages.events) if self.outages else []
            checked = self.outages.checked if self.outages else 0
        snapshot = self.units.snapshot if self.units else None
        live = (snapshot or {}).get('stations', {})
        groups, closed = {}, {}
        for e in events:
            asset = e['asset'] or e['unit']
            if e['end'] is not None and e['end'] > now + 2 * 365 * 86400 and e['start'] <= now:
                # Out for years: a closure or mothballing rather than an outage
                closed[e['unit'] or asset] = max(closed.get(e['unit'] or asset, 0), e['lost'])
                continue
            if asset.startswith('I_'):
                link = asset.split('-', 1)[-1]
                name = next((v for k, v in LINK_NAMES.items() if link.upper().startswith(k)), asset[2:])
                key, sid, cat = 'link:' + name, None, 'imports'
            else:
                unit = e['unit'] or short_unit_id(asset)
                sid, cat, name = self.units.describe(unit, asset) if self.units else (None, 'other', unit)
                key = 'st:%s' % sid if sid is not None else 'unit:' + unit
            current = e['start'] <= now
            g = groups.setdefault((key, current), {'name': name, 'sid': sid, 'cat': cat, 'per_unit': {}, 'units': set(),
                                                    'start': e['start'], 'end': e['end'], 'type': set(), 'cause': set()})
            # Import and export notices describe one cable; overlapping notices for one unit don't add up
            unit_key = 'cable' if key.startswith('link:') else (e['unit'] or asset)
            best = g['per_unit'].get(unit_key)
            if best is None or e['lost'] > best[0]:
                g['per_unit'][unit_key] = (e['lost'], e['normal'] or 0.0)
            g['units'].add(e['unit'] or asset)
            g['start'] = min(g['start'], e['start'])
            g['end'] = None if (g['end'] is None or e['end'] is None) else max(g['end'], e['end'])
            if e['type']:
                g['type'].add(e['type'])
            if e['cause']:
                g['cause'].add(e['cause'])
        offline, upcoming = [], []
        for (key, current), g in groups.items():
            lost = sum(v[0] for v in g['per_unit'].values())
            normal = sum(v[1] for v in g['per_unit'].values())
            item = {'name': g['name'], 'sid': g['sid'], 'cat': g['cat'], 'lost': round(lost), 'normal': round(normal) or None,
                    'units': len(g['per_unit']), 'start': g['start'], 'end': g['end'], 'planned': g['type'] == {'Planned'},
                    'cause': '; '.join(sorted(g['cause']))[:120], 'mw': round(live[g['sid']]['mw']) if g['sid'] in live else None}
            (offline if current else upcoming).append(item)
        offline.sort(key=lambda i: -i['lost'])
        upcoming.sort(key=lambda i: i['start'])
        lost_by_cat = {}
        for item in offline:
            lost_by_cat[item['cat']] = lost_by_cat.get(item['cat'], 0) + item['lost']
        nuclear = []
        if self.fleet:
            outage_by_sid = {i['sid']: i for i in offline if i['sid'] is not None}
            for sid, st in self.fleet.stations.items():
                if st['cat'] != 'nuclear' or not st.get('capacity'):
                    continue
                o = outage_by_sid.get(sid)
                nuclear.append({'sid': sid, 'name': st['name'], 'capacity': round(st['capacity']),
                                'mw': round(live[sid]['mw']) if sid in live else None,
                                'out': o['lost'] if o else 0, 'end': o['end'] if o else None, 'cause': o['cause'] if o else '',
                                'planned': o['planned'] if o else None})
            nuclear.sort(key=lambda n: -n['capacity'])
        notices = [n for n in self.notices(30) if not n['routine']]
        notices.sort(key=lambda n: -n['published'])
        return {'margin': self.margin_summary(), 'offline': offline[:60], 'offline_count': len(offline),
                'closed_count': len(closed), 'closed_mw': round(sum(closed.values())),
                'upcoming': upcoming[:40], 'lost_by_cat': lost_by_cat, 'lost_total': round(sum(lost_by_cat.values())),
                'nuclear': nuclear, 'notices': notices[:30], 'checked': int(checked) or None, 'generated': int(now)}


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

class App:
    store = None
    grid = None
    ui = None
    geo = (b'', '')
    regions = (b'', '')


def load_file(path, variable, fallback):
    try:
        with open(path, 'rb') as handle:
            body = handle.read()
    except OSError as error:
        log.error('Could not read %s: %s. Check %s points at the right file.', path, error, variable)
        body = fallback
    return body, '"' + hashlib.sha1(body).hexdigest()[:16] + '"'


# Pages: (key, path, tab label, file). The tabs appear in this order.
PAGES = [
    ('overview', '/', 'Overview', 'index.html'),
    ('map', '/map', 'Map', 'map.html'),
    ('stations', '/stations', 'Stations', 'map.html'),
    ('regions', '/regions', 'Regions', 'regions.html'),
    ('balancing', '/balancing', 'Balancing', 'balancing.html'),
    ('outages', '/outages', 'Outages', 'outages.html'),
    ('history', '/history', 'History', 'history.html'),
    ('display', '/display', 'Display', 'display.html'),  # kiosk view: a header button, not a tab
]
STATIC_TYPES = {
    '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml',
    '.png': 'image/png', '.json': 'application/json; charset=utf-8', '.webmanifest': 'application/manifest+json',
}
MISSING_PAGE = (b'<!doctype html><title>GB Grid</title>'
                b'<p>This page is missing from the image. Rebuild or pull it again.</p>')
BRAND_SVG = ('<svg viewBox="0 0 26 26" aria-hidden="true"><rect x="0.5" y="0.5" width="25" height="25" rx="7" '
             'fill="none" stroke="currentColor" stroke-opacity="0.25"/><path d="M4 13c1.5-5 3-5 4.5 0s3 5 4.5 0 3-5 '
             '4.5 0 3 5 4.5 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>')
HEAD_HTML = (
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    '<meta name="color-scheme" content="light dark">\n'
    '<!--#importmap-->\n'
    '<link rel="stylesheet" href="{{static:base.css}}">\n'
    '<link rel="icon" href="{{static:favicon.svg}}" type="image/svg+xml">\n'
    '<link rel="apple-touch-icon" href="{{static:icon-180.png}}">\n'
    "<script>try{var t=localStorage.getItem('gb-grid-theme'),m=location.search.match(/[?&]theme=(light|dark)/);if(m)t=m[1];"
    "if(t==='light'||t==='dark')"
    "document.documentElement.setAttribute('data-theme',t)}catch(e){}"
    "if(/[?&]display(=|&|$)/.test(location.search))document.documentElement.classList.add('display')</script>")
ELEXON_CREDIT = ('Contains BMRS data © Elexon Limited copyright and database right <span class="year">2026</span> '
                 '(<a href="https://www.elexon.co.uk/data/balancing-mechanism-reporting-agent/copyright-licence-bmrs-data/" '
                 'rel="noopener">licence</a>).')
NESO_CREDIT = ('Supported by National Energy SO Open Data '
               '(<a href="https://www.neso.energy/data-portal/neso-open-licence" rel="noopener">licence</a>).')
CREDITS_HTML = (
    '<p class="credit">' + ELEXON_CREDIT + ' ' + NESO_CREDIT + ' Carbon intensity from the '
    '<a href="https://carbonintensity.org.uk/" rel="noopener">NESO Carbon Intensity API</a>; embedded wind and solar '
    'region boundaries, constraint costs and the generation mix since 2009 from the <a href="https://www.neso.energy/data-portal" rel="noopener">NESO Data Portal</a>. Solar estimates from '
    '<a href="https://www.solar.sheffield.ac.uk/pvlive/" rel="noopener">Sheffield Solar PV_Live</a>. Station locations '
    'from the <a href="https://github.com/OSUKED/Power-Station-Dictionary" rel="noopener">Power Station Dictionary</a>; '
    'map outlines from <a href="https://www.naturalearthdata.com/" rel="noopener">Natural Earth</a>.</p>\n'
    '<p class="credit">A self-hosted dashboard, not an official Elexon or NESO service. Inspired by Kate Morley’s '
    '<a href="https://grid.iamkate.com/" rel="noopener">National Grid: Live</a>. All times are UK time.</p>')
CREDITS_SHORT_HTML = (
    '<p class="credit">' + ELEXON_CREDIT + ' ' + NESO_CREDIT + ' Station locations: '
    '<a href="https://github.com/OSUKED/Power-Station-Dictionary" rel="noopener">Power Station Dictionary</a>. '
    'Map: <a href="https://www.naturalearthdata.com/" rel="noopener">Natural Earth</a>.</p>')


def header_html(current):
    tabs = ''.join('<a href="%s"%s%s>%s</a>' % (
        path, ' data-view="%s"' % key if key in ('map', 'stations') else '',
        ' aria-current="page"' if key == current else '', label) for key, path, label, _ in PAGES if key != 'display')
    return (
        '<div class="alert-bar" id="alert-bar" role="region" aria-label="Grid notices" hidden></div>\n'
        '<header class="top">\n  <div class="wrap top-inner">\n'
        '    <a class="brand" href="/" aria-label="GB grid overview">' + BRAND_SVG + '<span>GB grid</span></a>\n'
        '    <nav class="nav" aria-label="Pages">' + tabs + '</nav>\n'
        '    <p class="live"><span class="dot" id="live-dot"></span>'
        '<span id="live-text">Connecting to the dashboard server…</span></p>\n'
        '    <div class="tools"><a class="tool-btn" href="/display" title="A full-screen view for a spare screen or tablet">'
        'Display</a><button class="theme-btn" id="theme-btn" type="button">Theme: auto</button></div>\n'
        '  </div>\n</header>\n<script src="{{static:base.js}}"></script>')


class UI:
    """The pages, with the shared head, header and credits filled in, and the static files."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pages, self.static = {}, {}
        self.load()

    @staticmethod
    def _static_url(name, static):
        item = static.get(name)
        return '/static/' + name + ('?v=' + item[1].strip('"')[:10] if item else '')

    def load(self):
        static = {}
        for folder, dirs, files in os.walk(STATIC_DIR):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
            for filename in sorted(files):
                path = os.path.join(folder, filename)
                name = os.path.relpath(path, STATIC_DIR).replace(os.sep, '/')
                kind = STATIC_TYPES.get(os.path.splitext(name)[1].lower())
                if kind and os.path.isfile(path) and STATIC_NAME.match(name):
                    body, etag = load_file(path, 'the static folder', b'')
                    static[name] = (body, etag, kind)
        # Modules import each other by plain path; the import map adds each file's version
        imports = {'/static/' + name: self._static_url(name, static) for name in static if name.endswith('.js')}
        importmap = '<script type="importmap">' + json.dumps({'imports': imports}, separators=(',', ':')) + '</script>'
        sources, pages = {}, {}
        for key, path, label, filename in PAGES:
            if filename not in sources:
                source = (UI_PATH if filename == 'index.html' else MAP_UI_PATH if filename == 'map.html'
                          else os.path.join(APP_DIR, filename))
                sources[filename] = load_file(source, 'the app folder', MISSING_PAGE)[0].decode('utf-8', 'replace')
            html = sources[filename].replace('<!--#head-->', HEAD_HTML.replace('<!--#importmap-->', importmap))
            html = html.replace('<!--#header-->', header_html(key))
            html = html.replace('<!--#credits-->', CREDITS_HTML).replace('<!--#credits-short-->', CREDITS_SHORT_HTML)
            html = re.sub(r'\{\{static:([\w./-]+)\}\}', lambda m: self._static_url(m.group(1), static), html)
            body = html.encode('utf-8')
            pages[key] = (body, '"' + hashlib.sha1(body).hexdigest()[:16] + '"')
        with self.lock:
            self.static, self.pages = static, pages

    def page(self, key):
        if UI_RELOAD:
            self.load()
        with self.lock:
            return self.pages.get(key)

    def asset(self, name):
        if UI_RELOAD:
            self.load()
        with self.lock:
            return self.static.get(name)


PAGE_PATHS = {path: key for key, path, _, _ in PAGES}
PAGE_PATHS['/index.html'] = 'overview'
STATIC_NAME = re.compile(r'^(?:[a-z0-9][a-z0-9_-]*/)?[A-Za-z0-9][A-Za-z0-9._-]*$')


class Handler(BaseHTTPRequestHandler):
    server_version = 'gb-grid-live/' + VERSION
    protocol_version = 'HTTP/1.1'

    def version_string(self):
        return self.server_version

    def do_GET(self):
        self.route(send_body=True)

    def do_HEAD(self):
        self.route(send_body=False)

    def route(self, send_body):
        parts = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parts.query)
        try:
            if parts.path in PAGE_PATHS:
                body, etag = App.ui.page(PAGE_PATHS[parts.path])
                self.respond(200, body, 'text/html; charset=utf-8', send_body, etag=etag, cache='no-cache')
            elif len(parts.path) > 1 and parts.path.endswith('/') and parts.path.rstrip('/') in PAGE_PATHS:
                self.redirect(parts.path.rstrip('/'), send_body)
            elif parts.path.startswith('/static/'):
                name = parts.path[len('/static/'):]
                item = App.ui.asset(name) if STATIC_NAME.match(name) else None
                if item is None:
                    self.respond(404, b'Not found\n', 'text/plain; charset=utf-8', send_body)
                else:
                    cache = 'public, max-age=31536000, immutable' if 'v=' in parts.query else 'no-cache'
                    self.respond(200, item[0], item[2], send_body, etag=item[1], cache=cache)
            elif parts.path == '/regions.json':
                body, etag = App.regions
                self.respond(200, body, 'application/json; charset=utf-8', send_body, etag=etag, cache='max-age=3600')
            elif parts.path == '/api/longterm':
                def number(name):
                    raw = query.get(name, [''])[0]
                    return int(raw) if raw.isdigit() else None
                self.respond_json(App.grid.longterm(number('from'), number('to')), send_body)
            elif parts.path == '/api/longterm/summary':
                self.respond_json(App.grid.longterm_summary(), send_body)
            elif parts.path == '/api/outlook':
                self.respond_json(App.grid.outlook_payload(), send_body)
            elif parts.path == '/api/outages':
                self.respond_json(App.grid.outages_payload(), send_body)
            elif parts.path == '/api/balancing':
                self.respond_json(App.grid.balancing(), send_body)
            elif parts.path == '/api/regions':
                self.respond_json(App.grid.regions(), send_body)
            elif parts.path == '/api/replay':
                self.respond_json(App.grid.replay(), send_body)
            elif parts.path == '/api/alerts':
                self.respond_json(App.grid.alerts(), send_body)
            elif parts.path == '/geo.json':
                body, etag = App.geo
                self.respond(200, body, 'application/json; charset=utf-8', send_body, etag=etag, cache='max-age=3600')
            elif parts.path == '/api/map':
                self.respond_json(App.grid.map_data(), send_body)
            elif parts.path == '/api/station':
                raw = query.get('id', [''])[0]
                data = App.grid.station_history(int(raw)) if raw.isdigit() else None
                if data is None:
                    self.respond(404, b'Unknown station\n', 'text/plain; charset=utf-8', send_body)
                else:
                    self.respond_json(data, send_body)
            elif parts.path == '/api/live':
                self.respond_json(App.grid.live(), send_body)
            elif parts.path == '/api/history':
                self.respond_json(App.grid.history(query.get('range', ['24h'])[0]), send_body)
            elif parts.path == '/api/status':
                status = App.grid.status_payload(time.time())
                latest = status.get('generation', {}).get('data_time')
                healthy = bool(latest and time.time() - latest < 30 * 60)
                self.respond_json({'healthy': healthy, 'sources': status}, send_body, code=200 if healthy else 503)
            elif parts.path == '/healthz':
                self.respond(200, b'ok\n', 'text/plain; charset=utf-8', send_body, cache='no-store')
            elif parts.path == '/favicon.ico':  # for clients that ignore the page's icon link
                item = App.ui.asset('icon-180.png')
                if item:
                    self.respond(200, item[0], 'image/png', send_body, etag=item[1], cache='max-age=86400')
                else:
                    self.respond(204, b'', 'image/x-icon', send_body, cache='max-age=86400')
            else:
                self.respond(404, b'Not found\n', 'text/plain; charset=utf-8', send_body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception('Error handling %s', self.path)
            try:
                self.respond(500, b'Internal error\n', 'text/plain; charset=utf-8', send_body)
            except Exception:
                pass

    def redirect(self, location, send_body):
        self.send_response(301)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def respond_json(self, payload, send_body, code=200):
        body = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
        self.respond(code, body, 'application/json; charset=utf-8', send_body, etag=etag, cache='no-cache')

    def respond(self, code, body, content_type, send_body, etag=None, cache='no-cache'):
        if etag and code == 200 and self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', cache)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        encoding = None
        if len(body) > 1024 and 'gzip' in (self.headers.get('Accept-Encoding') or ''):
            body = gzip.compress(body, compresslevel=5)
            encoding = 'gzip'
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if encoding:
            self.send_header('Content-Encoding', encoding)
            self.send_header('Vary', 'Accept-Encoding')
        if etag:
            self.send_header('ETag', etag)
        if content_type.startswith('text/html'):
            self.send_header('Content-Security-Policy',
                             "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'")
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        if ACCESS_LOG:
            log.info('%s %s', self.address_string(), fmt % args)


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
    threading.Thread(target=run_backfill, args=(ingest, stop), name='backfill', daemon=True).start()
    threading.Thread(target=run_maintenance, args=(store, stop), name='maintenance', daemon=True).start()

    server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    server.daemon_threads = True

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


if __name__ == '__main__':
    main()
