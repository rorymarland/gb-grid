import G from '/static/js/gb.js';
import { LEVEL_CLASS, LEVEL_NAME, MAP_REGIONS, INSET, levelFor, isDarkFill, regionShapes } from '/static/js/regionmap.js';


var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num, clamp = G.clamp;
var FUEL_NAME = { biomass: 'Biomass', coal: 'Coal', imports: 'Imports', gas: 'Gas', nuclear: 'Nuclear', other: 'Other', hydro: 'Hydro', solar: 'Solar', wind: 'Wind' };
var GB_ID = 18;

var state = { geo: null, near: null, data: null, poll: null, index: null, atNow: true, selected: null, hover: null,
  solar: false, playing: false, timer: null, scale: 1, nodes: {}, labels: {}, suns: {} };
try { state.solar = localStorage.getItem('gb-grid-regions-solar') === '1'; } catch (error) { /* storage unavailable */ }

/* ---------- data helpers ---------- */

function D() { return state.data; }
function tAt(i) { return D().t0 + i * D().step; }
function valueAt(id, i) { var series = D().ci[String(id)]; return series ? series[i] : null; }
function levelAt(id, i) { var series = D().index[String(id)]; return series ? series[i] : null; }
/* Solar estimates arrive after each half hour ends, so use the latest within an hour */
function solarAt(id, i) {
  var series = D().solar[String(id)];
  if (!series) return null;
  for (var k = i; k >= Math.max(0, i - 2); k--) if (num(series[k])) return series[k];
  return null;
}
function mixAt(id, i) {
  var series = D().mix[String(id)], text = series ? series[i] : null;
  if (!text) return null;
  var parts = text.split(','), out = {};
  D().fuels.forEach(function (fuel, k) { out[fuel] = parseFloat(parts[k]) || 0; });
  return out;
}
function nameOf(id) { return (D() && D().names[String(id)]) || (id === GB_ID ? 'Great Britain' : 'Region ' + id); }
function whenText(i) {
  var t = tAt(i);
  return G.when(t) + (i === D().now ? ' (now)' : t > tAt(D().now) ? ' (forecast)' : '');
}

/* ---------- the map ---------- */

function buildMap() {
  var svg = byId('rg-svg');
  var main = s('g', { transform: 'scale(0.5)' });
  var inset = s('g', { transform: 'translate(' + (INSET.dx * 2) + ' ' + (INSET.dy * 2) + ')' });
  var shapes = regionShapes(state.geo), regions = shapes.regions;
  state.insetFrame = shapes.insetFrame;
  state.view = shapes.view;
  svg.setAttribute('viewBox', state.view.map(function (v) { return v.toFixed(1); }).join(' '));
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  var near = s('g', { transform: 'scale(0.5)' });
  (state.near || []).forEach(function (unit) {
    if (unit.kind !== 'gb') near.appendChild(s('path', { d: unit.d, 'class': 'near', 'fill-rule': 'evenodd' }));
  });
  svg.appendChild(near);
  regions.forEach(function (item) {
    var region = item.region, label = nameOf(region.id);
    var node = s('path', { d: item.mainland, 'class': 'region', 'fill-rule': 'evenodd', tabindex: '0', role: 'button', 'aria-label': label });
    var extra = item.island ? s('path', { d: item.island, 'class': 'region', 'fill-rule': 'evenodd', 'aria-hidden': 'true' }) : null;
    [node, extra].forEach(function (n) {
      if (!n) return;
      n.addEventListener('pointerenter', function (e) { if (e.pointerType === 'mouse') setHover(region.id); });
      n.addEventListener('pointerleave', function () { setHover(null); });
      n.addEventListener('click', function () { select(state.selected === region.id ? null : region.id); });
    });
    node.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); select(state.selected === region.id ? null : region.id); } });
    node.addEventListener('focus', function () { setHover(region.id); });
    node.addEventListener('blur', function () { setHover(null); });
    main.appendChild(node);
    if (extra) inset.appendChild(extra);
    state.nodes[region.id] = [node, extra];
  });
  main.appendChild(inset);
  svg.appendChild(main);
  if (state.insetFrame) {
    var f = state.insetFrame;
    svg.appendChild(s('rect', { 'class': 'inset-box', x: f[0].toFixed(1), y: f[1].toFixed(1), width: (f[2] - f[0]).toFixed(1), height: (f[3] - f[1]).toFixed(1), rx: 5 }));
  }
  state.gLabels = s('g');
  state.gSun = s('g');
  svg.appendChild(state.gSun);
  svg.appendChild(state.gLabels);
  state.geo.regions.forEach(function (region) {
    var text = s('text', { 'class': 'rg-label', x: region.lx, y: region.ly });
    state.gLabels.appendChild(text);
    state.labels[region.id] = text;
    var sun = s('circle', { 'class': 'sun', cx: region.lx, cy: region.ly, r: 0 });
    var sunText = s('text', { 'class': 'sun-label', x: region.lx, y: region.ly });
    state.gSun.appendChild(sun);
    state.gSun.appendChild(sunText);
    state.suns[region.id] = [sun, sunText];
  });
  svg.addEventListener('pointerleave', function () { setHover(null); });
  measure();
  if (window.ResizeObserver) new ResizeObserver(function () { measure(); paint(); }).observe(svg);
}

