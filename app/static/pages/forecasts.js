/* Forecasts: /forecasts. How close NESO's forecasts came (gridlive/forecasts.py) */
import G from '/static/js/gb.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num, niceStep = G.niceStep, makeTip = G.makeTip;
var state = { data: null };
var dayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: G.TZ });
var shortDay = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric', timeZone: G.TZ });
var monthFmt = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: 'UTC' });
var longDate = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: G.TZ });
function fromIso(text) { return new Date(text + 'T12:00:00Z'); }

/* ---------- formatting ---------- */

function pct(v, dp) { return num(v) ? (v * 100).toFixed(dp === undefined ? 1 : dp) + '%' : '–'; }
function amount(v, unit) {
  if (!num(v)) return '–';
  if (unit === 'MW') return Math.abs(v) >= 1000 ? (v / 1000).toFixed(2) + ' GW' : G.int(v) + ' MW';
  return (Math.abs(v) >= 10 ? G.int(v) : v.toFixed(1)) + ' ' + unit;
}
/* The headline measure each forecast is judged by */
function measure(k, sc) {
  if (!sc) return '–';
  if (k.measure === 'cap') return pct(sc.cap_pct) + ' of capacity';
  if (k.measure === 'pct') return pct(sc.pct) + ' of demand';
  return amount(sc.mae, k.unit);
}
/* "6.3°C, 3.0° below normal" */
function tempText(tp) {
  if (!tp || !num(tp[0])) return '';
  var diff = num(tp[1]) ? Math.round((tp[0] - tp[1]) * 10) / 10 : null;
  return tp[0].toFixed(1) + '°C' + (diff === null ? '' : Math.abs(diff) < 0.5 ? ', about normal' : ', ' + Math.abs(diff).toFixed(1) + '° ' + (diff < 0 ? 'below' : 'above') + ' normal');
}
function measureValue(k, sc) { return !sc ? null : k.measure === 'cap' ? sc.cap_pct : k.measure === 'pct' ? sc.pct : sc.mae; }
function lean(k, sc) {
  if (!sc) return '–';
  var rel = sc.mean ? Math.abs(sc.bias) / sc.mean : 0;
  if (rel < 0.01) return 'Even';
  return amount(Math.abs(sc.bias), k.unit) + (sc.bias > 0 ? ' too high' : ' too low');
}

/* ---------- the latest day ---------- */

function dayChart(box, k) {
  var series = k.day.series, unit = k.unit;
  var width = Math.max(260, box.clientWidth || 400), height = 190, padL = unit === 'MW' ? 52 : 58, padR = 6, padT = 10, padB = 24;
  var values = [];
  series.forEach(function (p) { values.push(p[1], p[2]); });
  var lo = Math.min(0, Math.min.apply(null, values)), top = Math.max.apply(null, values.concat([1]));
  var step = niceStep(top - lo, 3), hi = Math.ceil(top / step) * step, low = Math.floor(lo / step) * step;
  var start = series[0][0], end = series[series.length - 1][0] + 1800;
  var x = function (t) { return padL + (t + 900 - start) / (end - start) * (width - padL - padR); };
  var y = function (v) { return padT + (hi - v) / (hi - low) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': k.title + ': forecast and outcome on ' + dayFmt.format(fromIso(k.day.d)) });
  for (var v = low; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end',
      text: unit === 'MW' ? (v / 1000).toFixed(step >= 1000 ? 0 : 1) + ' GW' : G.int(v) + ' ' + unit }));
  }
  [0, 6, 12, 18].forEach(function (hr) {
    var t = start + hr * 3600;
    svg.appendChild(s('text', { 'class': 'axis-text', x: x(t - 900).toFixed(1), y: height - 6, 'text-anchor': hr ? 'middle' : 'start', text: G.hm(t) }));
  });
  // The gap between them, shaded; then the outcome and the forecast
  var gap = '', fc = '', act = '';
  series.forEach(function (p, i) { fc += (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1); act += (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[2]).toFixed(1); });
  gap = fc + series.slice().reverse().map(function (p) { return 'L' + x(p[0]).toFixed(1) + ' ' + y(p[2]).toFixed(1); }).join('') + 'Z';
  svg.appendChild(s('path', { 'class': 'fc-gap', d: gap }));
  svg.appendChild(s('path', { 'class': 'fc-act', d: act }));
  svg.appendChild(s('path', { 'class': 'fc-fc', d: fc }));
  var cross = s('line', { 'class': 'xhair', y1: padT, y2: height - padB, visibility: 'hidden' });
  svg.appendChild(cross);
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var r = svg.getBoundingClientRect(), at = start + ((e.clientX - r.left) * width / r.width - padL) / (width - padL - padR) * (end - start);
    var i = Math.max(0, Math.min(series.length - 1, Math.floor((at - start) / 1800))), p = series[i];
    var cx = x(p[0]);
    cross.setAttribute('x1', cx.toFixed(1)); cross.setAttribute('x2', cx.toFixed(1)); cross.setAttribute('visibility', 'visible');
    tip.show(G.hm(p[0]) + ' to ' + G.hm(p[0] + 1800), [
      { cls: 'fc-fc-sw', value: amount(p[1], unit), label: 'forecast' },
      { cls: 'fc-act-sw', value: amount(p[2], unit), label: k.key === 'wind' ? 'made, plus turned down' : 'what happened' },
      { value: (p[1] - p[2] > 0 ? '+' : p[1] - p[2] < 0 ? '−' : '') + amount(Math.abs(p[1] - p[2]), unit), label: p[1] > p[2] ? 'too high' : p[1] < p[2] ? 'too low' : 'spot on' }
    ], cx * r.width / width, 30);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); cross.setAttribute('visibility', 'hidden'); });
}

