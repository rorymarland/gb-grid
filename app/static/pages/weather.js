/* Weather: /weather. Open-Meteo's wind, temperature and sunshine for Britain, and how the grid answers them (gridlive/weather.py) */
import G from '/static/js/gb.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num, niceStep = G.niceStep, makeTip = G.makeTip;
var state = { data: null };
var KINDS = { wind: { i: 1, unit: 'm/s', name: 'Wind at hub height', digits: 1 }, temp: { i: 2, unit: '°C', name: 'Temperature', digits: 1 },
  sun: { i: 3, unit: 'W/m²', name: 'Sunshine', digits: 0 } };

function fmt(v, digits) { return num(v) ? v.toFixed(digits) : '–'; }
function gw(v) { return num(v) ? v.toFixed(1) : '–'; }
function dayName(t) { return G.day(t).split(' ')[0]; }
function figure(dl, label, value, unit, note) {
  dl.appendChild(h('div', null, [h('dt', { text: label }), h('dd', null, [value, unit ? h('small', { text: unit }) : null,
    note ? h('span', { cls: 'more', text: note }) : null])]));
}

/* ---------- now ---------- */

function renderNow(d) {
  var c = d.current, g = d.grid || {}, dl = clear(byId('figures'));
  figure(dl, 'Wind at hub height', fmt(c.wind, 1), 'm/s', num(g.wind_gw) ? 'Wind farms are making ' + gw(g.wind_gw) + ' GW' : null);
  figure(dl, 'Temperature', fmt(c.temp, 1), '°C', num(g.demand_gw) ? 'Britain is using ' + gw(g.demand_gw) + ' GW' : null);
  figure(dl, 'Sunshine', fmt(c.sun, 0), 'W/m²', num(g.solar_gw) ? 'Solar panels are making ' + gw(g.solar_gw) + ' GW' : null);
}

/* ---------- the week ahead ---------- */

function extreme(rows, i, after, pick) {
  var best = null;
  rows.forEach(function (r) { if (r[0] >= after && num(r[i]) && (!best || pick(r[i], best[i]))) best = r; });
  return best;
}

