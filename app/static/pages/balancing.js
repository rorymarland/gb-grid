import G from '/static/js/gb.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num;
var CAT_NAME = { wind: 'Wind', solar: 'Solar', nuclear: 'Nuclear', gas: 'Gas', biomass: 'Biomass', hydro: 'Hydro', storage: 'Storage', imports: 'Imports', coal: 'Coal', other: 'Other' };
var state = { data: null, geo: null, day: 'today', hot: null, poll: null };

function mwh(v) { return num(v) ? (Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + ' GWh' : G.int(v) + ' MWh') : '–'; }
function mwText(v) { return Math.abs(v) >= 1000 ? (v / 1000).toFixed(2) + ' GW' : G.int(v) + ' MW'; }

/* ---------- now ---------- */

function renderTiles() {
  var d = state.data, now = d.now, t = d.today, host = clear(byId('tiles'));
  function tile(key, value, unit, note, dot) {
    host.appendChild(h('div', { cls: 'tile' }, [h('div', { cls: 'k' }, [dot ? h('i', { cls: dot }) : null, key]),
      h('div', { cls: 'v' }, [value, unit ? h('small', { text: unit }) : null]), h('div', { cls: 'n', text: note })]));
  }
  tile('Turned down now', G.gw(now.down), 'GW', G.plural(now.units.filter(function (u) { return u.delta < 0; }).length, 'unit', 'units'), 'dot-down');
  tile('Turned up now', G.gw(now.up), 'GW', G.plural(now.units.filter(function (u) { return u.delta > 0; }).length, 'unit', 'units'), 'dot-up');
  if (!now.units.length) tile('Cost now', G.money(0), 'an hour', 'Nothing is being instructed');
  else tile('Cost now', num(now.cost_hour) ? G.money(now.cost_hour) : '–', 'an hour', num(now.cost_hour) ? 'Estimate from bid and offer prices' : 'Waiting for prices');
  tile('Paid so far today', t.periods ? G.money(t.paid) : '–', '', t.periods ? 'Wind turned down ' + mwh(t.wind_down_mwh) + ', ' + G.money(t.wind_paid) : 'Waiting for Elexon’s figures');
}

function renderLede() {
  var d = state.data, now = d.now, t = d.today, text;
  if (!now.units.length) text = 'NESO isn’t instructing any units to change their output right now.';
  else text = 'Right now NESO has ' + G.gw(now.down) + ' GW turned down and ' + G.gw(now.up) + ' GW turned up' +
    (num(now.cost_hour) ? ', costing about ' + G.money(now.cost_hour) + ' an hour.' : '.');
  if (t.periods) text += ' So far today it has paid ' + G.money(t.paid) + ', including ' + G.money(t.wind_paid) + ' to turn wind farms down by ' + mwh(t.wind_down_mwh) + '.';
  byId('lede').textContent = text;
}

function buildMap() {
  var svg = byId('bsvg'), geo = state.geo;
  var view = [-420, -1335, 700, 1250];
  svg.setAttribute('viewBox', view.join(' '));
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  var g = s('g', { transform: 'scale(0.5)' });
  geo.units.forEach(function (unit) { g.appendChild(s('path', { d: unit.d, 'class': unit.kind === 'gb' ? 'land' : 'near', 'fill-rule': 'evenodd' })); });
  svg.appendChild(g);
  state.dots = s('g');
  svg.appendChild(state.dots);
  state.view = view;
}

