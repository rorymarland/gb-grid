import G from '/static/js/gb.js';
import { createStationMap } from '/static/js/stationmap.js';
import { regionShapes, LEVEL_CLASS, MAP_REGIONS, INSET, levelFor, isDarkFill } from '/static/js/regionmap.js';
import { FUEL_ORDER, FUEL_NAMES, fuelColour, stack, stackedSeries, axes, tzDate } from '/static/js/charts.js';
import { mountOutlook } from '/static/js/outlook.js';

/* The Display view: single-screen slides for a tablet or spare screen,
   each designed to fit without scrolling. Options in the address:
   views (which slides, in order), every (seconds each), night (dim hours,
   UK time, or off) and theme (dark or light). */

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num;
var params = new URLSearchParams(location.search);
var DEFAULT_VIEWS = 'now,day,map,regions,balancing,outlook,outages,records';
var every = G.clamp(Number(params.get('every')) || 20, 5, 600);
var nightSpec = (params.get('night') || '23-7').match(/^(\d{1,2})-(\d{1,2})$/);
var nightFrom = nightSpec ? Number(nightSpec[1]) : null, nightTo = nightSpec ? Number(nightSpec[2]) : null;
var state = { index: 0, paused: false, started: Date.now(), peekUntil: 0, offline: false };
var CI_LEVEL = { 'very low': '--ci-very-low', low: '--ci-low', moderate: '--ci-moderate', high: '--ci-high', 'very high': '--ci-very-high' };
var clockFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: G.TZ });
var hourFmt = new Intl.DateTimeFormat('en-GB', { hour: 'numeric', hourCycle: 'h23', timeZone: G.TZ });

function gw(mw) { return G.gw(mw); }
/* Drop list rows from the end until the container stops overflowing */
function fit(container) {
  function trim() {
    var rows = container.querySelectorAll('.row');
    for (var i = rows.length - 1; i > 0 && container.scrollHeight > container.clientHeight + 1; i--) rows[i].remove();
  }
  trim();
  requestAnimationFrame(trim);  /* again once layout has settled */
}
function pct(x) { return Math.round(100 * x) + '%'; }
function lower(text) { return text.charAt(0).toLowerCase() + text.slice(1); }
/* "at 16:00", "tomorrow at 03:00", "on Mon 9 Nov at 11:30" */
function whenPhrase(t) {
  var w = G.when(t), parts = w.split(' '), time = parts.pop();
  if (!parts.length) return 'at ' + time;
  var day = parts.join(' ');
  return (/^(Yesterday|Tomorrow)$/.test(day) ? lower(day) : 'on ' + day) + ' at ' + time;
}

/* ---------- data: one poll per feed, shared by the slides ---------- */

var FEEDS = {
  live: ['/api/live', 30], alerts: ['/api/alerts', 60], map: ['/api/map', 60], day: ['/api/history?range=24h', 300],
  regions: ['/api/regions', 300], balancing: ['/api/balancing', 60], outages: ['/api/outages', 120], summary: ['/api/longterm/summary', 1800]
};
var data = {}, polls = {};
function startFeed(name) {
  if (polls[name]) return;
  polls[name] = G.poll(FEEDS[name][0], {
    onData: function (d) { data[name] = d; slides.forEach(function (sl) { if (sl.needs.indexOf(name) >= 0) sl.update(); }); if (name === 'live') renderNight(); },
    onTick: function (p) { if (name !== 'live') return; var off = G.offline(p); if (off !== state.offline) { state.offline = off; renderState(); } },
    interval: function () { return FEEDS[name][1]; }
  });
}

/* ---------- slide frame ---------- */

var host = byId('slides'), catcher = byId('catcher');
function frame(key, title) {
  var take = h('p', { cls: 's-take' });
  var body = h('div', { cls: 's-body' });
  var el = h('section', { cls: 'slide d-' + key, 'aria-label': title }, [
    h('header', { cls: 's-head' }, [h('h2', { text: title }), take]), body
  ]);
  host.insertBefore(el, catcher);
  return { el: el, body: body, take: take };
}
function makeSlide(key, title, needs, build) {
  var f = key === 'now' ? null : frame(key, title);
  var sl = { key: key, name: title, needs: needs, shown: false };
  if (!f) {
    sl.el = h('section', { cls: 'slide d-now', 'aria-label': title });
    host.insertBefore(sl.el, catcher);
  } else { sl.el = f.el; }
  var api = build(f ? f.body : sl.el, f ? f.take : null);
  sl.render = api.render;
  sl.ready = api.ready || function () { return needs.every(function (n) { return data[n]; }); };
  sl.update = function () { if (sl.shown || sl.el.classList.contains('on')) sl.render(); };
  return sl;
}

