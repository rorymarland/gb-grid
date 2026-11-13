"""Notifications: push new timeline events to ntfy or a webhook.

After each events poll, any event not seen before is considered. Events that
pass the filters (level, kinds, quiet hours, not too old) are sent to every
configured channel; everything considered is then remembered, so an event is
sent at most once, and turning notifications on later doesn't replay the past.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .fetch import OPENER as fetch_opener
from .config import (NOTIFY_KINDS, NOTIFY_LEVEL, NOTIFY_NTFY_TOKEN, NOTIFY_NTFY_URL, NOTIFY_QUIET_HOURS, NOTIFY_WEBHOOK_URL,
                     PUBLIC_URL, USER_AGENT, log)
from .events import DAILY, KINDS, MAJOR, NOTABLE
from .timeutil import uk_clock

LEVELS = {'major': MAJOR, 'notable': NOTABLE, 'all': DAILY}
LEVEL_NAMES = {MAJOR: 'major', NOTABLE: 'notable', DAILY: 'daily'}
MAX_AGE = 2 * 3600          # older than this when first seen (a backfill, a late notice): not sent
LATE_KINDS = {'record', 'balancing', 'clean', 'demand'}   # only known once the day's figures are published
MAX_AGE_LATE = 3 * 86400
MAX_PER_POLL = 6            # a burst beyond this is dropped rather than flooding a phone
NTFY_PRIORITY = {MAJOR: 4, NOTABLE: 3, DAILY: 2}
NTFY_TAGS = {'frequency': 'zap', 'outage': 'warning', 'price': 'pound', 'notice': 'loudspeaker', 'record': 'trophy',
             'clean': 'leaves', 'demand': 'electric_plug', 'balancing': 'balance_scale'}


def plain(parts):
    """Text of a title or detail, which come as strings and {'t': text, ...} parts."""
    return ''.join(p if isinstance(p, str) else str(p.get('t', '')) for p in parts or []).strip()


def parse_quiet(text):
    """'23:00-07:00' as (start, end) minutes of the day, or None."""
    try:
        start, end = text.split('-')
        (h1, m1), (h2, m2) = start.strip().split(':'), end.strip().split(':')
        a, b = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
        return (a, b) if 0 <= a < 1440 and 0 <= b < 1440 and a != b else None
    except (ValueError, AttributeError):
        return None


def in_quiet(quiet, t):
    if not quiet:
        return False
    hh, mm = uk_clock(t)[1].split(':')
    minute = int(hh) * 60 + int(mm)
    start, end = quiet
    return start <= minute < end if start < end else minute >= start or minute < end


def post_json(url, payload, headers=None):
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=body, method='POST', headers=dict(
        {'Content-Type': 'application/json; charset=utf-8', 'User-Agent': USER_AGENT}, **(headers or {})))
    with fetch_opener.open(request, timeout=15) as response:   # never carries credentials to another host
        response.read(4096)
        return response.status


def ntfy_target(url):
    """(server URL, topic) from a topic URL such as https://ntfy.sh/my-topic."""
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip('/')
    base, _, topic = path.rpartition('/')
    if parts.scheme not in ('http', 'https') or not parts.netloc or not topic:
        return None
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, base + '/', '', '')), urllib.parse.unquote(topic)