function renderMap() {
  if (!state.geo || !state.dots) return;
  var svg = byId('bsvg'), k = state.view[2] / Math.max(200, svg.clientWidth);
  var units = state.data.now.units.filter(function (u) { return num(u.x); });
  var stations = {};
  units.forEach(function (u) {
    var key = u.sid + (u.delta < 0 ? 'd' : 'u');
    var st = stations[key] || (stations[key] = { name: u.name, x: u.x, y: u.y, delta: 0, sid: u.sid, cls: u.delta < 0 ? 'u-down' : 'u-up' });
    st.delta += u.delta;
  });
  var list = Object.keys(stations).map(function (key) { return stations[key]; }).sort(function (a, b) { return Math.abs(b.delta) - Math.abs(a.delta); });
  var dots = clear(state.dots);
  list.forEach(function (st) {
    var r = (Math.sqrt(Math.abs(st.delta)) * 0.75 + 2.5) * k;
    var c = s('circle', { 'class': 'ucirc ' + st.cls + (state.hot === st.sid ? ' hot' : ''), cx: st.x, cy: st.y, r: r.toFixed(2), 'stroke-width': (1.2 * k).toFixed(2) });
    c.appendChild(s('title', { text: st.name + ': ' + (st.delta < 0 ? 'turned down ' : 'turned up ') + mwText(Math.abs(st.delta)) }));
    c.addEventListener('pointerenter', function () { highlight(st.sid); });
    c.addEventListener('pointerleave', function () { highlight(null); });
    dots.appendChild(c);
  });
  var note = byId('map-note'), unplaced = state.data.now.units.length - units.length;
  note.hidden = !(unplaced > 0 || !state.data.now.units.length);
  note.textContent = !state.data.now.units.length ? 'No units are following instructions at the moment.' : G.plural(unplaced, 'unit has', 'units have') + ' no known location, so only appear in the list.';
}

function highlight(sid) {
  state.hot = sid;
  Array.prototype.forEach.call(document.querySelectorAll('#ulist tr'), function (row) { row.classList.toggle('hot', sid !== null && row.getAttribute('data-sid') === String(sid)); });
  renderMap();
}

function renderList() {
  var units = state.data.now.units, body = clear(byId('ulist'));
  var max = Math.max.apply(null, units.map(function (u) { return Math.abs(u.delta); }).concat([1]));
  byId('ulist-title').textContent = units.length ? G.plural(units.length, 'unit is', 'units are') + ' following instructions' : 'Units following instructions';
  units.forEach(function (u) {
    var row = h('tr', { 'data-sid': u.sid === null ? '' : String(u.sid), onmouseenter: function () { if (u.sid !== null) highlight(u.sid); }, onmouseleave: function () { highlight(null); } }, [
      h('td', null, [G.stationName(u.name, u.sid), h('span', { cls: 'tag', text: CAT_NAME[u.cat] || u.cat })]),
      h('td', { cls: 'num' }, [h('i', { cls: 'bar', style: 'width:' + Math.max(2, 60 * Math.abs(u.delta) / max).toFixed(0) + 'px;background:var(' + (u.delta < 0 ? '--down' : '--up') + ')' }), (u.delta > 0 ? '+' : '−') + mwText(Math.abs(u.delta))]),
      h('td', { cls: 'num hide-sm muted', text: num(u.price) ? (u.price < 0 ? '−£' : '£') + Math.abs(u.price).toFixed(0) + '/MWh' : '–' })
    ]);
    body.appendChild(row);
  });
  if (!units.length) body.appendChild(h('tr', null, h('td', { colspan: '3', cls: 'muted', text: 'None right now.' })));
}

/* ---------- the day ---------- */