function measure() {
  var svg = byId('rg-svg');
  var width = svg.clientWidth, height = svg.clientHeight;
  if (!width || !state.view) return;
  /* km per screen pixel, so labels keep a constant size */
  state.scale = Math.max(state.view[2] / width, state.view[3] / height);
}


function paint() {
  if (!state.data || !state.geo) return;
  var i = state.index, k = state.scale;
  var maxSolar = 1;
  MAP_REGIONS.forEach(function (id) { var v = solarAt(id, i); if (num(v)) maxSolar = Math.max(maxSolar, v); });
  var anyData = false;
  MAP_REGIONS.forEach(function (id) {
    var value = valueAt(id, i), level = levelFor(value, levelAt(id, i));
    if (num(value)) anyData = true;
    (state.nodes[id] || []).forEach(function (node) {
      if (!node) return;
      node.setAttribute('class', 'region ' + (num(level) ? LEVEL_CLASS[level] : 'nodata') + (state.selected === id ? ' sel' : '') + (state.hover === id ? ' hover' : ''));
      node.setAttribute('aria-label', nameOf(id) + ': ' + (num(value) ? value + ' grams of CO2 per kilowatt-hour, ' + LEVEL_NAME[level].toLowerCase() : 'no data'));
    });
    var label = state.labels[id];
    label.textContent = num(value) ? String(value) : '–';
    label.setAttribute('font-size', (13.5 * k).toFixed(2));
    label.setAttribute('stroke-width', (3 * k).toFixed(2));
    label.setAttribute('y', (state.geo.regions.filter(function (r) { return r.id === id; })[0].ly + 4.6 * k).toFixed(1));
    label.setAttribute('class', 'rg-label' + (isDarkFill(level) && !state.solar ? ' on-dark' : ''));
    label.style.display = state.solar ? 'none' : '';
    var sun = state.suns[id], mw = solarAt(id, i);
    var showSun = state.solar && num(mw) && mw >= 1;
    sun[0].setAttribute('r', showSun ? (Math.sqrt(mw / maxSolar) * 26 * k + 3 * k).toFixed(2) : 0);
    sun[0].setAttribute('stroke-width', (1.5 * k).toFixed(2));
    sun[1].textContent = state.solar && num(mw) ? (mw >= 1000 ? (mw / 1000).toFixed(1) + ' GW' : Math.round(mw) + ' MW') : '';
    sun[1].setAttribute('font-size', (12 * k).toFixed(2));
    sun[1].setAttribute('stroke-width', (3 * k).toFixed(2));
    var r = showSun ? (Math.sqrt(mw / maxSolar) * 26 * k + 3 * k) : 0;
    sun[1].setAttribute('y', (state.geo.regions.filter(function (x) { return x.id === id; })[0].ly + r + 13 * k).toFixed(1));
  });
  byId('rg-svg').classList.toggle('dim-regions', state.solar);
  var msg = byId('rg-msg');
  msg.hidden = anyData;
  if (!anyData) msg.textContent = state.data.n ? 'No regional figures for this half hour yet.' : 'Waiting for NESO’s regional forecast…';
}

