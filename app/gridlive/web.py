"""The HTTP server: pages, static files and the JSON API."""

import gzip
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import ACCESS_LOG, APP_DIR, MAP_UI_PATH, STATIC_DIR, UI_PATH, UI_RELOAD, VERSION, log


# --------------------------------------------------------------------------
# HTTP server
# --------------------------------------------------------------------------

class App:
    store = None
    grid = None
    ui = None
    geo = (b'', '')
    regions = (b'', '')
    photos = None


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
    ('frequency', '/frequency', 'Frequency', 'frequency.html'),
    ('balancing', '/balancing', 'Balancing', 'balancing.html'),
    ('outages', '/outages', 'Outages', 'outages.html'),
    ('timeline', '/timeline', 'Timeline', 'timeline.html'),
    ('history', '/history', 'History', 'history.html'),
    ('sandbox', '/sandbox', 'What if?', 'sandbox.html'),
    ('glossary', '/glossary', 'Glossary', 'glossary.html'),
    ('station', '/station', 'Station', 'station.html'),  # one station: reached from the map, Stations and search
    ('display', '/display', 'Display', 'display.html'),  # kiosk view: a header button, not in the menus
]
# The pages in the menus: groups in order, each page's group and a one-line description
NAV_GROUPS = [('now', 'Now'), ('system', 'Running the grid'), ('time', 'Over time'), ('explore', 'Explore')]
PAGE_META = {
    'overview': ('now', 'What Britain is using now, and the next 48 hours'),
    'map': ('now', 'Every power station and interconnector, live'),
    'stations': ('now', 'Output by fuel, nation and station, in tables'),
    'regions': ('now', 'Carbon intensity across the 14 regions'),
    'frequency': ('system', 'Live frequency, inertia and the response held'),
    'balancing': ('system', 'Who NESO is paying to turn up or down'),
    'outages': ('system', 'Spare capacity, offline units and grid notices'),
    'timeline': ('time', 'Notable moments, kept for a year'),
    'history': ('time', 'The mix, carbon and price since 2009'),
    'sandbox': ('explore', 'Trip a power station and see what happens'),
    'glossary': ('explore', 'Every term the dashboard uses, in plain English'),
}
STATIC_TYPES = {
    '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.svg': 'image/svg+xml',
    '.png': 'image/png', '.woff2': 'font/woff2', '.json': 'application/json; charset=utf-8', '.webmanifest': 'application/manifest+json',
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
    '<link rel="preload" href="/static/fonts/hanken-grotesk.woff2" as="font" type="font/woff2" crossorigin>\n'
    # Light unless the viewer chose dark or their device's setting; set before the page paints
    "<script>(function(){var t='light';try{t=localStorage.getItem('gb-theme')||'light'}catch(e){}"
    "var m=location.search.match(/[?&]theme=(light|dark|system|auto)/);if(m)t=m[1];"
    "var d=t==='dark'||((t==='system'||t==='auto')&&window.matchMedia&&matchMedia('(prefers-color-scheme: dark)').matches);"
    "document.documentElement.setAttribute('data-theme',d?'dark':'light')})()</script>")
ELEXON_CREDIT = ('Contains BMRS data © Elexon Limited copyright and database right <span class="year">2026</span> '
                 '(<a href="https://www.elexon.co.uk/data/balancing-mechanism-reporting-agent/copyright-licence-bmrs-data/" '
                 'rel="noopener">licence</a>).')
NESO_CREDIT = ('Supported by National Energy SO Open Data '
               '(<a href="https://www.neso.energy/data-portal/neso-open-licence" rel="noopener">licence</a>).')
