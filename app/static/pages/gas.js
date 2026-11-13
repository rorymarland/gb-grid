/* Gas: /gas. National Gas's flows, linepack, allocations and price (gridlive/gas.py) */
import G from '/static/js/gb.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num, niceStep = G.niceStep, makeTip = G.makeTip;
var state = { data: null, mode: 'uses' };
try {
  var saved = localStorage.getItem('gb-gas-year');
  if (saved === 'uses' || saved === 'sources') state.mode = saved;
} catch (e) { /* storage unavailable */ }

var SOURCES = { norway: 'Norway, through Langeled', lng: 'LNG, by ship', northsea: 'North Sea fields', continent: 'The continent', storage: 'Storage', other: 'Other' };
var USES = { ldz: 'Homes and businesses', power: 'Power stations', industrial: 'Industry', exports: 'Exports', storage: 'Into storage' };
var dayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: G.TZ });
var shortDay = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: G.TZ });
var monthFmt = new Intl.DateTimeFormat('en-GB', { month: 'short', timeZone: 'UTC' });
function fromIso(text) { return new Date(text + 'T12:00:00Z'); }

function mcm(v) { return num(v) ? v.toFixed(v >= 100 ? 0 : 1) : '–'; }
function gw(mcmd, cv) { return mcmd * 1e6 * cv * 1e6 / 86400 / 1e9; }
function pct(v) { return !num(v) ? '–' : v > 0 && v < 0.005 ? 'under 1%' : Math.round(v * 100) + '%'; }
function pence(v) { return num(v) ? v.toFixed(2) + 'p' : '–'; }
function figure(dl, label, value, unit, note) {
  dl.appendChild(h('div', null, [h('dt', { text: label }), h('dd', null, [value, unit ? h('small', { text: unit }) : null,
    note ? h('span', { cls: 'more', text: note }) : null])]));
}

/* ---------- now ---------- */

function renderNow(d) {
  var dl = clear(byId('figures')), uses = {}, total = d.demand;
  d.uses.forEach(function (u) { uses[u[0]] = u[1]; });
  figure(dl, 'Gas used now', mcm(d.demand), 'mcm/d', 'Energy flowing at about ' + G.int(gw(d.demand, d.cv)) + ' GW');
  if (num(uses.power)) figure(dl, 'Power stations', mcm(uses.power), 'mcm/d', pct(uses.power / total) + ' of the gas being used');
  var t = d.today, change = num(t.linepack_open) ? d.linepack - t.linepack_open : null;
  figure(dl, 'Linepack', mcm(d.linepack), 'mcm', change === null ? null : (Math.abs(change) < 0.5 ? 'About where the gas day began' :
    (change > 0 ? 'Up ' : 'Down ') + mcm(Math.abs(change)) + ' mcm since 05:00') + (num(t.pclp) ? '; forecast to close at ' + mcm(t.pclp) : ''));
  if (d.sap) {
    figure(dl, 'Wholesale gas', pence(d.sap[1]), 'per kWh', 'The System Average Price, ' + dayFmt.format(fromIso(d.sap[0])) +
      (num(d.home_rate) ? '. Your unit rate is ' + pence(d.home_rate) + ', with VAT' : ''));
  }
}

/* ---------- coming in, going out ---------- */

function barList(host, items, names, total, prefix) {
  clear(host);
  var top = Math.max.apply(null, items.map(function (i) { return i[1]; }).concat([1]));
  items.forEach(function (i) {
    host.appendChild(h('li', null, [
      h('span', { cls: 'nm' }, [h('i', { cls: 'sw ' + prefix + i[0] }), names[i[0]] || i[0]]),
      h('span', { cls: 'track' }, [h('i', { cls: prefix + i[0], style: 'width:' + (i[1] / top * 100).toFixed(1) + '%' })]),
      h('span', { cls: 'val' }, [mcm(i[1]), h('small', { text: total ? pct(i[1] / total) : '' })])
    ]));
  });
}

