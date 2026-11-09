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
    # The newer feeds take a few minutes to fill on a fresh start
    while time.time() < deadline:
        try:
            ready = (any(get(base, '/api/regions').get('ci', {}).values())
                     and get(base, '/api/balancing')['today']['periods'] > 0
                     and get(base, '/api/outages').get('checked')
                     and (get(base, '/api/longterm/summary').get('extent') or {}).get('rows'))
            if ready:
                break
        except Exception as error:
            print('waiting:', error)
        time.sleep(10)
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

    try:
        reg = get(base, '/api/regions')
        i = reg.get('now')
        row = lambda key: ', '.join('%s %s' % (reg['names'][rid], reg[key][rid][i]) for rid in sorted(reg[key], key=int) if i is not None and reg[key][rid][i] is not None)
        print('== Regions now (g/kWh):', row('ci'))
        print('== Regional solar (MW, latest):', ', '.join('%s %s' % (reg['names'][rid], next((v for v in reversed(vals[:i + 1]) if v is not None), None)) for rid, vals in sorted(reg['solar'].items(), key=lambda kv: int(kv[0]))) if i is not None else '-')
    except Exception as error:
        print('== Regions unavailable:', error)

    try:
        bal = get(base, '/api/balancing')
        now = bal['now']
        print('== Balancing now: down %s MW, up %s MW, %d units, cost/hour %s (%d priced)' % (now['down'], now['up'], len(now['units']), now['cost_hour'], now['priced']))
        for u in now['units'][:8]:
            print('    %-28s %-8s %8.1f MW at %s' % (u['name'][:28], u['cat'], u['delta'], u['price']))
        print('  today:', bal['today'])
        print('  top:', ', '.join('%s %s' % (u['name'], u['paid']) for u in bal['top'][:6]))
        print('  constraint days:', len(bal['constraints']), bal['constraints'][-1] if bal['constraints'] else '')
    except Exception as error:
        print('== Balancing unavailable:', error)

    try:
        o = get(base, '/api/outages')
        m = o.get('margin') or {}
        print('== Margin: now %s MW, lowest %s MW, %s' % (m.get('margin'), m.get('min'), m.get('label')))
        print('== Offline: %s MW in %d groups' % (o['lost_total'], o['offline_count']))
        for item in o['offline'][:12]:
            print('    %-30s %-8s %6s MW of %s, %s, back %s, %s' % (item['name'][:30], item['cat'], item['lost'], item['normal'], 'planned' if item['planned'] else 'unplanned', item['end'], item['cause'][:40]))
        print('  nuclear:', ', '.join('%s %s/%s out %s' % (n['name'], n['mw'], n['capacity'], n['out']) for n in o['nuclear']))
        print('  upcoming:', len(o['upcoming']), '| notices:', len(o['notices']))
    except Exception as error:
        print('== Outages unavailable:', error)

    try:
        lt = get(base, '/api/longterm/summary')
        e = lt.get('extent') or {}
        fmt = lambda t: time.strftime('%Y-%m-%d %H:%M', time.gmtime(t)) if t else None
        print('== Long-term history: %s rows, %s to %s (%s%%, complete %s); prices %s to %s' % (e.get('rows'), fmt(e.get('first')), fmt(e.get('last')), round(100 * (e.get('progress') or 0)), e.get('complete'), fmt(e.get('price_first')), fmt(e.get('price_last'))))
        for r in lt.get('records', []):
            print('    %-28s %10s %-6s %s%s' % (r['title'], r['value'], r['unit'], fmt(r['t']), ' (recent)' if r['recent'] else ''))
        print('  last coal:', fmt(lt.get('last_coal')))
        c = lt.get('compare') or {}
        for k in ('now', 'then'):
            if c.get(k):
                print('  %s: %s ci %s price %s wind %.2f gas %.2f' % (k, fmt(c[k]['t']), c[k]['ci'], c[k]['price'], c[k]['mix']['wind'], c[k]['mix']['gas']))
        print('  years:', ', '.join('%s %s' % (y['year'], y['ci']) for y in lt.get('years', [])))
        recent = get(base, '/api/longterm?from=%d' % (e['last'] - 7 * 86400)) if e.get('last') else {}
        print('  last week: bucket %s, %s points, price points %s' % (recent.get('bucket'), len(recent.get('t', [])), sum(1 for p in recent.get('price', []) if p is not None)))
    except Exception as error:
        print('== Long-term history unavailable:', error)

    try:
        ol = get(base, '/api/outlook')
        print('== Outlook: ' + ', '.join('%s %d points (%s..%s)' % (k, len(ol[k]), ol[k][0][1] if ol[k] else '-', max(p[1] for p in ol[k]) if ol[k] else '-') for k in ('demand', 'wind', 'solar', 'carbon')) + ', raw %s' % ol.get('raw'))
    except Exception as error:
        print('== Outlook unavailable:', error)

    def words(parts):
        return ''.join(p if isinstance(p, str) else p['t'] for p in parts)

    for day in ('today', 'yesterday'):
        try:
            sm = get(base, '/api/summary?day=' + day)
            print('== Summary, %s (%s, ready %s): %s' % (day, sm.get('date'), sm.get('ready'), words(sm.get('headline', []))))
            for point in sm.get('points', []):
                print('    - %s' % words(point['parts']))
        except Exception as error:
            print('== Summary %s unavailable: %s' % (day, error))

    try:
        fq = get(base, '/api/frequency?range=24h')
        t = fq.get('today') or {}
        print('== Frequency: latest %s, %d readings in 24 h, sd %s mHz, low %s, high %s' % (
            fq.get('latest'), len(fq['series']['t']), t.get('sd_mhz'), t.get('min'), t.get('max')))
        print('  inertia:', {k: v for k, v in (fq.get('inertia') or {}).items() if k != 'series'})
        print('  response held:', fq['response'])
        for e in fq.get('events', [])[:6]:
            print('  event %s: %s | %s' % (e['id'], words(e['title']), words(e['detail'])))
    except Exception as error:
        print('== Frequency unavailable:', error)

    try:
        ev = get(base, '/api/events?days=7')
        print('== Timeline: %s' % ev['counts'])
        for e in ev['events'][:15]:
            print('  %s %-9s L%d %s | %s' % (e['t'], e['kind'], e['level'], words(e['title']), [l['href'] for l in e['links']]))
    except Exception as error:
        print('== Timeline unavailable:', error)

    print('== Interconnectors (MW, positive = importing)')
    print('  ' + ', '.join('%s %s' % (link['name'], link['mw']) for link in data['links']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
