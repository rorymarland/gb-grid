/* The station page: /station?id=<station id> */
import G from '/static/js/gb.js';
import { renderParts } from '/static/js/summary.js';
import { placeCard } from '/static/js/stationcard.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num, hm = G.hm, plural = G.plural, niceStep = G.niceStep, makeTip = G.makeTip;
var ID = +(new URLSearchParams(location.search).get('id') || 0);
var state = { data: null, poll: null, range: 'day', card: null, drawn: '' };
try { state.range = localStorage.getItem('gb-station-range') === 'week' ? 'week' : 'day'; } catch (e) { /* storage unavailable */ }
var asked = new URLSearchParams(location.search).get('range');
if (asked === 'week' || asked === 'day') state.range = asked;

var intFmt = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
var dayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short', timeZone: G.TZ });
var dateFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: G.TZ });
var longDate = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: G.TZ });
function mw(v) { return num(v) ? intFmt.format(Math.round(v)) : '–'; }
function energy(mwh) { return mwh >= 10000 ? [(mwh / 1000).toFixed(mwh >= 100000 ? 0 : 1), 'GWh'] : [intFmt.format(mwh), 'MWh']; }
function pct(v) { return Math.round(v * 100) + '%'; }
function ordinal(n) {
  var t = n % 100, u = n % 10;
  return n + (t >= 11 && t <= 13 ? 'th' : u === 1 ? 'st' : u === 2 ? 'nd' : u === 3 ? 'rd' : 'th');
}
function place(d) { return d.offshore ? 'offshore, ' + (d.nation || 'Great Britain') : (d.nation || 'Great Britain'); }
function storage(d) { return d.cat === 'storage'; }

/* ---------- header and the live figure ---------- */

function renderHead(d) {
  document.title = d.name + ' – GB grid';
  byId('name').textContent = d.name;
  var kind = clear(byId('kind'));
  kind.appendChild(h('span', { cls: 'sw' }));
  kind.appendChild(h('span', { text: d.kind + ', ' + place(d) }));
  if (d.rank && d.capacity) {
    var what = d.rank.label === 'storage' ? 'storage site' : d.rank.label === 'wind' ? 'wind farm' : d.rank.label + ' station';
    kind.appendChild(h('span', { cls: 'rank', text: (d.rank.place === 1 ? 'The largest ' : 'The ' + ordinal(d.rank.place) + ' largest ') + what + ' of ' + d.rank.of + ' on the map' }));
  }
}

function renderNow(d) {
  var figure = clear(byId('now-figure')), meter = byId('now-meter'), text = byId('now-text');
  var now = d.now, out = now ? now.mw : null;
  if (!now) {
    figure.className = 'st-figure idle';
    figure.appendChild(h('b', { text: '–' }));
    meter.firstChild.style.width = '0';
    text.textContent = 'None of its units has reported yet. ' + (d.capacity ? 'It can generate up to ' + mw(d.capacity) + ' MW.' : '');
    return;
  }
  var storing = out <= -1, idle = Math.abs(out) < 1;
  figure.className = 'st-figure' + (idle ? ' idle' : '');
  figure.appendChild(h('b', { text: mw(Math.abs(out)) }));
  figure.appendChild(h('span', { text: storing ? 'MW being stored now' : 'MW now' }));
  var share = d.capacity ? Math.min(1, Math.abs(out) / d.capacity) : 0;
  meter.className = 'st-meter' + (storing ? ' storing' : '');
  meter.firstChild.style.width = (share * 100).toFixed(1) + '%';
  var parts = [];
  if (idle) parts.push(storage(d) ? 'Standing by, neither storing nor supplying.' : 'Not generating at the moment.');
  else if (d.capacity) parts.push(pct(share) + ' of its ' + mw(d.capacity) + ' MW capacity.');
  if (now.units > 1 || !idle) parts.push(now.running + ' of ' + plural(now.units, 'unit', 'units') + ' running' + (now.instructed ? ', ' + now.instructed + ' following an instruction from the grid operator' : '') + '.');
  if (idle && d.capacity) parts.push('It can ' + (storage(d) ? 'supply' : 'generate') + ' up to ' + mw(d.capacity) + ' MW.');
  text.textContent = parts.join(' ');
}

/* ---------- output chart: 24 hours at five minutes, or 7 days at half hours ---------- */