function renderFlows(d) {
  var supply = d.sources.reduce(function (a, s2) { return a + s2[1]; }, 0);
  var balance = d.supply - d.demand;
  byId('flow-sub').textContent = 'Right now, in million cubic metres a day (mcm/d): ' + mcm(d.supply) + ' coming in and ' + mcm(d.demand) + ' going out, so the pipes are ' +
    (Math.abs(balance) < 2 ? 'holding steady.' : balance > 0 ? 'filling at ' + mcm(balance) + ' mcm/d.' : 'emptying at ' + mcm(-balance) + ' mcm/d.') +
    ' As at ' + G.hm(d.t) + '.';
  barList(byId('sources'), d.sources.filter(function (x) { return x[1] > 0.05; }), SOURCES, supply, 'gs-src-');
  var usesTotal = d.uses.reduce(function (a, u) { return a + u[1]; }, 0);
  barList(byId('uses'), d.uses.filter(function (x) { return x[1] > 0.05; }), USES, usesTotal, 'gs-use-');
  var p = d.power, note = byId('power');
  if (p && num(p.efficiency)) {
    note.textContent = 'Power stations are taking ' + mcm(p.gas_mcmd) + ' mcm/d, about ' + p.thermal_gw.toFixed(1) + ' GW of gas, and gas-fired stations are making ' +
      p.elec_gw.toFixed(1) + ' GW of electricity: roughly ' + pct(p.efficiency) + ' of the energy in the gas. A rough guide only, as some gas stations take their gas from local networks and flows are measured a few minutes apart.';
  } else note.textContent = '';
}

/* ---------- the gas day ---------- */