function renderDay() {
  var d = state.data, rows = d.series[state.day], host = clear(byId('day-chart'));
  var filled = rows.filter(function (r) { return r[1] !== null; });
  var up = 0, down = 0, paid = 0, wind = 0, windPaid = 0;
  filled.forEach(function (r) { down -= r[1]; up += r[2]; paid += r[3] + r[4]; wind -= r[5]; windPaid += r[6]; });
  byId('day-sub').textContent = filled.length ? (state.day === 'today' ? 'So far today' : 'Yesterday') + ': ' + mwh(up) + ' turned up and ' + mwh(down) + ' turned down (' + mwh(wind) + ' of it wind), costing ' + G.money(paid) + ' in total.'
    : 'Waiting for Elexon’s figures for ' + state.day + '.';
  var width = Math.max(300, host.clientWidth || 900), height = 230, padL = 48, padR = 8, padT = 10, padB = 22;
  var hi = 1, lo = -1;
  rows.forEach(function (r) { if (r[2] !== null) { hi = Math.max(hi, r[2]); lo = Math.min(lo, r[1]); } });
  var step = G.niceStep(hi - lo, 4);
  hi = Math.ceil(hi / step) * step; lo = Math.floor(lo / step) * step;
  var n = rows.length, bw = (width - padL - padR) / n;
  var y = function (v) { return padT + (hi - v) / (hi - lo) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': 'Volumes turned up and down each half hour' });
  for (var v = lo; v <= hi + 0.01; v += step) {
    svg.appendChild(s('line', { 'class': v === 0 ? 'zero-line' : 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 3.5).toFixed(1), 'text-anchor': 'end', text: (Math.abs(v) >= 1000 ? (v / 1000) + 'k' : String(v)) }));
  }
  var tip = G.makeTip(host);
  rows.forEach(function (r, i) {
    var x = padL + i * bw;
    if (G.hm(r[0]).slice(3) === '00' && +G.hm(r[0]).slice(0, 2) % 3 === 0) svg.appendChild(s('text', { 'class': 'axis-text', x: (x + bw / 2).toFixed(1), y: height - 5, 'text-anchor': 'middle', text: G.hm(r[0]) }));
    if (r[1] === null) return;
    var w = Math.max(1, bw - 1.5);
    var parts = [];
    if (r[2] > 0) parts.push(s('rect', { 'class': 'bar-up', x: x.toFixed(1), y: y(r[2]).toFixed(1), width: w.toFixed(1), height: (y(0) - y(r[2])).toFixed(1), rx: 1.5 }));
    if (r[1] < 0) parts.push(s('rect', { 'class': 'bar-down', x: x.toFixed(1), y: y(0).toFixed(1), width: w.toFixed(1), height: (y(r[1]) - y(0)).toFixed(1), rx: 1.5 }));
    if (r[5] < 0) parts.push(s('rect', { 'class': 'bar-wind', x: x.toFixed(1), y: y(0).toFixed(1), width: w.toFixed(1), height: (y(r[5]) - y(0)).toFixed(1), rx: 1.5, style: 'fill:var(--surface);fill-opacity:.38' }));
    var hit = s('rect', { x: x.toFixed(1), y: padT, width: bw.toFixed(1), height: height - padT - padB, fill: 'transparent' });
    hit.addEventListener('pointermove', function () {
      parts.forEach(function (p) { p.classList.add('bar-hot'); });
      tip.show(G.hm(r[0]) + ' to ' + G.hm(r[0] + 1800), [
        { value: mwh(r[2]), label: 'turned up', color: 'var(--up)' },
        { value: G.money(r[4]), label: 'paid for that' },
        { sep: true },
        { value: mwh(-r[1]), label: 'turned down', color: 'var(--down)' },
        { value: mwh(-r[5]), label: 'of which wind' },
        { value: G.money(r[3]), label: 'paid for turning down' }
      ], x + bw / 2, y(0));
    });
    hit.addEventListener('pointerleave', function () { parts.forEach(function (p) { p.classList.remove('bar-hot'); }); tip.hide(); });
    parts.forEach(function (p) { svg.appendChild(p); });
    svg.appendChild(hit);
  });
  if (state.day === 'today') {
    var nowI = Math.floor((Date.now() / 1000 - rows[0][0]) / 1800);
    if (nowI >= 0 && nowI < n) svg.appendChild(s('line', { 'class': 'now-mark', x1: (padL + nowI * bw).toFixed(1), x2: (padL + nowI * bw).toFixed(1), y1: padT, y2: height - padB }));
  }
  host.insertBefore(svg, host.firstChild);
}

function renderTop() {
  var body = clear(byId('top'));
  state.data.top.forEach(function (u) {
    body.appendChild(h('tr', null, [
      h('td', null, G.stationName(u.name, u.sid)),
      h('td', { cls: 'hide-sm', text: CAT_NAME[u.cat] || u.cat }),
      h('td', { cls: 'num', text: u.down >= 0.5 ? mwh(u.down) : '–' }),
      h('td', { cls: 'num', text: u.up >= 0.5 ? mwh(u.up) : '–' }),
      h('td', { cls: 'num', text: G.money(u.paid) })
    ]));
  });
  if (!state.data.top.length) body.appendChild(h('tr', null, h('td', { colspan: '5', cls: 'muted', text: 'Waiting for Elexon’s figures for today.' })));
}

