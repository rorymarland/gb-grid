"""API view: /api/summary, a plain-English account of today or yesterday.

The account is assembled from rules, not written freely: each point is a
sentence built from the half-hourly history, and the headline is the most
notable of a short list of candidates. Text comes as a list of parts so
the pages can underline glossary terms and link to other pages:

    "plain text" | {"t": "text", "term": "glossary-key"} | {"t": "text", "href": "/page"}
"""

import time
from datetime import timedelta

from .timeutil import uk_clock, uk_midnight_utc

FUEL_KEYS = ['nuclear', 'wind', 'solar', 'hydro', 'biomass', 'gas', 'coal', 'oil', 'other', 'imports', 'storage_out']
FUEL_WORDS = {'nuclear': 'nuclear', 'wind': 'wind', 'solar': 'solar', 'hydro': 'hydro', 'biomass': 'biomass', 'gas': 'gas',
              'coal': 'coal', 'oil': 'oil', 'other': 'other sources', 'imports': 'imports', 'storage_out': 'storage'}
LOW_CARBON = ('nuclear', 'wind', 'solar', 'hydro')


def T(text, term):
    return {'t': text, 'term': term}


def L(text, href):
    return {'t': text, 'href': href}


def hm(t):
    return uk_clock(t)[1]


def gw(value):
    return ('%.1f' % value) if abs(value) >= 1 else ('%.2f' % value)


def pct(share):
    return '%d%%' % round(100 * share)


def money(pounds):
    if pounds >= 1e6:
        return '£%.1fm' % (pounds / 1e6)
    if pounds >= 1e3:
        return '£%dk' % round(pounds / 1e3)
    return '£%d' % round(pounds)


def capital(text):
    return text[:1].upper() + text[1:]


def article(title):
    """'an Electricity Margin Notice', 'a Capacity Market Notice', 'a high risk of ...'"""
    if 'Flexibility' in title:
        return 'a Demand Flexibility Service event'
    text = title if title[1:2].isupper() or title.istitle() else title[:1].lower() + title[1:]
    return ('an ' if title[:1].lower() in 'aeiou' else 'a ') + text


def notice_term(title):
    return 'dfs' if 'Flexibility' in title else 'margin-notice'


def join_words(words):
    return words[0] if len(words) == 1 else ', '.join(words[:-1]) + ' and ' + words[-1]


