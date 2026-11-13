"""SQLite storage."""

import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import log
from .timeutil import uk_day, uk_midnight_utc


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
UPSERT_RESP = ('INSERT INTO resp (start, end, product, mw, price) VALUES (?, ?, ?, ?, ?) '
               'ON CONFLICT (start, product) DO UPDATE SET end = excluded.end, mw = excluded.mw, price = excluded.price '
               'WHERE resp.mw IS NOT excluded.mw OR resp.price IS NOT excluded.price OR resp.end IS NOT excluded.end')
UPSERT_EV = ('INSERT INTO ev (id, t, kind, level, title, detail, data, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?) '
             'ON CONFLICT (id) DO UPDATE SET t = excluded.t, kind = excluded.kind, level = excluded.level, title = excluded.title, '
             'detail = excluded.detail, data = excluded.data, updated = excluded.updated '
             'WHERE ev.title IS NOT excluded.title OR ev.detail IS NOT excluded.detail OR ev.data IS NOT excluded.data '
             'OR ev.level IS NOT excluded.level OR ev.t IS NOT excluded.t')
UPSERT_WARN = ('INSERT INTO warn (t, kind, body) VALUES (?, ?, ?) '
               'ON CONFLICT (t, kind) DO UPDATE SET body = excluded.body WHERE warn.body IS NOT excluded.body')
# Your home: half hours of electricity ('elec') and gas use. Meter readings
# ('meter', from Octopus's consumption API) replace live estimates ('live', from
# the Home Mini) and are never replaced by them. ci is the grid's carbon
# intensity for that half hour, kept here because the ci table is pruned.
UPSERT_HOME = ('INSERT INTO home (fuel, t, kwh, src, ci) VALUES (?, ?, ?, ?, ?) '
               'ON CONFLICT (fuel, t) DO UPDATE SET kwh = excluded.kwh, src = excluded.src, '
               'ci = COALESCE(excluded.ci, home.ci) '
               "WHERE (home.src = 'live' OR excluded.src = 'meter') "
               'AND (home.kwh IS NOT excluded.kwh OR home.src IS NOT excluded.src '
               'OR home.ci IS NOT COALESCE(excluded.ci, home.ci))')