function renderConstraints() {
  var rows = state.data.constraints, host = clear(byId('cons-chart'));
  if (!rows.length) { host.appendChild(h('p', { cls: 'empty', text: 'Waiting for NESO’s constraint cost figures.' })); return; }
  var total = rows.slice(-30).reduce(function (a, r) { return a + r.cost; }, 0);
  var last = rows[rows.length - 1];
  byId('cons-sub').textContent = 'What NESO spends each day keeping flows within the network’s limits, mostly paying Scottish wind to stop and plants further south to start. ' +
    'The last 30 days published cost ' + G.money(total) + '; the latest figure is for ' + G.date(Date.parse(last.d + 'T12:00:00Z') / 1000) + '.';
  var width = Math.max(300, host.clientWidth || 900), height = 200, padL = 48, padR = 8, padT = 10, padB = 22;
  var hi = Math.max.apply(null, rows.map(function (r) { return r.cost; }).concat([1e6]));
  var step = G.niceStep(hi, 4);
  hi = Math.ceil(hi / step) * step;
  var n = rows.length, bw = (width - padL - padR) / n;
  var y = function (v) { return padT + (hi - v) / hi * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': 'Daily constraint costs' });
  for (var v = 0; v <= hi + 1; v += step) {
    svg.appendChild(s('line', { 'class': v === 0 ? 'zero-line' : 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 3.5).toFixed(1), 'text-anchor': 'end', text: '£' + (v / 1e6) + 'm' }));
  }
  var tip = G.makeTip(host);
  rows.forEach(function (r, i) {
    var x = padL + i * bw;
    var date = Date.parse(r.d + 'T12:00:00Z') / 1000;
    if (r.d.slice(8) === '01') svg.appendChild(s('text', { 'class': 'axis-text', x: x.toFixed(1), y: height - 5, text: G.date(date).split(' ')[1] }));
    var bar = s('rect', { 'class': 'bar-cost', x: x.toFixed(1), y: y(r.cost).toFixed(1), width: Math.max(1, bw - 1).toFixed(1), height: Math.max(0, y(0) - y(r.cost)).toFixed(1), rx: 1 });
    var hit = s('rect', { x: x.toFixed(1), y: padT, width: bw.toFixed(1), height: height - padT - padB, fill: 'transparent' });
    hit.addEventListener('pointermove', function () {
      bar.classList.add('bar-hot');
      var lines = Object.keys(r.parts).filter(function (k) { return r.parts[k] > 0; }).map(function (k) { return { value: G.money(r.parts[k]), label: k.toLowerCase() }; });
      tip.show(G.day(date), [{ value: G.money(r.cost), label: 'in total' }, { sep: true }].concat(lines).concat(r.volume ? [{ sep: true }, { value: mwh(Math.abs(r.volume)), label: 'turned down for thermal limits' }] : []), x + bw / 2, y(r.cost));
    });
    hit.addEventListener('pointerleave', function () { bar.classList.remove('bar-hot'); tip.hide(); });
    svg.appendChild(bar);
    svg.appendChild(hit);
  });
  host.insertBefore(svg, host.firstChild);
}

function renderAll() {
  if (!state.data) return;
  // Nothing instructed (most of the time): one line instead of an empty map and table
  var none = !state.data.now.units.length;
  byId('none-now').hidden = !none;
  byId('bal-grid').hidden = none;
  renderLede(); renderTiles(); renderList(); renderMap(); renderDay(); renderTop(); renderConstraints();
}

function updateHeader() {
  var poll = state.poll;
  if (!poll) return;
  if (G.offline(poll)) G.setLive('bad', 'Can’t reach the dashboard server. Retrying.');
  else if (!state.data) G.setLive('', 'Connecting to the dashboard server…');
  else if (!state.data.now.time) G.setLive('warn', 'Waiting for the first unit data…');
  else G.setLive('good', 'Live. Instructions as of ' + G.hm(state.data.now.time) + ', checked ' + G.ago((Date.now() - poll.lastOk) / 1000) + '.');
}

Array.prototype.forEach.call(document.querySelectorAll('#day-toggle button'), function (button) {
  button.addEventListener('click', function () {
    state.day = button.getAttribute('data-day');
    Array.prototype.forEach.call(document.querySelectorAll('#day-toggle button'), function (b) { b.setAttribute('aria-pressed', String(b === button)); });
    if (state.data) renderDay();
  });
});
window.addEventListener('gb:theme', renderAll);
window.addEventListener('gb:width', renderAll);
G.fetchJSON('/geo.json').then(function (geo) { state.geo = geo; buildMap(); renderMap(); }).catch(function () { /* the list still works */ });
state.poll = G.poll('/api/balancing', { onData: function (data) { state.data = data; renderAll(); updateHeader(); }, onTick: updateHeader, interval: function () { return 30; } });
setInterval(updateHeader, 5000);
