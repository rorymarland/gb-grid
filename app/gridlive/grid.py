"""Grid: builds every API payload from the store and the live models."""

import threading
import time

from .config import HISTORY_DAYS, REGION_ID
from .reference import CARBON_FACTORS, FUELS, IMPORT_FACTORS, INTERCONNECTORS
from .timeutil import floor_to
from .views_live import LiveViews
from .views_map import MapViews
from .views_system import SystemViews
from .views_longterm import LongtermViews
from .views_summary import SummaryViews
from .views_events import EventViews
from .views_sandbox import SandboxViews


# --------------------------------------------------------------------------
# Derived views for the API
# --------------------------------------------------------------------------


class Grid(LiveViews, MapViews, SystemViews, LongtermViews, SummaryViews, EventViews, SandboxViews):
    """Builds the /api/live and /api/history payloads from the store."""

    def __init__(self, store, status, fleet=None, units=None, balancing=None, outages=None):
        self.store = store
        self.outages = outages
        self.outlook = None
        self.home = None
        self.constraints = None
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
