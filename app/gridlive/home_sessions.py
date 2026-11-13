"""Your home: Octoplus points and Saving Sessions, with Octopus's baselines."""

import json
from datetime import timedelta

from .octopus import POINTS_QUERY, SESSIONS_QUERY, as_number
from .timeutil import bank_holidays, floor_to, parse_time, uk_clock, uk_midnight_utc

POINT_PENCE = 0.125           # 800 points to £1 (Octopus's help pages)


class SessionsMixin:
    def poll_sessions(self, now):
        data = self._query(SESSIONS_QUERY % json.dumps(self.account), now, url=self.backend + '/v1/graphql/')
        block = data.get('savingSessions') or {}
        account = block.get('account') or {}
        events = []
        for item in block.get('events') or []:
            try:
                events.append({'id': int(item['id']), 'start': parse_time(item['startAt']), 'end': parse_time(item['endAt']),
                               'reward': int(item.get('rewardPerKwhInOctoPoints') or 0), 'type': str(item.get('eventType') or ''),
                               'regions': [str(r.get('regionId')) for r in item.get('targetRegion') or [] if isinstance(r, dict)]})
            except (KeyError, TypeError, ValueError):
                continue
        joined = {}
        for item in account.get('joinedEvents') or []:
            try:
                joined[int(item['eventId'])] = {'start': parse_time(item['startAt']), 'end': parse_time(item['endAt']),
                                                'given': as_number(item.get('rewardGivenInOctoPoints')), 'type': str(item.get('eventType') or '')}
            except (KeyError, TypeError, ValueError):
                continue
        region = (account.get('signedUpMeterPoint') or {}).get('regionId')
        with self.lock:
            self.sessions = {'at': now, 'joined_campaign': bool(account.get('hasJoinedCampaign')),
                             'can_join': bool(account.get('canJoinCampaign')), 'region': None if region is None else str(region),
                             'events': events, 'joined': joined}
            self.version += 1

    def poll_points(self, now):
        ledgers = self._query(POINTS_QUERY, now, prefix='JWT ').get('loyaltyPointLedgers') or []
        balance = None
        if ledgers and isinstance(ledgers[0], dict):
            balance = as_number(ledgers[0].get('balanceCarriedForward'))
        with self.lock:
            self.points = None if balance is None else int(balance)
            self.version += 1

    def _baseline(self, start, end, skip_days):
        """Octopus's baseline for a session, half hour by half hour: the same half hours
        averaged over the previous 10 weekdays, leaving out session days and bank holidays;
        for a weekend or bank holiday session, the previous 4 weekend days.
        Returns ([baseline kWh per half hour], complete)."""
        def holiday(d):
            return d.weekday() >= 5 or d in bank_holidays(d.year)

        day = uk_clock(start)[0]
        day_start = uk_midnight_utc(day)
        offsets = list(range(int(floor_to(start, 1800)) - day_start, int(end) - day_start, 1800))
        weekend = holiday(day)
        need, dates, d = 4 if weekend else 10, [], day
        for _ in range(70):
            d -= timedelta(days=1)
            if (d.weekday() >= 5 if weekend else not holiday(d)) and d not in skip_days:
                dates.append(d)
                if len(dates) == need:
                    break
        # On a clock-change day, the same clock time is matched on each earlier day
        wanted = {uk_midnight_utc(x) + off: off for x in dates for off in offsets}
        if not wanted:
            return None, False
        found = {}
        for t, kwh in self.store.query("SELECT t, kwh FROM home WHERE fuel = 'elec' AND t >= ? AND t <= ?",
                                       (min(wanted), max(wanted))):
            if t in wanted:
                found.setdefault(wanted[t], []).append(kwh)
        if not found:
            return None, False
        slots = [sum(found[off]) / len(found[off]) if off in found else None for off in offsets]
        complete = all(off in found and len(found[off]) == need for off in offsets) and len(dates) == need
        return slots, complete

    def _sessions(self, now):
        with self.lock:
            s, points = self.sessions, self.points
        if s is None and points is None:
            return None
        out = {'points': points, 'pounds': None if points is None else round(points * POINT_PENCE / 100, 2),
               'joined_campaign': None, 'can_join': None, 'upcoming': [], 'active': None, 'past': []}
        if not s:
            return out
        out['joined_campaign'], out['can_join'] = s['joined_campaign'], s['can_join']
        # Region IDs come as '_N' or 'N' in different parts of the API; compare the letters,
        # falling back to the region in the tariff code
        letter = lambda value: str(value or '').strip().lstrip('_').upper()  # noqa: E731
        region = letter(s['region']) or (self.region or '')
        events = [dict(e) for e in s['events'] if e['type'] in ('TURN_DOWN', 'TURN_UP')
                  and (not e['regions'] or not region or region in {letter(r) for r in e['regions']})]
        known = {e['id'] for e in events}
        for eid, j in s['joined'].items():
            if eid not in known and j['type'] in ('TURN_DOWN', 'TURN_UP'):
                events.append({'id': eid, 'start': j['start'], 'end': j['end'], 'reward': None, 'type': j['type'], 'regions': []})
        skip = {uk_clock(e['start'])[0] for e in events}
        for e in sorted(events, key=lambda e: e['start']):
            if e['start'] > now + 7 * 86400 or e['end'] < now - 150 * 86400:
                continue
            joined = s['joined'].get(e['id'])
            item = {'id': e['id'], 'start': e['start'], 'end': e['end'], 'kind': 'down' if e['type'] == 'TURN_DOWN' else 'up',
                    'reward': e['reward'], 'joined': joined is not None, 'given': joined['given'] if joined else None}
            slots, complete = self._baseline(e['start'], e['end'], skip)
            item['baseline'] = None if not slots or None in slots else round(sum(slots), 3)
            item['baseline_complete'] = complete
            if e['start'] <= now:
                first = floor_to(e['start'], 1800)
                used = dict(self.store.query("SELECT t, kwh FROM home WHERE fuel = 'elec' AND t >= ? AND t < ?", (first, e['end'])))
                item['used'] = round(sum(used.values()), 3) if used else None
                if item['baseline'] is not None and used:
                    # Each half hour counts on its own: a power-down earns for use below that half
                    # hour's baseline, a power-up for use above it
                    sign = 1 if item['kind'] == 'down' else -1
                    earned = sum(max(0.0, sign * (b - used[first + i * 1800]))
                                 for i, b in enumerate(slots) if first + i * 1800 in used)
                    item['saved'] = round(earned, 3)
                    item['estimate'] = round(earned * (item['reward'] or 0))
            if e['end'] <= now:
                out['past'].append(item)
            elif e['start'] <= now:
                out['active'] = item
            else:
                out['upcoming'].append(item)
        out['past'] = out['past'][::-1][:8]
        return out
