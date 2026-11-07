#!/usr/bin/env python3
"""Summarise what a running GB Grid instance is getting from the live APIs.

Prints each data feed's health, how many BM units were matched to power
stations, the biggest stations, and the largest units without a known
location (candidates for tools/build_stations.py).

Usage: python3 tools/live_report.py [BASE_URL] [--wait SECONDS]
       BASE_URL defaults to http://127.0.0.1:8080; --wait (default 180) is how
       long to wait for the first station data after a fresh start.
"""
import json
import sys
import time
import urllib.request


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=30) as response:
        return json.load(response)


def gw(mw):
    return '-' if mw is None else '%.2f' % (mw / 1000.0)


def main():
    args = [a for a in sys.argv[1:]]
    wait = 180
    if '--wait' in args:
        index = args.index('--wait')
        wait = int(args[index + 1])
        del args[index:index + 2]
    base = (args[0] if args else 'http://127.0.0.1:8080').rstrip('/')

    deadline = time.time() + wait
    data = None
    while time.time() < deadline:
        try:
            data = get(base, '/api/map')
            if data.get('model') and data['model'].get('basis') == 'measured':
                break
        except Exception as error:  # the server may still be starting
            print('waiting:', error)
        time.sleep(5)
    live = get(base, '/api/live')

    print('== Feeds')
    for key, feed in sorted((live.get('status') or {}).items()):
        state = 'ok' if not feed.get('failures') else 'FAILING (%s)' % feed.get('last_error')
        print('  %-11s %s' % (key, state))

    region = live.get('region')
    if region:
        print('== Region: %s, now %s g/kWh, %d forecast half hours' % (region.get('name'), region.get('value'), len(region.get('forecast') or [])))
        print('  mix:', (region.get('mix') or {}).get('mix'), '| recent:', region.get('recent'))

    if not data or not data.get('model'):
        print('== No station model yet')
        return 0
    model = data['model']
    print('== Station model: basis %s, settlement period %s %s' % (model['basis'], *model['period']))
    print('  units %(units)d, placed %(placed)d, following instructions %(instructed)d' % model['counts'])
    print('  unplaced units: %d | BM unit register: %s units %s' % (
        model['unplaced_count'], model['reference_units'], '(error: %s)' % model['reference_error'] if model['reference_error'] else ''))
    print('  scale factors:', json.dumps(model['scale'], sort_keys=True))

    print('== By type (GW): measured, embedded, on map, unplaced, stations generating')
    for cat in data['categories']:
        print('  %-8s %6s %6s %6s %6s %4d' % (cat['key'], gw(cat['measured']), gw(cat['embedded']), gw(cat['mapped']),
                                             gw(cat['unplaced']), cat['stations']))

    stations = data['stations']
    print('== Biggest stations now (%d placed, %d generating)' % (len(stations), sum(1 for s in stations if s['mw'] >= 1)))
    for st in sorted(stations, key=lambda s: -s['mw'])[:12]:
        print('  %-34s %-8s %7.0f MW of %s' % (st['n'][:34], st['c'], st['mw'], st['cap'] or '?'))

    print('== Largest units without a known location')
    for unit in data['unplaced'][:30]:
        print('  %-14s %-8s %7.1f MW  %s' % (unit['unit'], unit['cat'], unit['mw'], unit['name']))

    try:
        alerts = get(base, '/api/alerts')
        print('== Grid notices in force: %d' % len(alerts.get('active', [])))
        for notice in alerts.get('active', []):
            print('  %s: %s' % (notice['title'], notice['summary']))
    except Exception as error:
        print('== Grid notices unavailable:', error)

    print('== Interconnectors (MW, positive = importing)')
    print('  ' + ', '.join('%s %s' % (link['name'], link['mw']) for link in data['links']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