function drawChart(d) {
  var box = clear(byId('chart'));
  var week = state.range === 'week';
  var points = week ? d.week : d.points;
  var span = week ? 7 * 86400 : 86400, gapAfter = week ? 5400 : 900;
  if (!points || points.length < 2) {
    box.appendChild(h('p', { cls: 'empty', text: 'Output history builds up from when the dashboard started recording' + (d.since ? ' (' + dateFmt.format(new Date(d.since * 1000)) + ', ' + hm(d.since) + ')' : '') + '. Check back in a few minutes.' }));
    return;
  }
  var width = Math.max(280, box.clientWidth || 600), height = 230;
  var padL = 44, padR = 10, padT = 14, padB = 24;
  var end = Math.floor(Date.now() / 1000), start = end - span;
  var values = points.filter(function (p) { return p[0] >= start; }).map(function (p) { return p[1]; });
  var hi = Math.max(d.capacity || 0, Math.max.apply(null, values.concat([10])));
  var lo = Math.min(0, Math.min.apply(null, values.concat([0])));
  var step = niceStep(hi - lo, 4);
  hi = Math.ceil(hi / step) * step;
  lo = Math.floor(lo / step) * step;
  var x = function (t) { return padL + (t - start) / span * (width - padL - padR); };
  var y = function (v) { return padT + (hi - v) / (hi - lo || 1) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img',
    'aria-label': d.name + ' output over the last ' + (week ? '7 days' : '24 hours') + ', in megawatts' });
  for (var v = lo; v <= hi + 0.001; v += step) {
    svg.appendChild(s('line', { 'class': Math.abs(v) < 0.001 && lo < 0 ? 'zero-line' : 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 8, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: intFmt.format(v) }));
  }
  if (week) {
    // A label at each UK midnight; skip any too close to the edges to fit
    for (var t = Math.ceil(start / 1800) * 1800; t <= end; t += 1800) {
      if (hm(t) !== '00:00') continue;
      svg.appendChild(s('line', { 'class': 'grid-line', x1: x(t).toFixed(1), x2: x(t).toFixed(1), y1: padT, y2: height - padB }));
      if (x(t) < width - padR - 30) svg.appendChild(s('text', { 'class': 'axis-text', x: (x(t) + 5).toFixed(1), y: height - 6, text: dayFmt.format(new Date((t + 3600) * 1000)) }));
    }
  } else {
    var tick = width < 420 ? 21600 : 10800;
    for (var hour = Math.ceil(start / tick) * tick; hour <= end; hour += tick) {
      svg.appendChild(s('text', { 'class': 'axis-text', x: x(hour).toFixed(1), y: height - 6, 'text-anchor': 'middle', text: hm(hour) }));
    }
  }
  var segments = [], current = null, prev = null;
  points.forEach(function (p) {
    if (p[0] < start) return;
    if (!prev || p[0] - prev[0] > gapAfter) { current = []; segments.push(current); }
    current.push(p);
    prev = p;
  });
  segments.forEach(function (seg) {
    var line = seg.map(function (p, i) { return (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1); }).join('');
    if (seg.length === 1) line += 'l0.01 0';
    var area = line + 'L' + x(seg[seg.length - 1][0]).toFixed(1) + ' ' + y(0).toFixed(1) + 'L' + x(seg[0][0]).toFixed(1) + ' ' + y(0).toFixed(1) + 'Z';
    svg.appendChild(s('path', { d: area, 'class': 'area f-' + d.cat }));
    svg.appendChild(s('path', { d: line, 'class': 'series k-' + d.cat }));
  });
  if (d.capacity && d.capacity <= hi) {
    svg.appendChild(s('line', { 'class': 'cap-line', x1: padL, x2: width - padR, y1: y(d.capacity).toFixed(1), y2: y(d.capacity).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'cap-label', x: width - padR, y: (y(d.capacity) - 5).toFixed(1), 'text-anchor': 'end', text: 'Capacity ' + mw(d.capacity) + ' MW' }));
  }
  var cross = s('line', { 'class': 'xhair', y1: padT, y2: height - padB, visibility: 'hidden' });
  svg.appendChild(cross);
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var rect = svg.getBoundingClientRect();
    var px = (e.clientX - rect.left) * width / rect.width;
    var at = start + (px - padL) / (width - padL - padR) * span;
    var best = null;
    points.forEach(function (p) { if (!best || Math.abs(p[0] - at) < Math.abs(best[0] - at)) best = p; });
    if (!best || Math.abs(best[0] - at) > gapAfter) { tip.hide(); cross.setAttribute('visibility', 'hidden'); return; }
    cross.setAttribute('x1', x(best[0]).toFixed(1));
    cross.setAttribute('x2', x(best[0]).toFixed(1));
    cross.setAttribute('visibility', 'visible');
    var label = week ? dateFmt.format(new Date(best[0] * 1000)) + ', ' + hm(best[0]) + ' to ' + hm(best[0] + 1800) : hm(best[0]);
    var storing = best[1] <= -1;
    tip.show(label, [{ cls: 'b-' + d.cat, value: mw(Math.abs(best[1])) + ' MW', label: storing ? 'being stored' : week ? 'average output' : 'output' }],
      x(best[0]) * rect.width / width, 44);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); cross.setAttribute('visibility', 'hidden'); });
}