function lineChart(host, rows, series, opts) {
  clear(host);
  rows = rows.filter(function (r) { return series.some(function (sr) { return num(r[sr.i]); }); });
  if (rows.length < 2) { host.appendChild(h('p', { cls: 'gs-empty', text: 'Filling in as the gas day goes on.' })); return; }
  var width = Math.max(300, host.clientWidth || 900), height = opts.height || 220, padL = 64, padR = 8, padT = 10, padB = 24;
  var start = opts.start, end = start + 86400;
  var values = [];
  rows.forEach(function (r) { series.forEach(function (sr) { if (num(r[sr.i])) values.push(r[sr.i]); }); });
  if (opts.extra) values.push.apply(values, opts.extra);
  var lo = opts.zero ? 0 : Math.min.apply(null, values), top = Math.max.apply(null, values);
  var step = niceStep(top - lo || 1, 4), hi = Math.ceil(top / step) * step;
  lo = Math.floor(lo / step) * step;
  if (hi === lo) hi = lo + step;
  var x = function (t) { return padL + (t - start) / (end - start) * (width - padL - padR); };
  var y = function (v) { return padT + (hi - v) / (hi - lo) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': opts.label });
  for (var v = lo; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: (step < 1 ? v.toFixed(step < 0.1 ? 2 : 1) : G.int(v)) + ' ' + opts.unit }));
  }
  for (var hr = 0, every = width < 600 ? 6 : 3; hr <= 24; hr += every) {
    var tt = start + hr * 3600;
    svg.appendChild(s('text', { 'class': 'axis-text', x: x(tt).toFixed(1), y: height - 6, 'text-anchor': hr === 0 ? 'start' : hr === 24 ? 'end' : 'middle', text: G.hm(tt) }));
  }
  if (opts.target) {
    svg.appendChild(s('line', { 'class': 'gs-target', x1: padL, x2: width - padR, y1: y(opts.target).toFixed(1), y2: y(opts.target).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text gs-target-label', x: width - padR - 4, y: (y(opts.target) - 5).toFixed(1), 'text-anchor': 'end', text: opts.targetLabel }));
  }
  series.forEach(function (sr) {
    var d = '';
    rows.forEach(function (r, i) {
      if (!num(r[sr.i])) return;
      var gap = i && rows[i][0] - rows[i - 1][0] > 900;
      d += (d && !gap ? 'L' : 'M') + x(r[0]).toFixed(1) + ' ' + y(r[sr.i]).toFixed(1);
    });
    svg.appendChild(s('path', { 'class': sr.cls, d: d }));
  });
  var cross = s('line', { 'class': 'xhair', y1: padT, y2: height - padB, visibility: 'hidden' });
  svg.appendChild(cross);
  host.appendChild(svg);
  var tip = makeTip(host);
  svg.addEventListener('pointermove', function (e) {
    var b = svg.getBoundingClientRect(), at = start + ((e.clientX - b.left) * width / b.width - padL) / (width - padL - padR) * (end - start);
    var best = null;
    rows.forEach(function (r) { if (!best || Math.abs(r[0] - at) < Math.abs(best[0] - at)) best = r; });
    if (!best || Math.abs(best[0] - at) > 1200) { tip.hide(); cross.setAttribute('visibility', 'hidden'); return; }
    var cx = x(best[0]);
    cross.setAttribute('x1', cx.toFixed(1)); cross.setAttribute('x2', cx.toFixed(1)); cross.setAttribute('visibility', 'visible');
    tip.show(G.hm(best[0]), series.filter(function (sr) { return num(best[sr.i]); }).map(function (sr) {
      return { cls: sr.cls + '-sw', value: mcm(best[sr.i]) + ' ' + opts.unit, label: sr.name.toLowerCase() };
    }), cx * b.width / width, 30);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); cross.setAttribute('visibility', 'hidden'); });
}

function renderDay(d) {
  var t = d.today, rows = t.rows;
  byId('day-sub').textContent = 'The gas day of ' + dayFmt.format(fromIso(d.gas_day)) + ' runs from 05:00 to 05:00. Flows in mcm/d, every few minutes' +
    (num(t.demand_fc) ? '; National Gas forecasts ' + mcm(t.demand_fc) + ' mcm of demand for the whole day' : '') +
    (num(t.temp_normal) ? ', with a seasonal normal temperature of ' + t.temp_normal.toFixed(1) + '°C.' : '.');
  var series = [{ i: 1, cls: 'gs-in', name: 'Coming in' }, { i: 2, cls: 'gs-out', name: 'Going out' }, { i: 3, cls: 'gs-pw', name: 'To power stations' }];
  lineChart(byId('day-chart'), rows, series, { start: d.day_start, unit: 'mcm/d', zero: true, label: 'Gas coming in, going out and to power stations through the gas day' });
  var legend = clear(byId('day-legend'));
  series.forEach(function (sr) { legend.appendChild(h('li', null, [h('i', { cls: 'sw line ' + sr.cls + '-sw' }), sr.name])); });
  var open = t.linepack_open;
  byId('pack-sub').textContent = 'The gas in the pipes, in million cubic metres' + (num(open) ? ': ' + mcm(open) + ' at 05:00' : '') +
    (num(t.pclp) ? ', and National Gas’s latest prediction for where it closes.' : '.');
  lineChart(byId('pack-chart'), rows, [{ i: 4, cls: 'gs-lp', name: 'Linepack' }], {
    start: d.day_start, unit: 'mcm', height: 170, label: 'Linepack through the gas day', target: t.pclp, targetLabel: 'Predicted close ' + mcm(t.pclp),
    extra: num(t.pclp) ? [t.pclp] : []
  });
}

/* ---------- the last year ---------- */

function monthTick(d, slot) {
  // The first of each month, or of every other month when a month is narrower than its label
  return d.slice(8) === '01' && (slot * 30 >= 36 || Number(d.slice(5, 7)) % 2 === 1);
}

var YEAR_USES = [['ndm', 'Homes and small businesses', 'gs-y-ndm'], ['power', 'Power stations', 'gs-y-power'], ['dmc', 'Larger users on local networks', 'gs-y-dmc'],
  ['exports', 'Exports', 'gs-y-exports'], ['industrial', 'Industry', 'gs-y-industrial'], ['storage_in', 'Into storage', 'gs-y-storage-in']];
var YEAR_SOURCES = [['fields', 'North Sea and Norway', 'gs-y-fields'], ['lng', 'LNG', 'gs-y-lng'], ['imports', 'The continent', 'gs-y-imports'],
  ['storage_out', 'Out of storage', 'gs-y-storage-out']];

function yearRows(d) {
  var cols = d.year_columns;
  return d.year.map(function (r) {
    var o = {};
    cols.forEach(function (c, i) { o[c] = r[i]; });
    // Where it came from: what was used, less what LNG, the continent and storage supplied (GWh)
    var used = ['ndm', 'dmc', 'industrial', 'power', 'storage_in', 'exports'].reduce(function (a, k) { return a + (o[k] || 0); }, 0);
    o.fields = Math.max(0, used - (o.lng || 0) - (o.imports || 0) - (o.storage_out || 0));
    o.used = used;
    return o;
  });
}

function renderYear(d) {
  Array.prototype.forEach.call(byId('year-mode').querySelectorAll('button'), function (b) { b.setAttribute('aria-pressed', String(b.dataset.mode === state.mode)); });
  var rows = yearRows(d), host = clear(byId('year-chart')), legend = clear(byId('year-legend'));
  var parts = state.mode === 'uses' ? YEAR_USES : YEAR_SOURCES;
  if (rows.length < 2) { byId('year-sub').textContent = 'Downloading a year of National Gas’s daily figures; this takes a few minutes the first time.'; return; }
  var last = rows[rows.length - 1], sum = function (k) { return rows.reduce(function (a, r) { return a + (r[k] || 0); }, 0); };
  var total = sum('used');
  byId('year-sub').textContent = state.mode === 'uses'
    ? 'Each gas day’s use, in gigawatt hours, to ' + dayFmt.format(fromIso(last.d)) + ' (National Gas settles each day two days later). Over these ' + rows.length + ' days, power stations took ' + pct(sum('power') / total) + ' and homes and small businesses ' + pct(sum('ndm') / total) + '.'
    : 'Each gas day’s supply, in gigawatt hours. LNG, the continent and storage are as National Gas publishes them; the North Sea and Norway are the rest. Over these ' + rows.length + ' days, LNG supplied ' + pct(sum('lng') / total) + '.';
  var width = Math.max(300, host.clientWidth || 900), height = 240, padL = 72, padR = 6, padT = 10, padB = 24;
  var top = Math.max.apply(null, rows.map(function (r) { return parts.reduce(function (a, p) { return a + (r[p[0]] || 0); }, 0); }));
  var step = niceStep(top, 4), hi = Math.ceil(top / step) * step, n = rows.length, slot = (width - padL - padR) / n;
  var y = function (v) { return padT + (1 - v / hi) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': state.mode === 'uses' ? 'Where gas went each day for the last year' : 'Where gas came from each day for the last year' });
  for (var v = 0; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: G.int(v) + ' GWh' }));
  }
  var paths = {};
  parts.forEach(function (p) { paths[p[0]] = ''; });
  rows.forEach(function (r, i) {
    var x0 = padL + i * slot, base = 0;
    parts.forEach(function (p) {
      var val = r[p[0]] || 0;
      if (val <= 0) return;
      paths[p[0]] += 'M' + x0.toFixed(2) + ' ' + y(base + val).toFixed(1) + 'h' + Math.max(0.6, slot - (slot > 3 ? 0.6 : 0)).toFixed(2) + 'V' + y(base).toFixed(1) + 'h-' + Math.max(0.6, slot - (slot > 3 ? 0.6 : 0)).toFixed(2) + 'Z';
      base += val;
    });
    if (monthTick(r.d, slot)) svg.appendChild(s('text', { 'class': 'axis-text', x: (x0 + slot / 2).toFixed(1), y: height - 6, 'text-anchor': 'middle', text: monthFmt.format(fromIso(r.d)) }));
  });
  parts.forEach(function (p) { svg.appendChild(s('path', { 'class': p[2], d: paths[p[0]] })); });
  host.appendChild(svg);
  var tip = makeTip(host);
  svg.addEventListener('pointermove', function (e) {
    var b = svg.getBoundingClientRect(), i = Math.floor(((e.clientX - b.left) * width / b.width - padL) / slot);
    if (i < 0 || i >= n) { tip.hide(); return; }
    var r = rows[i];
    tip.show(shortDay.format(fromIso(r.d)), parts.slice().reverse().map(function (p) {
      return { cls: p[2], value: G.int(r[p[0]] || 0) + ' GWh', label: p[1] };
    }).concat([{ sep: true }, { value: G.int(r.used) + ' GWh', label: 'in all' }]), (padL + (i + 0.5) * slot) * b.width / width, 40);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); });
  parts.forEach(function (p) { legend.appendChild(h('li', null, [h('i', { cls: 'sw ' + p[2] }), p[1]])); });
  renderSap(d, rows);
}

