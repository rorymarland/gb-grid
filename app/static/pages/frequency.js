import G from '/static/js/gb.js';
import { renderParts } from '/static/js/summary.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num;
var params = new URLSearchParams(location.search);
var state = { range: 'auto', data: null, poll: null, event: params.get('event'), scrolled: false };
var HELD_ROWS = [
  ['dc', 'Dynamic Containment', 'dynamic-containment', 'DCL', 'DCH', 'Within a second, beyond ±0.2 Hz'],
  ['dm', 'Dynamic Moderation', 'dynamic-moderation', 'DML', 'DMH', 'Within a second, 0.1 to 0.2 Hz out'],
  ['dr', 'Dynamic Regulation', 'dynamic-regulation', 'DRL', 'DRH', 'Within ten seconds, all the time'],
  ['qr', 'Quick reserve', 'reserve', 'PQR', 'NQR', 'Within minutes, on instruction']
];

function hz(v, places) { return num(v) ? v.toFixed(places === undefined ? 3 : places) : '–'; }
function mhz(v) { return Math.round(Math.abs(v - 50) * 1000); }
function deviation(v) {
  var d = mhz(v);
  if (d === 0) return 'Exactly 50 Hz';
  return d + ' mHz ' + (v < 50 ? 'below' : 'above') + ' 50 Hz';
}
function stateOf(v) {
  var d = Math.abs(v - 50);
  if (d <= 0.05) return ['good', 'Steady'];
  if (d <= 0.1) return ['good', 'A little off centre'];
  if (d <= 0.2) return ['warn', 'Further out than usual'];
  return ['bad', 'Outside the normal limits'];
}
function meaning(v) {
  if (Math.abs(v - 50) <= 0.015) return 'Supply and demand are almost exactly balanced.';
  return v < 50 ? 'Demand is running slightly ahead of supply.' : 'Supply is running slightly ahead of demand.';
}
function duration(sec) {
  if (!num(sec)) return 'not yet';
  if (sec < 60) return sec + ' s';
  var m = Math.floor(sec / 60), r = sec % 60;
  return m + ' min' + (r ? ' ' + r + ' s' : '');
}

/* ---- the live reading and the limits scale ------------------------------ */

function renderReading() {
  var d = state.data, host = clear(byId('reading'));
  if (!d.latest) { host.appendChild(h('p', { cls: 'empty', text: 'Waiting for Elexon’s frequency readings.' })); return; }
  var v = d.latest.hz, st = stateOf(v);
  byId('lede').textContent = 'Right now ' + hz(v) + ' Hz. ' + meaning(v);
  host.appendChild(h('div', { cls: 'big' }, [
    h('b', { text: hz(v) }), h('span', { cls: 'unit', text: 'Hz' }),
    h('p', null, [h('span', { cls: 'pill' }, [h('i', { cls: 'dot ' + st[0] }), st[1]]), ' ', deviation(v) + ' at ' + G.hm(d.latest.t) + '.'])
  ]));
  host.appendChild(scale(v, d.today));
}

