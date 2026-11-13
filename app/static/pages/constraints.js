/* Constraints: /constraints. Boundary flows against limits, wind turned down and constraint costs (gridlive/constraints.py) */
import G from '/static/js/gb.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num, hm = G.hm, niceStep = G.niceStep, makeTip = G.makeTip;
var state = { data: null, day: 'tomorrow', focus: null, geo: null, bounds: null };
try {
  var savedDay = localStorage.getItem('gb-constraints-day');
  if (savedDay === 'today' || savedDay === 'tomorrow') state.day = savedDay;
} catch (e) { /* storage unavailable */ }

var dayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: G.TZ });
var shortDay = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: G.TZ });
var tickDay = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', timeZone: G.TZ });
var dateShort = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: G.TZ });
function isoDay(text) { return new Date(text + 'T12:00:00Z'); }

/* ---------- formatting ---------- */

function pct(v) { return Math.round(v * 100) + '%'; }
function gwh(mwh) { return mwh >= 10000 ? G.int(mwh / 1000) : (mwh / 1000).toLocaleString('en-GB', { maximumFractionDigits: 1, minimumFractionDigits: 1 }); }
function twh(mwh) { return (mwh / 1e6).toLocaleString('en-GB', { maximumFractionDigits: 1, minimumFractionDigits: 1 }); }
function money(pounds) {
  if (!num(pounds)) return '–';
  if (pounds >= 1e9) return '£' + (pounds / 1e9).toFixed(2) + 'bn';
  if (pounds >= 1e6) return '£' + (pounds >= 1e8 ? Math.round(pounds / 1e6) : (pounds / 1e6).toFixed(1)) + 'm';
  if (pounds >= 1e3) return '£' + Math.round(pounds / 1e3) + 'k';
  return '£' + Math.round(pounds);
}
function hours(halfHours) { return halfHours === 1 ? 'half an hour' : halfHours % 2 ? (halfHours / 2).toFixed(1) + ' hours' : (halfHours / 2) + (halfHours === 2 ? ' hour' : ' hours'); }
function name(g) { return g.label || g.code; }
function level(peak) { return peak >= 1 ? 'over' : peak >= 0.9 ? 'near' : 'ok'; }
function figure(dl, label, value, unit, note) {
  dl.appendChild(h('div', null, [h('dt', { text: label }), h('dd', null, [value, unit ? h('small', { text: unit }) : null,
    note ? h('span', { cls: 'more', text: note }) : null])]));
}

/* ---------- today or tomorrow ---------- */

