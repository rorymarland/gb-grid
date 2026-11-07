import G from '/static/js/gb.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num;
var CAT = { wind: 'Wind', solar: 'Solar', nuclear: 'Nuclear', gas: 'Gas', biomass: 'Biomass', hydro: 'Hydro', storage: 'Storage', imports: 'Interconnector', coal: 'Coal', other: 'Other' };
var LEVEL_COLOUR = { comfortable: 'z-comfortable', normal: 'z-normal', tight: 'z-tight', 'very-tight': 'z-very-tight' };
var state = { data: null, poll: null };

function gw(mw) { return G.gw(mw) + ' GW'; }
function back(t) { if (!num(t)) return 'Not given'; var days = (t - Date.now() / 1000) / 86400; return days > 60 ? G.date(t) : G.when(t); }

function renderMargin() {
  var m = state.data.margin, host = clear(byId('margin'));
  if (!m) { host.appendChild(h('p', { cls: 'empty', text: 'Waiting for Elexon’s margin forecast.' })); return; }
  var max = 15000, pct = function (v) { return (100 * G.clamp(v, 0, max) / max).toFixed(2) + '%'; };
  host.appendChild(h('div', { cls: 'margin-head' }, [
    h('b', null, [G.gw(m.margin), h('small', { text: 'GW spare at ' + G.hm(m.t) })]),
    h('span', { cls: 'pill' }, [h('i', { cls: LEVEL_COLOUR[m.level] }), m.label + ' over the next 12 hours']),
    h('span', { cls: 'sub', style: 'margin:0', text: 'Lowest ' + gw(m.min) + ' at ' + G.when(m.min_t) + '. ' + (m.lolp_max > 0 ? 'Chance of a shortfall up to ' + (m.lolp_max * 100).toFixed(2) + '%.' : 'No chance of a shortfall forecast.') })
  ]));
  var gauge = h('div', { cls: 'gauge', role: 'img', 'aria-label': 'Margin ' + gw(m.margin) + ', lowest ' + gw(m.min) + ' in the next 12 hours, on a scale from 0 to 15 GW' }, [
    h('div', { cls: 'gauge-track' }, [h('i', { cls: 'z-very-tight' }), h('i', { cls: 'z-tight' }), h('i', { cls: 'z-normal' }), h('i', { cls: 'z-comfortable' })]),
    h('span', { cls: 'gauge-marker min', style: 'left:' + pct(m.min), title: 'Lowest in the next 12 hours' }),
    h('span', { cls: 'gauge-marker', style: 'left:' + pct(m.margin), title: 'Now' })
  ]);
  host.appendChild(gauge);
  var scale = h('div', { cls: 'gauge-scale', 'aria-hidden': 'true' });
  [[0, '0'], [1000, '1'], [2500, '2.5'], [5000, '5 GW'], [10000, '10'], [15000, '15']].forEach(function (p) { scale.appendChild(h('span', { style: 'left:' + pct(p[0]), text: p[1] })); });
  host.appendChild(scale);
  host.appendChild(h('ul', { cls: 'legend' }, [
    h('li', null, [h('i', { cls: 'sw z-very-tight' }), 'Very tight, under 1 GW']), h('li', null, [h('i', { cls: 'sw z-tight' }), 'Tight, 1 to 2.5 GW']),
    h('li', null, [h('i', { cls: 'sw z-normal' }), 'Normal, 2.5 to 5 GW']), h('li', null, [h('i', { cls: 'sw z-comfortable' }), 'Comfortable']),
    h('li', null, [h('i', { style: 'width:14px;border-top:2px dashed var(--ink-2)' }), 'Lowest ahead'])
  ]));
  drawMarginChart(host, m.series);
}

function drawMarginChart(host, series) {
  var box = h('div', { cls: 'chart', style: 'margin-top:14px' });
  host.appendChild(box);
  var width = Math.max(300, host.clientWidth || 800), height = 120, padL = 40, padR = 8, padT = 8, padB = 20;
  var hi = Math.max.apply(null, series.map(function (r) { return r[1]; }).concat([5000]));
  var lo = Math.min.apply(null, series.map(function (r) { return r[1]; }).concat([0]));
  var step = G.niceStep(hi - lo, 3); hi = Math.ceil(hi / step) * step; lo = Math.floor(lo / step) * step;
  var n = series.length, x = function (i) { return padL + (n > 1 ? i / (n - 1) : 0) * (width - padL - padR); };
  var y = function (v) { return padT + (hi - v) / (hi - lo) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': 'Margin over the next 12 hours' });
  for (var v = lo; v <= hi + 1; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 3.5).toFixed(1), 'text-anchor': 'end', text: (v / 1000) + '' }));
  }
  var d = series.map(function (r, i) { return (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(r[1]).toFixed(1); }).join('');
  svg.appendChild(s('path', { d: d, fill: 'none', stroke: 'var(--line)', 'stroke-width': 2 }));
  series.forEach(function (r, i) { if (i % 4 === 0) svg.appendChild(s('text', { 'class': 'axis-text', x: x(i).toFixed(1), y: height - 4, 'text-anchor': 'middle', text: G.hm(r[0]) })); });
  box.appendChild(svg);
}