function weekChart(host, d, kind, cls) {
  var k = KINDS[kind], rows = d.week.filter(function (r) { return num(r[k.i]); });
  clear(host);
  if (rows.length < 2) return;
  var width = Math.max(300, host.clientWidth || 900), height = 130, padL = 52, padR = 8, padT = 8, padB = 22;
  var start = d.week[0][0], end = d.week[d.week.length - 1][0];
  var vals = rows.map(function (r) { return r[k.i]; });
  var lo = kind === 'temp' ? Math.min.apply(null, vals) : 0, top = Math.max.apply(null, vals);
  var step = niceStep(Math.max(top - lo, kind === 'sun' ? 100 : 4), 3), hi = Math.ceil(top / step) * step;
  lo = Math.floor(lo / step) * step;
  if (hi <= lo) hi = lo + step;
  var x = function (t) { return padL + (t - start) / (end - start) * (width - padL - padR); };
  var y = function (v) { return padT + (hi - v) / (hi - lo) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': k.name + ', the last two days and the week ahead' });
  var nowX = x(Math.min(end, Math.max(start, d.now)));
  svg.appendChild(s('rect', { 'class': 'wx-past', x: padL, y: padT, width: Math.max(0, nowX - padL).toFixed(1), height: height - padT - padB }));
  for (var v = lo; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: (step < 1 ? v.toFixed(1) : String(Math.round(v))) + ' ' + (kind === 'sun' ? '' : k.unit) }));
  }
  for (var t = start - start % 3600; t <= end; t += 3600) {
    if (t >= start && G.hm(t) === '00:00') {
      svg.appendChild(s('line', { 'class': 'grid-line', x1: x(t).toFixed(1), x2: x(t).toFixed(1), y1: padT, y2: height - padB }));
      if (end - t > 6 * 3600) svg.appendChild(s('text', { 'class': 'axis-text', x: (x(t) + 4).toFixed(1), y: height - 6, text: dayName(t) }));
    }
  }
  if (kind === 'temp' && lo < 0 && hi > 0) svg.appendChild(s('line', { 'class': 'wx-zero', x1: padL, x2: width - padR, y1: y(0).toFixed(1), y2: y(0).toFixed(1) }));
  var dd = '';
  rows.forEach(function (r, j) { dd += (j && r[0] - rows[j - 1][0] <= 7200 ? 'L' : 'M') + x(r[0]).toFixed(1) + ' ' + y(r[k.i]).toFixed(1); });
  svg.appendChild(s('path', { 'class': 'wx-line ' + cls, d: dd }));
  svg.appendChild(s('line', { 'class': 'wx-nowline', x1: nowX.toFixed(1), x2: nowX.toFixed(1), y1: padT, y2: height - padB }));
  var cross = s('line', { 'class': 'xhair', y1: padT, y2: height - padB, visibility: 'hidden' });
  svg.appendChild(cross);
  host.appendChild(svg);
  var tip = makeTip(host);
  svg.addEventListener('pointermove', function (e) {
    var b = svg.getBoundingClientRect(), at = start + ((e.clientX - b.left) * width / b.width - padL) / (width - padL - padR) * (end - start);
    var best = null;
    rows.forEach(function (r) { if (!best || Math.abs(r[0] - at) < Math.abs(best[0] - at)) best = r; });
    if (!best || Math.abs(best[0] - at) > 5400) { tip.hide(); cross.setAttribute('visibility', 'hidden'); return; }
    var cx = x(best[0]);
    cross.setAttribute('x1', cx.toFixed(1)); cross.setAttribute('x2', cx.toFixed(1)); cross.setAttribute('visibility', 'visible');
    // Sunshine is the mean over the hour before its time
    var title = kind === 'sun' ? G.day(best[0] - 3600) + ', ' + G.hm(best[0] - 3600) + ' to ' + G.hm(best[0]) : G.day(best[0]) + ', ' + G.hm(best[0]);
    tip.show(title, [{ cls: cls + '-sw', value: fmt(best[k.i], k.digits) + ' ' + k.unit, label: best[0] > d.now ? 'forecast' : '' }], cx * b.width / width, 30);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); cross.setAttribute('visibility', 'hidden'); });
}

function renderWeek(d) {
  var ahead = d.now, rows = d.week;
  byId('week-sub').textContent = 'Hour by hour, from two days ago to a week ahead. The shaded part has passed; the rest is Open-Meteo’s forecast.';
  var windy = extreme(rows, 1, ahead, function (a, b) { return a > b; }), calm = extreme(rows, 1, ahead, function (a, b) { return a < b; });
  byId('wind-note').textContent = windy && calm ? 'Windiest ahead ' + fmt(windy[1], 1) + ' m/s, ' + dayName(windy[0]) + ' ' + G.hm(windy[0]) +
    '; calmest ' + fmt(calm[1], 1) + ' m/s, ' + dayName(calm[0]) + ' ' + G.hm(calm[0]) : '';
  var cold = extreme(rows, 2, ahead, function (a, b) { return a < b; }), warm = extreme(rows, 2, ahead, function (a, b) { return a > b; });
  byId('temp-note').textContent = cold && warm ? 'Coldest ahead ' + fmt(cold[2], 1) + '°C, ' + dayName(cold[0]) + ' ' + G.hm(cold[0]) +
    '; warmest ' + fmt(warm[2], 1) + '°C, ' + dayName(warm[0]) + ' ' + G.hm(warm[0]) : '';
  // The sunniest day ahead, by its total
  var days = {};
  rows.forEach(function (r) { if (r[0] > ahead && num(r[3])) { var key = G.dayKey(r[0] - 3600); days[key] = days[key] || { t: r[0] - 3600, wh: 0 }; days[key].wh += r[3]; } });
  var best = null;
  Object.keys(days).forEach(function (key) { if (!best || days[key].wh > best.wh) best = days[key]; });
  byId('sun-note').textContent = best && best.wh > 0 ? 'Sunniest day ahead ' + dayName(best.t) + ', ' + (best.wh / 1000).toFixed(1) + ' kWh/m² in all' : '';
  weekChart(byId('wind-chart'), d, 'wind', 'wx-wind');
  weekChart(byId('temp-chart'), d, 'temp', 'wx-temp');
  weekChart(byId('sun-chart'), d, 'sun', 'wx-sun');
}