function renderSap(d, rows) {
  var host = clear(byId('sap-chart')), pts = rows.filter(function (r) { return num(r.sap); });
  if (pts.length < 2) return;
  var mean = pts.reduce(function (a, r) { return a + r.sap; }, 0) / pts.length;
  var hiR = pts.reduce(function (a, r) { return r.sap > a.sap ? r : a; }), loR = pts.reduce(function (a, r) { return r.sap < a.sap ? r : a; });
  byId('sap-sub').textContent = 'The System Average Price each gas day, pence per kWh. Over the year it averaged ' + pence(mean) + ', from ' + pence(loR.sap) + ' on ' +
    shortDay.format(fromIso(loR.d)) + ' to ' + pence(hiR.sap) + ' on ' + shortDay.format(fromIso(hiR.d)) + '.' + (num(d.home_rate) ? ' Your unit rate now is ' + pence(d.home_rate) + ', with VAT.' : '');
  var width = Math.max(300, host.clientWidth || 900), height = 160, padL = 72, padR = 6, padT = 10, padB = 24, n = rows.length, slot = (width - padL - padR) / n;
  var top = Math.max.apply(null, pts.map(function (r) { return r.sap; }).concat(num(d.home_rate) ? [d.home_rate] : []));
  var step = niceStep(top, 3), hi = Math.ceil(top / step) * step;
  var y = function (v) { return padT + (1 - v / hi) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': 'The System Average Price each day for the last year' });
  for (var v = 0; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: v.toFixed(step < 1 ? 1 : 0) + 'p/kWh' }));
  }
  if (num(d.home_rate)) {
    svg.appendChild(s('line', { 'class': 'gs-target', x1: padL, x2: width - padR, y1: y(d.home_rate).toFixed(1), y2: y(d.home_rate).toFixed(1) }));
  }
  var line = '';
  rows.forEach(function (r, i) {
    if (!num(r.sap)) return;
    line += (line ? 'L' : 'M') + (padL + (i + 0.5) * slot).toFixed(1) + ' ' + y(r.sap).toFixed(1);
    if (monthTick(r.d, slot)) svg.appendChild(s('text', { 'class': 'axis-text', x: (padL + (i + 0.5) * slot).toFixed(1), y: height - 6, 'text-anchor': 'middle', text: monthFmt.format(fromIso(r.d)) }));
  });
  svg.appendChild(s('path', { 'class': 'gs-sap-line', d: line }));
  host.appendChild(svg);
  var legend = clear(byId('sap-legend'));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw line gs-lp-sw' }), 'System Average Price']));
  if (num(d.home_rate)) legend.appendChild(h('li', null, [h('i', { cls: 'sw line gs-target-sw' }), 'Your unit rate, with VAT']));
  var tip = makeTip(host);
  svg.addEventListener('pointermove', function (e) {
    var b = svg.getBoundingClientRect(), i = Math.floor(((e.clientX - b.left) * width / b.width - padL) / slot);
    if (i < 0 || i >= n || !num(rows[i].sap)) { tip.hide(); return; }
    tip.show(shortDay.format(fromIso(rows[i].d)), [{ value: pence(rows[i].sap), label: 'per kWh' }], (padL + (i + 0.5) * slot) * b.width / width, 40);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); });
}

