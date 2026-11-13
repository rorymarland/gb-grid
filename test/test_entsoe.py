#!/usr/bin/env python3
"""Tests for the neighbours' generation mix from ENTSO-E: the parser and carbon intensity by hand,
then the mock (run: python3 test/test_entsoe.py)."""
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(HERE, '..', 'app')
sys.path.insert(0, APP)
sys.path.insert(0, HERE)

import server  # noqa: E402
sys.argv = [sys.argv[0], '0', APP]
import mock_upstream as mock  # noqa: E402
from gridlive import entsoe as en  # noqa: E402
from gridlive import neighbours as nb  # noqa: E402
from gridlive.reference import ENTSOE_FACTORS  # noqa: E402

failures = 0


def check(name, condition, detail=''):
    global failures
    if not condition:
        failures += 1
        print('FAIL', name, detail)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


NS = 'urn:iec62325.351:tc57wg16:451-6:generationloaddocument:3:0'


def series(psr, start, end, resolution, points, out=False, curve='A01'):
    zone = '<outBiddingZone_Domain.mRID codingScheme="A01">Z</outBiddingZone_Domain.mRID>' if out else \
        '<inBiddingZone_Domain.mRID codingScheme="A01">Z</inBiddingZone_Domain.mRID>'
    return ('<TimeSeries>%s<curveType>%s</curveType><MktPSRType><psrType>%s</psrType></MktPSRType><Period><timeInterval><start>%s</start>'
            '<end>%s</end></timeInterval><resolution>%s</resolution>%s</Period></TimeSeries>') % (
        zone, curve, psr, start, end, resolution, ''.join('<Point><position>%d</position><quantity>%s</quantity></Point>' % p for p in points))


def doc(*parts):
    return ('<?xml version="1.0" encoding="UTF-8"?><GL_MarketDocument xmlns="%s"><type>A75</type>%s</GL_MarketDocument>' % (NS, ''.join(parts))).encode()


T0 = 1794441600          # 2026-11-12 00:00 UTC
# ---- the parser, by hand ----------------------------------------------------------------
body = doc(
    series('B14', '2026-11-12T00:00Z', '2026-11-12T01:00Z', 'PT15M', [(1, 1000), (2, 1000), (3, 1200), (4, 1200)]),
    # A03: positions 2 and 4 left out, so they repeat 1 and 3
    series('B04', '2026-11-12T00:00Z', '2026-11-12T01:00Z', 'PT15M', [(1, 500), (3, 700)], curve='A03'),
    # Pumping at pumped storage is consumption: left out
    series('B10', '2026-11-12T00:00Z', '2026-11-12T01:00Z', 'PT15M', [(1, 900), (2, 900), (3, 900), (4, 900)], out=True),
    # An hourly series, and the same type split over two series adds up
    series('B19', '2026-11-12T00:00Z', '2026-11-12T01:00Z', 'PT60M', [(1, 300)]),
    series('B19', '2026-11-12T00:00Z', '2026-11-12T00:30Z', 'PT30M', [(1, 50)]))
g = en.parse_generation(body)
check('parse: periods', sorted(g) == [T0, T0 + 900, T0 + 1800, T0 + 2700], sorted(g))
check('parse: the first quarter hour', g[T0] == {'B14': 1000, 'B04': 500, 'B19': 350}, g[T0])
check('parse: A03 repeats the one before', g[T0 + 900]['B04'] == 500 and g[T0 + 2700]['B04'] == 700, g)
check('parse: consumption left out', all('B10' not in mix for mix in g.values()))
check('parse: hourly only on the hour', g[T0 + 900].get('B19') is None)
ack = b'<?xml version="1.0"?><Acknowledgement_MarketDocument xmlns="urn:x"><Reason><code>999</code><text>No matching data found</text></Reason></Acknowledgement_MarketDocument>'
check('parse: no data is nothing', en.parse_generation(ack) == {})
try:
    en.parse_generation(ack.replace(b'999', b'401').replace(b'No matching data found', b'Invalid token'))
    check('parse: other acknowledgements are errors', False)
except server.FetchError as error:
    check('parse: says why', 'Invalid token' in str(error))
try:
    en.parse_generation(b'<html>busy</html')
    check('parse: not XML is an error', False)
except server.FetchError:
    pass

# Carbon intensity on NESO's factors: 1,000 MW nuclear, 500 MW gas at 394 and 350 MW wind
check('intensity: by hand', close(en.intensity({'B14': 1000, 'B04': 500, 'B19': 350}), 500 * 394 / 1850))
check('intensity: coal, biomass and waste', close(en.intensity({'B05': 100, 'B01': 100, 'B17': 100, 'B16': 100}), (937 + 120 + 300) / 4))
check('intensity: an unknown type counts as other', close(en.intensity({'B99': 100, 'B14': 100}), 150.0))
check('intensity: nothing generating', en.intensity({'B04': 0}) is None)
check('factors: every production type', sorted(ENTSOE_FACTORS) == ['B%02d' % i for i in range(1, 21)] + ['B25'])
# The latest period with only one of three types in so far is left out
rows = en.complete_intensities({T0: {'B14': 1000, 'B04': 500, 'B19': 350}, T0 + 900: {'B04': 500}})
check('complete: a period missing most types is skipped', rows == [(T0, round(500 * 394 / 1850, 1))], rows)