function renderStats(d) {
  var host = clear(byId('stats')), note = byId('stats-note');
  var st = d.stats && d.stats[state.range];
  var full = state.range === 'week' ? 168 : 24;
  note.textContent = '';
  if (!st) return;
  var tile = function (label, value, unit, more) {
    host.appendChild(h('div', null, [h('dt', { text: label }), h('dd', null, [value, unit ? h('small', { text: unit }) : null, more ? h('span', { cls: 'more', text: more }) : null])]));
  };
  var e = energy(st.mwh);
  tile(storage(d) ? 'Energy supplied' : 'Energy generated', e[0], e[1], st.stored_mwh ? energy(st.stored_mwh).join(' ') + ' stored' : null);
  if (num(st.factor)) tile('Capacity factor', pct(st.factor), null, 'average ' + mw(st.mean) + ' MW');
  tile('Highest output', mw(st.peak), 'MW', (state.range === 'week' ? dateFmt.format(new Date(st.peak_t * 1000)) + ', ' : 'at ') + hm(st.peak_t));
  tile(storage(d) ? 'Time supplying' : 'Time generating', pct(st.running), null, null);
  if (st.hours < full * 0.9) {
    note.textContent = 'Based on the ' + (st.hours >= 48 ? Math.round(st.hours / 24) + ' days' : Math.round(st.hours) + ' hours') + ' recorded so far' +
      (d.since ? ' (the dashboard began recording stations on ' + dateFmt.format(new Date(d.since * 1000)) + ')' : '') + ', not the full ' + (state.range === 'week' ? '7 days' : '24 hours') + '.';
  }
}

/* ---------- units, events, facts, neighbours ---------- */

function renderUnits(d) {
  var units = d.units || [];
  byId('units-panel').hidden = units.length < 2;
  if (units.length < 2) return;
  var body = clear(byId('units'));
  var biggest = Math.max.apply(null, units.map(function (u) { return Math.max(u.cap || 0, Math.abs(u.mw)); }).concat([1]));
  byId('units-sub').textContent = 'Each unit trades separately in the market' + (d.cat === 'wind' ? ', usually a section of the wind farm' : d.cat === 'gas' || d.cat === 'nuclear' ? ', usually one turbine and generator' : '') + '. Capacities are the units’ registered maximums.';
  units.forEach(function (u) {
    var on = Math.abs(u.mw) >= 1;
    body.appendChild(h('tr', { cls: on ? '' : 'off' }, [
      h('td', { cls: 'unit' }, [u.unit.replace(/^[A-Z0-9]_/, ''), u.instructed ? h('small', { text: 'Following a grid instruction' }) : null]),
      h('td', { cls: 'n', text: on ? mw(Math.abs(u.mw)) + ' MW' + (u.mw < 0 ? ' in' : '') : 'Off' }),
      h('td', { cls: 'n', text: u.cap ? mw(u.cap) + ' MW' : '–' }),
      h('td', { cls: 'bar' }, h('span', { cls: 'track' }, h('i', { style: 'width:' + (100 * Math.abs(u.mw) / biggest).toFixed(1) + '%' })))
    ]));
  });
}

function renderEvents(d) {
  var events = d.events || [];
  byId('events-panel').hidden = !events.length;
  var host = clear(byId('events'));
  events.forEach(function (e) {
    var body = h('div', null, [h('h3', null, renderParts(e.title))]);
    if (e.detail && e.detail.length) body.appendChild(h('p', null, renderParts(e.detail)));
    var links = [{ t: 'In the timeline', href: '/timeline#' + encodeURIComponent(e.id) }].concat(e.links || []);
    body.appendChild(h('p', { cls: 'links' }, links.map(function (l) { return h('a', { href: l.href, text: l.t }); })));
    host.appendChild(h('li', null, [
      h('time', { datetime: new Date(e.t * 1000).toISOString(), text: longDate.format(new Date(e.t * 1000)) + ', ' + hm(e.t) }), body
    ]));
  });
}

