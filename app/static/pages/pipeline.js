/* What's being built: /pipeline. NESO's TEC register, by kind, stage and year (gridlive/pipeline.py) */
import G from '/static/js/gb.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num, niceStep = G.niceStep, makeTip = G.makeTip;
/* Nearest to connecting first; the colours step from light (scoping) to dark (building) */
var STAGES = [['building', 'Under construction'], ['consented', 'Consented'], ['awaiting', 'Awaiting consent'], ['scoping', 'Scoping']];
var STAGE_NAME = { building: 'Under construction', consented: 'Consents approved', awaiting: 'Awaiting consents', scoping: 'Scoping', built: 'Built' };
var state = { data: null, scoping: true };
try { state.scoping = localStorage.getItem('gb-pipeline-scoping') !== '0'; } catch (e) { /* private window */ }

function gw(mw) { return num(mw) ? (Math.abs(mw) >= 10000 ? Math.round(mw / 1000) : (mw / 1000).toFixed(1)) : '–'; }
function stages() { return STAGES.filter(function (st) { return state.scoping || st[0] !== 'scoping'; }); }
function queued(k) { return stages().reduce(function (a, st) { return a + (k[st[0]] || 0); }, 0); }
function figure(dl, label, value, unit, note) {
  dl.appendChild(h('div', null, [h('dt', { text: label }), h('dd', null, [value, unit ? h('small', { text: unit }) : null,
    note ? h('span', { cls: 'more', text: note }) : null])]));
}
function longDate(iso) { return G.date(Date.parse(iso + 'T12:00:00Z') / 1000); }

/* ---------- in short ---------- */

function renderFigures(d) {
  var t = d.totals, dl = clear(byId('figures'));
  figure(dl, 'Connected', gw(t.built), 'GW', 'Projects in the register that are built');
  figure(dl, 'In the queue', gw(t.pipeline), 'GW', G.int(t.n_pipeline) + ' projects not yet built');
  figure(dl, 'Due in the next 12 months', gw(t.year_ahead), 'GW', G.plural(t.n_year_ahead, 'project', 'projects'));
  figure(dl, 'Past their date', gw(d.late.mw), 'GW', G.plural(d.late.n, 'project', 'projects') + ' whose date has passed, not yet built');
}

/* ---------- by kind ---------- */

function renderKey() {
  var key = clear(byId('key'));
  stages().forEach(function (st) { key.appendChild(h('li', null, [h('i', { cls: 'sw pl-' + st[0], 'aria-hidden': 'true' }), st[1]])); });
}