function setHover(id) {
  if (state.hover === id) return;
  state.hover = id;
  paint();
  renderDetail();
}
function select(id) {
  state.selected = id;
  paint();
  renderDetail();
  renderRank();
}

/* ---------- time ---------- */

function setIndex(i, fromUser) {
  if (!state.data) return;
  state.index = clamp(Math.round(i), 0, state.data.n - 1);
  state.atNow = state.index === state.data.now;
  if (fromUser) state.chosenT = tAt(state.index);
  byId('slider').value = String(state.index);
  byId('slider').setAttribute('aria-valuetext', whenText(state.index));
  renderAll();
}

var PLAY = '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3.5 2.2v9.6L11.6 7z" fill="currentColor"/></svg>';
var PAUSE = '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3.4 2.5h2.5v9H3.4zM8.1 2.5h2.5v9H8.1z" fill="currentColor"/></svg>';
function setPlaying(on) {
  state.playing = on;
  clearInterval(state.timer);
  byId('play').innerHTML = on ? PAUSE : PLAY;
  byId('play').setAttribute('aria-label', on ? 'Pause' : 'Play the forecast');
  if (!on) return;
  if (state.index >= state.data.n - 1) setIndex(0, true);
  state.timer = setInterval(function () {
    if (state.index >= state.data.n - 1) { setPlaying(false); return; }
    setIndex(state.index + 1, true);
  }, 260);
}

function renderMarks() {
  var host = clear(byId('marks'));
  var d = D(), n = d.n;
  function pct(i) { return (100 * i / (n - 1)).toFixed(2) + '%'; }
  host.appendChild(h('span', { cls: 'edge-l', style: 'left:0', text: '−24 h' }));
  if (num(d.now)) host.appendChild(h('span', { cls: 'now', style: 'left:' + pct(d.now), text: 'Now' }));
  host.appendChild(h('span', { cls: 'edge-r', style: 'left:100%', text: '+48 h' }));
}

/* ---------- side panel ---------- */

function renderDetail() {
  var host = clear(byId('detail'));
  if (!state.data) { host.appendChild(h('p', { cls: 'empty', text: 'Waiting for regional data…' })); return; }
  var id = state.hover || state.selected || GB_ID, i = state.index;
  var value = valueAt(id, i), level = levelFor(value, levelAt(id, i));
  var head = h('div', { cls: 'rg-detail-head' }, [h('div', null, [
    h('h2', { text: nameOf(id) }),
    h('p', { cls: 'meta', text: id === GB_ID ? 'The whole of Great Britain. Select a region for its figures.' : (state.selected === id ? 'Selected region' : 'Hovering') + ', ' + whenText(i) })
  ])]);
  if (state.selected && id === state.selected) head.appendChild(h('button', { type: 'button', cls: 'close', text: 'Show Great Britain', onclick: function () { select(null); } }));
  host.appendChild(head);
  host.appendChild(h('div', { cls: 'big' }, [
    h('b', null, [num(value) ? String(value) : '–', h('small', { text: 'g/kWh' })]),
    num(level) ? h('span', { cls: 'level' }, [h('i', { cls: 'sw ' + LEVEL_CLASS[level] }), LEVEL_NAME[level]]) : null
  ]));
  var mix = mixAt(id, i);
  if (mix) {
    var parts = D().fuels.map(function (f) { return { fuel: f, v: mix[f] }; }).filter(function (p) { return p.v >= 0.5; })
      .sort(function (a, b) { return b.v - a.v; });
    var bar = h('div', { cls: 'mixbar', role: 'img', 'aria-label': 'Generation mix: ' + parts.map(function (p) { return FUEL_NAME[p.fuel] + ' ' + Math.round(p.v) + '%'; }).join(', ') });
    parts.forEach(function (p) { bar.appendChild(h('i', { cls: 'b-' + p.fuel, style: 'width:' + p.v.toFixed(1) + '%' })); });
    host.appendChild(bar);
    var legend = h('ul', { cls: 'mixlegend' });
    parts.slice(0, 6).forEach(function (p) { legend.appendChild(h('li', null, [h('i', { cls: 'sw b-' + p.fuel }), FUEL_NAME[p.fuel] + ' ', h('b', { text: Math.round(p.v) + '%' })])); });
    host.appendChild(legend);
  }
  var mw = solarAt(id, i);
  if (num(mw) && id !== GB_ID) host.appendChild(h('p', { cls: 'chart-note', text: 'Solar output in the region: ' + (mw >= 1000 ? (mw / 1000).toFixed(1) + ' GW' : Math.round(mw) + ' MW') + ' (Sheffield Solar).' }));
  var chart = h('div', { cls: 'rg-chart' });
  host.appendChild(chart);
  drawChart(chart, id);
}