function renderFacts(d) {
  var host = clear(byId('facts')), f = d.facts || {};
  var row = function (label, value, small) {
    host.appendChild(h('div', null, [h('dt', { text: label }), h('dd', null, [value, small ? h('small', { text: small }) : null])]));
  };
  row('Type', d.kind);
  if (d.capacity) row('Capacity', mw(d.capacity) + ' MW');
  if (d.now && d.now.units) row('Units', String(d.now.units));
  if (f.turbines) row('Turbines', f.turbines + (f.turbine_mw ? ' of ' + f.turbine_mw + ' MW' : ''), f.turbine_height ? 'Up to ' + f.turbine_height + ' m to the blade tip' : null);
  if (f.opened) row('Opened', f.opened);
  if (f.owner) row('Owner', f.owner);
  if (f.operator && f.operator !== f.owner) row('Operator', f.operator);
  if (f.applicant && !f.owner && !f.operator) row('Developer', f.applicant, 'The planning applicant');
  if (f.wikipedia_url) row('Read more', h('a', { href: f.wikipedia_url, target: '_blank', rel: 'noopener', text: 'Wikipedia: ' + f.wikipedia }));
  var source = clear(byId('facts-source')), notes = [];
  if (f.owner || f.operator || f.opened) notes.push('Owner, operator and opening year are Wikidata’s record, which can lag behind a sale.');
  var developer = f.applicant && !f.owner && !f.operator;
  var fromRepd = [f.turbines ? 'turbines' : null, developer ? 'developer' : null, f.located === 'repd' ? 'location' : null].filter(Boolean);
  if (fromRepd.length) {
    var list = fromRepd.length > 1 ? fromRepd.slice(0, -1).join(', ') + ' and ' + fromRepd[fromRepd.length - 1] : fromRepd[0];
    notes.push(list.charAt(0).toUpperCase() + list.slice(1) + (fromRepd.length > 1 ? ' are' : ' is') + ' from the government’s Renewable Energy Planning Database.');
  }
  var where = { wikidata: 'Wikidata', site: 'the site it stands on', photo: 'where its photo was taken' }[f.located];
  if (where) notes.push('Located from ' + where + '.');
  else if (f.located !== 'repd') notes.push('Located from the Power Station Dictionary, which is sometimes several kilometres out.');
  source.textContent = notes.join(' ');
}

function renderNearby(d) {
  var host = clear(byId('nearby'));
  (d.nearby || []).forEach(function (n) {
    host.appendChild(h('li', null, h('a', { href: '/station?id=' + n.id }, [
      h('span', { cls: 'sw b-' + n.cat }),
      h('span', { cls: 'nm' }, [n.name, h('small', { text: n.km + ' km away' })]),
      h('span', { cls: 'mw', text: !num(n.mw) ? '' : Math.abs(n.mw) < 1 ? 'Idle' : mw(Math.abs(n.mw)) + ' MW' })
    ])));
  });
}

function renderPlace(d) {
  if (state.card) return;       // built once, so the photo isn't reloaded on every refresh
  state.card = placeCard({ id: d.id, n: d.name, c: d.cat, x: d.x, y: d.y, off: d.offshore ? 1 : 0 }, d.place) || {};
  if (state.card.figure) byId('photo').appendChild(state.card.figure);
  if (state.card.where) {
    byId('where').appendChild(h('h2', { cls: 'st-where-title', text: 'Where it is' }));
    byId('where').appendChild(state.card.where);
  }
}

/* ---------- wiring ---------- */

function onData(d) {
  if (d.unknown) {
    byId('missing').hidden = false;
    byId('name').textContent = 'Station not found';
    return;
  }
  state.data = d;
  byId('cols').hidden = false;
  byId('cols').style.setProperty('--k', 'var(--c-' + d.cat + ')');
  byId('kind').style.setProperty('--k', 'var(--c-' + d.cat + ')');
  renderHead(d);
  renderNow(d);
  drawChart(d);
  renderStats(d);
  renderUnits(d);
  renderEvents(d);
  renderFacts(d);
  renderNearby(d);
  renderPlace(d);
}

function updateHeader(poll) {
  var d = state.data;
  if (G.offline(poll)) G.setLive('bad', 'Can’t reach the dashboard server. Retrying.');
  else if (!d) G.setLive('', 'Connecting to the dashboard server…');
  else if (d.now && d.now.time) G.setLive('good', 'Live. Station data for ' + hm(d.now.time) + '.');
  else G.setLive('warn', 'Waiting for the first station data.');
}

byId('range').addEventListener('click', function (ev) {
  var b = ev.target.closest('button[data-range]');
  if (!b || !state.data) return;
  state.range = b.dataset.range;
  try { localStorage.setItem('gb-station-range', state.range); } catch (e) { /* ignore */ }
  syncRange();
  drawChart(state.data);
  renderStats(state.data);
});
function syncRange() {
  byId('range').querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', String(x.dataset.range === state.range)); });
}
syncRange();
if (window.ResizeObserver) {
  var last = 0;
  new ResizeObserver(function () {
    var w = byId('chart').clientWidth;
    if (state.data && w && Math.abs(w - last) > 4) { last = w; drawChart(state.data); }
  }).observe(byId('chart'));
}
document.addEventListener('gb:theme', function () { if (state.data) drawChart(state.data); });

if (!ID) onData({ unknown: true });
else state.poll = G.poll('/api/station?id=' + ID + '&full=1', { onData: onData, onTick: updateHeader, interval: function () { return 60; } });