/* ---------- how the grid answers ---------- */

function scatter(host, opts) {
  clear(host);
  var pts = opts.points;
  if (pts.length < 10) { host.appendChild(h('p', { cls: 'wx-empty', text: 'Not enough yet: this fills in as NESO’s figures and the weather records build up.' })); return; }
  var width = Math.max(260, host.clientWidth || 400), height = 240, padL = 46, padR = 8, padT = 10, padB = 34;
  var xs = pts.map(function (p) { return p[opts.xi]; }), ys = pts.map(function (p) { return p[opts.yi]; });
  var xlo = opts.xlo !== undefined ? opts.xlo : Math.min.apply(null, xs), xhi = Math.max.apply(null, xs);
  var ylo = opts.ylo !== undefined ? opts.ylo : Math.min.apply(null, ys), yhi = opts.yhi || Math.max.apply(null, ys);
  var xs_ = niceStep(xhi - xlo || 1, 4), ys_ = niceStep(yhi - ylo || 1, 4);
  xlo = Math.floor(xlo / xs_) * xs_; xhi = Math.ceil(xhi / xs_) * xs_; ylo = Math.floor(ylo / ys_) * ys_; yhi = Math.ceil(yhi / ys_) * ys_;
  var x = function (v) { return padL + (v - xlo) / (xhi - xlo) * (width - padL - padR); };
  var y = function (v) { return padT + (yhi - v) / (yhi - ylo) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': opts.label });
  for (var v = ylo; v <= yhi + 1e-9; v += ys_) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: String(+v.toFixed(2)) + opts.yunit }));
  }
  for (var u = xlo; u <= xhi + 1e-9; u += xs_) {
    svg.appendChild(s('text', { 'class': 'axis-text', x: x(u).toFixed(1), y: height - 18, 'text-anchor': u + xs_ > xhi + 1e-9 ? 'end' : 'middle', text: String(+u.toFixed(2)) }));
  }
  svg.appendChild(s('text', { 'class': 'axis-text wx-axis-title', x: ((padL + width - padR) / 2).toFixed(1), y: height - 3, 'text-anchor': 'middle', text: opts.xtitle }));
  pts.forEach(function (p) {
    svg.appendChild(s('circle', { 'class': 'wx-dot ' + (opts.dotClass ? opts.dotClass(p) : ''), cx: x(p[opts.xi]).toFixed(1), cy: y(p[opts.yi]).toFixed(1), r: opts.r || 2 }));
  });
  if (opts.curve && opts.curve.length > 1) {
    var dd = '';
    opts.curve.forEach(function (c, j) { dd += (j ? 'L' : 'M') + x(c[0]).toFixed(1) + ' ' + y(c[1]).toFixed(1); });
    svg.appendChild(s('path', { 'class': 'wx-curve', d: dd }));
  }
  if (opts.fit) svg.appendChild(s('path', { 'class': 'wx-fit', d: 'M' + x(opts.fit[0][0]).toFixed(1) + ' ' + y(opts.fit[0][1]).toFixed(1) + 'L' + x(opts.fit[1][0]).toFixed(1) + ' ' + y(opts.fit[1][1]).toFixed(1) }));
  host.appendChild(svg);
  if (opts.tip) {
    var tip = makeTip(host);
    svg.addEventListener('pointermove', function (e) {
      var b = svg.getBoundingClientRect(), px = (e.clientX - b.left) * width / b.width, py = (e.clientY - b.top) * height / b.height;
      var best = null, bd = 1e9;
      pts.forEach(function (p) { var dx = x(p[opts.xi]) - px, dy = y(p[opts.yi]) - py, dist = dx * dx + dy * dy; if (dist < bd) { bd = dist; best = p; } });
      if (!best || bd > 200) { tip.hide(); return; }
      var t = opts.tip(best);
      tip.show(t.title, t.rows, x(best[opts.xi]) * b.width / width, y(best[opts.yi]) * b.height / height);
    });
    svg.addEventListener('pointerleave', function () { tip.hide(); });
  }
}