function renderKinds(d) {
  var host = clear(byId('kinds')), kinds = d.kinds.filter(function (k) { return k.kind !== 'demand'; });
  var max = Math.max.apply(null, kinds.map(queued).concat([1]));
  var total = kinds.reduce(function (a, k) { return a + queued(k); }, 0);
  byId('kind-sub').textContent = (state.scoping ? 'Every generator and store queuing, ' : 'Generators and stores past scoping, ') + gw(total) + ' GW in all, by kind and how far along it is. ' +
    'The figure on the right is what’s connected already.';
  var tip = makeTip(host);
  kinds.slice().sort(function (a, b) { return queued(b) - queued(a); }).forEach(function (k) {
    var bar = h('div', { cls: 'pl-bar', role: 'img', 'aria-label': k.name + ': ' + stages().map(function (st) { return st[1] + ' ' + gw(k[st[0]]) + ' GW'; }).join(', ') });
    stages().forEach(function (st) {
      var mw = k[st[0]] || 0;
      if (mw <= 0) return;
      bar.appendChild(h('i', { cls: 'pl-' + st[0], style: 'width:' + (100 * mw / max).toFixed(3) + '%' }));
    });
    var row = h('div', { cls: 'pl-row' }, [
      h('div', { cls: 'nm', text: k.name }),
      bar,
      h('div', { cls: 'val' }, [gw(queued(k)) + ' GW', h('small', { text: num(k.built) && k.built > 0 ? gw(k.built) + ' GW connected' : 'none connected' })])
    ]);
    host.appendChild(row);
    row.addEventListener('pointermove', function (e) {
      var b = host.getBoundingClientRect();
      tip.show(k.name, stages().map(function (st) { return { cls: 'pl-' + st[0], value: gw(k[st[0]]) + ' GW', label: st[1] }; })
        .concat([{ sep: true }, { value: gw(k.built) + ' GW', label: 'connected' }]), e.clientX - b.left, e.clientY - b.top,
        G.plural(k.n, 'project', 'projects') + ' queuing');
    });
    row.addEventListener('pointerleave', tip.hide);
  });
  var other = d.kinds.filter(function (k) { return k.kind === 'demand'; })[0];
  if (other && queued(other) > 0) host.appendChild(h('p', { cls: 'pl-note', text: 'Also ' + gw(queued(other)) + ' GW of demand and network equipment with no generation.' }));
  // The same figures as a table
  var table = clear(byId('kind-table'));
  table.appendChild(h('thead', null, [h('tr', null, [h('th', { text: 'Kind' }), h('th', { cls: 'num', text: 'Connected' })]
    .concat(STAGES.map(function (st) { return h('th', { cls: 'num', text: st[1] }); })))]));
  var body = h('tbody');
  d.kinds.forEach(function (k) {
    body.appendChild(h('tr', null, [h('th', { scope: 'row', text: k.name }), h('td', { cls: 'num', text: G.int(k.built) })]
      .concat(STAGES.map(function (st) { return h('td', { cls: 'num', text: G.int(k[st[0]]) }); }))));
  });
  table.appendChild(body);
  table.appendChild(h('caption', { cls: 'visually-hidden', text: 'Capacity in MW by kind and stage' }));
}

/* ---------- by year ---------- */

function renderYears(d) {
  var host = clear(byId('years')), years = d.years;
  byId('year-sub').textContent = 'Capacity due to connect each year, by the date in the register and how far along each project is.';
  var note = [];
  if (d.late.n) note.push(gw(d.late.mw) + ' GW across ' + G.plural(d.late.n, 'project', 'projects') + ' had a date that has passed');
  if (d.undated.n) note.push(gw(d.undated.mw) + ' GW across ' + G.plural(d.undated.n, 'project', 'projects') + ' had no date');
  byId('year-note').textContent = note.length ? 'Not shown: ' + note.join('; ') + '.' : '';
  if (!years.length) { host.appendChild(h('p', { cls: 'pl-empty', text: 'No dated projects ahead.' })); return; }
  var width = Math.max(300, host.clientWidth || 900), height = 260, padL = 46, padR = 8, padT = 10, padB = 26;
  var totals = years.map(function (y) { return stages().reduce(function (a, st) { return a + (y[st[0]] || 0); }, 0); });
  var top = Math.max.apply(null, totals.concat([1])) / 1000, step = niceStep(top, 4), hi = Math.ceil(top / step) * step;
  var band = (width - padL - padR) / years.length, barW = Math.max(6, Math.min(44, band - 6));
  var y = function (gwv) { return padT + (hi - gwv) / hi * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': 'Capacity due to connect each year from ' + years[0].year + ' to ' + years[years.length - 1].year });
  for (var v = 0; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: Math.round(v) + ' GW' }));
  }
  var every = band < 34 ? 2 : 1;
  years.forEach(function (yr, i) {
    var x = padL + i * band + (band - barW) / 2, base = 0;
    var g = s('g', { 'class': 'pl-col' });
    // Stack from the ground: building at the bottom, scoping on top
    stages().forEach(function (st) {
      var mw = yr[st[0]] || 0;
      if (mw <= 0) return;
      var y0 = y(base / 1000), y1 = y((base + mw) / 1000);
      // A 2px gap between segments, rounded only at the top of the column
      g.appendChild(s('rect', { 'class': 'pl-' + st[0], x: x.toFixed(1), y: (y1 + 1).toFixed(1), width: barW.toFixed(1), height: Math.max(0.5, y0 - y1 - 2).toFixed(1) }));
      base += mw;
    });
    g.appendChild(s('rect', { 'class': 'hit', x: (padL + i * band).toFixed(1), y: padT, width: band.toFixed(1), height: height - padT - padB, fill: 'transparent' }));
    svg.appendChild(g);
    if (i % every === 0) svg.appendChild(s('text', { 'class': 'axis-text', x: (x + barW / 2).toFixed(1), y: height - 8, 'text-anchor': 'middle', text: String(yr.year) }));
  });
  host.appendChild(svg);
  var tip = makeTip(host);
  svg.addEventListener('pointermove', function (e) {
    var b = svg.getBoundingClientRect(), px = (e.clientX - b.left) * width / b.width, i = Math.floor((px - padL) / band);
    if (i < 0 || i >= years.length) { tip.hide(); return; }
    var yr = years[i];
    tip.show(String(yr.year), stages().map(function (st) { return { cls: 'pl-' + st[0], value: gw(yr[st[0]]) + ' GW', label: st[1] }; }),
      (padL + (i + 0.5) * band) * b.width / width, 40, 'In all ' + gw(totals[i]) + ' GW');
  });
  svg.addEventListener('pointerleave', tip.hide);
}