function renderOutlook(d) {
  var day = d.days[state.day], groups = day.groups;
  Array.prototype.forEach.call(byId('day-toggle').querySelectorAll('button'), function (b) {
    b.setAttribute('aria-pressed', String(b.dataset.day === state.day));
  });
  var when = state.day === 'tomorrow' ? 'Tomorrow' : 'Today';
  var head = byId('headline');
  if (!groups.length) {
    head.textContent = state.day === 'tomorrow' ? 'NESO hasn’t published tomorrow’s forecast flows yet. They usually appear in the afternoon.' : 'No forecast flows for today.';
  } else {
    var top = groups[0], over = groups.filter(function (g) { return g.over > 0; });
    head.textContent = when + ', ' + (top.peak >= 1
      ? name(top) + (top.boundary ? ' (' + top.boundary + ')' : '') + ' is forecast to need ' + pct(top.peak) + ' of its limit at its busiest, over it for ' + hours(top.over) + '.'
      : 'every boundary is forecast to stay within its limit. The busiest is ' + name(top) + ', at ' + pct(top.peak) + '.') +
      (over.length > 1 ? ' ' + (over.length - 1) + ' other ' + (over.length === 2 ? 'boundary is' : 'boundaries are') + ' forecast over too.' : '');
  }
  byId('groups-sub').textContent = 'Each boundary’s busiest half hour ' + (state.day === 'tomorrow' ? 'tomorrow' : 'today') +
    ' as a share of its limit, from NESO’s forecast the day before (' + dayFmt.format(isoDay(day.d)) + '). Choose one to chart it.';
  var list = clear(byId('groups'));
  var shown = groups.filter(function (g) { return g.peak >= 0.5 || g.code === state.focus; }).slice(0, 9);
  var top2 = Math.max(1.2, Math.max.apply(null, shown.map(function (g) { return g.peak; }).concat([0])));
  shown.forEach(function (g) {
    var row = h('button', { type: 'button', cls: 'cn-group ' + level(g.peak), 'data-code': g.code, 'aria-pressed': String(g.code === state.focus) }, [
      h('span', { cls: 'nm' }, [name(g), h('small', { text: (g.boundary ? 'Boundary ' + g.boundary + ', ' : '') + 'NESO code ' + g.code })]),
      h('span', { cls: 'track' }, [h('i', { style: 'width:' + Math.min(100, g.peak / top2 * 100).toFixed(1) + '%' }),
        h('b', { cls: 'limit', style: 'left:' + (1 / top2 * 100).toFixed(1) + '%', 'aria-hidden': 'true' })]),
      h('span', { cls: 'val' }, [pct(g.peak), h('small', { text: g.over ? 'Over for ' + hours(g.over) : g.near ? 'Near for ' + hours(g.near) : 'Within its limit' })])
    ]);
    list.appendChild(row);
  });
  if (groups.length > shown.length) {
    list.appendChild(h('p', { cls: 'cn-more', text: (groups.length - shown.length) + ' more boundaries stay under half their limit.' }));
  }
  drawMap(d);
}

/* ---------- the map ---------- */

var VIEW = [-360, -1230, 680, 900];   // Scotland and the north of England, where the constraints are   // Great Britain, in map units (km)

function drawMap(d) {
  var box = clear(byId('map'));
  if (!state.geo || !state.bounds) return;
  var svg = s('svg', { viewBox: VIEW.join(' '), preserveAspectRatio: 'xMidYMid meet', 'aria-hidden': 'true' });
  var land = s('g', { transform: 'scale(0.5)' });
  state.geo.units.forEach(function (u) {
    land.appendChild(s('path', { d: u.d, 'class': u.kind === 'gb' ? 'land' : 'land-near', 'fill-rule': 'evenodd' }));
  });
  svg.appendChild(land);
  // Each map boundary's busiest group on the chosen day
  var byBoundary = {};
  d.days[state.day].groups.forEach(function (g) {
    if (g.boundary && (!byBoundary[g.boundary] || g.peak > byBoundary[g.boundary].peak)) byBoundary[g.boundary] = g;
  });
  var lines = s('g', { transform: 'scale(0.5)' });
  state.bounds.boundaries.forEach(function (b) {
    var g = byBoundary[b.id];
    lines.appendChild(s('path', { d: b.d, 'class': 'cn-bnd' + (g ? ' key ' + level(g.peak) : '') }));
  });
  svg.appendChild(lines);
  // Wind farms turned down this week, sized by energy
  var farms = d.wind.map7 || [], biggest = farms.length ? farms[0].mwh : 1;
  farms.slice().reverse().forEach(function (f) {
    var r = 4 + 18 * Math.sqrt(f.mwh / biggest);
    svg.appendChild(s('circle', { cx: f.x, cy: f.y, r: r.toFixed(1), 'class': 'cn-farm' }));
  });
  state.bounds.boundaries.forEach(function (b) {
    var g = byBoundary[b.id];
    if (!g) return;
    // Label the boundary at its middle point, just above the line
    var pts = (b.d.match(/-?\d+(\.\d+)? -?\d+(\.\d+)?/g) || []).map(function (p) { return p.split(' ').map(Number); });
    if (!pts.length) return;
    var mid = pts[Math.floor(pts.length / 2)];
    svg.appendChild(s('text', { x: (mid[0] / 2).toFixed(1), y: (mid[1] / 2 - 12).toFixed(1), 'class': 'cn-bnd-label ' + level(g.peak), 'text-anchor': 'middle', text: b.id + ' ' + pct(g.peak) }));
  });
  box.appendChild(svg);
  var legend = clear(byId('map-legend'));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw line over' }), 'Over its limit']));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw line ok' }), 'Within it']));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw line faint' }), 'Other boundaries']));
  if (farms.length) legend.appendChild(h('li', null, [h('i', { cls: 'sw dot' }), 'Wind turned down, last 7 days']));
}