class Notifier:
    def __init__(self, store, post=post_json):
        self.store = store
        self.post = post
        self.level = LEVELS.get(NOTIFY_LEVEL, MAJOR)
        self.kinds = {k for k in NOTIFY_KINDS if k in KINDS} or set(KINDS)
        self.quiet = parse_quiet(NOTIFY_QUIET_HOURS)
        self.ntfy = ntfy_target(NOTIFY_NTFY_URL) if NOTIFY_NTFY_URL else None
        self.webhook = NOTIFY_WEBHOOK_URL if urllib.parse.urlsplit(NOTIFY_WEBHOOK_URL).scheme in ('http', 'https') else None
        self.sent, self.last, self.error = 0, None, None
        store.db.execute('CREATE TABLE IF NOT EXISTS notified (id TEXT PRIMARY KEY, t INTEGER NOT NULL, sent INTEGER NOT NULL)')
        if NOTIFY_NTFY_URL and not self.ntfy:
            log.warning('[notify] NOTIFY_NTFY_URL should be a topic address such as https://ntfy.sh/my-topic; ignored')
        if NOTIFY_WEBHOOK_URL and not self.webhook:
            log.warning('[notify] NOTIFY_WEBHOOK_URL should start http:// or https://; ignored')
        if NOTIFY_QUIET_HOURS and not self.quiet:
            log.warning('[notify] NOTIFY_QUIET_HOURS should look like 23:00-07:00; ignored')
        if self.channels():
            log.info('[notify] sending %s events (%s) to %s%s', LEVEL_NAMES[self.level] if self.level != DAILY else 'all',
                     'every kind' if self.kinds == set(KINDS) else ', '.join(sorted(self.kinds)), ' and '.join(self.channels()),
                     ', quiet %s' % NOTIFY_QUIET_HOURS if self.quiet else '')

    def channels(self):
        return [name for name, on in (('ntfy', self.ntfy), ('webhook', self.webhook)) if on]

    def state(self):
        return {'channels': self.channels(), 'level': NOTIFY_LEVEL if NOTIFY_LEVEL in LEVELS else 'major',
                'kinds': sorted(self.kinds), 'quiet_hours': NOTIFY_QUIET_HOURS if self.quiet else None,
                'sent': self.sent, 'last': self.last, 'error': self.error}

    # ---- sending -------------------------------------------------------------

    def message(self, e):
        title, text = plain(e['title']), plain([p for p in e['detail'] if isinstance(p, str) or 'href' not in p])
        link = PUBLIC_URL + '/timeline#' + urllib.parse.quote(e['id']) if PUBLIC_URL else None
        return title, text, link

    def send(self, e):
        """Send one event to every channel; True if any accepted it."""
        title, text, link = self.message(e)
        ok = False
        if self.ntfy:
            server, topic = self.ntfy
            payload = {'topic': topic, 'title': title, 'message': text or title, 'priority': NTFY_PRIORITY.get(e['level'], 3),
                       'tags': [NTFY_TAGS.get(e['kind'], 'bulb')]}
            if link:
                payload['click'] = link
            headers = {'Authorization': 'Bearer ' + NOTIFY_NTFY_TOKEN} if NOTIFY_NTFY_TOKEN else None
            ok = self.attempt('ntfy', server, payload, headers) or ok
        if self.webhook:
            payload = {'id': e['id'], 't': e['t'], 'time': datetime.fromtimestamp(e['t'], timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                       'kind': e['kind'], 'kind_name': KINDS.get(e['kind'], e['kind']), 'level': LEVEL_NAMES.get(e['level'], 'notable'),
                       'title': title, 'message': text, 'url': link,
                       'data': {k: v for k, v in (e.get('data') or {}).items() if k != 'series'}}
            ok = self.attempt('webhook', self.webhook, payload, None) or ok
        return ok

    def attempt(self, name, url, payload, headers):
        try:
            status = self.post(url, payload, headers)
            if 200 <= status < 300:
                return True
            self.error = '%s: HTTP %s' % (name, status)
        except urllib.error.HTTPError as error:
            self.error = '%s: HTTP %d' % (name, error.code)
        except (urllib.error.URLError, OSError, ValueError) as error:
            self.error = '%s: %s' % (name, getattr(error, 'reason', error))
        log.warning('[notify] %s', self.error)
        return False

    # ---- the job -------------------------------------------------------------

    def poll(self, now=None):
        """Look at events not seen before; returns how many were sent."""
        now = now or time.time()
        store = self.store
        first = store.scalar("SELECT v FROM meta WHERE k = 'notify_started'") is None
        rows = store.query('SELECT id, t, kind, level, title, detail, data FROM ev WHERE t <= ? AND id NOT IN (SELECT id FROM notified) '
                           'ORDER BY level DESC, t', (now + 3600,))
        seen, sent = [], 0
        for eid, t, kind, level, title, detail, data in rows:
            wanted = (not first and self.channels() and level >= self.level and kind in self.kinds
                      and now - t <= (MAX_AGE_LATE if kind in LATE_KINDS else MAX_AGE) and not in_quiet(self.quiet, now))
            if wanted and sent >= MAX_PER_POLL:
                log.warning('[notify] too many at once; not sending "%s"', plain(json.loads(title)))
                wanted = False
            if wanted:
                event = {'id': eid, 't': t, 'kind': kind, 'level': level, 'title': json.loads(title),
                         'detail': json.loads(detail or '[]'), 'data': json.loads(data or '{}')}
                if not self.send(event):
                    continue                    # every channel failed: try again next time, until it's too old
                sent += 1
                self.sent += 1
                self.last = {'t': int(now), 'title': plain(event['title'])}
                self.error = None
                log.info('[notify] sent: %s', self.last['title'])
            seen.append((eid, int(now), 1 if wanted else 0))
        with store.lock:
            store.db.execute('BEGIN')
            store.db.executemany('INSERT OR IGNORE INTO notified (id, t, sent) VALUES (?, ?, ?)', seen)
            store.db.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('notify_started', ?)", (str(int(now)),))
            store.db.execute('DELETE FROM notified WHERE id NOT IN (SELECT id FROM ev)')
            store.db.execute('COMMIT')
        return sent

    def test(self):
        """Send a test message; returns True if any channel accepted it."""
        if not self.channels():
            return False
        return self.send({'id': 'test', 't': int(time.time()), 'kind': 'notice', 'level': NOTABLE,
                          'title': ['Test notification from GB grid'],
                          'detail': ['If you can read this, notifications are working. You will hear about %s events.'
                                     % (LEVEL_NAMES[self.level] if self.level != DAILY else 'all')], 'data': {}})
