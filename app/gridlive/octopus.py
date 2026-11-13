"""The Octopus Energy API: its two GraphQL endpoints and its REST API.

Used by Your home (home.py). The API key stays here: it's only sent to
OCTOPUS_BASE_URL (and never with public requests such as tariff prices), and
the HTTP client drops it on any redirect to another host.
"""

import base64
import json
import re
import urllib.parse
from datetime import datetime, timezone

from . import fetch
from .config import GAS_CALORIFIC_VALUE, OCTOPUS_ACCOUNT, log
from .fetch import FetchError
from .timeutil import parse_time

MAX_PAGES = 30
TOKEN_MARGIN = 300            # renew the token this long before it expires

KEY_RE = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
ACCOUNT_RE = re.compile(r'^A-[A-Z0-9]{4,16}$')
DEVICE_RE = re.compile(r'^[A-Za-z0-9-]{4,64}$')

ACCOUNT_QUERY = '''query {
  account(accountNumber: %s) {
    electricityAgreements(active: true) {
      meterPoint {
        mpan
        direction
        meters(includeInactive: false) {
          serialNumber
          activeFrom
          smartImportElectricityMeter { deviceId }
        }
        agreements(includeInactive: true) {
          validFrom
          validTo
          tariff { ... on TariffType { productCode tariffCode } }
        }
      }
    }
    gasAgreements(active: true) {
      meterPoint {
        mprn
        meters(includeInactive: false) {
          serialNumber
          activeFrom
          consumptionUnits
        }
        agreements(includeInactive: true) {
          validFrom
          validTo
          tariff { tariffCode productCode }
        }
      }
    }
  }
}'''
SESSIONS_QUERY = '''query {
  savingSessions {
    events(includeDev: false) {
      id
      code
      rewardPerKwhInOctoPoints
      startAt
      endAt
      eventType
      targetRegion { regionId }
    }
    account(accountNumber: %s) {
      hasJoinedCampaign
      canJoinCampaign
      signedUpMeterPoint { regionId }
      joinedEvents {
        eventId
        startAt
        endAt
        rewardGivenInOctoPoints
        eventType
      }
    }
  }
}'''
POINTS_QUERY = 'query { loyaltyPointLedgers { balanceCarriedForward } }'
TELEMETRY = '''%s: smartMeterTelemetry(deviceId: %s, grouping: %s, start: %s, end: %s) {
    readAt
    consumptionDelta
    demand
  }'''


class OctopusError(FetchError):
    pass


