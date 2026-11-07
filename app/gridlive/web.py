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
from http.server import BaseHTTPRequestHandler

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
        '  </div>\n</header>\n<script type="module" src="{{static:js/gb.js}}"></script>')


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