CREDITS_HTML = (
    '<p class="credit">' + ELEXON_CREDIT + ' ' + NESO_CREDIT + ' Carbon intensity from the '
    '<a href="https://carbonintensity.org.uk/" rel="noopener">NESO Carbon Intensity API</a>; embedded wind and solar '
    'region boundaries, constraint costs, inertia, frequency response results and the generation mix since 2009 from the <a href="https://www.neso.energy/data-portal" rel="noopener">NESO Data Portal</a>. Solar estimates from '
    '<a href="https://www.solar.sheffield.ac.uk/pvlive/" rel="noopener">Sheffield Solar PV_Live</a>. Station locations '
    'from '
    '<a href="https://www.wikidata.org/" rel="noopener">Wikidata</a>, the government’s <a href="https://www.gov.uk/government/publications/renewable-energy-planning-database-quarterly-extract" rel="noopener">Renewable Energy Planning Database</a> (Open Government Licence v3.0) and the <a href="https://github.com/OSUKED/Power-Station-Dictionary" rel="noopener">Power Station Dictionary</a>; '
    'station photos from Wikimedia Commons, credited with each; map outlines from <a href="https://www.naturalearthdata.com/" rel="noopener">Natural Earth</a>.</p>\n'
    '<p class="credit">Unfamiliar terms are underlined; the <a href="/glossary">glossary</a> explains them all. A self-hosted dashboard, not an official Elexon or NESO service. Inspired by Kate Morley’s '
    '<a href="https://grid.iamkate.com/" rel="noopener">National Grid: Live</a>. All times are UK time.</p>')
CREDITS_SHORT_HTML = (
    '<p class="credit">' + ELEXON_CREDIT + ' ' + NESO_CREDIT + ' Station locations: '
    '<a href="https://www.wikidata.org/" rel="noopener">Wikidata</a>, the <a href="https://www.gov.uk/government/publications/renewable-energy-planning-database-quarterly-extract" rel="noopener">Renewable Energy Planning Database</a> (OGL v3.0) and the <a href="https://github.com/OSUKED/Power-Station-Dictionary" rel="noopener">Power Station Dictionary</a>. '
    'Map: <a href="https://www.naturalearthdata.com/" rel="noopener">Natural Earth</a>.</p>')


ICONS = {
    'search': '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="m13 13 4.5 4.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
    'display': '<svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="3.5" width="15" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M7 17h6M10 13.5V17" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    'menu': '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3 6h14M3 10h14M3 14h14" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
    'close': '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
    'light': '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 1.8v2.1M10 16.1v2.1M1.8 10h2.1M16.1 10h2.1M4.2 4.2l1.5 1.5M14.3 14.3l1.5 1.5M4.2 15.8l1.5-1.5M14.3 5.7l1.5-1.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    'dark': '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M16.5 12.4A7 7 0 0 1 7.6 3.5a7 7 0 1 0 8.9 8.9Z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
    'system': '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 3a7 7 0 0 1 0 14Z" fill="currentColor"/></svg>',
}


def nav_groups():
    """[(group key, group name, [(key, path, label, description)])]"""
    groups = []
    for gkey, gname in NAV_GROUPS:
        items = [(key, path, label, PAGE_META[key][1]) for key, path, label, _ in PAGES
                 if key in PAGE_META and PAGE_META[key][0] == gkey]
        groups.append((gkey, gname, items))
    return groups