/* ---------- flow against the limit ---------- */

function renderFlow(d) {
  var series = d.series || [], focus = d.groups.concat(d.days.today.groups, d.days.tomorrow.groups).filter(function (g) {
    return g.code === d.focus || (g.variants && g.variants.indexOf(d.focus) >= 0);
  })[0];
  var label = focus ? name(focus) : d.focus;
  byId('flow-title').textContent = 'Flow against the limit: ' + (label || '–');
  byId('flow-sub').textContent = 'Forecast flow and limit every half hour for the last week and tomorrow, as NESO forecast them the day before' +
    (d.focus ? ' (' + d.focus + ')' : '') + '. Where the flow is above the limit, NESO expects to turn generation down on one side and up on the other.';
  var box = clear(byId('flow-chart'));
  if (series.length < 2) return;
  var width = Math.max(300, box.clientWidth || 800), height = 260, padL = 62, padR = 8, padT = 12, padB = 28;
  var start = series[0][0], end = series[series.length - 1][0] + 1800;
  var top = Math.max.apply(null, series.map(function (p) { return Math.max(p[1], p[2]); }));
  var low = Math.min(0, Math.min.apply(null, series.map(function (p) { return p[1]; })));
  var step = niceStep(top - low || 1, 4), hi = Math.ceil(top / step) * step, lo = Math.floor(low / step) * step;
  var x = function (t) { return padL + (t - start) / (end - start) * (width - padL - padR); };
  var y = function (v) { return padT + (hi - v) / (hi - lo) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img',
    'aria-label': 'Forecast flow and limit for ' + label + ' over the last week and tomorrow' });
  for (var v = lo; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 8, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: G.int(v) + ' MW' }));
  }
  // Tomorrow, shaded
  var tomorrowStart = d.days.tomorrow.groups.length ? Math.max(start, localMidnight(d.days.tomorrow.d)) : null;
  if (tomorrowStart && tomorrowStart < end) {
    svg.appendChild(s('rect', { 'class': 'cn-ahead', x: x(tomorrowStart).toFixed(1), y: padT, width: (x(end) - x(tomorrowStart)).toFixed(1), height: height - padT - padB }));
    var room = x(end) - x(tomorrowStart) > 64;
    svg.appendChild(s('text', { 'class': 'axis-text', x: (room ? x(tomorrowStart) + 6 : x(end) - 4).toFixed(1), y: padT + 12, 'text-anchor': room ? 'start' : 'end', text: 'Tomorrow' }));
  }
  // Above the limit, shaded; then the limit (stepped) and the flow
  var over = '', limit = '', flow = '';
  series.forEach(function (p, i) {
    var x0 = x(p[0]), x1 = x(p[0] + 1800);
    if (p[1] > p[2]) over += 'M' + x0.toFixed(1) + ' ' + y(p[2]).toFixed(1) + 'H' + x1.toFixed(1) + 'V' + y(p[1]).toFixed(1) + 'H' + x0.toFixed(1) + 'Z';
    limit += (i ? 'H' + x0.toFixed(1) + 'V' : 'M' + x0.toFixed(1) + ' ') + y(p[2]).toFixed(1) + (i === series.length - 1 ? 'H' + x1.toFixed(1) : '');
    var gap = i && p[0] - series[i - 1][0] > 1800;
    flow += (i && !gap ? 'L' : 'M') + ((x0 + x1) / 2).toFixed(1) + ' ' + y(p[1]).toFixed(1);
  });
  svg.appendChild(s('path', { 'class': 'cn-over', d: over }));
  svg.appendChild(s('path', { 'class': 'cn-limit', d: limit }));
  svg.appendChild(s('path', { 'class': 'cn-flow', d: flow }));
  // Day ticks
  for (var t = localMidnight(new Date(start * 1000).toISOString().slice(0, 10)); t < end; t += 86400) {
    var mid = t + 43200;
    var every = width < 560 ? 2 : 1, k = Math.round((t - start) / 86400);
    if (mid > start && mid < end && (k % every === 0)) svg.appendChild(s('text', { 'class': 'axis-text', x: x(mid).toFixed(1), y: height - 8, 'text-anchor': 'middle', text: tickDay.format(new Date(mid * 1000)) }));
  }
  var nowX = x(d.now);
  if (nowX > padL && nowX < width - padR) svg.appendChild(s('line', { 'class': 'cn-now', x1: nowX.toFixed(1), x2: nowX.toFixed(1), y1: padT, y2: height - padB }));
  var cross = s('line', { 'class': 'xhair', y1: padT, y2: height - padB, visibility: 'hidden' });
  svg.appendChild(cross);
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var r = svg.getBoundingClientRect(), at = start + ((e.clientX - r.left) * width / r.width - padL) / (width - padL - padR) * (end - start);
    var i = Math.floor((at - start) / 1800), p = series.filter(function (q) { return q[0] <= at && at < q[0] + 1800; })[0];
    if (!p) { tip.hide(); cross.setAttribute('visibility', 'hidden'); return; }
    var cx = x(p[0] + 900);
    cross.setAttribute('x1', cx.toFixed(1)); cross.setAttribute('x2', cx.toFixed(1)); cross.setAttribute('visibility', 'visible');
    tip.show(shortDay.format(new Date(p[0] * 1000)) + ', ' + hm(p[0]) + ' to ' + hm(p[0] + 1800), [
      { cls: 'cn-flow-sw', value: G.int(p[1]) + ' MW', label: 'forecast flow' },
      { cls: 'cn-limit-sw', value: G.int(p[2]) + ' MW', label: 'limit' },
      { value: pct(p[1] / p[2]), label: p[1] > p[2] ? 'over the limit' : 'of the limit' }
    ], cx * r.width / width, 40);
    void i;
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); cross.setAttribute('visibility', 'hidden'); });
  var legend = clear(byId('flow-legend'));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw line flow' }), 'Forecast flow']));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw line limit' }), 'Limit']));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw over-area' }), 'Above the limit']));
}