function renderOffline() {
  var d = state.data, body = clear(byId('offline')), cats = clear(byId('cats'));
  cats.appendChild(h('span', null, [h('b', { text: gw(d.lost_total) }), ' unavailable in total']));
  Object.keys(d.lost_by_cat).sort(function (a, b) { return d.lost_by_cat[b] - d.lost_by_cat[a]; }).forEach(function (k) {
    cats.appendChild(h('span', null, [(CAT[k] || k) + ' ', h('b', { text: gw(d.lost_by_cat[k]) })]));
  });
  var max = Math.max.apply(null, d.offline.map(function (o) { return o.lost; }).concat([1]));
  d.offline.forEach(function (o) {
    body.appendChild(h('tr', null, [
      h('td', null, [h('span', { cls: 'name', text: o.name }), h('span', { cls: 'tag', text: CAT[o.cat] || o.cat })]),
      h('td', { cls: 'num' }, [h('i', { cls: 'lostbar', style: 'width:' + Math.max(2, 60 * o.lost / max).toFixed(0) + 'px' }), G.int(o.lost) + ' MW' + (o.normal && o.normal !== o.lost ? ' of ' + G.int(o.normal) : '')]),
      h('td', null, h('span', { cls: 'chip ' + (o.planned ? 'planned' : 'unplanned'), text: o.planned ? 'Planned' : 'Unplanned' })),
      h('td', { cls: 'hide-sm muted', text: G.when(o.start) }),
      h('td', { text: back(o.end) }),
      h('td', { cls: 'hide-sm muted', text: o.cause || '–' })
    ]));
  });
  if (!d.offline.length) body.appendChild(h('tr', null, h('td', { colspan: '6', cls: 'muted', text: d.checked ? 'No outages over 10 MW reported.' : 'Waiting for the outage notices.' })));
  if (d.closed_count) cats.appendChild(h('span', { text: 'Not counted: ' + G.plural(d.closed_count, 'unit', 'units') + ' (' + gw(d.closed_mw) + ') out until at least two years from now, which usually means closed or mothballed.' }));
}

function renderNuclear() {
  var host = clear(byId('nuke'));
  state.data.nuclear.forEach(function (n) {
    var cap = n.capacity, mw = num(n.mw) ? Math.max(0, n.mw) : null;
    host.appendChild(h('li', null, [
      h('span', { cls: 'name', text: n.name }),
      h('span', { cls: 'track', role: 'img', 'aria-label': (num(mw) ? G.int(mw) + ' MW' : 'No output data') + ' of ' + G.int(cap) + ' MW' + (n.out ? ', ' + G.int(n.out) + ' MW out of action' : '') }, [
        n.out ? h('i', { cls: 'out', style: 'right:0;width:' + (100 * Math.min(cap, n.out) / cap).toFixed(1) + '%' }) : null,
        num(mw) ? h('i', { cls: 'gen', style: 'width:' + (100 * Math.min(cap, mw) / cap).toFixed(1) + '%' }) : null
      ]),
      h('span', { cls: 'val', text: (num(mw) ? G.int(mw) : '–') + ' MW' }),
      n.out ? h('span', { cls: 'note', text: G.int(n.out) + ' MW out (' + (n.planned ? 'planned' : 'unplanned') + (n.cause ? ', ' + n.cause : '') + '), due back ' + back(n.end) + '.' }) : null
    ]));
  });
  if (!state.data.nuclear.length) host.appendChild(h('li', { cls: 'muted', text: 'No nuclear stations in the station list.' }));
}

function renderUpcoming() {
  var body = clear(byId('upcoming'));
  state.data.upcoming.forEach(function (o) {
    body.appendChild(h('tr', null, [
      h('td', null, [h('span', { cls: 'name', text: o.name }), h('span', { cls: 'tag hide-sm', text: CAT[o.cat] || o.cat })]),
      h('td', { cls: 'num', text: G.int(o.lost) }),
      h('td', { text: G.day(o.start) }),
      h('td', { text: num(o.end) ? G.day(o.end) : '–' })
    ]));
  });
  if (!state.data.upcoming.length) body.appendChild(h('tr', null, h('td', { colspan: '4', cls: 'muted', text: 'Nothing over 10 MW announced.' })));
}

function renderNotices() {
  var host = clear(byId('notices'));
  state.data.notices.forEach(function (n) {
    host.appendChild(h('li', null, [
      h('span', { cls: 't', text: n.title }), h('span', { cls: 'w', text: G.when(n.published) }),
      h('p', { text: n.cancelled ? 'Cancelled.' : n.summary }),
      h('details', null, [h('summary', { text: 'Original text' }), h('p', { text: n.text })])
    ]));
  });
  if (!state.data.notices.length) host.appendChild(h('li', { cls: 'muted', text: 'No notices in the last 30 days.' }));
}

function renderLede() {
  var d = state.data, text = '';
  if (d.offline.length) text = gw(d.lost_total) + ' of capacity is offline right now, the most at ' + d.offline[0].name + ' (' + G.int(d.offline[0].lost) + ' MW). ';
  if (d.margin) text += 'Spare capacity over the next 12 hours looks ' + d.margin.label.toLowerCase() + ', with the lowest margin ' + gw(d.margin.min) + ' at ' + G.hm(d.margin.min_t) + '.';
  if (text) byId('lede').textContent = text;
}

function renderAll() { if (!state.data) return; renderLede(); renderMargin(); renderOffline(); renderNuclear(); renderUpcoming(); renderNotices(); }
function updateHeader() {
  var poll = state.poll;
  if (!poll) return;
  if (G.offline(poll)) G.setLive('bad', 'Can’t reach the dashboard server. Retrying.');
  else if (!state.data) G.setLive('', 'Connecting to the dashboard server…');
  else if (!state.data.checked) G.setLive('warn', 'Waiting for the outage notices…');
  else G.setLive('good', 'Live. Outage notices checked ' + G.ago(Date.now() / 1000 - state.data.checked) + '.');
}
window.addEventListener('gb:theme', renderAll);
window.addEventListener('gb:width', renderAll);
state.poll = G.poll('/api/outages', { onData: function (d) { state.data = d; renderAll(); updateHeader(); }, onTick: updateHeader, interval: function () { return 60; } });
setInterval(updateHeader, 5000);