def header_html(current):
    def link(key, path, label, desc, extra=''):
        return '<a href="%s"%s%s data-desc="%s"%s>%s</a>' % (
            path, ' data-view="%s"' % key if key in ('map', 'stations') else '',
            ' aria-current="page"' if key == current else '', html_escape(desc), extra, label)
    bar = ''.join('<div class="nav-group" role="group" aria-label="%s">%s</div>' % (
        html_escape(gname), ''.join(link(*item) for item in items)) for gkey, gname, items in nav_groups())
    sheet = ''.join('<section class="sheet-group"><h2>%s</h2><ul>%s</ul></section>' % (
        html_escape(gname), ''.join('<li><a href="%s"%s%s><b>%s</b><span>%s</span></a></li>' % (
            path, ' data-view="%s"' % key if key in ('map', 'stations') else '',
            ' aria-current="page"' if key == current else '', label, html_escape(desc))
            for key, path, label, desc in items)) for gkey, gname, items in nav_groups())
    return (
        '<a class="skip" href="#main" id="skip">Skip to content</a>\n'
        '<div class="alert-bar" id="alert-bar" role="region" aria-label="Grid notices" hidden></div>\n'
        '<header class="top">\n  <div class="wrap top-bar">\n'
        '    <a class="brand" href="/" aria-label="GB grid overview">' + BRAND_SVG + '<span>GB grid</span></a>\n'
        '    <p class="live" title=""><span class="dot" id="live-dot"></span>'
        '<span id="live-text">Connecting to the dashboard server…</span></p>\n'
        '    <div class="tools">'
        '<button class="tool-btn search-btn" id="search-btn" type="button" aria-label="Search pages, power stations and terms" aria-keyshortcuts="/ Control+K Meta+K">'
        + ICONS['search'] + '<span class="tool-word">Search</span><kbd>/</kbd></button>'
        '<a class="tool-btn" href="/display" title="A full-screen view for a spare screen or tablet">' + ICONS['display']
        + '<span class="tool-word">Display</span></a>'
        '<div class="theme-wrap"><button class="tool-btn" id="theme-btn" type="button" aria-haspopup="true" aria-expanded="false">'
        '<span class="theme-icon" id="theme-icon">' + ICONS['light'] + '</span><span class="tool-word" id="theme-word">Light</span></button>'
        '<div class="theme-menu" id="theme-menu" role="radiogroup" aria-label="Colour theme" hidden>'
        + ''.join('<button type="button" role="radio" aria-checked="false" data-theme-choice="%s">%s<span>%s</span></button>'
                  % (k, ICONS[k], n) for k, n in (('light', 'Light'), ('dark', 'Dark'), ('system', 'Match my device')))
        + '</div></div>'
        '<button class="tool-btn menu-btn" id="menu-btn" type="button" aria-haspopup="dialog" aria-controls="menu-sheet">'
        + ICONS['menu'] + '<span class="tool-word">Menu</span></button>'
        '</div>\n  </div>\n'
        '  <nav class="navbar" aria-label="Pages"><div class="wrap nav">' + bar + '</div></nav>\n'
        '</header>\n'
        '<dialog class="sheet" id="menu-sheet" aria-label="Menu">'
        '<div class="sheet-head"><span class="brand">' + BRAND_SVG + '<span>GB grid</span></span>'
        '<button class="tool-btn" type="button" data-close aria-label="Close the menu">' + ICONS['close'] + '</button></div>'
        '<nav aria-label="Pages">' + sheet + '</nav>'
        '<div class="sheet-tools"><button class="tool-btn" type="button" data-open-search>' + ICONS['search'] + '<span>Search</span></button>'
        '<a class="tool-btn" href="/display">' + ICONS['display'] + '<span>Display mode</span></a>'
        '<div class="theme-seg" role="radiogroup" aria-label="Colour theme">'
        + ''.join('<button type="button" role="radio" aria-checked="false" data-theme-choice="%s">%s<span>%s</span></button>'
                  % (k, ICONS[k], n) for k, n in (('light', 'Light'), ('dark', 'Dark'), ('system', 'Device')))
        + '</div></div></dialog>\n'
        '<dialog class="palette" id="palette" aria-label="Search">'
        '<div class="palette-box"><label class="palette-input">' + ICONS['search']
        + '<input id="palette-q" type="search" placeholder="Search pages, power stations and terms" autocomplete="off" spellcheck="false" '
        'role="combobox" aria-expanded="true" aria-controls="palette-list" aria-autocomplete="list"></label>'
        '<ul class="palette-list" id="palette-list" role="listbox" aria-label="Results"></ul>'
        '<p class="palette-foot"><span><kbd>↑</kbd><kbd>↓</kbd> to move</span><span><kbd>Enter</kbd> to open</span><span><kbd>Esc</kbd> to close</span></p></div></dialog>\n'
        '<script type="module" src="{{static:js/gb.js}}"></script>')