function scale(v, today) {
  var W = 640, H = 64, lo = 49.5, hi = 50.5, pad = 10;
  var x = function (f) { return pad + (G.clamp(f, lo, hi) - lo) / (hi - lo) * (W - 2 * pad); };
  var svg = s('svg', { 'class': 'scale', viewBox: '0 0 ' + W + ' ' + H, role: 'img',
    'aria-label': 'Frequency ' + hz(v) + ' Hz on a scale from 49.5 to 50.5 Hz. The normal range is 49.8 to 50.2 Hz.' });
  svg.appendChild(s('rect', { 'class': 'z-out', x: x(lo), y: 16, width: x(hi) - x(lo), height: 14, rx: 7 }));
  svg.appendChild(s('rect', { 'class': 'z-normal', x: x(49.8), y: 16, width: x(50.2) - x(49.8), height: 14 }));
  svg.appendChild(s('rect', { 'class': 'z-calm', x: x(49.9), y: 16, width: x(50.1) - x(49.9), height: 14 }));
  if (today && today.min && today.max) {
    svg.appendChild(s('line', { 'class': 'range-line', x1: x(today.min.hz), x2: x(today.max.hz), y1: 38, y2: 38 }));
    svg.appendChild(s('line', { 'class': 'range-line', x1: x(today.min.hz), x2: x(today.min.hz), y1: 35, y2: 41 }));
    svg.appendChild(s('line', { 'class': 'range-line', x1: x(today.max.hz), x2: x(today.max.hz), y1: 35, y2: 41 }));
  }
  [[49.5, '49.5'], [49.8, '49.8'], [50, '50 Hz'], [50.2, '50.2'], [50.5, '50.5']].forEach(function (p) {
    svg.appendChild(s('text', { 'class': 'axis-text', x: x(p[0]), y: 56, 'text-anchor': p[0] === lo ? 'start' : p[0] === hi ? 'end' : 'middle', text: p[1] }));
  });
  svg.appendChild(s('line', { 'class': 'mid', x1: x(50), x2: x(50), y1: 12, y2: 34 }));
  var mx = x(v);
  svg.appendChild(s('path', { 'class': 'needle', d: 'M' + (mx - 6) + ' 4 L' + (mx + 6) + ' 4 L' + mx + ' 14 Z' }));
  svg.appendChild(s('line', { 'class': 'needle-line', x1: mx, x2: mx, y1: 14, y2: 32 }));
  var wrap = h('div', { cls: 'scale-wrap' }, [svg, h('ul', { cls: 'legend' }, [
    h('li', null, [h('i', { cls: 'sw z-calm' }), 'Within 0.1 Hz']),
    h('li', null, [h('i', { cls: 'sw z-normal' }), 'Normal limits, ±0.2 Hz']),
    h('li', null, [h('i', { cls: 'sw z-out' }), 'Allowed only briefly, to ±0.5 Hz']),
    today && today.min ? h('li', null, [h('i', { cls: 'range-key' }), 'Range today, ' + hz(today.min.hz, 2) + ' to ' + hz(today.max.hz, 2) + ' Hz']) : null
  ])]);
  return wrap;
}

/* ---- the main chart ------------------------------------------------------ */

