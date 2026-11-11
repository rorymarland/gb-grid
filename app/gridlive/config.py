"""Settings from the environment, data source addresses and the logger."""

import logging
import os
import sys


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


def env_float(name, default, lo, hi):
    raw = os.environ.get(name, '').strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        print('Ignoring invalid ' + name + '=' + repr(raw), file=sys.stderr)
        value = default
    return min(hi, max(lo, value))


def env_secret(name):
    """A secret from NAME, or from the file NAME_FILE points at (a Docker secret)."""
    path = env_str(name + '_FILE', '')
    if path:
        try:
            with open(path, encoding='utf-8') as handle:
                return handle.read().strip()
        except OSError as error:
            print('Could not read ' + name + '_FILE (' + type(error).__name__ + ')', file=sys.stderr)
            return ''
    return env_str(name, '')


# Your home: electricity and gas use from an Octopus Energy smart meter (see README).
# The key is a secret: set it in your .env file or as a Docker secret, never in the compose file.
OCTOPUS_API_KEY = env_secret('OCTOPUS_API_KEY')
OCTOPUS_ACCOUNT = env_str('OCTOPUS_ACCOUNT', '').upper()
OCTOPUS_BASE = env_str('OCTOPUS_BASE_URL', 'https://api.octopus.energy').rstrip('/')
OCTOPUS_LIVE_SECONDS = env_int('OCTOPUS_LIVE_SECONDS', 60, 30, 600)   # live readings while the page is open
HOME_ENABLED = bool(OCTOPUS_API_KEY and OCTOPUS_ACCOUNT)
GAS_CALORIFIC_VALUE = env_float('GAS_CALORIFIC_VALUE', 39.5, 30, 45)     # MJ/m³, for meters that report gas in m³
GAS_KG_PER_KWH = env_float('GAS_KG_CO2_PER_KWH', 0.183, 0.1, 0.3)       # UK government factor for natural gas
PORT = env_int('GRID_INTERNAL_PORT', 8080, 1, 65535)
DATA_DIR = env_str('DATA_DIR', '/data')
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_PATH = env_str('UI_PATH', os.path.join(APP_DIR, 'index.html'))
MAP_UI_PATH = env_str('MAP_UI_PATH', os.path.join(APP_DIR, 'map.html'))
GEO_PATH = env_str('GEO_PATH', os.path.join(APP_DIR, 'geo.json'))
STATIONS_PATH = env_str('STATIONS_PATH', os.path.join(APP_DIR, 'stations.csv'))
WIKIDATA_PATH = env_str('STATION_WIKIDATA_PATH', os.path.join(APP_DIR, 'station_wikidata.csv'))
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
    'response': env_int('POLL_RESPONSE_SECONDS', 3600, 600, 86400),
    'events': env_int('POLL_EVENTS_SECONDS', 120, 30, 3600),
    'home': OCTOPUS_LIVE_SECONDS,
}
LONG_HISTORY = env_str('LONG_HISTORY', 'true').lower() not in ('0', 'false', 'no', 'off')
REGION_ID = env_int('CARBON_REGION_ID', 0, 0, 17)  # 0 disables the regional card
LOG_LEVEL = env_str('LOG_LEVEL', 'INFO').upper()
ACCESS_LOG = env_str('ACCESS_LOG', 'false').lower() in ('1', 'true', 'yes', 'on')
# Station photos from Wikimedia Commons, fetched by the server and kept in DATA_DIR/photos
PHOTOS = env_str('STATION_PHOTOS', 'true').lower() not in ('0', 'false', 'no', 'off')
# Notifications: push new timeline events to ntfy and/or a webhook (see README)
NOTIFY_NTFY_URL = env_str('NOTIFY_NTFY_URL', '').strip()          # a topic address, e.g. https://ntfy.sh/my-topic
NOTIFY_NTFY_TOKEN = env_str('NOTIFY_NTFY_TOKEN', '').strip()      # access token, if the topic needs one
NOTIFY_WEBHOOK_URL = env_str('NOTIFY_WEBHOOK_URL', '').strip()    # receives a JSON POST per event
NOTIFY_LEVEL = env_str('NOTIFY_LEVEL', 'major').strip().lower()   # major, notable or all
NOTIFY_KINDS = [k.strip().lower() for k in env_str('NOTIFY_KINDS', '').split(',') if k.strip()]   # empty: every kind
NOTIFY_QUIET_HOURS = env_str('NOTIFY_QUIET_HOURS', '').strip()    # e.g. 23:00-07:00 (UK time); nothing is sent then
PUBLIC_URL = env_str('PUBLIC_URL', '').strip().rstrip('/')        # this site's address, for links in notifications
UI_RELOAD = env_str('UI_RELOAD', 'false').lower() in ('1', 'true', 'yes', 'on')  # development: reread pages per request
STATIC_DIR = os.path.join(APP_DIR, 'static')
REGIONS_PATH = os.path.join(APP_DIR, 'regions.json')

ELEXON = env_str('ELEXON_BASE_URL', 'https://data.elexon.co.uk/bmrs/api/v1').rstrip('/')
CARBON = env_str('CARBON_BASE_URL', 'https://api.carbonintensity.org.uk').rstrip('/')
PVLIVE = env_str('PVLIVE_BASE_URL', 'https://api.pvlive.uk/pvlive/api/v4').rstrip('/')
NESO = env_str('NESO_BASE_URL', 'https://api.neso.energy').rstrip('/')
COMMONS = env_str('COMMONS_BASE_URL', 'https://commons.wikimedia.org').rstrip('/')

# NESO "Embedded Wind and Solar Forecasts" (live resource, updated hourly)
EMBEDDED_RESOURCE_ID = 'db6c038f-98af-4570-ab60-24d71ebd0ae5'
# NESO "Demand Data Update" (daily; includes embedded wind/solar history)
DDU_PATH = ('/dataset/7a12172a-939c-404c-b581-a6128b74f588/resource/'
            '177f6fa4-ae49-4182-81ea-0c6b35f26ca6/download/demanddataupdate.csv')

USER_AGENT = 'gb-grid-live/' + VERSION + ' (self-hosted dashboard; https://github.com/rorymarland/gb-grid) python-urllib'