def html_escape(text):
    return (str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;'))


def sitemap_html():
    cols = ''.join('<div><h2>%s</h2><ul>%s</ul></div>' % (html_escape(gname), ''.join(
        '<li><a href="%s">%s</a></li>' % (path, label) for key, path, label, desc in items)) for gkey, gname, items in nav_groups())
    return '<nav class="sitemap" aria-label="All pages">' + cols + '</nav>\n'


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
            html = html.replace('<!--#header-->', header_html('stations' if key == 'station' else key))
            html = html.replace('<!--#credits-->', sitemap_html() + CREDITS_HTML).replace('<!--#credits-short-->', CREDITS_SHORT_HTML)
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


class Server(ThreadingHTTPServer):
    """ThreadingHTTPServer with a cap on open connections.

    Each connection gets a thread, so without a cap a client that opens
    connections and never finishes a request (a slowloris) could tie up
    threads without limit. Over the cap, a connection gets a quick 503."""

    daemon_threads = True
    max_connections = 100

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(self.max_connections)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    # Close connections that go quiet: an idle keep-alive, or a client
    # sending its request too slowly. Open pages poll well within this.
    timeout = 30
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
            elif parts.path == '/api/summary':
                self.respond_json(App.grid.summary(query.get('day', ['today'])[0]), send_body)
            elif parts.path == '/api/events':
                try:
                    days = int(query.get('days', ['30'])[0])
                except ValueError:
                    days = 30
                kinds = [k for k in query.get('kind', [''])[0].split(',') if k] or None
                self.respond_json(App.grid.events_payload(days, kinds), send_body)
            elif parts.path == '/api/sandbox':
                try:
                    at = int(query.get('at', ['0'])[0])
                except ValueError:
                    at = 0
                self.respond_json(App.grid.sandbox_payload(at or None), send_body)
            elif parts.path == '/api/frequency':
                event = query.get('event', [''])[0][:40] or None
                self.respond_json(App.grid.frequency_payload(query.get('range', ['1h'])[0], event), send_body)
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
                full = query.get('full', [''])[0] == '1'
                data = (App.grid.station_page(int(raw)) if full else App.grid.station_history(int(raw))) if raw.isdigit() else None
                if data is None and full:
                    self.respond_json({'unknown': True}, send_body)
                elif data is None:
                    self.respond(404, b'Unknown station\n', 'text/plain; charset=utf-8', send_body)
                else:
                    self.respond_json(data, send_body)
            elif parts.path == '/api/stations':
                self.respond_json(App.grid.station_index(), send_body)
            elif parts.path == '/api/photo':
                raw = query.get('id', [''])[0]
                station = App.grid.fleet.stations.get(int(raw)) if raw.isdigit() and App.grid.fleet else None
                photo = station and station['photo']
                found = App.photos.get(photo['file']) if photo and App.photos else None
                if not found:
                    self.respond(404, b'No photo\n', 'text/plain; charset=utf-8', send_body, cache='max-age=300')
                else:
                    body, content_type = found
                    etag = '"' + hashlib.sha1(body).hexdigest()[:16] + '"'
                    cache = 'public, max-age=31536000, immutable' if 'v=' in parts.query else 'max-age=86400'
                    self.respond(200, body, content_type, send_body, etag=etag, cache=cache)
            elif parts.path == '/api/live':
                self.respond_json(App.grid.live(), send_body)
            elif parts.path == '/api/history':
                self.respond_json(App.grid.history(query.get('range', ['24h'])[0]), send_body)
            elif parts.path == '/api/status':
                status = App.grid.status_payload(time.time())
                latest = status.get('generation', {}).get('data_time')
                healthy = bool(latest and time.time() - latest < 30 * 60)
                notifier = getattr(App.grid, 'notifier', None)
                self.respond_json({'healthy': healthy, 'sources': status, 'notifications': notifier.state() if notifier else None},
                                  send_body, code=200 if healthy else 503)
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
        compressed = content_type.startswith(('font/', 'image/png', 'image/jpeg', 'image/webp'))
        if len(body) > 1024 and not compressed and 'gzip' in (self.headers.get('Accept-Encoding') or ''):
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