function renderChart() {
  var d = state.data, host = clear(byId('chart')), se = d.series;
  if (!se.t.length) { host.appendChild(h('p', { cls: 'empty', text: 'No readings for this range yet.' })); return; }
  var banded = !!se.min;
  var W = Math.max(320, host.clientWidth || 900), H = W < 600 ? 220 : 280, padL = 46, padR = 10, padT = 10, padB = 24;
  var t0 = se.t[0], t1 = se.t[se.t.length - 1];
  var lows = banded ? se.min : se.hz, highs = banded ? se.max : se.hz;
  var lo = Math.min(49.9, Math.min.apply(null, lows)), hi = Math.max(50.1, Math.max.apply(null, highs));
  var step = G.niceStep(hi - lo, 4); lo = Math.floor(lo / step + 1e-6) * step; hi = Math.ceil(hi / step - 1e-6) * step;
  var x = function (t) { return padL + (t - t0) / Math.max(1, t1 - t0) * (W - padL - padR); };
  var y = function (v) { return padT + (hi - v) / (hi - lo) * (H - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, height: H, role: 'img',
    'aria-label': 'Frequency from ' + G.when(t0) + ' to ' + G.hm(t1) + ', between ' + hz(Math.min.apply(null, lows), 2) + ' and ' + hz(Math.max.apply(null, highs), 2) + ' Hz' });
  // Shade outside the normal limits
  if (lo < 49.8) svg.appendChild(s('rect', { 'class': 'out-band', x: padL, y: y(49.8), width: W - padL - padR, height: y(lo) - y(49.8) }));
  if (hi > 50.2) svg.appendChild(s('rect', { 'class': 'out-band', x: padL, y: y(hi), width: W - padL - padR, height: y(50.2) - y(hi) }));
  for (var v = lo; v <= hi + 1e-9; v += step) {
    var yy = y(v).toFixed(1);
    svg.appendChild(s('line', { 'class': Math.abs(v - 50) < 1e-6 ? 'zero-line' : 'grid-line', x1: padL, x2: W - padR, y1: yy, y2: yy }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (+yy + 3.5).toFixed(1), 'text-anchor': 'end', text: v.toFixed(step < 0.1 ? 2 : 1) }));
  }
  // Time labels
  var span = t1 - t0, every = span <= 3600 ? 600 : span <= 6 * 3600 ? 3600 : span <= 86400 ? 4 * 3600 : 86400;
  for (var t = Math.ceil(t0 / every) * every; t <= t1; t += every) {
    svg.appendChild(s('text', { 'class': 'axis-text', x: x(t).toFixed(1), y: H - 6, 'text-anchor': 'middle', text: every >= 86400 ? G.date(t).replace(/ \d{4}$/, '') : G.hm(t) }));
  }
  if (banded) {
    var top = se.t.map(function (t, i) { return (i ? 'L' : 'M') + x(t).toFixed(1) + ' ' + y(se.max[i]).toFixed(1); }).join('');
    var bottom = se.t.slice().reverse().map(function (t, j) { var i = se.t.length - 1 - j; return 'L' + x(t).toFixed(1) + ' ' + y(se.min[i]).toFixed(1); }).join('');
    svg.appendChild(s('path', { 'class': 'f-band', d: top + bottom + 'Z' }));
    svg.appendChild(s('path', { 'class': 'f-line', d: path(se.t, se.mean, x, y) }));
  } else {
    svg.appendChild(s('path', { 'class': 'f-line', d: path(se.t, se.hz, x, y) }));
  }
  // Events in view
  (d.events || []).forEach(function (e) {
    if (e.t < t0 || e.t > t1) return;
    var f = e.freq || {}, ex = x(e.t);
    var a = s('a', { href: '#' + e.id, 'aria-label': plainParts(e.title) + ' at ' + G.hm(e.t) });
    a.appendChild(s('line', { 'class': 'ev-line', x1: ex, x2: ex, y1: padT, y2: H - padB }));
    a.appendChild(s('circle', { 'class': 'ev-dot ' + (f.direction || 'low'), cx: ex, cy: y(f.peak || 50), r: 5 }));
    svg.appendChild(a);
  });
  var cross = s('line', { 'class': 'xhair', x1: 0, x2: 0, y1: padT, y2: H - padB, visibility: 'hidden' });
  svg.appendChild(cross);
  host.appendChild(svg);
  var tip = G.makeTip(host);
  svg.addEventListener('pointermove', function (ev) {
    var r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * W / r.width;
    var tt = t0 + (px - padL) / (W - padL - padR) * (t1 - t0);
    var i = nearest(se.t, tt);
    if (i < 0) return;
    cross.setAttribute('x1', x(se.t[i])); cross.setAttribute('x2', x(se.t[i])); cross.setAttribute('visibility', 'visible');
    var rows = banded
      ? [{ value: hz(se.max[i]), label: 'Highest' }, { value: hz(se.mean[i]), label: 'Average', cls: 'b-line' }, { value: hz(se.min[i]), label: 'Lowest' }]
      : [{ value: hz(se.hz[i]) + ' Hz', label: deviation(se.hz[i]).replace(' 50 Hz', ''), cls: 'b-line' }];
    tip.show(banded ? G.when(se.t[i]) + ', 5 minutes' : G.when(se.t[i]), rows, ev.clientX - host.getBoundingClientRect().left, ev.clientY - host.getBoundingClientRect().top);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); cross.setAttribute('visibility', 'hidden'); });
}

function path(ts, vs, x, y) {
  var out = '', pen = false;
  for (var i = 0; i < ts.length; i++) {
    if (!num(vs[i]) || (i && ts[i] - ts[i - 1] > 120 && ts[i] - ts[i - 1] > 3 * (ts[1] - ts[0]))) { pen = false; if (!num(vs[i])) continue; }
    out += (pen ? 'L' : 'M') + x(ts[i]).toFixed(1) + ' ' + y(vs[i]).toFixed(1);
    pen = true;
  }
  return out;
}
function nearest(ts, t) {
  if (!ts.length) return -1;
  var lo = 0, hi = ts.length - 1;
  while (hi - lo > 1) { var mid = (lo + hi) >> 1; if (ts[mid] < t) lo = mid; else hi = mid; }
  return Math.abs(ts[lo] - t) <= Math.abs(ts[hi] - t) ? lo : hi;
}
function plainParts(parts) { return (parts || []).map(function (p) { return typeof p === 'string' ? p : p.t; }).join(''); }

/* ---- how steady ---------------------------------------------------------- */

function renderSteady() {
  var d = state.data, f = d.today || d.last24, host = clear(byId('figures'));
  if (!f) { host.appendChild(h('p', { cls: 'empty', text: 'Waiting for readings.' })); return; }
  function fig(value, label, note) {
    host.appendChild(h('div', null, [h('dt', { text: label }), h('dd', null, [h('b', { text: value }), note ? h('small', { text: note }) : null])]));
  }
  fig(f.sd_mhz + ' mHz', 'Typical distance from 50 Hz', 'The standard deviation');
  fig(f.within_005 + '%', 'Of readings within 0.05 Hz');
  fig(f.outside_02_s ? duration(f.outside_02_s) : 'None', 'Time outside ±0.2 Hz', f.outside_02_s ? 'In 15-second readings' : 'Every reading inside the normal limits');
  fig(f.outside_01 + '%', 'Of readings more than 0.1 Hz out');
  fig(hz(f.min.hz, 3), 'Lowest reading', 'At ' + G.hm(f.min.t));
  fig(hz(f.max.hz, 3), 'Highest reading', 'At ' + G.hm(f.max.t));
  renderHistogram();
}