function drawChart(box, id) {
  var d = D(), n = d.n;
  var width = Math.max(260, box.clientWidth || 420), height = 132;
  var padL = 34, padR = 6, padT = 8, padB = 20;
  var series = d.ci[String(id)] || [], ref = id === GB_ID ? [] : (d.ci[String(GB_ID)] || []);
  var hi = 0;
  series.concat(ref).forEach(function (v) { if (num(v)) hi = Math.max(hi, v); });
  var step = G.niceStep(Math.max(hi, 40), 3);
  hi = Math.max(step, Math.ceil(hi / step) * step);
  var x = function (k) { return padL + k / (n - 1) * (width - padL - padR); };
  var y = function (v) { return padT + (hi - v) / hi * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': nameOf(id) + ' carbon intensity, past day and next two days' });
  for (var v = 0; v <= hi + 0.01; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 3.5).toFixed(1), 'text-anchor': 'end', text: String(v) }));
  }
  for (var k = 0; k < n; k++) {
    var t = d.t0 + k * d.step;
    if (G.hm(t) === '00:00') svg.appendChild(s('text', { 'class': 'axis-text', x: x(k).toFixed(1), y: height - 4, 'text-anchor': 'middle', text: G.day(t).split(' ')[0] }));
  }
  function path(values, from, to) {
    var dd = '', pen = false;
    for (var k = from; k <= to; k++) {
      var val = values[k];
      if (!num(val)) { pen = false; continue; }
      dd += (pen ? 'L' : 'M') + x(k).toFixed(1) + ' ' + y(val).toFixed(1);
      pen = true;
    }
    return dd;
  }
  if (ref.length) svg.appendChild(s('path', { 'class': 'ref', d: path(ref, 0, n - 1) }));
  var now = num(d.now) ? d.now : n - 1;
  svg.appendChild(s('path', { 'class': 'series', d: path(series, 0, now) }));
  svg.appendChild(s('path', { 'class': 'series future', d: path(series, now, n - 1) }));
  svg.appendChild(s('line', { 'class': 'now-line', x1: x(now).toFixed(1), x2: x(now).toFixed(1), y1: padT, y2: height - padB }));
  if (state.index !== now) svg.appendChild(s('line', { 'class': 'pos-line', x1: x(state.index).toFixed(1), x2: x(state.index).toFixed(1), y1: padT, y2: height - padB }));
  box.appendChild(svg);
  box.appendChild(h('p', { cls: 'chart-note', text: 'Solid: NESO’s estimates for the past day. Dashed: its forecast.' + (ref.length ? ' Dotted grey: Great Britain.' : '') }));
}