function localMidnight(isoDate) {
  /* UTC epoch of UK midnight at the start of an ISO date */
  var noon = Date.parse(isoDate + 'T12:00:00Z') / 1000;
  var parts = new Intl.DateTimeFormat('en-GB', { timeZone: G.TZ, hour: '2-digit', hourCycle: 'h23' }).format(new Date(noon * 1000));
  return noon - 43200 - (Number(parts) - 12) * 3600;
}

/* ---------- bar charts: wind turned down, and costs ---------- */

function bars(box, rows, opts) {
  clear(box);
  if (!rows.length) return;
  var width = Math.max(300, box.clientWidth || 800), height = 200, padL = 62, padR = 6, padT = 12, padB = 26;
  var n = rows.length, slotW = (width - padL - padR) / n;
  var totals = rows.map(function (r) { return r.parts.reduce(function (a, b) { return a + b; }, 0); });
  var top = Math.max.apply(null, totals.concat([1e-9])), step = niceStep(top, 3), hi = Math.ceil(top / step) * step;
  var y = function (v) { return padT + (1 - v / hi) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': opts.label });
  for (var v = 0; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 8, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: opts.axis(v, step) }));
  }
  var gap = Math.min(3, slotW * 0.25), barW = Math.max(1.5, slotW - gap);
  rows.forEach(function (r, i) {
    var x0 = padL + i * slotW + gap / 2, base = y(0);
    r.parts.forEach(function (value, k) {
      if (value <= 0) return;
      var top0 = y(value + r.parts.slice(0, k).reduce(function (a, b) { return a + b; }, 0));
      var hgt = Math.max(1, base - top0 - (k ? 1 : 0));
      svg.appendChild(s('rect', { 'class': opts.classes[k], x: x0.toFixed(1), y: (base - hgt).toFixed(1), width: barW.toFixed(1), height: hgt.toFixed(1), rx: 1.5 }));
      base -= hgt + 1;
    });
    if ((n - 1 - i) % (slotW * 7 < 70 ? 14 : 7) === 0) {
      svg.appendChild(s('text', { 'class': 'axis-text', x: (x0 + barW / 2).toFixed(1), y: height - 6, 'text-anchor': 'middle', text: dateShort.format(isoDay(r.d)) }));
    }
  });
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var b = svg.getBoundingClientRect(), i = Math.floor(((e.clientX - b.left) * width / b.width - padL) / slotW);
    if (i < 0 || i >= n) { tip.hide(); return; }
    tip.show(shortDay.format(isoDay(rows[i].d)), opts.tip(rows[i]), (padL + (i + 0.5) * slotW) * b.width / width, 40);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); });
}