function legend(id, items) {
  var ul = clear(byId(id));
  items.forEach(function (it) { ul.appendChild(h('li', null, [h('i', { cls: 'sw ' + it[0] }), it[1]])); });
}

function renderRelations(d) {
  var r = d.relations;
  // Wind: the speed where the typical hour first reaches half of capacity
  var half = null;
  r.wind.curve.forEach(function (c) { if (half === null && c[1] >= 50) half = c[0]; });
  byId('wind-rel-sub').textContent = r.wind.n ? r.wind.n + ' hours. ' + (half !== null ? 'In a typical hour the farms reach half their capacity at about ' + Math.round(half) + ' m/s.' : '') : '';
  scatter(byId('wind-rel'), { points: r.wind.points, xi: 0, yi: 1, xlo: 0, ylo: 0, yhi: 100, yunit: '%', xtitle: 'Wind at hub height, m/s', curve: r.wind.curve,
    label: 'Wind farms’ output as a share of capacity against wind speed at hub height, each hour of the last 90 days', dotClass: function () { return 'wx-wind-dot'; } });
  legend('wind-rel-legend', [['wx-wind-dot-sw', 'An hour'], ['line wx-curve-sw', 'The typical hour at each speed (median)']]);

  var t = r.temp, pts = t.points.map(function (p) { return [p[1], p[2], p[3], p[0]]; }), fit = null;
  if (num(t.per_degree)) {
    var wd = pts.filter(function (p) { return !p[2] && p[0] < 15.5; });
    var mx = wd.reduce(function (a, p) { return a + p[0]; }, 0) / wd.length, my = wd.reduce(function (a, p) { return a + p[1]; }, 0) / wd.length;
    var lo = Math.min.apply(null, wd.map(function (p) { return p[0]; })), hi = Math.max.apply(null, wd.map(function (p) { return p[0]; }));
    fit = [[lo, my - t.per_degree * (lo - mx)], [hi, my - t.per_degree * (hi - mx)]];
  }
  byId('temp-rel-sub').textContent = pts.length ? pts.length + ' days. ' + (num(t.per_degree) ? 'On working days below 15.5°C, each degree colder adds about ' + t.per_degree.toFixed(2) + ' GW to average demand.' : '') : '';
  scatter(byId('temp-rel'), { points: pts, xi: 0, yi: 1, yunit: '', xtitle: 'Average temperature, °C (demand in GW)', fit: fit, r: 3.2,
    label: 'Each day’s average national demand against its average temperature over the last 90 days',
    dotClass: function (p) { return p[2] ? 'wx-weekend' : 'wx-weekday'; },
    tip: function (p) { return { title: G.date(Date.parse(p[3] + 'T12:00:00Z') / 1000), rows: [{ value: p[1].toFixed(1) + ' GW', label: 'average demand' }, { value: p[0].toFixed(1) + '°C', label: 'average temperature' }] }; } });
  legend('temp-rel-legend', [['wx-weekday-sw', 'Working day'], ['wx-weekend-sw', 'Weekend']].concat(fit ? [['line wx-fit-sw', 'The line through working days below 15.5°C']] : []));

  byId('sun-rel-sub').textContent = r.sun.n ? r.sun.n + ' daylight hours.' : '';
  scatter(byId('sun-rel'), { points: r.sun.points, xi: 0, yi: 1, xlo: 0, ylo: 0, yunit: '', xtitle: 'Sunshine, W/m² (solar in GW)', curve: r.sun.curve,
    label: 'Solar output against sunshine, each hour of the last 90 days', dotClass: function () { return 'wx-sun-dot'; } });
  legend('sun-rel-legend', [['wx-sun-dot-sw', 'An hour'], ['line wx-curve-sw', 'The typical hour at each level (median)']]);
}

/* ---------- the page ---------- */

function render(d) {
  state.data = d;
  byId('waiting').hidden = Boolean(d.ready);
  byId('content').hidden = !d.ready;
  if (!d.ready) { G.setLive('', 'Fetching the weather'); return; }
  renderNow(d);
  renderWeek(d);
  renderRelations(d);
  G.setLive('good', 'Open-Meteo, for ' + G.hm(d.hour));
}

G.poll('/api/weather', {
  interval: function () { return 900; },
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