function renderHistogram() {
  var hg = state.data.histogram, host = clear(byId('hist'));
  var total = hg.counts.reduce(function (a, b) { return a + b; }, 0);
  if (!total) return;
  var W = Math.max(280, host.clientWidth || 480), H = 170, padB = 22, padT = 8;
  var n = hg.counts.length, bw = W / n, max = Math.max.apply(null, hg.counts);
  var svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, height: H, role: 'img', 'aria-label': 'How the last 24 hours of readings spread between 49.7 and 50.3 Hz: most sit within 0.05 Hz of 50.' });
  hg.counts.forEach(function (c, i) {
    var f = hg.from + (i + 0.5) * hg.width, bh = c / max * (H - padB - padT);
    var cls = Math.abs(f - 50) > 0.2 ? 'hb out' : Math.abs(f - 50) > 0.1 ? 'hb mid' : 'hb';
    var r = s('rect', { 'class': cls, x: (i * bw + 0.5).toFixed(1), y: (H - padB - bh).toFixed(1), width: Math.max(1, bw - 1).toFixed(1), height: bh.toFixed(1) });
    r.appendChild(s('title', { text: (hg.from + i * hg.width).toFixed(2) + ' to ' + (hg.from + (i + 1) * hg.width).toFixed(2) + ' Hz: ' + (100 * c / total).toFixed(1) + '% of readings' }));
    svg.appendChild(r);
  });
  [49.8, 49.9, 50, 50.1, 50.2].forEach(function (f) {
    var xx = (f - hg.from) / (hg.width * n) * W;
    svg.appendChild(s('text', { 'class': 'axis-text', x: xx.toFixed(1), y: H - 5, 'text-anchor': 'middle', text: f === 50 ? '50 Hz' : f.toFixed(1) }));
  });
  host.appendChild(svg);
  host.appendChild(h('p', { cls: 'note', text: 'Last 24 hours, in 0.01 Hz steps. The narrower the peak, the steadier the grid.' }));
}

/* ---- inertia and response ------------------------------------------------- */

function renderInertia() {
  var d = state.data.inertia, host = clear(byId('inertia'));
  if (!d) { host.appendChild(h('p', { cls: 'empty', text: 'Waiting for NESO’s inertia figures, which are published after the day.' })); return; }
  host.appendChild(h('div', { cls: 'big small' }, [h('b', { text: G.int(d.gvas) }), h('span', { cls: 'unit', text: 'GVA·s' }),
    h('p', { text: 'Latest published half hour: ' + G.when(d.t) + '. Over the week to then it ranged from ' + G.int(d.low) + ' to ' + G.int(d.high) + ' GVA·s.' })]));
  var se = d.series, W = Math.max(280, host.clientWidth || 480), H = 120, padL = 36, padB = 20, padT = 6;
  var lo = Math.floor(d.low / 20) * 20, hi = Math.ceil(d.high / 20) * 20 || 1;
  var t0 = se.t[0], t1 = se.t[se.t.length - 1];
  var x = function (t) { return padL + (t - t0) / Math.max(1, t1 - t0) * (W - padL - 6); };
  var y = function (v) { return padT + (hi - v) / Math.max(1, hi - lo) * (H - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, height: H, role: 'img', 'aria-label': 'Inertia over the last week, between ' + d.low + ' and ' + d.high + ' GVA·s' });
  [lo, (lo + hi) / 2, hi].forEach(function (v) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: W - 6, y1: y(v), y2: y(v) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: y(v) + 3.5, 'text-anchor': 'end', text: Math.round(v) }));
  });
  for (var t = Math.ceil(t0 / 86400) * 86400; t <= t1; t += 86400) svg.appendChild(s('text', { 'class': 'axis-text', x: x(t), y: H - 5, 'text-anchor': 'middle', text: G.day(t).slice(0, 3) }));
  svg.appendChild(s('path', { 'class': 'i-line', d: path(se.t, se.v, x, y) }));
  host.appendChild(h('div', { cls: 'chart' }, [svg]));
  var note = [G.term('inertia', 'Inertia'), ' is highest when gas, nuclear and biomass plants are running and lowest on windy, sunny nights with plenty of imports.'];
  if (num(d.market)) note.push(' Without NESO’s own actions to keep enough spinning, it would have been ' + G.int(d.market) + ' GVA·s.');
  host.appendChild(h('p', { cls: 'note' }, note));
}