function renderWind(d) {
  var w = d.wind, dl = clear(byId('wind-figures'));
  if (w.latest) figure(dl, shortDay.format(isoDay(w.latest)) + (w.latest === d.days.today.d ? ', so far' : ''), gwh(w.latest_mwh || 0), 'GWh');
  figure(dl, 'Last 30 days', gwh(w.mwh30), 'GWh', w.share ? pct(w.share.share) + ' of what wind farms could have made' : null);
  figure(dl, 'Last 365 days', twh(w.mwh365), 'TWh', 'About ' + G.int(w.mwh365 / 2700) + ' homes’ electricity for a year');
  byId('wind-sub').textContent = 'Energy wind farms were paid not to generate, each day for the last 60 days, from NESO’s record of accepted bids. Most is in Scotland, behind B4 and B6.';
  bars(byId('wind-chart'), w.days.map(function (r) { return { d: r[0], parts: [r[1]] }; }), {
    label: 'Wind turned down each day for the last 60 days',
    classes: ['cn-wind'],
    axis: function (v, step) { return (v / 1000).toFixed(step % 1000 === 0 ? 0 : 1) + ' GWh'; },
    tip: function (r) { return [{ cls: 'cn-wind', value: gwh(r.parts[0]) + ' GWh', label: 'turned down' }]; }
  });
  var tbody = clear(byId('farms'));
  (w.farms30 || []).forEach(function (f) {
    var nameCell = num(f.sid) ? G.stationName(f.n, f.sid) : h('span', { cls: 'name', text: f.n });
    tbody.appendChild(h('tr', null, [
      h('th', { scope: 'row' }, nameCell),
      h('td', { cls: 'num', text: G.int(f.mwh) }),
      h('td', { cls: 'num cn-hide-sm', text: num(f.cap) ? G.int(f.cap) : '–' }),
      h('td', { cls: 'num cn-hide-sm', text: num(f.cap) && f.cap > 0 ? G.int(f.mwh / f.cap) : '–' })
    ]));
  });
}