/* ---------- the page ---------- */

function render(d) {
  state.data = d;
  byId('waiting').hidden = Boolean(d.ready);
  byId('content').hidden = !d.ready;
  if (!d.ready) { G.setLive('', 'Fetching National Gas’s figures'); return; }
  byId('cv').textContent = String(d.cv);
  renderNow(d);
  renderFlows(d);
  renderDay(d);
  renderYear(d);
  G.setLive(Date.now() / 1000 - d.t < 1200 ? 'good' : '', 'National Gas, ' + G.hm(d.t));
}

byId('year-mode').addEventListener('click', function (e) {
  var b = e.target.closest('button[data-mode]');
  if (!b || !state.data) return;
  state.mode = b.dataset.mode;
  try { localStorage.setItem('gb-gas-year', state.mode); } catch (err) { /* storage unavailable */ }
  renderYear(state.data);
});

G.poll('/api/gas', {
  interval: function () { return 300; },
  onData: render,
  onTick: function (p) { if (G.offline(p)) G.setLive('bad', 'Can’t reach the dashboard server'); }
});

var resizeTimer = null, lastWidth = window.innerWidth;
window.addEventListener('resize', function () {
  if (window.innerWidth === lastWidth) return;
  lastWidth = window.innerWidth;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () { if (state.data && state.data.ready) render(state.data); }, 150);
});