class SummaryViews:
    """Mixed into Grid; uses its history, balancing, notices and margin views."""

    def summary(self, which='today'):
        which = which if which in ('today', 'yesterday') else 'today'
        version = (self.store.history_version, int(time.time() // 300))
        return self.cached(('summary', which), version, lambda: self._build_summary_day(which))

    def _build_summary_day(self, which):
        now = time.time()
        today = uk_clock(now)[0]
        day = today if which == 'today' else today - timedelta(days=1)
        start, end = uk_midnight_utc(day), uk_midnight_utc(day + timedelta(days=1))
        hist = self.history('7d')
        times = hist['t']
        idx = [i for i, t in enumerate(times) if start <= t < end and hist['demand'][i] is not None]
        result = {'day': which, 'date': day.isoformat(), 'from': start, 'to': end, 'ready': False,
                  'headline': [], 'points': [], 'stats': {}, 'generated': int(now)}
        if len(idx) < 4:
            return result
        step_hours = hist['step'] / 3600.0
        covered_to = times[idx[-1]] + hist['step']
        series = hist['series']
        so_far = which == 'today'
        result.update({'ready': True, 'covered_to': covered_to, 'complete': covered_to >= end - hist['step']})

        # ---- generation mix -------------------------------------------------
        energy = {k: sum(max(0.0, series[k][i] or 0.0) for i in idx) * step_hours for k in FUEL_KEYS}
        total = sum(energy.values()) or 1.0
        shares = {k: v / total for k, v in energy.items()}
        ranked = sorted(FUEL_KEYS, key=lambda k: -shares[k])
        low = sum(shares[k] for k in LOW_CARBON)
        fossil = shares['gas'] + shares['coal'] + shares['oil']
        lead = ranked[0]
        second = [k for k in ranked[1:3] if shares[k] >= 0.05]
        named = [FUEL_WORDS[k] + ' ' + pct(shares[k]) for k in second]
        gas_named = lead == 'gas' or 'gas' in second
        mix_point = [capital(FUEL_WORDS[lead]) + ' supplied ' + pct(shares[lead]) + ' of Britain’s electricity' + (' so far' if so_far else '')
                     + (', then ' + join_words(named) if named else '') + '. ',
                     T('Low-carbon', 'low-carbon'),
                     ' sources made ' + pct(low) + ('.' if gas_named else ' and gas ' + pct(shares['gas']) + '.')]

        # ---- carbon intensity ----------------------------------------------
        ci = [(times[i], hist['carbon'][i]) for i in idx if hist['carbon'][i] is not None]
        carbon_point, carbon_avg, week_rank = None, None, None
        if ci:
            carbon_avg = sum(v for _, v in ci) / len(ci)
            low_t, low_v = min(ci, key=lambda p: p[1])
            high_t, high_v = max(ci, key=lambda p: p[1])
            # Daily averages for the complete days before this one in the last week
            others = []
            for back in range(1, 7):
                d = day - timedelta(days=back)
                s, e = uk_midnight_utc(d), uk_midnight_utc(d + timedelta(days=1))
                vals = [hist['carbon'][i] for i, t in enumerate(times) if s <= t < e and hist['carbon'][i] is not None]
                if len(vals) >= 0.9 * 86400 / hist['step']:
                    others.append(sum(vals) / len(vals))
            compare = ''
            if others and not so_far and carbon_avg < min(others):
                week_rank, compare = 'cleanest', ' It was the cleanest day of the past week.'
            elif others and not so_far and carbon_avg > max(others):
                week_rank, compare = 'dirtiest', ' It was the most carbon-intensive day of the past week.'
            elif others:
                compare = ' The past week averaged %d.' % round(sum(others) / len(others))
            carbon_point = [T('Carbon intensity', 'carbon-intensity'), ' averaged %d g/kWh%s, ranging from %d at %s to %d at %s.%s'
                            % (round(carbon_avg), ' so far' if so_far else '', low_v, hm(low_t), high_v, hm(high_t), compare)]

        # ---- demand ------------------------------------------------------
        demand = [(times[i], hist['demand'][i]) for i in idx]
        peak_t, peak_v = max(demand, key=lambda p: p[1])
        trough_t, trough_v = min(demand, key=lambda p: p[1])
        demand_point = [T('Demand', 'demand'), ' peaked at %s GW at %s and was lowest at %s GW at %s.'
                        % (gw(peak_v), hm(peak_t), gw(trough_v), hm(trough_t))]

        # ---- prices --------------------------------------------------------
        prices = [(times[i], hist['price'][i]) for i in idx if hist['price'][i] is not None]
        price_point, negative = None, []
        if prices:
            avg = sum(v for _, v in prices) / len(prices)
            lo = min(prices, key=lambda p: p[1])
            hi = max(prices, key=lambda p: p[1])
            negative = [t for t, v in prices if v < 0]
            price_point = ['The ', T('wholesale price', 'wholesale-price'), ' averaged £%d/MWh, from %s at %s to £%d at %s.'
                           % (round(avg), ('−£%d' % round(-lo[1])) if lo[1] < 0 else '£%d' % round(lo[1]), hm(lo[0]), round(hi[1]), hm(hi[0]))]
            if negative:
                hours = len(negative) * step_hours
                price_point += [' It was ', T('below zero', 'negative-price'), ' for %s, starting at %s.'
                                % (('%g hours' % hours) if hours != 1 else 'an hour', hm(negative[0]))]

        # ---- wind and solar peaks -------------------------------------------
        wind = max(((times[i], series['wind'][i] or 0.0) for i in idx), key=lambda p: p[1])
        solar = max(((times[i], series['solar'][i] or 0.0) for i in idx), key=lambda p: p[1])
        peaks = 'Wind peaked at %s GW at %s' % (gw(wind[1]), hm(wind[0]))
        peaks += ('; solar at %s GW at %s.' % (gw(solar[1]), hm(solar[0]))) if solar[1] >= 0.5 else '.'
        peak_point = [peaks]

        # ---- interconnectors ---------------------------------------------
        net = sum((series['imports'][i] or 0.0) + (series['exports'][i] or 0.0) for i in idx) / len(idx)
        countries = hist.get('countries') or {}
        flows = {name: sum(values[i] or 0.0 for i in idx) / len(idx) for name, values in countries.items()}
        if net >= 0:
            sources = [n for n, v in sorted(flows.items(), key=lambda x: -x[1]) if v >= 0.3][:2]
            flow_point = ['Britain was a net importer through its ', T('interconnectors', 'interconnector'),
                          ', averaging %s GW%s.' % (gw(net), (', mostly from ' + join_words(sources)) if sources else '')]
        else:
            sinks = [n for n, v in sorted(flows.items(), key=lambda x: x[1]) if v <= -0.3][:2]
            flow_point = ['Britain was a net exporter through its ', T('interconnectors', 'interconnector'),
                          ', averaging %s GW%s.' % (gw(-net), (', mostly to ' + join_words(sinks)) if sinks else '')]

        # ---- balancing -----------------------------------------------------
        balancing_point, paid, wind_paid, wind_mwh = None, None, None, None
        if self.bal is not None:
            bal = self.balancing()
            if which == 'today':
                t = bal.get('today') or {}
                paid, wind_paid, wind_mwh = t.get('paid'), t.get('wind_paid'), t.get('wind_down_mwh')
            else:
                rows = [r for r in (bal.get('series') or {}).get('yesterday') or [] if r[1] is not None]
                if rows:
                    paid = sum((r[3] or 0) + (r[4] or 0) for r in rows)
                    wind_mwh = -sum(r[5] or 0 for r in rows)
                    wind_paid = sum(r[6] or 0 for r in rows)
            if paid:
                balancing_point = [L('NESO', '/balancing'), ' paid ' + money(paid) + ' in the ', T('Balancing Mechanism', 'balancing-mechanism')]
                if wind_mwh and wind_mwh >= 500:
                    balancing_point += [', ' + money(wind_paid or 0) + ' of it to ', T('turn wind down', 'curtailment'),
                                        ' by %.1f GWh.' % (wind_mwh / 1000.0)]
                else:
                    balancing_point.append('.')

        # ---- notices -------------------------------------------------------
        notices = [n for n in self.notices(3) if not n['routine'] and start <= n['published'] < end]
        notice_point = None
        if notices:
            kinds = []
            for n in notices:
                if n['title'] not in kinds:
                    kinds.append(n['title'])
            notice_point = ['NESO issued '] + sum([([', '] if i and i < len(kinds) - 1 else [' and '] if i else []) + [T(article(k), notice_term(k))]
                                                   for i, k in enumerate(kinds)], []) + [
                            ' (' + ', '.join('%s at %s' % ('cancelled' if n['cancelled'] else 'published', hm(n['published'])) for n in notices[:3]) + ').']

        # ---- spare capacity (today only) -------------------------------------
        margin_point = None
        if which == 'today' and getattr(self, 'outages', None):
            m = self.margin_summary()
            if m:
                margin_point = [T('Spare capacity', 'derated-margin'), ' over the next 12 hours looks %s, with the lowest point %s GW at %s.'
                                % (m['label'].lower(), gw(m['min'] / 1000.0), hm(m['min_t']))]

        # ---- the headline: the most notable thing ---------------------------
        candidates = []
        if negative:
            candidates.append((90, ['Prices went ', T('below zero', 'negative-price'), ' for %s%s' % (
                ('%g hours' % (len(negative) * step_hours)) if len(negative) * step_hours != 1 else 'an hour',
                ', with wind supplying ' + pct(shares['wind']) if shares['wind'] >= 0.3 else '')]))
        if notices:
            candidates.append((85, ['NESO issued ', T(article(notices[0]['title']), notice_term(notices[0]['title']))]))
        if week_rank == 'cleanest':
            candidates.append((80, ['The cleanest day of the past week, at %d g/kWh' % round(carbon_avg)]))
        if wind_mwh and wind_mwh >= 40000:
            candidates.append((70, ['A busy day for ', T('curtailment', 'curtailment'), ': %.0f GWh of wind turned down' % (wind_mwh / 1000.0)]))
        if week_rank == 'dirtiest':
            candidates.append((65, ['The most carbon-intensive day of the past week, at %d g/kWh' % round(carbon_avg)]))
        if shares['wind'] >= 0.5:
            candidates.append((60, ['Wind supplied more than half of Britain’s electricity' + (' so far' if so_far else '')]))
        if lead == 'gas' and shares['gas'] >= 0.35:
            candidates.append((40, ['Gas did the heavy lifting, at ' + pct(shares['gas']) + ' of supply' + (' so far' if so_far else '')]))
        candidates.append((10, [capital(FUEL_WORDS[lead]) + ' led the mix at ' + pct(shares[lead]) + (', with ' if carbon_avg is not None else ''),
                                T('carbon intensity', 'carbon-intensity') if carbon_avg is not None else '',
                                (' averaging %d g/kWh' % round(carbon_avg)) if carbon_avg is not None else '']))
        headline = max(candidates, key=lambda c: c[0])[1]
        headline = [p for p in headline if p != ''] + ['.']

        points = [('mix', mix_point), ('carbon', carbon_point), ('demand', demand_point), ('price', price_point),
                  ('peaks', peak_point), ('flows', flow_point), ('balancing', balancing_point), ('notices', notice_point),
                  ('margin', margin_point)]
        result['headline'] = headline
        result['points'] = [{'key': k, 'parts': v} for k, v in points if v]
        result['stats'] = {
            'shares': {k: round(v, 4) for k, v in shares.items()}, 'low_carbon': round(low, 4), 'fossil': round(fossil, 4),
            'carbon_avg': None if carbon_avg is None else round(carbon_avg), 'demand_peak': [peak_t, peak_v],
            'wind_peak': [wind[0], wind[1]], 'negative_half_hours': len(negative), 'net_imports': round(net, 2),
            'balancing_paid': paid, 'wind_turned_down_mwh': wind_mwh,
        }
        return result
