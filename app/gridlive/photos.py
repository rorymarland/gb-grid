"""Station photos from Wikimedia Commons, fetched once and kept in DATA_DIR.

The browser only ever talks to this server: /api/photo?id=N serves the
cached copy, fetching it from Commons the first time it's asked for.
"""

import hashlib
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import COMMONS, USER_AGENT, log

WIDTH = 960                 # one of Wikimedia's standard thumbnail widths
MAX_BYTES = 4 * 1024 * 1024
RETRY_AFTER = 3600          # after a failed fetch, try again an hour later
TYPES = {b'\xff\xd8\xff': ('image/jpeg', '.jpg'), b'\x89PNG': ('image/png', '.png')}
HOSTS = ('commons.wikimedia.org', 'upload.wikimedia.org', 'thumb.wikimedia.org')


def kind_of(body):
    """(content type, extension) from the file's first bytes, or None."""
    for magic, kind in TYPES.items():
        if body.startswith(magic):
            return kind
    if body[:4] == b'RIFF' and body[8:12] == b'WEBP':
        return ('image/webp', '.webp')
    return None


class PhotoError(Exception):
    pass


class WikimediaOnly(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to Wikimedia's own hosts, over HTTPS."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parts = urllib.parse.urlsplit(newurl)
        if parts.scheme != 'https' or parts.hostname not in HOSTS:
            raise PhotoError('redirected to ' + (parts.hostname or newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def commons_get(url):
    """(status, body) for a Commons URL, reading at most MAX_BYTES + 1."""
    opener = urllib.request.build_opener(WikimediaOnly)
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'image/jpeg,image/png,image/webp'})
    try:
        with opener.open(request, timeout=20) as response:
            return response.status, response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise PhotoError('HTTP %d' % error.code)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise PhotoError(type(error).__name__ + ': ' + str(getattr(error, 'reason', error)))


def file_key(name):
    return hashlib.sha1(name.encode('utf-8')).hexdigest()[:16]


class Photos:
    def __init__(self, directory, fetch=commons_get):
        self.directory = directory
        self.fetch = fetch
        self.lock = threading.Lock()
        self.locks = {}
        self.failed = {}
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as error:
            log.warning('[photos] cannot create %s (%s); photos will be fetched but not kept', directory, error)

    def _cached(self, key):
        for content_type, ext in list(TYPES.values()) + [('image/webp', '.webp')]:
            path = os.path.join(self.directory, key + ext)
            try:
                with open(path, 'rb') as handle:
                    return handle.read(), content_type
            except OSError:
                continue
        return None

    def get(self, name):
        """(body, content type) for a Commons file, or None if it can't be had."""
        key = file_key(name)
        found = self._cached(key)
        if found:
            return found
        with self.lock:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:                       # one fetch per photo, however many ask at once
            found = self._cached(key)
            if found:
                return found
            if time.time() - self.failed.get(key, 0) < RETRY_AFTER:
                return None
            try:
                body, kind = self._download(name)
            except (PhotoError, ValueError) as error:
                self.failed[key] = time.time()
                log.warning('[photos] %s: %s', name, error)
                return None
            try:
                temp = os.path.join(self.directory, key + '.part')
                with open(temp, 'wb') as handle:
                    handle.write(body)
                os.replace(temp, os.path.join(self.directory, key + kind[1]))
            except OSError as error:
                log.warning('[photos] could not keep %s: %s', name, error)
            return body, kind[0]

    def _download(self, name):
        url = COMMONS + '/w/index.php?' + urllib.parse.urlencode(
            {'title': 'Special:FilePath/' + name, 'width': WIDTH})
        status, body = self.fetch(url)
        if status != 200 or not body:
            raise ValueError('HTTP %s' % status)
        if len(body) > MAX_BYTES:
            raise ValueError('too large (%d bytes)' % len(body))
        kind = kind_of(body)
        if not kind:
            raise ValueError('not a JPEG, PNG or WebP image')
        return body, kind