/* ---------- soonest ---------- */

function renderSoon(d) {
  var table = clear(byId('soon')), rows = d.soon;
  byId('soon-sub').textContent = rows.length ? 'The ' + rows.length + ' largest projects due to connect by ' + G.date(d.now + 365 * 86400) +
    ', of ' + G.plural(d.totals.n_year_ahead, 'project', 'projects') + ' in all. Dates move often; scoping projects due this soon are unlikely to make them.' : 'Nothing due in the next 12 months.';
  if (!rows.length) return;
  table.appendChild(h('thead', null, [h('tr', null, [h('th', { text: 'Due' }), h('th', { text: 'Project' }), h('th', { text: 'Kind' }),
    h('th', { cls: 'num', text: 'MW' }), h('th', { text: 'Stage' })])]));
  var body = h('tbody');
  rows.forEach(function (p) {
    body.appendChild(h('tr', null, [
      h('td', { cls: 'nowrap', text: longDate(p.eff) }),
      h('th', { scope: 'row' }, [h('span', { cls: 'pl-name', text: p.name }), h('small', { text: p.site + (p.embedded ? ', on a distribution network' : '') })]),
      h('td', { text: d.names[p.kind] || p.kind }),
      h('td', { cls: 'num', text: G.int(p.mw) }),
      h('td', null, [h('i', { cls: 'sw pl-' + p.status, 'aria-hidden': 'true' }), ' ', STAGE_NAME[p.status] || p.status])
    ]));
  });
  table.appendChild(body);
}

/* ---------- gates and the trend ---------- */

function renderGates(d) {
  var host = clear(byId('gates')), g = d.gates, two = g['2'] || 0, one = g['1'] || 0, none = g[''] || 0, all = two + one + none || 1;
  var parts = [['two', 'Gate 2', two], ['one', 'Gate 1', one], ['none', 'Not marked', none]];
  var bar = h('div', { cls: 'pl-gatebar', role: 'img', 'aria-label': parts.map(function (p) { return p[1] + ' ' + gw(p[2]) + ' GW'; }).join(', ') });
  parts.forEach(function (p) { if (p[2] > 0) bar.appendChild(h('i', { cls: 'pl-gate-' + p[0], style: 'width:' + (100 * p[2] / all).toFixed(3) + '%' })); });
  host.appendChild(bar);
  var list = h('ul', { cls: 'pl-gatekey' });
  parts.forEach(function (p) {
    list.appendChild(h('li', null, [h('i', { cls: 'sw pl-gate-' + p[0], 'aria-hidden': 'true' }), h('b', { text: p[1] }), ' ' + gw(p[2]) + ' GW, ' + Math.round(100 * p[2] / all) + '%']));
  });
  host.appendChild(list);
  host.appendChild(h('p', { cls: 'pl-note', text: 'Of the capacity queuing, as the register marks each project today.' }));
}