def gql_time(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+00:00')


def as_number(value):
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def single_rate(tariff):
    """True for single-rate tariffs (E-1R-..., G-1R-...); day/night and multi-rate tariffs aren't costed."""
    parts = str(tariff or '').split('-')
    return len(parts) > 2 and parts[1] == '1R'


def gas_kwh(value, units):
    """Gas meters report kWh (SMETS1) or cubic metres (SMETS2)."""
    if str(units or '').strip().lower().startswith('m'):
        return value * 1.02264 * GAS_CALORIFIC_VALUE / 3.6
    return value


def parse_agreements(point):
    out = []
    for agreement in point.get('agreements') or []:
        tariff = agreement.get('tariff') or {}
        code, product = tariff.get('tariffCode'), tariff.get('productCode')
        if not code or not product:
            continue
        try:
            start = parse_time(agreement['validFrom']) if agreement.get('validFrom') else 0
            end = parse_time(agreement['validTo']) if agreement.get('validTo') else None
        except (TypeError, ValueError):
            continue
        out.append({'start': start, 'end': end, 'product': product, 'tariff': code})
    out.sort(key=lambda a: a['start'])
    return out


def parse_account(data):
    """The import electricity meter and the gas meter, from the account query."""
    account = (data or {}).get('account')
    if not account:
        raise OctopusError('Octopus has no account ' + OCTOPUS_ACCOUNT + ' for this API key. Check OCTOPUS_ACCOUNT.')
    meters = {}
    for agreement in account.get('electricityAgreements') or []:
        point = agreement.get('meterPoint') or {}
        if str(point.get('direction') or '').upper() == 'EXPORT' or not point.get('mpan'):
            continue
        listed = [m for m in point.get('meters') or [] if m.get('serialNumber')]
        if not listed:
            continue
        # The active smart meter: one with a device ID first, then the most recently fitted
        listed.sort(key=lambda m: (bool((m.get('smartImportElectricityMeter') or {}).get('deviceId')),
                                   m.get('activeFrom') or ''), reverse=True)
        meter = listed[0]
        device = (meter.get('smartImportElectricityMeter') or {}).get('deviceId')
        found = {'mpan': str(point['mpan']), 'serial': str(meter['serialNumber']),
                 'device': device if device and DEVICE_RE.match(str(device)) else None,
                 'agreements': parse_agreements(point)}
        if 'elec' not in meters or (found['device'] and not meters['elec']['device']):
            meters['elec'] = found
    for agreement in account.get('gasAgreements') or []:
        point = agreement.get('meterPoint') or {}
        listed = [m for m in point.get('meters') or [] if m.get('serialNumber')]
        if not point.get('mprn') or not listed:
            continue
        listed.sort(key=lambda m: m.get('activeFrom') or '', reverse=True)
        meters.setdefault('gas', {'mprn': str(point['mprn']), 'serial': str(listed[0]['serialNumber']),
                                  'units': listed[0].get('consumptionUnits') or 'kWh',
                                  'agreements': parse_agreements(point)})
    if 'elec' not in meters and 'gas' not in meters:
        raise OctopusError('Octopus lists no electricity or gas meter on account ' + OCTOPUS_ACCOUNT + '.')
    return meters


def default_request(url, headers=None, data=None):
    """(status, body bytes). Never follows a redirect to another host with the key."""
    status, body, _ = fetch.http_get(url, extra_headers=headers, data=data, timeout=40)
    return status, body


class OctopusQueryError(Exception):
    """The extensions of a GraphQL error, attached as the cause of an OctopusError."""

    def __init__(self, ext):
        super().__init__(ext.get('errorCode'))
        self.code = ext.get('errorCode')
        kind = str(ext.get('errorType') or '').upper()
        description = str(ext.get('errorDescription') or '').lower()
        self.auth = kind in ('AUTHORIZATION', 'AUTHENTICATION') or 'token' in description or 'expired' in description


class OctopusApi:
    """Requests to Octopus, as a mixin for Home: self.key, self.account, self.base,
    self.backend and self.request are set by Home.__init__."""

    def _auth_basic(self):
        return 'Basic ' + base64.b64encode((self.key + ':').encode('ascii')).decode('ascii')

    def _json(self, url, headers=None, data=None):
        _, body = self.request(url, headers, data)
        try:
            return json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise OctopusError('Invalid JSON from Octopus')

    def _graphql(self, query, token=None, url=None):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = token
        data = self._json(url or self.base + '/v1/graphql/', headers, json.dumps({'query': query}).encode('utf-8'))
        errors = data.get('errors') if isinstance(data, dict) else None
        if errors:
            first = errors[0] if isinstance(errors[0], dict) else {}
            ext = first.get('extensions') or {}
            cause = OctopusQueryError(ext) if ext else None
            raise OctopusError('Octopus: ' + str(ext.get('errorDescription') or first.get('message') or 'error')
                               + (' (' + str(ext['errorCode']) + ')' if ext.get('errorCode') else '')) from cause
        return (data or {}).get('data') or {}

    def _get_token(self, now):
        if self.token and now < self.token_expires - TOKEN_MARGIN:
            return self.token
        if not KEY_RE.match(self.key or ''):
            raise OctopusError("OCTOPUS_API_KEY doesn't look like an Octopus API key (it starts sk_live_).")
        try:
            data = self._graphql('mutation { obtainKrakenToken(input: { APIKey: %s }) { token } }' % json.dumps(self.key))
        except OctopusError as error:
            if 'KT-CT-1139' in str(error) or 'Authentication failed' in str(error):
                raise OctopusError("Octopus didn't accept the API key. Check OCTOPUS_API_KEY "
                                   '(Octopus account, Personal details, API access).') from None
            raise
        token = ((data.get('obtainKrakenToken') or {}).get('token') or '').strip()
        if not token:
            raise OctopusError('Octopus returned no token for the API key.')
        expires = now + 3000
        try:
            payload = token.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4)))
            expires = float(claims.get('exp') or expires)
        except (IndexError, ValueError, TypeError):
            pass
        self.token, self.token_expires = token, expires
        return token

    def _query(self, query, now, prefix='', url=None):
        """An authorised GraphQL query; a rejected token is renewed and the query tried once more."""
        for attempt in (1, 2):
            token = self._get_token(now)
            try:
                return self._graphql(query, prefix + token, url)
            except OctopusError as error:
                cause = error.__cause__
                if attempt == 1 and isinstance(cause, OctopusQueryError) and cause.auth:
                    self.token = None
                    continue
                raise

    def _rest(self, path, params, pages=MAX_PAGES, auth=True):
        """Every page of a REST list. Only the page number is taken from `next`,
        so the key is only ever sent to OCTOPUS_BASE_URL."""
        results, page = [], 1
        for _ in range(pages):
            query = dict(params, **({'page': page} if page > 1 else {}))
            data = self._json(self.base + path + '?' + urllib.parse.urlencode(query),
                              {'Authorization': self._auth_basic()} if auth else None)
            if not isinstance(data, dict):
                raise OctopusError('Unexpected response from Octopus')
            results.extend(r for r in data.get('results') or [] if isinstance(r, dict))
            following = data.get('next')
            if not following or pages == 1:
                return results
            try:
                page = int(urllib.parse.parse_qs(urllib.parse.urlsplit(following).query)['page'][0])
            except (KeyError, ValueError, IndexError):
                return results
        log.warning('[home] stopped after %d pages of %s', MAX_PAGES, path.split('/')[2])
        return results

    # ---- the steps ---------------------------------------------------------