function renderHeld() {
  var r = state.data.response, host = clear(byId('held'));
  if (!r || !Object.keys(r.held).length) { host.appendChild(h('p', { cls: 'empty', text: 'Waiting for NESO’s auction results.' })); return; }
  var max = Math.max.apply(null, Object.keys(r.held).map(function (k) { return r.held[k]; }).concat([1]));
  var table = h('table', { cls: 'held' }, [h('thead', null, [h('tr', null, [h('th', { text: 'Service' }), h('th', { cls: 'num', text: 'Low' }), h('th', { cls: 'num', text: 'High' })])])]);
  var body = h('tbody');
  HELD_ROWS.forEach(function (row) {
    var low = r.held[row[3]], high = r.held[row[4]];
    if (!num(low) && !num(high)) return;
    body.appendChild(h('tr', null, [
      h('th', { scope: 'row' }, [G.term(row[2], row[1]), h('small', { text: row[5] })]),
      cell(low, max, 'low'), cell(high, max, 'high')
    ]));
  });
  table.appendChild(body);
  host.appendChild(table);
  if (r.from) host.appendChild(h('p', { cls: 'note', text: 'Block ' + G.hm(r.from) + ' to ' + G.hm(r.to) + '. Quick reserve’s columns are raising and lowering output. Figures in MW.' }));
  var dc = r.held.DCL;
  if (num(dc)) host.appendChild(h('p', { cls: 'note', text: 'NESO sizes its response to cover the largest single loss it expects at the time, usually a big nuclear unit or interconnector of around 1,000 to 1,400 MW.' }));
}
function cell(v, max, dir) {
  if (!num(v)) return h('td', { cls: 'num', text: '–' });
  return h('td', { cls: 'num' }, [h('span', { cls: 'bar ' + dir, style: 'width:' + Math.max(2, 100 * v / max).toFixed(1) + '%' }), h('b', { text: G.int(v) })]);
}

/* ---- events ---------------------------------------------------------------- */

function renderEvents() {
  var d = state.data, host = clear(byId('events'));
  var list = (d.events || []).slice();
  if (d.event && !list.some(function (e) { return e.id === d.event.id; })) list.unshift(Object.assign({ freq: d.event.data }, d.event));
  if (!list.length) { host.appendChild(h('p', { cls: 'empty', text: 'No sudden frequency changes in the last week. Events appear when frequency moves 0.08 Hz or more within 30 seconds, or crosses ±0.2 Hz.' })); return; }
  var shown = state.all ? list : list.slice(0, 5);
  if (state.event && !shown.some(function (e) { return e.id === state.event; })) shown = list;
  shown.forEach(function (e) {
    var f = e.freq || {}, card = h('article', { cls: 'fev' + (e.id === state.event ? ' selected' : ''), id: e.id });
    card.appendChild(h('div', { cls: 'fev-chart' }, [miniChart(f)]));
    var facts = h('dl', { cls: 'fev-facts' }, [
      h('div', null, [h('dt', { text: 'Started' }), h('dd', { text: G.when(e.t) })]),
      h('div', null, [h('dt', { text: f.direction === 'high' ? 'Highest reading' : 'Lowest reading' }), h('dd', { text: hz(f.peak, 3) + ' Hz' })]),
      h('div', null, [h('dt', { text: 'Change' }), h('dd', { text: (f.change > 0 ? '+' : '') + Math.round(f.change * 1000) + ' mHz' })]),
      h('div', null, [h('dt', { text: 'Back to normal' }), h('dd', { text: duration(f.recovery) })])
    ]);
    var links = h('p', { cls: 'fev-links' }, (e.links || []).filter(function (l) { return l.href.indexOf('/frequency') !== 0; }).map(function (l) { return h('a', { href: l.href, text: l.t }); }));
    card.appendChild(h('div', { cls: 'fev-text' }, [
      h('h3', null, renderParts(e.title)), h('p', null, renderParts(e.detail)), facts, links
    ]));
    host.appendChild(card);
  });
  if (shown.length < list.length) {
    var more = h('button', { type: 'button', cls: 'more-btn', text: 'Show all ' + list.length + ' events from the last week' });
    more.addEventListener('click', function () { state.all = true; renderEvents(); });
    host.appendChild(more);
  }
  if (state.event && !state.scrolled) {
    var el = byId(state.event);
    if (el) { state.scrolled = true; requestAnimationFrame(function () { el.scrollIntoView({ block: 'center' }); }); }
  }
}