function renderRank() {
  var host = clear(byId('rank'));
  if (!state.data) return;
  var i = state.index;
  byId('rank-when').textContent = whenText(i);
  var rows = MAP_REGIONS.map(function (id) { return { id: id, v: valueAt(id, i) }; }).concat([{ id: GB_ID, v: valueAt(GB_ID, i), gb: true }]);
  rows = rows.filter(function (r) { return num(r.v); }).sort(function (a, b) { return a.v - b.v || (a.gb ? 1 : -1); });
  var max = Math.max.apply(null, rows.map(function (r) { return r.v; }).concat([50]));
  rows.forEach(function (row) {
    var level = levelFor(row.v, levelAt(row.id, i));
    host.appendChild(h('li', { cls: row.gb ? 'gb' : null }, h('button', {
      type: 'button', 'aria-pressed': String(state.selected === row.id || (row.gb && !state.selected)),
      onclick: function () { select(row.gb ? null : row.id); },
      onmouseenter: function () { if (!row.gb) setHover(row.id); },
      onmouseleave: function () { setHover(null); }
    }, [
      h('span', { cls: 'nm', text: nameOf(row.id) }),
      h('span', { cls: 'bar', 'aria-hidden': 'true' }, h('i', { cls: num(level) ? LEVEL_CLASS[level] : 'ci-none', style: 'width:' + (100 * row.v / max).toFixed(1) + '%' })),
      h('span', { cls: 'val', text: row.v + ' g' })
    ])));
  });
  if (!rows.length) host.appendChild(h('li', { cls: 'empty', text: 'No figures for this half hour yet.' }));
}

function renderScale() {
  var host = clear(byId('scale'));
  LEVEL_NAME.forEach(function (name, k) { host.appendChild(h('li', null, [h('i', { cls: 'sw ' + LEVEL_CLASS[k] }), name])); });
  host.appendChild(h('li', { cls: 'note', text: 'NESO’s rating. Numbers are g/kWh.' }));
}

function renderLede() {
  var d = D(), i = d.now;
  if (!num(i)) return;
  var rows = MAP_REGIONS.map(function (id) { return { id: id, v: valueAt(id, i) }; }).filter(function (r) { return num(r.v); })
    .sort(function (a, b) { return a.v - b.v; });
  if (rows.length < 2) return;
  var low = rows[0], high = rows[rows.length - 1], zero = rows.filter(function (r) { return r.v === 0; });
  var text = zero.length > 1 ? zero.map(function (r) { return nameOf(r.id); }).join(' and ') + ' are at 0 g/kWh right now'
    : 'Right now ' + nameOf(low.id) + ' is the cleanest at ' + low.v + ' g/kWh';
  text += ', and ' + nameOf(high.id) + ' is the most carbon-intensive at ' + high.v + ' g/kWh.';
  var gb = valueAt(GB_ID, i);
  if (num(gb)) text += ' Great Britain as a whole is at ' + gb + ' g/kWh.';
  byId('lede').textContent = text;
}

function renderAll() {
  if (!state.data) return;
  byId('when').innerHTML = '';
  byId('when').appendChild(h('b', { text: whenText(state.index) }));
  paint();
  renderDetail();
  renderRank();
}

function onData(data) {
  var first = !state.data;
  state.data = data;
  byId('slider').max = String(Math.max(0, data.n - 1));
  var next;
  if (first || state.atNow || !state.chosenT) next = num(data.now) ? data.now : 0;
  else next = clamp(Math.round((state.chosenT - data.t0) / data.step), 0, data.n - 1);
  if (first && data.home && MAP_REGIONS.indexOf(data.home) >= 0) state.selected = data.home;
  renderMarks();
  renderLede();
  setIndex(next, false);
  updateHeader();
}

function updateHeader() {
  var poll = state.poll;
  if (!poll) return;
  if (G.offline(poll)) G.setLive('bad', 'Can’t reach the dashboard server. Retrying.');
  else if (!state.data) G.setLive('', 'Connecting to the dashboard server…');
  else if (!state.data.n || !Object.keys(state.data.ci).length) G.setLive('warn', 'Waiting for NESO’s regional forecast…');
  else G.setLive('good', 'Live. Regional forecast updated ' + G.ago(Date.now() / 1000 - state.data.generated) + '.');
}