# Each country's figure now: RTE first, then ENTSO-E, then NESO's typical one
store = server.Store(tempfile.mkdtemp())
now = T0 + 10 * 3600
store.write('INSERT INTO ext_ci (zone, t, v) VALUES (?, ?, ?)', [('FR', now - 3 * 3600, 30), ('E:FR', now - 3 * 3600, 55),
                                                                ('E:NL', now - 3.5 * 3600, 330), ('E:BE', now - 5 * 3600, 150)], history=False)
c = nb.country_carbon(store, now)
check('now: RTE too old, so ENTSO-E for France', c['France']['source'] == 'ENTSO-E' and c['France']['v'] == 55, c['France'])
check('now: ENTSO-E for the Netherlands within four hours', c['Netherlands'] == {'v': 330, 'live': True, 't': now - 3.5 * 3600, 'source': 'ENTSO-E', 'what': 'Dutch generation'}, c['Netherlands'])
check('now: too old for Belgium, so the typical figure', c['Belgium'] == {'v': 179, 'live': False, 'source': 'NESO'}, c['Belgium'])

# ---- from the mock ---------------------------------------------------------------------
httpd = ThreadingHTTPServer(('127.0.0.1', 0), mock.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
en.ENTSOE = 'http://127.0.0.1:%d/entsoe/api' % httpd.server_address[1]
en.SPACING = 0
live = server.Store(tempfile.mkdtemp())
job = en.Entsoe(live, key=mock.ENTSOE_TOKEN)
mock.ENTSOE_ASKED.clear()
stored = job.poll()
check('poll: one request a zone', len(mock.ENTSOE_ASKED) == len(en.ZONES), len(mock.ENTSOE_ASKED))
check('poll: a week of each', all(live.scalar('SELECT COUNT(*) FROM ext_ci WHERE zone = ?', (z,)) >= 7 * 24 - 4 for z, _ in en.ZONES.values()))
t = live.scalar("SELECT MAX(t) FROM ext_ci WHERE zone = 'E:NL'")
mix = mock.entsoe_mix('10YNL----------L', t)
check('poll: the Netherlands as the mock mixes it', close(live.scalar("SELECT v FROM ext_ci WHERE zone = 'E:NL' AND t = ?", (t,)), round(en.intensity(mix), 1)))
check('poll: the half-reported latest period left out', t <= time.time() - mock.ENTSOE_LAG - 900 + 1, (t, time.time()))
check('poll: Norway near zero', live.scalar("SELECT MAX(v) FROM ext_ci WHERE zone = 'E:NO2'") < 5)
mock.ENTSOE_ASKED.clear()
job.poll()
starts = [q['periodStart'] for q in mock.ENTSOE_ASKED]
check('poll: then only the last few hours', all(time.time() - en.parse_time(s[:8] + 'T' + s[8:10] + ':' + s[10:] + 'Z') < 8 * 3600 for s in starts), starts)
check('poll: the token goes in the query, as ENTSO-E asks', all(q['securityToken'] == mock.ENTSOE_TOKEN for q in mock.ENTSOE_ASKED))

# A wrong token: one request, a clear message, and the token nowhere in it
bad = en.Entsoe(server.Store(tempfile.mkdtemp()), key='not-the-token-123')
mock.ENTSOE_ASKED.clear()
try:
    bad.poll()
    check('token: refused is reported', False)
except server.FetchError as error:
    check('token: says so', 'ENTSOE_API_KEY' in str(error) and 'not-the-token-123' not in str(error), str(error))
check('token: stops after the first refusal', len(mock.ENTSOE_ASKED) == 1, len(mock.ENTSOE_ASKED))
check('off without a key', en.Entsoe(server.Store(tempfile.mkdtemp()), key='').poll() is None)

# Nothing yet in the window: ENTSO-E's acknowledgement, not an error
check('fetch: no data yet', job.fetch_zone('10YFR-RTE------C', time.time() - 600, time.time()) == {})

w = nb.week(live, time.time())
check('week: every neighbour from ENTSO-E', [c['country'] for c in w['countries']] == ['France', 'Norway', 'Netherlands', 'Belgium', 'Denmark', 'Ireland'], [c['country'] for c in w['countries']])

httpd.shutdown()
print('entsoe tests:', 'all passed' if not failures else '%d failed' % failures)
sys.exit(1 if failures else 0)