function miniChart(f) {
  var se = f.series || [], W = 260, H = 120, padL = 34, padB = 18, padT = 8;
  if (se.length < 3) return h('p', { cls: 'empty', text: 'No readings kept.' });
  var t0 = se[0][0], t1 = se[se.length - 1][0];
  var vs = se.map(function (p) { return p[1]; });
  var lo = Math.min(49.9, Math.min.apply(null, vs)), hi = Math.max(50.1, Math.max.apply(null, vs));
  var step = G.niceStep(hi - lo, 3); lo = Math.floor(lo / step + 1e-6) * step; hi = Math.ceil(hi / step - 1e-6) * step;
  var x = function (t) { return padL + (t - t0) / Math.max(1, t1 - t0) * (W - padL - 4); };
  var y = function (v) { return padT + (hi - v) / (hi - lo) * (H - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': 'Frequency from five minutes before to fifteen minutes after' });
  if (lo < 49.8) svg.appendChild(s('rect', { 'class': 'out-band', x: padL, y: y(49.8), width: W - padL - 4, height: y(lo) - y(49.8) }));
  if (hi > 50.2) svg.appendChild(s('rect', { 'class': 'out-band', x: padL, y: y(hi), width: W - padL - 4, height: y(50.2) - y(hi) }));
  for (var v = lo; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': Math.abs(v - 50) < 1e-6 ? 'zero-line' : 'grid-line', x1: padL, x2: W - 4, y1: y(v), y2: y(v) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 5, y: y(v) + 3.5, 'text-anchor': 'end', text: v.toFixed(step < 0.1 ? 2 : 1) }));
  }
  [0, 300, 600, 900].forEach(function (dt) {
    var t = t0 + 300 + dt - 300;
    if (t > t1) return;
    svg.appendChild(s('text', { 'class': 'axis-text', x: x(t), y: H - 4, 'text-anchor': 'middle', text: dt === 300 ? 'start' : (dt - 300 > 0 ? '+' : '') + (dt - 300) / 60 + ' min' }));
  });
  svg.appendChild(s('path', { 'class': 'f-line', d: path(se.map(function (p) { return p[0]; }), vs, x, y) }));
  if (num(f.peak_t)) svg.appendChild(s('circle', { 'class': 'ev-dot ' + (f.direction || 'low'), cx: x(f.peak_t), cy: y(f.peak), r: 4 }));
  return svg;
}

/* ---- polling ------------------------------------------------------------- */

function chosenRange() { return state.range === 'auto' ? '1h' : state.range; }
function url() { return '/api/frequency?range=' + chosenRange() + (state.event ? '&event=' + encodeURIComponent(state.event) : ''); }

function onData(d) {
  state.data = d;
  renderReading();
  renderChart();
  renderSteady();
  renderInertia();
  renderHeld();
  renderEvents();
}
function updateHeader(poll) {
  var d = state.data;
  if (G.offline(poll)) G.setLive('bad', 'Can’t reach the dashboard server. Retrying.');
  else if (!d) G.setLive('', 'Connecting to the dashboard server…');
  else if (!d.latest) G.setLive('warn', 'Waiting for frequency readings…');
  else if (Date.now() / 1000 - d.latest.t > 600) G.setLive('warn', 'Frequency readings are ' + G.ago(Date.now() / 1000 - d.latest.t) + ' old.');
  else G.setLive('good', 'Live. Latest reading ' + G.ago(Date.now() / 1000 - d.latest.t) + '.');
}

byId('range').addEventListener('click', function (ev) {
  var b = ev.target.closest('button[data-range]');
  if (!b) return;
  state.range = b.dataset.range;
  byId('range').querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
  state.poll.setUrl(url());
});
window.addEventListener('gb:width', function () { if (state.data) { renderChart(); renderHistogram(); renderInertia(); } });
state.poll = G.poll(url(), { onData: onData, onTick: updateHeader, interval: function () { return 15; } });