/* ---------- start ---------- */

byId('play').innerHTML = PLAY;
byId('play').addEventListener('click', function () { setPlaying(!state.playing); });
byId('slider').addEventListener('input', function (e) { setPlaying(false); setIndex(+e.target.value, true); });
byId('solar-toggle').setAttribute('aria-pressed', String(state.solar));
byId('solar-toggle').addEventListener('click', function () {
  state.solar = !state.solar;
  byId('solar-toggle').setAttribute('aria-pressed', String(state.solar));
  try { localStorage.setItem('gb-grid-regions-solar', state.solar ? '1' : '0'); } catch (error) { /* ignore */ }
  paint();
});
window.addEventListener('gb:theme', renderAll);
window.addEventListener('gb:width', function () { measure(); renderAll(); });
renderScale();
Promise.all([G.fetchJSON('/regions.json'), G.fetchJSON('/geo.json')]).then(function (results) {
  state.geo = results[0];
  state.near = results[1].units;
  buildMap();
  paint();
}).catch(function () {
  var msg = byId('rg-msg');
  msg.hidden = false;
  msg.textContent = 'Couldn’t load the region outlines. Reload the page to try again.';
});
state.poll = G.poll('/api/regions', { onData: onData, onTick: updateHeader, interval: function () { return 60; } });
setInterval(updateHeader, 5000);

/* ---------- Britain and its neighbours: the last week ---------- */

var nbData = null;

function nbStatus(d) {
  /* Why there's nothing to show, in words */
  var feeds = d.feeds || {}, n = feeds.neighbours, e = feeds.entsoe;
  if (n && n.error && !n.ok) return 'RTE’s and Energinet’s figures haven’t come through yet: ' + n.error;
  if (n && n.error) return 'The last attempt to fetch RTE’s and Energinet’s figures failed (' + n.error + '); trying again every 15 minutes.';
  if (!d.gb.length) return 'Waiting for NESO’s carbon intensity for Britain.';
  if (!n) return 'Fetching RTE’s and Energinet’s figures; they arrive within a minute or two of the dashboard starting.';
  return '';
}

function nbChart(host, c, d, hi) {
  var width = Math.max(240, host.clientWidth || 320), height = 150, padL = 34, padR = 6, padT = 8, padB = 20;
  var x = function (t) { return padL + (t - d.start) / (d.end - d.start) * (width - padL - padR); };
  var y = function (v) { return padT + (hi - v) / hi * (height - padT - padB); };
  var step = G.niceStep(hi, 3);
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': c.country + '’s carbon intensity and Britain’s over the last week' });
  for (var v = 0; v <= hi + 0.01; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 5, y: (y(v) + 3.5).toFixed(1), 'text-anchor': 'end', text: String(v) }));
  }
  for (var t = d.start; t <= d.end; t += 1800) {
    if (G.hm(t) === '00:00' && t < d.end - 6 * 3600) svg.appendChild(s('text', { 'class': 'axis-text', x: (x(t) + 2).toFixed(1), y: height - 5, text: G.day(t).split(' ')[0].slice(0, 2) }));
  }
  function path(rows) {
    var dd = '';
    rows.forEach(function (r, i) { dd += (i && r[0] - rows[i - 1][0] <= 3600 ? 'L' : 'M') + x(r[0]).toFixed(1) + ' ' + y(Math.min(hi, r[1])).toFixed(1); });
    return dd;
  }
  svg.appendChild(s('path', { 'class': 'nb-line nb-gb', d: path(d.gb) }));
  svg.appendChild(s('path', { 'class': 'nb-line nb-them', d: path(c.series) }));
  var cross = s('line', { 'class': 'xhair', y1: padT, y2: height - padB, visibility: 'hidden' });
  svg.appendChild(cross);
  host.appendChild(svg);
  var tip = G.makeTip(host), them = {}, gb = {};
  c.series.forEach(function (r) { them[r[0]] = r[1]; });
  d.gb.forEach(function (r) { gb[r[0]] = r[1]; });
  svg.addEventListener('pointermove', function (e) {
    var b = svg.getBoundingClientRect(), at = d.start + ((e.clientX - b.left) * width / b.width - padL) / (width - padL - padR) * (d.end - d.start);
    var slot = Math.floor(at / 1800) * 1800, rows = [];
    if (num(them[slot])) rows.push({ cls: 'nb-them-sw', value: Math.round(them[slot]) + ' g/kWh', label: c.country });
    if (num(gb[slot])) rows.push({ cls: 'nb-gb-sw', value: Math.round(gb[slot]) + ' g/kWh', label: 'Great Britain' });
    if (at < d.start || at > d.end || !rows.length) { tip.hide(); cross.setAttribute('visibility', 'hidden'); return; }
    var cx = x(slot + 900);
    cross.setAttribute('x1', cx.toFixed(1)); cross.setAttribute('x2', cx.toFixed(1)); cross.setAttribute('visibility', 'visible');
    tip.show(G.day(slot) + ' ' + G.hm(slot), rows, cx * b.width / width, 40);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); cross.setAttribute('visibility', 'hidden'); });
}