STATION_HISTORY_DAYS = 7
FREQUENCY_DAYS = 8        # 15-second frequency readings (about 46,000 rows)
EVENT_DAYS = 400          # the timeline


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
        # UK days and years for GROUP BY (SQLite's own date functions only know UTC):
        # ukday(t) is the UTC epoch of the UK midnight that starts t's day
        self.db.create_function('ukday', 1, lambda t: None if t is None else uk_midnight_utc(uk_day(t)), deterministic=True)
        self.db.create_function('ukyear', 1, lambda t: None if t is None else uk_day(t).year, deterministic=True)
        self.db.create_function('ukmonth', 1, lambda t: None if t is None else uk_day(t).month, deterministic=True)
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
            CREATE TABLE IF NOT EXISTS resp (start INTEGER NOT NULL, end INTEGER NOT NULL, product TEXT NOT NULL,
                mw REAL, price REAL, PRIMARY KEY (start, product)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS ev (id TEXT PRIMARY KEY, t INTEGER NOT NULL, kind TEXT NOT NULL, level INTEGER NOT NULL,
                title TEXT NOT NULL, detail TEXT, data TEXT, updated INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS ev_t ON ev (t);
            CREATE TABLE IF NOT EXISTS home (fuel TEXT NOT NULL, t INTEGER NOT NULL, kwh REAL NOT NULL,
                src TEXT NOT NULL, ci REAL, PRIMARY KEY (fuel, t)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS home_live (t INTEGER PRIMARY KEY, w REAL NOT NULL) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS bflow (grp TEXT NOT NULL, t INTEGER NOT NULL, flow INTEGER NOT NULL,
                lim INTEGER NOT NULL, PRIMARY KEY (grp, t)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS bday (grp TEXT NOT NULL, d TEXT NOT NULL, peak REAL, mean REAL, near INTEGER,
                over INTEGER, n INTEGER, lim INTEGER, maxflow INTEGER, PRIMARY KEY (grp, d)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS bcost (grp TEXT NOT NULL, d TEXT NOT NULL, cost REAL NOT NULL,
                PRIMARY KEY (grp, d)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS cday (d TEXT PRIMARY KEY, thermal REAL, voltage REAL, inertia REAL, loss REAL,
                thermal_mwh REAL) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS wcurt (d TEXT NOT NULL, unit TEXT NOT NULL, name TEXT, mwh REAL NOT NULL,
                PRIMARY KEY (d, unit)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS wcurt_hh (t INTEGER PRIMARY KEY, mwh REAL NOT NULL) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS tariff_rate (tariff TEXT NOT NULL, kind TEXT NOT NULL, start INTEGER NOT NULL,
                end INTEGER, p REAL NOT NULL, PRIMARY KEY (tariff, kind, start)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS gas_flow (k TEXT NOT NULL, t INTEGER NOT NULL, v REAL NOT NULL, PRIMARY KEY (k, t)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS gas_daily (d TEXT NOT NULL, k TEXT NOT NULL, v REAL NOT NULL, PRIMARY KEY (d, k)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS wx (k TEXT NOT NULL, t INTEGER NOT NULL, v REAL NOT NULL, PRIMARY KEY (k, t)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS ext_ci (zone TEXT NOT NULL, t INTEGER NOT NULL, v REAL NOT NULL, PRIMARY KEY (zone, t)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS sysp (t INTEGER PRIMARY KEY, d TEXT NOT NULL, sp INTEGER NOT NULL, sell REAL, buy REAL, niv REAL) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS bsad (t INTEGER PRIMARY KEY, d TEXT NOT NULL, sp INTEGER NOT NULL, buy REAL, sell REAL,
                buy_mwh REAL, sell_mwh REAL, n INTEGER) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS temp (d TEXT PRIMARY KEY, temp REAL NOT NULL, normal REAL, high REAL, low REAL) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS fwind (t INTEGER PRIMARY KEY, fc INTEGER NOT NULL, cap INTEGER) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS fdemand (t INTEGER PRIMARY KEY, fc INTEGER NOT NULL, act INTEGER NOT NULL) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS fci (t INTEGER PRIMARY KEY, fc INTEGER NOT NULL, act INTEGER NOT NULL) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS fsnap (kind TEXT NOT NULL, t INTEGER NOT NULL, fc REAL NOT NULL, made INTEGER NOT NULL, act REAL,
                PRIMARY KEY (kind, t)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS review (year INTEGER PRIMARY KEY, made INTEGER NOT NULL, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS home_rate (fuel TEXT NOT NULL, kind TEXT NOT NULL, start INTEGER NOT NULL,
                end INTEGER, p REAL NOT NULL, PRIMARY KEY (fuel, kind, start)) WITHOUT ROWID;
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

    def replace(self, delete_sql, delete_params, insert_sql, rows, history=False):
        """Delete and insert in one transaction, so readers never see a gap."""
        with self.lock:
            before = self.db.total_changes
            self.db.execute('BEGIN')
            try:
                self.db.execute(delete_sql, delete_params)
                self.db.executemany(insert_sql, rows)
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
            # NESO publishes inertia some days after the fact, so it is kept longer
            self.db.execute("DELETE FROM hh WHERE t < ? AND k NOT IN ('inertia', 'inertia_mkt')", (cutoff,))
            self.db.execute("DELETE FROM hh WHERE t < ? AND k IN ('inertia', 'inertia_mkt')", (time.time() - 90 * 86400,))
            self.db.execute('DELETE FROM ci WHERE t < ?', (cutoff,))
            self.db.execute('DELETE FROM freq WHERE t < ?', (time.time() - FREQUENCY_DAYS * 86400,))
            self.db.execute('DELETE FROM resp WHERE end < ?', (time.time() - 10 * 86400,))
            self.db.execute('DELETE FROM ev WHERE t < ?', (time.time() - EVENT_DAYS * 86400,))
            self.db.execute('DELETE FROM st WHERE t < ?', (time.time() - STATION_HISTORY_DAYS * 86400,))
            self.db.execute('DELETE FROM warn WHERE t < ?', (time.time() - 400 * 86400,))
            self.db.execute('DELETE FROM reg WHERE t < ?', (time.time() - 3 * 86400,))
            old = (datetime.now(timezone.utc) - timedelta(days=9)).date().isoformat()
            self.db.execute('DELETE FROM bal WHERE d < ?', (old,))
            self.db.execute('DELETE FROM balp WHERE d < ?', (old,))
            removed = self.db.total_changes - before
        if removed:
            log.info('Pruned %d old rows', removed)