/* ---------- right now ---------- */

function inkFor(name) {
  var hex = G.css(name).replace('#', '');
  if (hex.length !== 6) return 'ink-light';
  var c = [0, 2, 4].map(function (i) { var v = parseInt(hex.substr(i, 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] > 0.3 ? 'ink-dark' : 'ink-light';
}
function cleanestWindow(forecast, hours) {
  var n = hours * 2, now = Date.now() / 1000, best = null;
  var points = (forecast || []).filter(function (p) { return p[0] >= now - 1800 && p[0] <= now + 86400; });
  for (var i = 0; i + n <= points.length; i++) {
    var sum = 0;
    for (var j = i; j < i + n; j++) sum += points[j][1];
    if (!best || sum / n < best.avg) best = { start: points[i][0], end: points[i + n - 1][0] + 1800, avg: sum / n };
  }
  return best;
}
function fact(colourVar, value, unit, label) {
  return h('div', { cls: 'fact' }, [
    h('i', { style: colourVar ? 'background:' + G.css(colourVar) : 'visibility:hidden' }),
    h('span', { cls: 'v' }, [value, unit ? h('small', { text: unit }) : null]),
    h('span', { cls: 'k', text: label })
  ]);
}
/* A full-width bar of the current mix, labelled where the labels fit */
function mixBar(mixHost, sources) {
  clear(mixHost);
  var total = sources.reduce(function (a, x) { return a + x.gw; }, 0) || 1, byKey = {};
  var width = mixHost.clientWidth || 600, size = parseFloat(getComputedStyle(mixHost).fontSize) || 16;
  sources.forEach(function (x) { byKey[x.key] = x; });
  mixHost.setAttribute('aria-label', 'Generation mix: ' + sources.map(function (x) { return x.label + ' ' + Math.round(x.pct) + '%'; }).join(', '));
  FUEL_ORDER.forEach(function (k) {
    var x = byKey[k];
    if (!x) return;
    var share = x.gw / total, p = pct(share), room = share * width - size * 1.4, name = FUEL_NAMES[k] || x.label, label = null;
    if (G.textWidth(name + ' ' + p, size, 650) <= room) label = [name, h('em', { text: p })];
    else if (G.textWidth(p, size, 650) <= room) label = [p];
    mixHost.appendChild(h('span', { cls: inkFor('--c-' + k), style: 'flex:' + share.toFixed(4) + ' 1 0;background:' + fuelColour(k) }, label));
  });
}

function buildNow(el) {
  var notice = h('div'), demand = h('p', { cls: 'lead-v', text: '–' }), note = h('p', { cls: 'lead-n' });
  var facts = h('div', { cls: 'facts' }), mix = h('div', { cls: 'mixbar', role: 'img' }), later = h('div', { cls: 'later' });
  el.appendChild(h('div', { cls: 'hero' }, [notice, h('div', { cls: 'headline' }, [h('div', null, [h('p', { cls: 'lead-k', text: 'Britain is using' }), demand, note]), facts]), mix, later]));
  return {
    render: function () {
      var live = data.live, alerts = data.alerts;
      if (!live) return;
      clear(demand);
      G.add(demand, [num(live.demand_gw) ? live.demand_gw.toFixed(1) : '–', h('small', { text: 'GW' })]);
      var sources = (live.sources || []).filter(function (x) { return x.gw > 0; });
      var top = sources.slice().sort(function (a, b) { return b.gw - a.gw; })[0], groups = live.groups || {};
      note.textContent = top ? top.label + ' is the biggest source at ' + Math.round(top.pct) + '%. ' + Math.round(groups.low_carbon || 0) + '% is low-carbon.' : '';
      clear(facts);
      var c = live.carbon || {}, p = live.price || {};
      if (num(c.value)) facts.appendChild(fact(CI_LEVEL[c.index], String(c.value), 'g/kWh', 'Carbon intensity, ' + (c.index || '')));
      if (num(p.value)) facts.appendChild(fact(null, '£' + p.value.toFixed(0), '/MWh', 'Wholesale price, ' + (p.value / 10).toFixed(1) + 'p per kWh'));
      var wind = sources.filter(function (x) { return x.key === 'wind'; })[0], solar = sources.filter(function (x) { return x.key === 'solar'; })[0];
      if (wind) facts.appendChild(fact('--c-wind', wind.gw.toFixed(1), 'GW', 'Wind' + (solar && solar.gw >= 0.5 ? ', with ' + solar.gw.toFixed(1) + ' GW of solar' : '')));
      mixBar(mix, sources);
      clear(later);
      var best = cleanestWindow(live.forecast, 3);
      if (best) later.appendChild(h('span', null, ['Cleanest 3 hours: ', h('b', { text: G.when(best.start).replace('Tomorrow', 'tomorrow') + ' to ' + G.hm(best.end) }), ', about ' + Math.round(best.avg) + ' g/kWh']));
      var m = alerts && alerts.margin;
      if (m) later.appendChild(h('span', null, ['Spare capacity: ', h('b', { text: gw(m.margin) + ' GW' }), ', ' + m.label.toLowerCase() + ' over the next 12 hours']));
      clear(notice);
      var active = (alerts && alerts.active) || [];
      if (active.length) notice.appendChild(h('div', { cls: 'notice' + (active[0].level === 'strong' ? ' strong' : '') }, [h('b', { text: active[0].title + '. ' }), active[0].summary]));
    }
  };
}

/* ---------- the last 24 hours ---------- */

function buildDay(body, take) {
  var chart = h('div', { cls: 'chart-box' }), side = h('div', { cls: 'side' });
  body.appendChild(h('div', { cls: 'split' }, [chart, side]));
  var plot = null, built = '';
  function mixOf(d) {
    var x = d.series, add = function (a, b) { return (a || []).map(function (v, i) { return (v || 0) + ((b || [])[i] || 0); }); };
    return { nuclear: x.nuclear, wind: x.wind, solar: x.solar, hydro: x.hydro, biomass: x.biomass, imports: x.imports,
      storage: x.storage_out, coal: x.coal, gas: x.gas, other: add(x.other, x.oil) };
  }
  return {
    render: function () {
      var d = data.day;
      if (!d || !d.t || !d.t.length) return;
      var mix = mixOf(d), rows = stack(d.t, mix, FUEL_ORDER).rows;
      var W = chart.clientWidth, H = chart.clientHeight, sig = W + 'x' + H + G.theme;
      if (!plot || built !== sig) {
        if (plot) plot.destroy();
        var st = stackedSeries(FUEL_ORDER), font = Math.max(12, Math.round(H / 34));
        plot = new uPlot({
          width: W, height: H, legend: { show: false }, cursor: { show: false }, padding: [10, 14, 0, 0], tzDate: tzDate,
          scales: { x: { time: true }, y: { range: function (u, lo, hi) { return [0, Math.ceil((hi || 10) / 5) * 5]; } } },
          axes: axes(function (u, splits) { return splits.map(function (v) { return v + ' GW'; }); }, { font: font, ySize: font * 4.6, space: font * 7 }),
          series: st.series, bands: st.bands
        }, rows, chart);
        built = sig;
      } else plot.setData(rows);
      /* Now, and each source's share of the day */
      var last = d.t.length - 1, energy = {}, total = 0;
      FUEL_ORDER.forEach(function (k) { var v = 0; (mix[k] || []).forEach(function (x) { v += Math.max(0, x || 0); }); energy[k] = v; total += v; });
      var nowRows = FUEL_ORDER.map(function (k) { return { k: k, v: (mix[k] || [])[last] || 0, share: total ? energy[k] / total : 0 }; })
        .filter(function (r) { return r.share >= 0.005 || r.v >= 0.05; }).sort(function (a, b) { return b.share - a.share; });
      clear(side);
      side.appendChild(h('div', { cls: 'cols' }, [h('span', { text: '' }), h('span', { text: 'Now' }), h('span', { text: 'Day' })]));
      nowRows.slice(0, 8).forEach(function (r) {
        side.appendChild(h('div', { cls: 'row' }, [h('span', null, [h('i', { cls: 'sw', style: 'background:' + fuelColour(r.k) }), FUEL_NAMES[r.k]]),
          h('b', { text: r.v.toFixed(1) }), h('span', { cls: 'muted', text: pct(r.share) })]));
      });
      fit(side);
      var ci = (d.carbon || []).filter(num);
      if (ci.length) side.appendChild(h('p', { cls: 'foot-note', text: 'Carbon intensity ranged from ' + Math.min.apply(null, ci) + ' to ' + Math.max.apply(null, ci) + ' g/kWh.' }));
      var lead = nowRows[0], second = nowRows[1];
      take.textContent = lead ? FUEL_NAMES[lead.k] + ' supplied ' + pct(lead.share) + ' of the last 24 hours' + (second ? ', then ' + lower(FUEL_NAMES[second.k]) + ' at ' + pct(second.share) : '') + '.' : '';
    }
  };
}

/* ---------- power stations ---------- */

function buildMap(body, take) {
  var stage = h('div', { cls: 'stage' }), svg = s('svg', { 'aria-hidden': 'true' });
  stage.appendChild(svg);
  var side = h('div', { cls: 'side' });
  body.appendChild(h('div', { cls: 'split wide' }, [stage, side]));
  var map = null;
  return {
    render: function () {
      var d = data.map;
      if (!d) return;
      if (!map) map = createStationMap({ stage: stage, svg: svg, scale: 1.15, maxLabels: 9 });
      map.setData(d);
      var gen = 0;
      (d.categories || []).forEach(function (c) { gen += Math.max(0, c.measured || 0) + Math.max(0, c.embedded || 0); });
      var net = (d.links || []).reduce(function (a, l) { return a + (num(l.mw) ? l.mw : 0); }, 0);
      clear(side);
      side.appendChild(h('div', { cls: 'stats' }, [
        h('div', null, [h('b', { text: gw(gen) }), h('span', { text: 'GW generated in GB' })]),
        h('div', null, [h('b', { text: gw(Math.abs(net)) }), h('span', { text: 'GW net ' + (net >= 0 ? 'imports' : 'exports') })])
      ]));
      side.appendChild(h('h3', { text: 'Biggest right now' }));
      var top = (d.stations || []).filter(function (x) { return x.mw > 0; }).sort(function (a, b) { return b.mw - a.mw; }).slice(0, 7);
      top.forEach(function (x) {
        side.appendChild(h('div', { cls: 'row' }, [h('span', null, [h('i', { cls: 'sw', style: 'background:' + fuelColour(x.c) }), x.n]), h('b', { text: gw(x.mw) + ' GW' })]));
      });
      fit(side);
      take.textContent = top.length ? top[0].n + ' is the biggest station running, at ' + gw(top[0].mw) + ' GW.' : 'Waiting for station data.';
    }
  };
}

/* ---------- regions ---------- */

function buildRegions(body, take) {
  var svg = s('svg', { 'aria-hidden': 'true', preserveAspectRatio: 'xMidYMid meet' }), list = h('div', { cls: 'side ranks' });
  body.appendChild(h('div', { cls: 'split' }, [h('div', { cls: 'map-box' }, svg), list]));
  var nodes = {}, labels = {}, geo = null;
  G.fetchJSON('/regions.json').then(function (g) {
    geo = g;
    var shapes = regionShapes(g);
    svg.setAttribute('viewBox', shapes.view.map(function (v) { return v.toFixed(1); }).join(' '));
    var main = s('g', { transform: 'scale(0.5)' }), inset = s('g', { transform: 'translate(' + INSET.dx * 2 + ' ' + INSET.dy * 2 + ')' });
    shapes.regions.forEach(function (item) {
      var a = s('path', { d: item.mainland, 'class': 'region', 'fill-rule': 'evenodd' }), b = item.island ? s('path', { d: item.island, 'class': 'region', 'fill-rule': 'evenodd' }) : null;
      main.appendChild(a);
      if (b) inset.appendChild(b);
      nodes[item.region.id] = [a, b];
    });
    main.appendChild(inset);
    svg.appendChild(main);
    g.regions.forEach(function (r) { var t = s('text', { 'class': 'rg-label', x: r.lx, y: r.ly + 6 }); svg.appendChild(t); labels[r.id] = t; });
    slides.forEach(function (sl) { if (sl.key === 'regions') sl.update(); });
  });
  return {
    ready: function () { return Boolean(data.regions && data.regions.n); },
    render: function () {
      var d = data.regions;
      if (!d || !geo) return;
      var i = d.now, rows = [];
      MAP_REGIONS.forEach(function (id) {
        var v = (d.ci[String(id)] || [])[i], level = levelFor(v, (d.index[String(id)] || [])[i]);
        (nodes[id] || []).forEach(function (n) { if (n) n.setAttribute('class', 'region ' + (num(level) ? LEVEL_CLASS[level] : 'nodata')); });
        if (labels[id]) { labels[id].textContent = num(v) ? String(v) : ''; labels[id].setAttribute('class', 'rg-label' + (isDarkFill(level) ? ' on-dark' : '')); }
        if (num(v)) rows.push({ id: id, v: v, level: level, name: d.names[String(id)] });
      });
      rows.sort(function (a, b) { return a.v - b.v; });
      var max = Math.max.apply(null, rows.map(function (r) { return r.v; }).concat([100]));
      clear(list);
      rows.forEach(function (r) {
        list.appendChild(h('div', { cls: 'row' }, [h('span', { text: r.name }),
          h('span', { cls: 'rbar' }, h('i', { cls: LEVEL_CLASS[r.level], style: 'width:' + Math.max(1.5, 100 * r.v / max).toFixed(1) + '%' })),
          h('b', { text: String(r.v) })]));
      });
      fit(list);
      if (rows.length) take.textContent = rows[0].name + ' is cleanest at ' + rows[0].v + ' g/kWh; ' + rows[rows.length - 1].name + ' is highest at ' + rows[rows.length - 1].v + '.';
    }
  };
}

/* ---------- balancing ---------- */

function buildBalancing(body, take) {
  var tiles = h('div', { cls: 'tiles' }), list = h('div', { cls: 'side units' }), chart = h('div', { cls: 'hh-chart' });
  body.appendChild(h('div', { cls: 'stack three' }, [tiles, list, chart]));
  /* Today's half hours: energy turned down below the line, turned up above it */
  function drawChart(rows) {
    clear(chart);
    var W = chart.clientWidth, H = chart.clientHeight;
    if (!rows || !rows.length || W < 50 || H < 60) return;
    var font = Math.max(12, Math.round(H / 14)), top = font * 1.6, bottom = font * 1.8;
    var max = 1;
    rows.forEach(function (r) { max = Math.max(max, Math.abs(r[1] || 0), Math.abs(r[2] || 0)); });
    var zero = top + (H - top - bottom) / 2, k = (H - top - bottom) / 2 / max, n = Math.max(rows.length, 46), bw = W / n;
    var svg = s('svg', { width: W, height: H, viewBox: '0 0 ' + W + ' ' + H, 'aria-hidden': 'true' });
    svg.appendChild(s('text', { x: 0, y: font, 'class': 'cap', text: 'Today, half hour by half hour: MWh turned up (above) and down (below)' }));
    rows.forEach(function (r, i) {
      var x = i * bw + 1, w = Math.max(1, bw - 2), up = Math.max(0, r[2] || 0), dn = Math.abs(Math.min(0, r[1] || 0));
      if (up) svg.appendChild(s('rect', { x: x.toFixed(1), y: (zero - up * k).toFixed(1), width: w.toFixed(1), height: (up * k).toFixed(1), 'class': 'up', rx: 2 }));
      if (dn) svg.appendChild(s('rect', { x: x.toFixed(1), y: zero.toFixed(1), width: w.toFixed(1), height: (dn * k).toFixed(1), 'class': 'down', rx: 2 }));
      if (G.hm(r[0]).slice(3) === '00' && Number(G.hm(r[0]).slice(0, 2)) % 6 === 0) svg.appendChild(s('text', { x: x.toFixed(1), y: H - font * 0.4, 'class': 'tick', text: G.hm(r[0]) }));
    });
    svg.appendChild(s('line', { x1: 0, x2: W, y1: zero, y2: zero, 'class': 'axis' }));
    chart.appendChild(svg);
  }
  function tile(label, value, unit, note, colour) {
    return h('div', { cls: 'tile' }, [h('span', { cls: 'k' }, [colour ? h('i', { cls: 'sw', style: 'background:' + G.css(colour) }) : null, label]),
      h('b', null, [value, unit ? h('small', { text: unit }) : null]), note ? h('span', { cls: 'n', text: note }) : null]);
  }
  return {
    render: function () {
      var d = data.balancing;
      if (!d) return;
      var now = d.now || {}, today = d.today || {}, units = now.units || [];
      var down = units.filter(function (u) { return u.delta < 0; }), up = units.filter(function (u) { return u.delta > 0; });
      clear(tiles);
      tiles.appendChild(tile('Turned down now', gw(Math.abs(now.down || 0)), 'GW', G.plural(down.length, 'unit', 'units'), '--down'));
      tiles.appendChild(tile('Turned up now', gw(now.up || 0), 'GW', G.plural(up.length, 'unit', 'units'), '--up'));
      tiles.appendChild(tile('Cost now', G.money(now.cost_hour || 0), 'an hour', 'From bid and offer prices'));
      tiles.appendChild(tile('Paid so far today', G.money(today.paid || 0), null, today.wind_down_mwh ? 'Wind turned down ' + (today.wind_down_mwh / 1000).toFixed(1) + ' GWh' : null));
      clear(list);
      units.slice().sort(function (a, b) { return Math.abs(b.delta) - Math.abs(a.delta); }).slice(0, 6).forEach(function (u) {
        list.appendChild(h('div', { cls: 'row' }, [h('span', null, [h('i', { cls: 'sw', style: 'background:' + fuelColour(u.cat) }), u.name]),
          h('b', { cls: u.delta < 0 ? 'down' : 'up', text: (u.delta < 0 ? '−' : '+') + G.int(Math.abs(u.delta)) + ' MW' }),
          h('span', { cls: 'muted', text: num(u.price) ? (u.price < 0 ? '−£' : '£') + Math.abs(u.price).toFixed(0) + '/MWh' : '' })]));
      });
      fit(list);
      if (!units.length) list.appendChild(h('p', { cls: 'foot-note', text: 'No units are following instructions at the moment.' }));
      drawChart(d.series && d.series.today);
      var windDown = down.filter(function (u) { return u.cat === 'wind'; });
      take.textContent = !units.length ? 'Nothing is being turned up or down right now.' :
        windDown.length ? 'NESO is paying ' + G.plural(windDown.length, 'wind farm', 'wind farms') + ' to turn down ' + gw(-windDown.reduce(function (a, u) { return a + u.delta; }, 0)) + ' GW.' :
        G.plural(units.length, 'unit is', 'units are') + ' following balancing instructions.';
    }
  };
}

/* ---------- the next 48 hours ---------- */

function buildOutlook(body, take) {
  var box = h('div', { cls: 'outlook' });
  body.appendChild(box);
  var outlook = null;
  return {
    ready: function () { return true; },
    render: function () {
      if (!outlook) outlook = mountOutlook(box, { fill: true, onData: function () { slides.forEach(function (sl) { if (sl.key === 'outlook') sl.update(); }); } });
      else outlook.render();
      var d = outlook.data();
      if (!d) return;
      var wind = d.wind || [], ci = d.carbon || [];
      var peak = wind.reduce(function (a, p) { return !a || p[1] > a[1] ? p : a; }, null);
      var clean = ci.reduce(function (a, p) { return !a || p[1] < a[1] ? p : a; }, null);
      take.textContent = [peak ? 'Wind peaks at ' + gw(peak[1]) + ' GW ' + whenPhrase(peak[0]) : '', clean ? 'carbon is lowest ' + whenPhrase(clean[0]) + ', at ' + clean[1] + ' g/kWh' : ''].filter(Boolean).join('; ') + '.';
    }
  };
}

/* ---------- spare capacity and outages ---------- */

function buildOutages(body, take) {
  var gauge = h('div', { cls: 'gauge-box' }), list = h('div', { cls: 'side' });
  body.appendChild(h('div', { cls: 'split even' }, [gauge, list]));
  var ZONES = [['very-tight', 1000, '--bad'], ['tight', 2500, '--warn'], ['normal', 5000, '--ci-low'], ['comfortable', 15000, '--ci-very-low']];
  return {
    render: function () {
      var d = data.outages;
      if (!d) return;
      var m = d.margin;
      clear(gauge);
      if (m) {
        var max = 15000, at = function (v) { return (100 * G.clamp(v, 0, max) / max).toFixed(2) + '%'; };
        var zone = ZONES.filter(function (z) { return z[0] === m.level; })[0] || ZONES[3];
        gauge.appendChild(h('p', { cls: 'big' }, [gw(m.margin), h('small', { text: 'GW spare' })]));
        gauge.appendChild(h('p', { cls: 'pill' }, [h('i', { style: 'background:' + G.css(zone[2]) }), m.label + ' over the next 12 hours']));
        var track = h('div', { cls: 'track' }), prev = 0;
        ZONES.forEach(function (z) { track.appendChild(h('i', { style: 'flex:' + (z[1] - prev) + ' 1 0;background:' + G.css(z[2]) })); prev = z[1]; });
        gauge.appendChild(h('div', { cls: 'gauge' }, [track, h('b', { cls: 'mark', style: 'left:' + at(m.margin) }), h('b', { cls: 'mark min', style: 'left:' + at(m.min) })]));
        gauge.appendChild(h('p', { cls: 'foot-note', text: 'Lowest ' + gw(m.min) + ' GW at ' + G.hm(m.min_t) + '. Spare capacity is what would be left after allowing for units that might fail.' }));
      } else gauge.appendChild(h('p', { cls: 'foot-note', text: 'Waiting for Elexon’s margin forecast.' }));
      clear(list);
      list.appendChild(h('h3', { text: gw(d.lost_total) + ' GW offline' }));
      (d.offline || []).slice(0, 6).forEach(function (o) {
        var back = num(o.end) ? ((o.end - Date.now() / 1000) / 86400 > 60 ? G.date(o.end) : G.day(o.end)) : 'not given';
        list.appendChild(h('div', { cls: 'row' }, [h('span', null, [h('i', { cls: 'sw', style: 'background:' + fuelColour(o.cat === 'imports' ? 'imports' : o.cat) }), o.name]),
          h('b', { text: G.int(o.lost) + ' MW' }), h('span', { cls: 'muted', text: 'back ' + back })]));
      });
      fit(list);
      take.textContent = m ? 'The grid looks ' + m.label.toLowerCase() + ', with ' + gw(d.lost_total) + ' GW of capacity offline.' : gw(d.lost_total) + ' GW of capacity is offline.';
    }
  };
}

/* ---------- records ---------- */

function buildRecords(body, take) {
  var versus = h('div', { cls: 'versus' }), recs = h('div', { cls: 'records' });
  body.appendChild(h('div', { cls: 'stack' }, [versus, recs]));
  function bar(mix) {
    return h('div', { cls: 'bar100' }, FUEL_ORDER.filter(function (k) { return mix[k] > 0.002; }).map(function (k) {
      return h('i', { style: 'flex:' + mix[k].toFixed(4) + ' 1 0;background:' + fuelColour(k) });
    }));
  }
  return {
    ready: function () { return Boolean(data.summary && data.summary.ready); },
    render: function () {
      var sm = data.summary;
      if (!sm || !sm.ready) return;
      var c = sm.compare || {};
      clear(versus);
      [['now', 'Latest full day'], ['then', 'A year earlier']].forEach(function (p) {
        var day = c[p[0]];
        if (!day) return;
        var zero = day.mix.wind + day.mix.solar + day.mix.nuclear + day.mix.hydro;
        versus.appendChild(h('div', { cls: 'vs' }, [h('span', { cls: 'lbl' }, [G.date(day.t), h('small', { text: p[1] })]), bar(day.mix),
          h('span', { cls: 'num' }, [h('b', { text: String(day.ci) }), ' g/kWh', h('small', { text: pct(zero) + ' zero-carbon' })])]));
      });
      clear(recs);
      var list = sm.records.slice().sort(function (a, b) { return (b.recent - a.recent) || 0; }).slice(0, 4);
      list.forEach(function (r) {
        var v = r.unit === 'MW' ? [gw(r.value), 'GW'] : r.unit === '%' ? [r.value.toFixed(1), '%'] : r.unit === '£/MWh' ? [(r.value < 0 ? '−£' : '£') + Math.abs(r.value).toFixed(0), '/MWh'] : [String(Math.round(r.value)), r.unit];
        recs.appendChild(h('div', { cls: 'rec' }, [h('span', { cls: 'k', text: r.title }), h('b', null, [v[0], h('small', { text: v[1] })]),
          h('span', { cls: 'n', text: G.date(r.t) + (r.recent ? ', this month' : '') })]));
      });
      var now = c.now, then = c.then;
      take.textContent = now && then && num(now.ci) && num(then.ci) ? 'The latest day ran at ' + now.ci + ' g/kWh, ' + (now.ci <= then.ci ? Math.round(100 * (then.ci - now.ci) / then.ci) + '% cleaner' : Math.round(100 * (now.ci - then.ci) / then.ci) + '% dirtier') + ' than a year before.' : 'Records since 2009.';
    }
  };
}

/* ---------- the slides in order ---------- */

var BUILDERS = {
  now: ['Right now', ['live', 'alerts'], buildNow],
  day: ['The last 24 hours', ['day'], buildDay],
  map: ['Power stations', ['map'], buildMap],
  regions: ['Carbon intensity by region', ['regions'], buildRegions],
  balancing: ['Balancing the grid', ['balancing'], buildBalancing],
  outlook: ['The next 48 hours', [], buildOutlook],
  outages: ['Spare capacity and outages', ['outages'], buildOutages],
  records: ['Then and now', ['summary'], buildRecords]
};
var order = (params.get('views') || DEFAULT_VIEWS).split(',').filter(function (k) { return BUILDERS[k]; });
if (!order.length) order = ['now'];
var slides = order.map(function (k) { var b = BUILDERS[k]; return makeSlide(k, b[0], b[1], b[2]); });
slides.forEach(function (sl) { sl.needs.forEach(startFeed); });
startFeed('live');
var dots = byId('dots');
slides.forEach(function () { dots.appendChild(h('i')); });

function show(index, direction) {
  var n = slides.length;
  index = ((index % n) + n) % n;
  /* Skip slides with nothing to show yet (the history still downloading, say) */
  for (var tries = 0; direction && tries < n && !slides[index].ready(); tries++) index = ((index + direction) % n + n) % n;
  state.index = index;
  state.started = Date.now();
  slides.forEach(function (sl, i) { sl.el.classList.toggle('on', i === index); });
  Array.prototype.forEach.call(dots.children, function (d, i) { d.classList.toggle('on', i === index); });
  var sl = slides[index];
  byId('view-name').textContent = sl.name;
  sl.shown = true;
  sl.render();
  renderState();
}
function step(delta) { show(state.index + delta, delta); }

/* ---------- clock, night and state ---------- */

function isNight() {
  if (nightFrom === null || nightFrom === nightTo) return false;
  var hour = Number(hourFmt.format(new Date()));
  return nightFrom > nightTo ? (hour >= nightFrom || hour < nightTo) : (hour >= nightFrom && hour < nightTo);
}
function renderNight() {
  var live = data.live;
  byId('night-ci').textContent = live && live.carbon && num(live.carbon.value) ? live.carbon.value + ' g/kWh' : '';
  byId('night-demand').textContent = live && num(live.demand_gw) ? live.demand_gw.toFixed(1) + ' GW' : '';
}
function renderClock() {
  var now = Date.now() / 1000, clock = byId('clock');
  clear(clock);
  G.add(clock, [G.hm(now), h('small', { text: clockFmt.format(new Date()) })]);
  byId('night-clock').textContent = G.hm(now);
  document.body.classList.toggle('is-night', isNight() && Date.now() > state.peekUntil);
}
function renderState() {
  var text = clear(byId('state'));
  if (state.paused) text.appendChild(h('span', { cls: 'paused', text: 'Paused. Tap to carry on.' }));
  else if (state.offline) text.appendChild(h('span', { text: 'Reconnecting…' }));
}
function tick() {
  renderClock();
  var bar = byId('progress');
  if (slides.length < 2 || document.body.classList.contains('is-night')) { bar.style.width = '0'; return; }
  if (state.paused) { state.started = Date.now() - (state.pausedAt || 0); return; }
  var elapsed = (Date.now() - state.started) / 1000;
  bar.style.width = Math.min(100, 100 * elapsed / every).toFixed(2) + '%';
  if (elapsed >= every) step(1);
}
function togglePause() {
  state.paused = !state.paused;
  state.pausedAt = state.paused ? Date.now() - state.started : 0;
  renderState();
}

/* Taps pause; swipes and arrow keys move between slides */
var down = null;
catcher.addEventListener('pointerdown', function (e) { down = { x: e.clientX, y: e.clientY }; });
catcher.addEventListener('pointerup', function (e) {
  if (!down) return;
  var dx = e.clientX - down.x, dy = e.clientY - down.y;
  down = null;
  if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy)) step(dx < 0 ? 1 : -1);
  else if (Math.abs(dx) < 12 && Math.abs(dy) < 12) togglePause();
});
byId('night').addEventListener('click', function () { state.peekUntil = Date.now() + 120000; renderClock(); });
document.addEventListener('keydown', function (e) {
  if (e.key === 'ArrowRight') step(1);
  else if (e.key === 'ArrowLeft') step(-1);
  else if (e.key === ' ') { e.preventDefault(); togglePause(); }
});

/* Full screen where the browser allows it, and keep the screen awake */
var root = document.documentElement, fsBtn = byId('fs-btn');
if (root.requestFullscreen && !window.matchMedia('(display-mode: fullscreen), (display-mode: standalone)').matches) {
  fsBtn.hidden = false;
  fsBtn.addEventListener('click', function () {
    if (document.fullscreenElement) document.exitFullscreen(); else root.requestFullscreen().catch(function () {});
  });
  document.addEventListener('fullscreenchange', function () { fsBtn.textContent = document.fullscreenElement ? 'Leave full screen' : 'Full screen'; });
}
function keepAwake() {
  if (!('wakeLock' in navigator) || document.hidden) return;
  navigator.wakeLock.request('screen').catch(function () {});
}
document.addEventListener('visibilitychange', function () { if (!document.hidden) keepAwake(); });
keepAwake();

function rerender() { slides.forEach(function (sl) { if (sl.shown) sl.render(); }); }
window.addEventListener('gb:theme', rerender);
window.addEventListener('gb:width', rerender);
/* Reload once a day so a long-running screen picks up new versions */
setTimeout(function () { location.reload(); }, (20 + Math.random() * 8) * 3600 * 1000);

show(0);
renderClock();
setInterval(tick, 250);