function renderDays(d) {
  var host = clear(byId('days')), charts = [];
  d.kinds.filter(function (k) { return k.n && k.day && k.day.series.length > 4 && k.key !== 'carbon'; }).forEach(function (k) {
    var chart = h('div', { cls: 'chart fc-day-chart' });
    var sc = k.day.score, leaning = lean(k, sc);
    host.appendChild(h('figure', { cls: 'fc-day' }, [
      h('figcaption', null, [h('b', { text: k.title }), h('span', { text: dayFmt.format(fromIso(k.day.d)) })]),
      h('p', { cls: 'fc-day-score' }, [h('strong', { text: measure(k, sc) }), h('span', { text: ' typical miss, ' + (leaning === 'Even' ? 'no lean' : leaning) })]),
      // A line for every card, so the charts line up whether or not there's a temperature
      h('p', { cls: 'fc-day-note', text: k.day.temp ? 'Temperature ' + tempText(k.day.temp) : '' }),
      chart
    ]));
    charts.push([chart, k]);
  });
  // Drawn once every figure is in place, so each knows its own width
  charts.forEach(function (c) { dayChart(c[0], c[1]); });
  var legend = h('ul', { cls: 'fc-legend' }, [
    h('li', null, [h('i', { cls: 'sw line fc-fc-sw' }), 'Forecast']),
    h('li', null, [h('i', { cls: 'sw line fc-act-sw' }), 'What happened']),
    h('li', null, [h('i', { cls: 'sw fc-gap-sw' }), 'The miss'])
  ]);
  host.appendChild(legend);
}

/* ---------- the scorecard ---------- */

function renderScore(d) {
  var body = clear(byId('score'));
  d.kinds.filter(function (k) { return k.n; }).forEach(function (k) {
    var then = k.year_ago, now30 = k.d30, change = null;
    if (then && now30) {
      var a = measureValue(k, now30), b = measureValue(k, then);
      if (num(a) && num(b) && b) change = (a - b) / b;
    }
    var worst = k.worst && k.worst[0];
    body.appendChild(h('tr', null, [
      h('th', { scope: 'row' }, [h('span', { cls: 'name', text: k.title }), h('small', { text: (k.own ? 'Kept here since ' : 'Since ') + longDate.format(new Date(k.since * 1000)) })]),
      h('td', { cls: 'num' }, [h('b', { text: measure(k, now30) }), k.measure !== 'abs' ? h('small', { text: amount(now30 && now30.mae, k.unit) }) : null]),
      h('td', { cls: 'num', text: lean(k, now30) }),
      h('td', { cls: 'num fc-hide-sm' }, then ? [measure(k, then), h('small', { text: change === null ? '' : Math.abs(change) < 0.03 ? 'About the same now' : (change < 0 ? 'Better now, by ' : 'Worse now, by ') + Math.round(Math.abs(change) * 100) + '%' })] : ['–']),
      h('td', { cls: 'num fc-hide-sm' }, worst ? [shortDay.format(fromIso(worst.d)), h('small', { text: amount(worst.mae, k.unit) + ' typical miss, ' + (worst.bias > 0 ? 'too high' : 'too low') + (worst.temp ? '; ' + tempText(worst.temp) : '') })] : ['–'])
    ]));
  });
}

/* ---------- the long view ---------- */