function renderNeighbours() {
  var d = nbData;
  if (!d) return;
  var grid = clear(byId('nb-grid')), countries = d.countries || [], why = nbStatus(d);
  if (!countries.length || !d.gb.length) {
    byId('nb-sub').textContent = why || 'No neighbour has a live figure for the last week yet.';
    clear(byId('nb-legend')); byId('nb-note').textContent = '';
    return;
  }
  byId('nb-sub').textContent = 'Carbon intensity over the last week, in g/kWh, half hour by half hour, each against Britain’s.' + (why ? ' ' + why : '');
  // One scale for every chart, so they compare at a glance; the highest values are clipped at 600
  var hi = 0;
  d.gb.forEach(function (r) { hi = Math.max(hi, r[1]); });
  countries.forEach(function (c) { c.series.forEach(function (r) { hi = Math.max(hi, r[1]); }); });
  hi = Math.min(600, Math.max(hi, 40));
  var step = G.niceStep(hi, 3);
  hi = Math.ceil(hi / step) * step;
  countries.forEach(function (c) {
    var card = h('figure', { cls: 'nb-card' }), chart = h('div', { cls: 'nb-chart' });
    card.appendChild(h('figcaption', null, [h('b', { text: c.country }),
      h('span', { text: num(c.mean) ? 'Averaged ' + c.mean + ' g/kWh to Britain’s ' + c.gb_mean + ' over the same half hours. ' : '' }),
      h('span', { cls: 'nb-src', text: c.source + ', ' + c.what })]));
    card.appendChild(chart);
    grid.appendChild(card);
    nbChart(chart, c, d, hi);
  });
  var legend = clear(byId('nb-legend'));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw line nb-them-sw' }), 'The neighbour']));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw line nb-gb-sw' }), 'Great Britain']));
  byId('nb-note').textContent = 'Britain is NESO’s figure for the electricity used here, imports included. RTE’s figure is for French generation and Energinet’s for electricity used in western Denmark, where Viking Link lands.' +
    (d.entsoe ? ' The rest are worked out from each country’s generation mix as reported to ENTSO-E, on NESO’s factors.' : ' The other neighbours need an ENTSO-E key; see the README.') +
    ' Each counts only what is burned at power stations, not the rest of a fuel’s life.';
}

G.poll('/api/neighbours', { onData: function (d) { nbData = d; renderNeighbours(); }, interval: function () { return 900; } });
var nbWidth = window.innerWidth;
window.addEventListener('resize', function () { if (window.innerWidth !== nbWidth) { nbWidth = window.innerWidth; renderNeighbours(); } });
