"""The HTTP client used for every upstream source, and parsing helpers."""

import csv
import gzip
import io
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from .config import USER_AGENT


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------

class FetchError(Exception):
    pass


def http_get(url, accept='application/json', timeout=30, extra_headers=None, data=None):
    """GET a URL (or POST `data`). Returns (status, body_bytes, headers). 304 is returned, not raised.

    Error messages name the host and quote the start of the response, never
    the request's headers or body, so credentials can't end up in the logs."""
    headers = {'User-Agent': USER_AGENT, 'Accept': accept, 'Accept-Encoding': 'gzip'}
    if extra_headers:
        headers.update(extra_headers)
    host = urllib.parse.urlsplit(url).netloc.rsplit('@', 1)[-1]
    request = urllib.request.Request(url, headers=headers, data=data, method='POST' if data is not None else 'GET')
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


def r2(value):
    return None if value is None else round(value, 2)