function longChart(box, k) {
  var months = k.months, n = months.length;
  if (n < 2) return;
  var pctMode = k.measure !== 'abs';
  var width = Math.max(260, box.clientWidth || 500), height = 170, padL = 50, padR = 8, padT = 10, padB = 22;
  var top = Math.max.apply(null, months.map(function (m) { return m.v; })), step = niceStep(top, 3), hi = Math.ceil(top / step) * step;
  var x = function (i) { return padL + (i + 0.5) / n * (width - padL - padR); };
  var y = function (v) { return padT + (1 - v / hi) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': k.title + ': typical miss each month' });
  for (var v = 0; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end',
      text: pctMode ? (v * 100).toFixed(Math.round(step * 1000) % 10 ? 1 : 0) + '%' : G.int(v) + ' ' + k.unit }));
  }
  var line = '', area = 'M' + x(0).toFixed(1) + ' ' + y(0).toFixed(1);
  months.forEach(function (m, i) {
    line += (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(m.v).toFixed(1);
    area += 'L' + x(i).toFixed(1) + ' ' + y(m.v).toFixed(1);
    if (m.m.slice(5) === '01' && (n < 40 || Number(m.m.slice(0, 4)) % 2 === 0)) {
      svg.appendChild(s('text', { 'class': 'axis-text', x: x(i).toFixed(1), y: height - 5, 'text-anchor': 'middle', text: m.m.slice(0, 4) }));
    }
  });
  area += 'L' + x(n - 1).toFixed(1) + ' ' + y(0).toFixed(1) + 'Z';
  svg.appendChild(s('path', { 'class': 'fc-long-area', d: area }));
  svg.appendChild(s('path', { 'class': 'fc-long-line', d: line }));
  var dot = s('circle', { 'class': 'fc-long-dot', r: 4, visibility: 'hidden' });
  svg.appendChild(dot);
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var r = svg.getBoundingClientRect(), i = Math.round(((e.clientX - r.left) * width / r.width - padL) / (width - padL - padR) * n - 0.5);
    i = Math.max(0, Math.min(n - 1, i));
    var m = months[i];
    dot.setAttribute('cx', x(i).toFixed(1)); dot.setAttribute('cy', y(m.v).toFixed(1)); dot.setAttribute('visibility', 'visible');
    tip.show(monthFmt.format(new Date(m.m + '-15T00:00:00Z')), [
      { value: pctMode ? pct(m.v) : amount(m.v, k.unit), label: k.measure === 'cap' ? 'of capacity, typical miss' : k.measure === 'pct' ? 'of demand, typical miss' : 'typical miss' },
      k.measure !== 'abs' ? { value: amount(m.mae, k.unit), label: 'typical miss' } : null,
      { value: lean(k, m), label: 'lean' }
    ].filter(Boolean), x(i) * r.width / width, 40);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); dot.setAttribute('visibility', 'hidden'); });
}

function renderLongs(d) {
  var host = clear(byId('longs')), charts = [];
  d.kinds.filter(function (k) { return k.months && k.months.length >= 2; }).forEach(function (k) {
    var first = k.months.slice(0, 12), last = k.months.slice(-12);
    var avg = function (list) { return list.reduce(function (a, m) { return a + m.v; }, 0) / list.length; };
    var note = '';
    if (k.months.length >= 36) {
      var change = (avg(last) - avg(first)) / avg(first);
      note = 'The last 12 months against the first 12: ' + (Math.abs(change) < 0.05 ? 'about the same.' : Math.round(Math.abs(change) * 100) + '% ' + (change < 0 ? 'smaller misses.' : 'bigger misses.'));
    }
    var chart = h('div', { cls: 'chart fc-long-chart' });
    host.appendChild(h('figure', { cls: 'fc-long' }, [h('figcaption', null, [h('b', { text: k.title }), h('span', { text: note })]), chart]));
    charts.push([chart, k]);
  });
  charts.forEach(function (c) { longChart(c[0], c[1]); });
}

/* ---------- kept here ---------- */

function renderOwn(d) {
  var list = clear(byId('own'));
  d.kinds.filter(function (k) { return k.own; }).forEach(function (k) {
    var text;
    if (!k.collecting) text = 'Starting with the next forecast.';
    else if (!k.n) text = 'Kept since ' + longDate.format(new Date(k.collecting * 1000)) + '. The first scores come once a forecast day is over.';
    else {
      var days = Math.round(k.n / 48);
      text = 'Kept since ' + longDate.format(new Date(k.collecting * 1000)) + '; ' + G.plural(days, 'day', 'days') + ' scored so far' +
        (days < 30 ? ', with the 30-day score filling in.' : '.') + (k.d30 ? ' Typical miss: ' + measure(k, k.d30) + '.' : '');
    }
    list.appendChild(h('li', null, [h('b', { text: k.title }), h('span', { text: text })]));
  });
}

/* ---------- the page ---------- */

function render(d) {
  state.data = d;
  var ready = d.kinds.some(function (k) { return k.n && !k.own; });
  byId('waiting').hidden = ready;
  byId('content').hidden = !ready;
  if (!ready) { G.setLive('', 'Fetching NESO’s forecast records'); return; }
  renderDays(d);
  renderScore(d);
  renderLongs(d);
  renderOwn(d);
  var wind = d.kinds.filter(function (k) { return k.key === 'wind'; })[0];
  G.setLive('good', wind && wind.d30 ? 'Wind forecast typical miss: ' + measure(wind, wind.d30) : 'Forecast records up to date');
}

G.poll('/api/forecasts', {
  interval: function () { return 1800; },
  onData: render,
  onTick: function (p) { if (G.offline(p)) G.setLive('bad', 'Can’t reach the dashboard server'); }
});

var resizeTimer = null, lastWidth = window.innerWidth;
window.addEventListener('resize', function () {
  if (window.innerWidth === lastWidth) return;
  lastWidth = window.innerWidth;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () { if (state.data) render(state.data); }, 150);
});