function renderTrend(d) {
  var host = clear(byId('trend')), tr = d.trend;
  if (tr.length < 2) {
    host.appendChild(h('p', { cls: 'pl-empty', text: tr.length ? 'The first reading here is NESO’s register of ' + longDate(tr[0].d) + ', with ' + gw(tr[0].pipeline) + ' GW queuing. This fills in as NESO publishes new versions.' : 'This fills in as NESO publishes new versions.' }));
    return;
  }
  var width = Math.max(260, host.clientWidth || 420), height = 150, padL = 50, padR = 10, padT = 10, padB = 24;
  var t0 = Date.parse(tr[0].d) / 1000, t1 = Date.parse(tr[tr.length - 1].d) / 1000;
  var vals = tr.map(function (p) { return p.pipeline / 1000; });
  var lo = Math.min.apply(null, vals), top = Math.max.apply(null, vals), step = niceStep(Math.max(1, top - lo), 3);
  lo = Math.floor(lo / step) * step; var hi = Math.ceil(top / step) * step; if (hi <= lo) hi = lo + step;
  var x = function (t) { return padL + (t - t0) / Math.max(1, t1 - t0) * (width - padL - padR); };
  var y = function (v) { return padT + (hi - v) / (hi - lo) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': 'Capacity queuing at each update, from ' + gw(tr[0].pipeline) + ' to ' + gw(tr[tr.length - 1].pipeline) + ' GW' });
  for (var v = lo; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: Math.round(v) + ' GW' }));
  }
  var path = '';
  tr.forEach(function (p, i) { path += (i ? 'L' : 'M') + x(Date.parse(p.d) / 1000).toFixed(1) + ' ' + y(p.pipeline / 1000).toFixed(1); });
  svg.appendChild(s('path', { 'class': 'pl-trend', d: path }));
  svg.appendChild(s('text', { 'class': 'axis-text', x: padL, y: height - 6, text: longDate(tr[0].d) }));
  svg.appendChild(s('text', { 'class': 'axis-text', x: width - padR, y: height - 6, 'text-anchor': 'end', text: longDate(tr[tr.length - 1].d) }));
  host.appendChild(svg);
  var change = tr[tr.length - 1].pipeline - tr[0].pipeline;
  host.appendChild(h('p', { cls: 'pl-note', text: (change >= 0 ? 'Up ' : 'Down ') + gw(Math.abs(change)) + ' GW since ' + longDate(tr[0].d) + '.' }));
}

function render(d) {
  state.data = d;
  byId('waiting').hidden = Boolean(d.ready);
  byId('content').hidden = !d.ready;
  if (!d.ready) { G.setLive('', 'Fetching the register'); return; }
  Array.prototype.forEach.call(byId('stage-pick').querySelectorAll('button'), function (b) { b.setAttribute('aria-pressed', String((b.getAttribute('data-scoping') === '1') === state.scoping)); });
  renderFigures(d);
  renderKey();
  renderKinds(d);
  renderYears(d);
  renderSoon(d);
  renderGates(d);
  renderTrend(d);
  G.setLive('good', 'NESO’s register of ' + (d.version ? longDate(d.version) : 'today'));
}

byId('stage-pick').addEventListener('click', function (e) {
  var b = e.target.closest('button');
  if (!b) return;
  state.scoping = b.getAttribute('data-scoping') === '1';
  try { localStorage.setItem('gb-pipeline-scoping', state.scoping ? '1' : '0'); } catch (err) { /* private window */ }
  if (state.data && state.data.ready) render(state.data);
});

G.poll('/api/pipeline', {
  interval: function () { return 3600; },
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