function renderCosts(d) {
  var c = d.costs, dl = clear(byId('cost-figures'));
  if (c.latest) figure(dl, shortDay.format(isoDay(c.latest.d)), money(c.latest.cost), null, 'The latest day NESO has published');
  if (c.d30) figure(dl, 'Last 30 days', money(c.d30.cost), null, c.d30.days < 30 ? c.d30.days + ' days published' : null);
  if (c.d365) figure(dl, 'Last 365 days', money(c.d365.cost), null, c.d365.days < 360 ? c.d365.days + ' days published' : null);
  byId('cost-sub').textContent = 'What NESO paid each day to keep flows within the network’s limits, for the last 60 days. NESO publishes each day a few days later.';
  bars(byId('cost-chart'), c.days.map(function (r) { return { d: r[0], parts: [r[1], r[2] + r[3] + r[4]], raw: r }; }), {
    label: 'Constraint costs each day for the last 60 days',
    classes: ['cn-thermal', 'cn-other'],
    axis: function (v) { return money(v); },
    tip: function (r) {
      return [{ cls: 'cn-thermal', value: money(r.raw[1]), label: 'lines at their limit' },
        { value: money(r.raw[2]), label: 'voltage' }, { value: money(r.raw[3]), label: 'inertia' }, { value: money(r.raw[4]), label: 'largest loss' }];
    }
  });
  var legend = clear(byId('cost-legend'));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw cn-thermal' }), 'Lines at their limit (thermal)']));
  legend.appendChild(h('li', null, [h('i', { cls: 'sw cn-other' }), 'Voltage, inertia and the largest loss']));
  var tbody = clear(byId('boundaries'));
  d.groups.filter(function (g) { return (g.cost365 || 0) > 0 || (g.over365 || 0) > 0; }).forEach(function (g) {
    var cell = h('button', { type: 'button', cls: 'cn-link', 'data-code': (g.variants && g.variants[0]) || g.code, disabled: g.flows ? null : '' }, [name(g)]);
    tbody.appendChild(h('tr', null, [
      h('th', { scope: 'row' }, [g.flows ? cell : h('span', { text: name(g) }), h('small', { text: (g.boundary ? g.boundary + ', ' : '') + g.code })]),
      h('td', { cls: 'num', text: num(g.cost30) ? money(g.cost30) : '–' }),
      h('td', { cls: 'num', text: num(g.cost365) ? money(g.cost365) : '–' }),
      h('td', { cls: 'num cn-hide-sm', text: num(g.over365) && g.n365 ? pct(g.over365 / g.n365) + ' of the time' : '–' })
    ]));
  });
}

/* ---------- the page ---------- */

function render(d) {
  state.data = d;
  var ready = d && d.days && (d.days.today.groups.length || d.days.tomorrow.groups.length || (d.wind && d.wind.days.length));
  byId('waiting').hidden = Boolean(ready);
  byId('content').hidden = !ready;
  if (!ready) { G.setLive('', 'Fetching NESO’s constraint data'); return; }
  if (!state.focus) state.focus = d.focus;
  if (!d.days[state.day].groups.length && d.days.today.groups.length) state.day = 'today';
  renderOutlook(d);
  renderFlow(d);
  renderWind(d);
  renderCosts(d);
  G.setLive('good', 'Forecast flows for ' + dayFmt.format(isoDay(d.days[d.days.tomorrow.groups.length ? 'tomorrow' : 'today'].d)) + '.');
}

function choose(code) {
  if (!code || code === state.focus) return;
  state.focus = code;
  poller.setUrl('/api/constraints?group=' + encodeURIComponent(code));
}

byId('day-toggle').addEventListener('click', function (e) {
  var b = e.target.closest('button[data-day]');
  if (!b) return;
  state.day = b.dataset.day;
  try { localStorage.setItem('gb-constraints-day', state.day); } catch (err) { /* storage unavailable */ }
  if (state.data) renderOutlook(state.data);
});
byId('groups').addEventListener('click', function (e) {
  var b = e.target.closest('button[data-code]');
  if (b) choose(b.dataset.code);
});
byId('boundaries').addEventListener('click', function (e) {
  var b = e.target.closest('button[data-code]');
  if (b) { choose(b.dataset.code); byId('flow-title').scrollIntoView({ behavior: 'smooth', block: 'start' }); }
});

Promise.all([fetch('/geo.json').then(function (r) { return r.json(); }), fetch('/boundaries.json').then(function (r) { return r.json(); })])
  .then(function (both) { state.geo = both[0]; state.bounds = both[1]; if (state.data && state.data.days) drawMap(state.data); })
  .catch(function () { /* the map stays empty; everything else works */ });

var poller = G.poll('/api/constraints', {
  interval: function () { return 600; },
  onData: render,
  onTick: function (p) { if (G.offline(p)) G.setLive('bad', 'Can’t reach the dashboard server'); }
});

var resizeTimer = null;
window.addEventListener('resize', function () {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () { if (state.data && state.data.days) render(state.data); }, 150);
});
