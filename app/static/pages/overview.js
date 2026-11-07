import G from '/static/js/gb.js';

var byId = G.byId, clear = G.clear, add = G.add, h = G.h, s = G.s, num = G.num, hm = G.hm, fetchText = G.fetchText, ago = G.ago;

var NS = 'http://www.w3.org/2000/svg';
var TZ = 'Europe/London';
var ORDER = ['nuclear', 'wind', 'solar', 'hydro', 'biomass', 'imports', 'storage', 'gas', 'other'];
var NAMES = {
  nuclear: 'Nuclear', wind: 'Wind', solar: 'Solar', hydro: 'Hydro', biomass: 'Biomass',
  imports: 'Imports', storage: 'Storage', gas: 'Gas', other: 'Other',
  exports: 'Exports', storage_in: 'Storage charging'
};
var ABOUT = {
  nuclear: 'Britain’s nuclear power stations. Output is steady and low-carbon, and changes slowly, usually for maintenance or refuelling.',
  wind: 'Large wind farms are metered every five minutes. Smaller ones on local networks are not, so their output is NESO’s estimate.',
  solar: 'Almost all of Britain’s solar panels are on local networks, so this is an estimate: Sheffield Solar’s PV_Live, updated every half hour, or NESO’s forecast for the current half hour.',
  hydro: 'Hydroelectric stations, mostly in Scotland, that run on river or reservoir water rather than pumped storage.',
  biomass: 'Mostly wood pellets burned at Drax and a handful of other plants. It counts as renewable but still emits CO₂ at the chimney; NESO uses 120 g/kWh.',
  imports: 'Power arriving through undersea cables from neighbouring countries. Details are in the imports, exports and storage panel.',
  storage: 'Pumped hydro and batteries returning stored energy to the grid. Elexon only reports battery discharging, not charging.',
  gas: 'Gas-fired power stations, mostly combined-cycle plants, with fast-starting open-cycle units for peaks. The main flexible source and the main source of emissions.',
  other: 'Metered generators that don’t fit the other categories, such as waste-to-energy plants and smaller units. Also includes any coal or oil, which is now almost always zero.'
};
var LEVELS = ['very low', 'low', 'moderate', 'high', 'very high'];
var LEVEL_NAMES = { 'very low': 'Very low', 'low': 'Low', 'moderate': 'Moderate', 'high': 'High', 'very high': 'Very high' };
var RANGE_NAMES = { '24h': '24 hours', '7d': '7 days', '30d': '30 days' };

var state = {
  live: null, liveText: '', history: {}, historyVersion: null, range: '24h',
  failures: 0, timer: null, lastOk: 0, windowHours: 3, useRegion: false, tableOpen: false,
  hoverIndex: null, openAbout: {}
};

/* ---------- small DOM helpers ---------- */


/* ---------- formatting ---------- */

var timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: TZ });
var weekdayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short', timeZone: TZ });
var weekdayLongFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'long', timeZone: TZ });
var dateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: TZ });
var ymdFmt = new Intl.DateTimeFormat('en-GB', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: TZ });

function ymd(t) { return ymdFmt.format(new Date(t * 1000)); }
function hourMinute(t) {
  var parts = hm(t).split(':');
  return [parseInt(parts[0], 10), parseInt(parts[1], 10)];
}
function relDay(t) {
  var now = Date.now() / 1000, day = ymd(t);
  if (day === ymd(now)) return 'today';
  if (day === ymd(now + 86400)) return 'tomorrow';
  if (day === ymd(now - 86400)) return 'yesterday';
  return 'on ' + weekdayLongFmt.format(new Date(t * 1000));
}
function stamp(t) { return weekdayFmt.format(new Date(t * 1000)) + ' ' + hm(t); }
function gw(v) {
  if (!num(v)) return '–';
  return Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(2);
}
function signed(v, text) { return (v < 0 ? '−' : '') + text(Math.abs(v)); }
function pct(v) {
  if (!num(v)) return '–';
  if (v > 0 && v < 1) return 'under 1%';
  return Math.round(v) + '%';
}
function money(v) {
  if (!num(v)) return '–';
  return (v < 0 ? '−£' : '£') + Math.abs(v).toFixed(2);
}
function capitalise(text) { return text.charAt(0).toUpperCase() + text.slice(1); }
function levelClass(level) { return 'ci-' + (level ? String(level).replace(' ', '-') : 'none'); }

var measureContext = null;
function textWidth(text, size, weight) {
  if (!measureContext) measureContext = document.createElement('canvas').getContext('2d');
  measureContext.font = (weight || 600) + ' ' + size + 'px ' + getComputedStyle(document.body).fontFamily;
  return measureContext.measureText(text).width;
}
function cssColour(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function inkOn(hex) {
  var match = /^#?([0-9a-f]{6})/i.exec(hex || '');
  if (!match) return 'on-dark';
  var n = parseInt(match[1], 16);
  var channel = function (c) { c = c / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  var lum = 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
  return (1.05 / (lum + 0.05)) >= ((lum + 0.05) / 0.06) ? 'on-dark' : 'on-light';
}
function niceStep(span, target) {
  var raw = span / Math.max(1, target);
  var magnitude = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
  var norm = raw / magnitude;
  var step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  return step * magnitude;
}

/* Segmented toggles keep their buttons (and keyboard focus) between renders */
function syncToggle(container, options, selected, onSelect) {
  var signature = options.map(function (option) { return option.label; }).join('|');
  if (container.getAttribute('data-signature') !== signature) {
    clear(container);
    options.forEach(function (option) {
      container.appendChild(h('button', {
        type: 'button', text: option.label, 'aria-label': option.aria || null,
        onclick: function () { onSelect(option.value); }
      }));
    });
    container.setAttribute('data-signature', signature);
  }
  Array.prototype.forEach.call(container.querySelectorAll('button'), function (button, index) {
    button.setAttribute('aria-pressed', String(options[index].value === selected));
  });
}

/* Skip re-rendering a section when nothing it shows has changed */
var signatures = {};
function changed(name, inputs) {
  var signature = JSON.stringify(inputs);
  if (signatures[name] === signature) return false;
  signatures[name] = signature;
  return true;
}

/* Axis labels: draw left to right, skipping any that would collide */
function placeLabels(svg, labels, y) {
  var lastRight = -Infinity;
  labels.forEach(function (label) {
    var width = textWidth(label.text, 11.5, label.strong ? 600 : 400);
    var left = label.x - width / 2;
    if (left < lastRight + 8) return;
    svg.appendChild(s('text', { x: label.x, y: y, 'text-anchor': 'middle', class: 'axis-text' + (label.strong ? ' strong' : ''), text: label.text }));
    lastRight = label.x + width / 2;
  });
}

/* ---------- tooltips ---------- */

function makeTip(container) {
  var tip = h('div', { cls: 'tip', 'aria-hidden': 'true' });
  tip.hidden = true;
  container.appendChild(tip);
  return {
    show: function (title, rows, x, y, placement) {
      clear(tip);
      tip.appendChild(h('div', { cls: 't', text: title }));
      rows.forEach(function (row) {
        if (row.sep) { tip.appendChild(h('div', { cls: 'sep' })); return; }
        tip.appendChild(h('div', { cls: 'row' }, [
          h('span', { cls: 'key ' + (row.cls || 'none') }),
          h('span', { cls: 'v', text: row.value }),
          h('span', { cls: 'l', text: row.label })
        ]));
      });
      tip.hidden = false;
      var width = container.clientWidth, tipWidth = tip.offsetWidth, tipHeight = tip.offsetHeight;
      var left, top;
      if (placement === 'below') {
        left = Math.max(0, Math.min(width - tipWidth, x - tipWidth / 2));
        top = y;
      } else {
        left = x + 16;
        if (left + tipWidth > width) left = x - tipWidth - 16;
        if (left < 0) left = Math.max(0, Math.min(width - tipWidth, x - tipWidth / 2));
        top = Math.max(0, Math.min(y - tipHeight / 2, container.clientHeight - tipHeight));
      }
      tip.style.left = left + 'px';
      tip.style.top = top + 'px';
    },
    hide: function () { tip.hidden = true; }
  };
}

/* ---------- data access ---------- */

function sourceMap(live) {
  var map = {};
  (live.sources || []).forEach(function (item) { map[item.key] = item; });
  return map;
}
function mixParts(live) {
  var map = sourceMap(live);
  var value = function (key) { return map[key] && num(map[key].gw) ? Math.max(0, map[key].gw) : 0; };
  var supply = live.supply_gw || 1;
  return ORDER.map(function (key) {
    var amount = key === 'other' ? value('other') + value('coal') + value('oil')
      : key === 'storage' ? value('storage_out') : value(key);
    return { key: key, gw: amount, pct: 100 * amount / supply };
  }).filter(function (part) { return part.gw >= 0.005; });
}
function history24() { return state.history['24h'] || null; }
function combinedSeries(hist, key) {
  if (!hist) return [];
  var series = hist.series;
  if (key === 'storage') return series.storage_out;
  if (key === 'other') {
    return series.other.map(function (v, i) {
      if (!num(v)) return null;
      return v + (series.coal[i] || 0) + (series.oil[i] || 0);
    });
  }
  return series[key] || [];
}

/* ---------- networking ---------- */


function tick() {
  clearTimeout(state.timer);
  fetchText('/api/live').then(function (text) {
    state.failures = 0;
    state.lastOk = Date.now();
    if (text !== state.liveText) {
      state.liveText = text;
      state.live = JSON.parse(text);
      renderLive();
    }
    var live = state.live;
    if (live.history_version !== state.historyVersion || !state.history[state.range]) {
      return loadHistory(live.history_version);
    }
  }).catch(function () {
    state.failures += 1;
  }).then(function () {
    renderHeader();
    schedule();
  });
}

function schedule() {
  clearTimeout(state.timer);
  if (document.hidden) return;
  var seconds = (state.live && state.live.refresh_seconds) || 10;
  if (state.failures) seconds = Math.min(60, seconds * Math.pow(2, state.failures - 1));
  state.timer = setTimeout(tick, seconds * 1000);
}

function loadHistory(version) {
  var ranges = ['24h'];
  if (state.range !== '24h') ranges.push(state.range);
  return Promise.all(ranges.map(function (range) {
    return fetchText('/api/history?range=' + range).then(function (text) {
      state.history[range] = JSON.parse(text);
    });
  })).then(function () {
    state.historyVersion = version;
    renderHistoryViews();
  });
}

/* ---------- header and title ---------- */

function renderHeader() {
  var live = state.live, dot = byId('live-dot'), text = byId('live-text');
  var cls = 'dot', message;
  var offline = state.failures > 0 && (!state.lastOk || Date.now() - state.lastOk > 25000);
  if (offline) {
    cls += ' bad';
    message = 'Can’t reach the dashboard server. Retrying.';
  } else if (!live) {
    message = 'Connecting to the dashboard server…';
  } else if (!live.time) {
    var generation = live.status && live.status.generation;
    if (generation && generation.failures) {
      cls += ' warn';
      message = 'No generation data yet: Elexon isn’t responding. Details are under Data feeds.';
    } else {
      message = 'Collecting the first readings…';
    }
  } else {
    var age = Date.now() / 1000 - (live.time + 300);
    var checked = ago((Date.now() - state.lastOk) / 1000);
    if (age > 20 * 60) {
      cls += ' warn';
      message = 'Delayed. The latest generation data is for ' + hm(live.time) + ' ' + relDay(live.time) + '; checked ' + checked + '.';
    } else {
      cls += ' good pulse';
      message = 'Live. Generation data for ' + hm(live.time) + ', checked ' + checked + '.';
    }
  }
  if (dot.className !== cls) dot.className = cls;
  if (text.textContent !== message) text.textContent = message;
}

function updateTitleAndIcon(live) {
  if (!live.time) return;
  var carbon = live.carbon && num(live.carbon.value) ? ', ' + live.carbon.value + ' g/kWh' : '';
  document.title = 'GB grid: ' + gw(live.demand_gw) + ' GW' + carbon;
}

/* ---------- hero ---------- */

function forecastTrend(live) {
  var forecast = live.forecast || [];
  if (!live.carbon || !num(live.carbon.value) || forecast.length < 7) return '';
  var next = forecast.slice(1, 7).map(function (point) { return point[1]; });
  var average = next.reduce(function (a, b) { return a + b; }, 0) / next.length;
  var now = live.carbon.value;
  if (now > 20 && average <= now * 0.85) return ', and it should fall over the next few hours';
  if (average >= now * 1.15 && average - now > 10) return ', and it’s expected to rise over the next few hours';
  return '';
}

function describe(live) {
  var map = sourceMap(live);
  var contenders = ['wind', 'solar', 'hydro', 'nuclear', 'biomass', 'gas', 'imports']
    .map(function (key) { return map[key]; })
    .filter(function (item) { return item && num(item.gw); })
    .sort(function (a, b) { return b.gw - a.gw; });
  var sentences = [];
  var top = contenders[0];
  var noun = { wind: 'wind', solar: 'solar', hydro: 'hydro', nuclear: 'nuclear power', biomass: 'biomass', gas: 'gas', imports: 'imports' };
  if (top) {
    if (top.key === 'wind' && top.pct >= 40) sentences.push('A windy spell: wind is providing ' + pct(top.pct) + ' of supply.');
    else if (top.key === 'solar' && top.pct >= 25) sentences.push('The sun is doing a lot of the work, with solar providing ' + pct(top.pct) + ' of supply.');
    else sentences.push(capitalise(noun[top.key]) + ' is the biggest source, providing ' + pct(top.pct) + ' of supply.');
  }
  if (live.groups) {
    sentences.push(capitalise(pct(live.groups.low_carbon)) + ' is low-carbon (wind, solar, hydro and nuclear) and ' +
      pct(live.groups.fossil) + ' comes from fossil fuels.');
  }
  var carbon = live.carbon;
  if (carbon && num(carbon.value)) {
    var rating = carbon.index ? ', rated ' + carbon.index : (carbon.kind === 'estimate' ? ' (estimated)' : '');
    sentences.push('Carbon intensity is ' + carbon.value + ' g/kWh' + rating + forecastTrend(live) + '.');
  }
  return sentences.join(' ');
}

function renderHero(live) {
  var title = byId('hero-title'), lede = byId('hero-lede');
  if (!live.time) {
    title.textContent = 'Collecting the first readings…';
    lede.textContent = 'The dashboard is fetching live data. The first full picture usually appears within a minute.';
    return;
  }
  title.textContent = 'Britain is using ' + gw(live.demand_gw) + ' GW right now.';
  lede.textContent = describe(live);
}

/* ---------- mix bar ---------- */

function renderMix(live) {
  var root = clear(byId('mix'));
  if (!live.time) return;
  var parts = mixParts(live);
  var total = parts.reduce(function (a, part) { return a + part.pct; }, 0) || 100;
  var width = root.clientWidth || 800;
  var gap = 2, usable = width - gap * Math.max(0, parts.length - 1);
  var positions = {}, x = 0;
  parts.forEach(function (part) {
    var w = usable * part.pct / total;
    positions[part.key] = { x: x, w: w };
    x += w + gap;
  });

  var brackets = h('div', { cls: 'brackets', 'aria-hidden': 'true' });
  [
    { label: 'Low-carbon', keys: ['nuclear', 'wind', 'solar', 'hydro'], value: live.groups.low_carbon },
    { label: 'Fossil', keys: ['gas'], value: live.groups.fossil }
  ].forEach(function (group) {
    var keys = group.keys.filter(function (key) { return positions[key]; });
    if (!keys.length) return;
    var first = positions[keys[0]], last = positions[keys[keys.length - 1]];
    var x0 = first.x, x1 = last.x + last.w;
    var label = group.label + ' ' + pct(group.value);
    if (x1 - x0 < textWidth(label, 13, 400) + 6) label = pct(group.value);
    if (x1 - x0 < textWidth(label, 13, 400) + 6) return;
    brackets.appendChild(h('div', { cls: 'bracket', style: 'left:' + x0.toFixed(1) + 'px;width:' + (x1 - x0).toFixed(1) + 'px' },
      h('span', { text: label })));
  });
  root.appendChild(brackets);

  var bar = h('div', { cls: 'bar', role: 'group', 'aria-label': 'Supply by source' });
  var tip;
  parts.forEach(function (part) {
    var w = positions[part.key].w;
    var label = pct(part.pct).replace('under 1%', '<1%');
    var fits = w >= textWidth(label, 12.5, 650) + 16;
    var description = NAMES[part.key] + ': ' + gw(part.gw) + ' GW, ' + pct(part.pct) + ' of supply';
    var segment = h('div', {
      cls: 'seg b-' + part.key + ' ' + inkOn(cssColour('--c-' + part.key)),
      style: 'width:' + w.toFixed(1) + 'px',
      tabindex: '0', role: 'img', 'aria-label': description
    }, fits ? label : null);
    var show = function () {
      var box = segment.getBoundingClientRect(), host = root.getBoundingClientRect();
      tip.show(NAMES[part.key], [
        { cls: 'b-' + part.key, value: gw(part.gw) + ' GW', label: 'right now' },
        { value: pct(part.pct), label: 'of supply' }
      ], box.left - host.left + box.width / 2, box.bottom - host.top + 10, 'below');
    };
    segment.addEventListener('pointerenter', show);
    segment.addEventListener('focus', show);
    segment.addEventListener('pointerleave', function () { tip.hide(); });
    segment.addEventListener('blur', function () { tip.hide(); });
    bar.appendChild(segment);
  });
  root.appendChild(bar);

  var legend = h('ul', { cls: 'legend', 'aria-hidden': 'true' });
  parts.forEach(function (part) {
    legend.appendChild(h('li', null, [h('span', { cls: 'sw b-' + part.key }), NAMES[part.key]]));
  });
  root.appendChild(legend);
  tip = makeTip(root);
}

/* ---------- sparklines ---------- */

function sparkline(svg, values, options) {
  clear(svg);
  options = options || {};
  var width = svg.clientWidth || 200, height = svg.clientHeight || 30;
  var points = [];
  values.forEach(function (v, i) { if (num(v)) points.push([i, v]); });
  if (points.length < 2) return;
  var lo = options.min !== undefined ? options.min : Math.min.apply(null, points.map(function (p) { return p[1]; }));
  var hi = options.max !== undefined ? options.max : Math.max.apply(null, points.map(function (p) { return p[1]; }));
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  var n = values.length;
  var X = function (i) { return 3 + (width - 6) * i / Math.max(1, n - 1); };
  var Y = function (v) { v = Math.max(lo, Math.min(hi, v)); return 3 + (height - 6) * (1 - (v - lo) / (hi - lo)); };
  svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
  if (options.reference !== undefined) {
    svg.appendChild(s('line', { x1: 0, x2: width, y1: Y(options.reference), y2: Y(options.reference), class: 'grid-line' }));
  }
  var d = '', pen = false;
  values.forEach(function (v, i) {
    if (!num(v)) { pen = false; return; }
    d += (pen ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v).toFixed(1);
    pen = true;
  });
  svg.appendChild(s('path', { d: d, class: 'data-line' + (options.cls ? ' ' + options.cls : ''), style: options.stroke ? 'stroke:' + options.stroke : null }));
  var last = points[points.length - 1];
  svg.appendChild(s('circle', { cx: X(last[0]), cy: Y(last[1]), r: 3.5, class: 'hover-dot', style: options.stroke ? 'fill:' + options.stroke : null }));
}

/* ---------- key figures ---------- */

function setValue(id, value, unit) {
  var node = clear(byId(id));
  node.appendChild(document.createTextNode(value));
  if (unit) node.appendChild(h('span', { cls: 'unit', text: unit }));
}

function rankText(rank, better, worse) {
  if (!num(rank)) return '';
  var percent = Math.round(rank * 100);
  if (percent >= 95) return 'Among the ' + better + ' 5% of the past week';
  if (percent <= 5) return 'Among the ' + worse + ' 5% of the past week';
  return capitalise(better.replace('st', 'r')) + ' than ' + percent + '% of the past week';
}

function freqNote(hz) {
  var d = hz - 50, a = Math.abs(d);
  if (a <= 0.05) return 'Steady: supply and demand are closely matched.';
  if (a <= 0.1) return d < 0 ? 'Slightly low: demand is just ahead of supply.' : 'Slightly high: supply is just ahead of demand.';
  if (a <= 0.2) return d < 0 ? 'Low but within normal limits: demand is ahead of supply.' : 'High but within normal limits: supply is ahead of demand.';
  return 'Outside the normal 49.8 to 50.2 Hz band.';
}

function renderKpis(live) {
  if (live.time) {
    setValue('k-demand', gw(live.demand_gw), 'GW');
    byId('k-demand-note').textContent = rankText(live.demand_rank, 'busiest', 'quietest');
  }
  var carbon = live.carbon;
  var carbonNode = clear(byId('k-carbon'));
  var carbonNote = clear(byId('k-carbon-note'));
  if (carbon && num(carbon.value)) {
    carbonNode.appendChild(document.createTextNode(String(carbon.value)));
    carbonNode.appendChild(h('span', { cls: 'unit', text: 'g/kWh' }));
    if (carbon.index) {
      carbonNote.appendChild(h('span', { cls: 'level' }, [h('span', { cls: 'sw ' + levelClass(carbon.index) }), LEVEL_NAMES[carbon.index] || carbon.index]));
      carbonNote.appendChild(document.createTextNode(' '));
    } else if (carbon.kind === 'estimate') {
      carbonNote.appendChild(document.createTextNode('Estimated from the live mix. '));
    }
    carbonNote.appendChild(document.createTextNode(rankText(carbon.rank, 'cleanest', 'dirtiest')));
  } else {
    carbonNode.textContent = '–';
  }
  var price = live.price;
  if (price && num(price.value)) {
    setValue('k-price', money(price.value), '/MWh');
    var pence = price.value / 10;
    byId('k-price-note').textContent = (pence < 0 ? '−' : '') + Math.abs(pence).toFixed(1) + 'p per kWh, for ' +
      hm(price.from) + ' to ' + hm(price.from + 1800);
    byId('k-price-note-2').textContent = rankText(price.rank, 'cheapest', 'priciest');
  }
  var freq = live.frequency;
  if (freq && num(freq.hz)) {
    setValue('k-freq', freq.hz.toFixed(3), 'Hz');
    byId('k-freq-note').textContent = freqNote(freq.hz);
    sparkline(byId('s-freq'), freq.series.map(function (p) { return p[1]; }), { min: 49.8, max: 50.2, reference: 50 });
  }
}

function renderKpiSparks() {
  var hist = history24();
  if (!hist) return;
  sparkline(byId('s-demand'), hist.demand);
  sparkline(byId('s-carbon'), hist.carbon);
  sparkline(byId('s-price'), hist.price);
}

/* ---------- forecast ---------- */

function bestWindow(points, hours, from, horizon) {
  var length = hours * 2, best = null, worst = null;
  var usable = points.filter(function (p) { return p[0] >= from && p[0] < from + horizon; });
  for (var i = 0; i + length <= usable.length; i++) {
    var contiguous = usable[i + length - 1][0] - usable[i][0] === (length - 1) * 1800;
    if (!contiguous) continue;
    var sum = 0;
    for (var j = i; j < i + length; j++) sum += usable[j][1];
    var average = sum / length;
    if (worst === null || average > worst) worst = average;
    if (!best || average < best.average - 0.01) best = { start: usable[i][0], end: usable[i][0] + length * 1800, average: average };
  }
  if (best) best.worst = worst;
  return best;
}

var MIX_NAMES = { wind: 'wind', nuclear: 'nuclear', hydro: 'hydro', solar: 'solar', gas: 'gas', biomass: 'biomass', imports: 'imports', coal: 'coal', other: 'other sources' };
var MIX_CLASS = { wind: 'wind', nuclear: 'nuclear', hydro: 'hydro', solar: 'solar', gas: 'gas', biomass: 'biomass', imports: 'imports', coal: 'other', other: 'other' };
var ZERO_CARBON = { wind: true, nuclear: true, hydro: true, solar: true };
function listWords(items) {
  return items.length < 2 ? items.join('') : items.slice(0, -1).join(', ') + ' and ' + items[items.length - 1];
}

/* Region only: what its electricity is made of, why it can be zero, and how it usually compares */
function renderRegionContext(live, flatZero) {
  var host = clear(byId('best-sub'));
  var region = live.region;
  host.hidden = !region;
  if (!region) return;
  var sentences = [];
  var mix = region.mix && region.mix.mix ? region.mix.mix.filter(function (m) { return m[1] >= 1; }).sort(function (a, b) { return b[1] - a[1]; }) : [];
  if (mix.length) {
    var clean = mix.every(function (m) { return ZERO_CARBON[m[0]]; });
    sentences.push('Right now ' + region.name + '’s electricity comes from ' +
      listWords(mix.slice(0, 4).map(function (m) { return Math.round(m[1]) + '% ' + (MIX_NAMES[m[0]] || m[0]); })) +
      (clean ? ', all counted as zero-carbon' : '') + '.');
  }
  if (flatZero || region.value === 0) {
    sentences.push('NESO measures the electricity used in each region and follows the flows between regions, so a region making more clean power than it uses comes out at zero.');
  }
  var recent = region.recent;
  if (recent && recent.days >= 1) {
    var span = 'Over the last ' + Math.round(recent.days) + (Math.round(recent.days) === 1 ? ' day' : ' days');
    var gbText = num(recent.gb_average) ? recent.gb_average + ' g/kWh for Great Britain' : '';
    if (recent.zero_share >= 0.995) {
      sentences.push(span + ' it was at zero throughout' + (gbText ? ', while Great Britain averaged ' + recent.gb_average + ' g/kWh' : '') + '.');
    } else {
      var zeroText = recent.zero_share >= 0.005 ? ' and was at zero ' + Math.max(1, Math.round(100 * recent.zero_share)) + '% of the time' : '';
      sentences.push(span + ' it averaged ' + recent.average + ' g/kWh' + zeroText + (gbText ? ', against ' + gbText : '') + '.');
    }
  }
  host.appendChild(h('p', { text: sentences.join(' ') }));
  if (mix.length) {
    var bar = h('div', { cls: 'mix-bar', role: 'img', 'aria-label': region.name + ' supply mix: ' + mix.map(function (m) { return Math.round(m[1]) + '% ' + m[0]; }).join(', ') });
    mix.forEach(function (m) { bar.appendChild(h('i', { cls: 'b-' + (MIX_CLASS[m[0]] || 'other'), title: Math.round(m[1]) + '% ' + (MIX_NAMES[m[0]] || m[0]), style: 'flex:' + m[1] })); });
    host.appendChild(bar);
  }
}

function renderForecastControls(live) {
  syncToggle(byId('window-toggle'), [1, 2, 3, 4].map(function (hours) {
    return { value: hours, label: hours + ' h', aria: 'Cleanest ' + hours + (hours === 1 ? ' hour' : ' hours') };
  }), state.windowHours, function (hours) { state.windowHours = hours; renderForecast(state.live); });
  var regionToggle = byId('region-toggle');
  if (live.region) {
    regionToggle.hidden = false;
    syncToggle(regionToggle, [
      { value: false, label: 'Great Britain' },
      { value: true, label: live.region.name || 'My region' }
    ], state.useRegion, function (useRegion) { state.useRegion = useRegion; renderForecast(state.live); });
  } else {
    regionToggle.hidden = true;
    state.useRegion = false;
  }
}

function renderForecast(live) {
  renderForecastControls(live);
  var host = clear(byId('forecast-chart'));
  var region = state.useRegion && live.region;
  var past = (region ? live.region.past : live.carbon_past) || [];
  var future = (region ? live.region.forecast : live.forecast) || [];
  var best = byId('best');
  var scale = clear(byId('forecast-scale'));
  if (!future.length && !past.length) {
    var feed = live.status && live.status[region ? 'region' : 'carbon'];
    var area = region ? live.region.name : 'Great Britain';
    best.textContent = 'No carbon intensity forecast for ' + area + ' has arrived yet.';
    host.appendChild(h('p', { cls: 'empty', text: feed && feed.last_error
      ? 'The last attempt failed: ' + feed.last_error
      : 'It usually appears within a minute of starting. The data feeds list at the bottom of the page shows its status.' }));
    return;
  }
  var nowSlot = Math.floor(Date.now() / 1000 / 1800) * 1800;
  var width = Math.max(300, host.clientWidth);
  var narrow = width < 640;
  var start = nowSlot - (narrow ? 6 : 24) * 3600;
  var end = nowSlot + (narrow ? 30 : 48) * 3600;
  var values = {};
  past.concat(future).forEach(function (point) { values[point[0]] = point; });
  var slots = [];
  for (var t = start; t < end; t += 1800) slots.push(values[t] ? values[t] : [t, null, null]);

  var future24 = future.filter(function (p) { return p[0] >= nowSlot; });
  var win = bestWindow(future24, state.windowHours, nowSlot, 24 * 3600);
  var current = values[nowSlot];
  var flat = Boolean(win) && win.worst - win.average < 2;
  clear(best);
  if (win && flat) {
    var area = region ? live.region.name : 'Great Britain';
    if (win.average < 0.5) {
      best.appendChild(document.createTextNode('NESO forecasts the electricity used in ' + area + ' at '));
      best.appendChild(h('strong', { text: '0 g/kWh' }));
      best.appendChild(document.createTextNode(' for the whole of the next day, so any time is as clean as it gets.'));
    } else {
      best.appendChild(document.createTextNode('NESO forecasts about '));
      best.appendChild(h('strong', { text: Math.round(win.average) + ' g/kWh' }));
      best.appendChild(document.createTextNode(' in ' + area + ' for the whole of the next day, so there’s no cleaner time to wait for.'));
    }
  } else if (win) {
    var hours = state.windowHours;
    var startsNow = win.start <= nowSlot;
    var place = region ? ' in ' + live.region.name : '';
    if (startsNow) {
      best.appendChild(document.createTextNode((hours === 1 ? 'Now is the cleanest hour' : 'Now is the start of the cleanest ' + hours + ' hours') +
        place + ' in the next day, lasting until '));
      best.appendChild(h('strong', { text: hm(win.end) }));
    } else {
      best.appendChild(document.createTextNode((hours === 1 ? 'The cleanest hour' : 'The cleanest ' + hours + ' hours') + place +
        ' in the next day ' + (hours === 1 ? 'is ' : 'are ')));
      best.appendChild(h('strong', { text: hm(win.start) + ' to ' + hm(win.end) + ' ' + relDay(win.start) }));
    }
    var detail = ', averaging about ' + Math.round(win.average) + ' g/kWh';
    // A percentage of a few grams says little, so only compare meaningful drops
    if (current && num(current[1]) && current[1] - win.average >= 10 && !startsNow) {
      var saving = Math.round(100 * (1 - win.average / current[1]));
      if (saving >= 5) detail += ', ' + saving + '% lower than now';
    }
    best.appendChild(document.createTextNode(detail + '.'));
  } else {
    best.textContent = 'Not enough forecast data yet to find a window.';
  }
  renderRegionContext(region ? live : {}, flat && win.average < 0.5);

  /* Region view: Great Britain as a dashed reference line on the same scale */
  var ref = null;
  if (region) {
    var national = {};
    (live.carbon_past || []).concat(live.forecast || []).forEach(function (p) { national[p[0]] = p[1]; });
    ref = slots.map(function (slot) { return num(national[slot[0]]) ? national[slot[0]] : null; });
  }

  var height = 176, padL = 40, padR = 6, padT = 30, padB = 26;
  var plotW = width - padL - padR, plotH = height - padT - padB;
  var max = 0;
  slots.forEach(function (slot) { if (num(slot[1])) max = Math.max(max, slot[1]); });
  var regionMax = max;
  if (ref) ref.forEach(function (v) { if (num(v)) max = Math.max(max, v); });
  var step = niceStep(Math.max(10, max), 3);
  var top = Math.max(step, Math.ceil(max / step) * step);
  var slotW = plotW / slots.length;
  var gap = slotW > 7 ? 2 : slotW > 3 ? 1 : 0;
  var X = function (t) { return padL + (t - start) / 1800 * slotW; };
  var Y = function (v) { return padT + plotH * (1 - v / top); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img',
    'aria-label': 'Carbon intensity for the past ' + (narrow ? '6' : '24') + ' hours and forecast for the next ' + (narrow ? '30' : '48') + ' hours' });
  for (var v = 0; v <= top + 0.001; v += step) {
    svg.appendChild(s('line', { x1: padL, x2: width - padR, y1: Y(v), y2: Y(v), class: v === 0 ? 'zero-line' : 'grid-line' }));
    svg.appendChild(s('text', { x: padL - 8, y: Y(v) + 4, 'text-anchor': 'end', class: 'axis-text', text: v === top ? Math.round(v) + ' g' : String(Math.round(v)) }));
  }
  var axisLabels = [];
  slots.forEach(function (slot) {
    var x = X(slot[0]);
    var minute = hourMinute(slot[0]);
    if (minute[1] === 0 && minute[0] % (narrow ? 12 : 6) === 0) {
      var midnight = minute[0] === 0;
      axisLabels.push({ x: x, strong: midnight, text: midnight ? weekdayFmt.format(new Date(slot[0] * 1000)) : hm(slot[0]) });
    }
    if (!num(slot[1])) return;
    var barHeight = Math.max(3, Y(0) - Y(slot[1]));
    svg.appendChild(s('rect', {
      x: (x + gap / 2).toFixed(2), y: (Y(0) - barHeight).toFixed(2), width: Math.max(0.8, slotW - gap).toFixed(2), height: barHeight.toFixed(2),
      rx: slotW > 5 ? 1.5 : 0, class: levelClass(slot[2]) + (slot[0] > nowSlot ? ' forecast-col' : '')
    }));
  });
  placeLabels(svg, axisLabels, height - 8);
  if (ref) {
    var d = '', open = false, lastRef = null;
    slots.forEach(function (slot, i) {
      var v = ref[i];
      if (!num(v)) { open = false; return; }
      d += (open ? 'L' : 'M') + X(slot[0]).toFixed(1) + ' ' + Y(v).toFixed(1) + 'L' + X(slot[0] + 1800).toFixed(1) + ' ' + Y(v).toFixed(1);
      open = true;
      lastRef = { x: X(slot[0] + 1800), y: Y(v) };
    });
    if (d) {
      svg.appendChild(s('path', { d: d, class: 'ref-line' }));
      svg.appendChild(s('text', { x: lastRef.x - 2, y: lastRef.y - 6, 'text-anchor': 'end', class: 'ref-text', text: 'Great Britain' }));
    }
  }
  if (win && !flat) {
    var bx0 = X(win.start) + 1, bx1 = X(win.end) - 1, by = padT - 8;
    svg.appendChild(s('path', { d: 'M' + bx0 + ' ' + (by + 5) + 'V' + by + 'H' + bx1 + 'V' + (by + 5), class: 'bracket-line' }));
    var label = 'Best ' + state.windowHours + ' h';
    var mid = (bx0 + bx1) / 2;
    var anchor = mid < padL + 30 ? 'start' : mid > width - 40 ? 'end' : 'middle';
    svg.appendChild(s('text', { x: anchor === 'start' ? bx0 : anchor === 'end' ? bx1 : mid, y: by - 5, 'text-anchor': anchor, class: 'bracket-text', text: label }));
  }
  var nowX = X(nowSlot + (Date.now() / 1000 - nowSlot));
  svg.appendChild(s('line', { x1: nowX, x2: nowX, y1: padT - 2, y2: Y(0), class: 'now-line' }));
  var nowLabelX = Math.min(width - 30, nowX + 5);
  svg.appendChild(s('text', { x: nowLabelX, y: padT + 10, class: 'now-text', text: 'Now' }));

  var hoverRect = s('rect', { class: 'col-hover', x: 0, y: padT, width: 0, height: plotH, visibility: 'hidden' });
  svg.insertBefore(hoverRect, svg.firstChild);
  var overlay = s('rect', { x: padL, y: 0, width: plotW, height: height, fill: 'transparent', tabindex: '0',
    'aria-label': 'Forecast chart. Use the left and right arrow keys to read each half hour.' });
  svg.appendChild(overlay);
  host.appendChild(svg);
  var tip = makeTip(host);
  var focusIndex = slots.findIndex(function (slot) { return slot[0] === nowSlot; });
  var showSlot = function (index, pointerY) {
    index = Math.max(0, Math.min(slots.length - 1, index));
    focusIndex = index;
    var slot = slots[index];
    hoverRect.setAttribute('x', X(slot[0]));
    hoverRect.setAttribute('width', slotW);
    hoverRect.setAttribute('visibility', 'visible');
    var rows = [];
    if (num(slot[1])) {
      rows.push({ cls: levelClass(slot[2]), value: Math.round(slot[1]) + ' g/kWh', label: slot[2] ? LEVEL_NAMES[slot[2]] || slot[2] : '' });
    } else {
      rows.push({ value: 'No data', label: '' });
    }
    if (ref && num(ref[index])) rows.push({ cls: 'b-line', value: Math.round(ref[index]) + ' g/kWh', label: 'Great Britain' });
    rows.push({ value: slot[0] > nowSlot ? 'Forecast' : slot[0] === nowSlot ? 'Now' : 'Past', label: region ? live.region.name : 'Great Britain' });
    tip.show(stamp(slot[0]) + ' to ' + hm(slot[0] + 1800), rows, X(slot[0]) + slotW / 2, pointerY === undefined ? padT + 20 : pointerY);
  };
  overlay.addEventListener('pointermove', function (event) {
    var box = svg.getBoundingClientRect();
    var x = (event.clientX - box.left) * width / box.width;
    showSlot(Math.floor((x - padL) / slotW), (event.clientY - box.top) * height / box.height);
  });
  overlay.addEventListener('pointerleave', function () { tip.hide(); hoverRect.setAttribute('visibility', 'hidden'); });
  overlay.addEventListener('blur', function () { tip.hide(); hoverRect.setAttribute('visibility', 'hidden'); });
  overlay.addEventListener('focus', function () { showSlot(focusIndex < 0 ? 0 : focusIndex); });
  overlay.addEventListener('keydown', function (event) {
    if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
      event.preventDefault();
      showSlot(focusIndex + (event.key === 'ArrowRight' ? 1 : -1));
    } else if (event.key === 'Escape') {
      tip.hide();
    }
  });

  LEVELS.forEach(function (level) {
    scale.appendChild(h('li', null, [h('span', { cls: 'sw ' + levelClass(level) }), LEVEL_NAMES[level]]));
  });
  if (ref) scale.appendChild(h('li', null, [h('span', { cls: 'sw line-sw' }), 'Great Britain']));
  var lowNote = !ref && regionMax > 0 && regionMax < 10 ? 'Every half hour shown is under 10 g/kWh, so the scale is zoomed in' : null;
  scale.appendChild(h('li', { cls: 'note', text: (lowNote ? lowNote + '. ' : '') + 'Faded bars are NESO’s forecast' }));
}

/* ---------- sources table ---------- */

function renderSources(live) {
  var host = clear(byId('sources'));
  if (!live.time) {
    host.appendChild(h('p', { cls: 'empty', text: 'Waiting for generation data from Elexon…' }));
    return;
  }
  var map = sourceMap(live);
  var hist = history24();
  var table = h('table', { cls: 'src' });
  table.appendChild(h('thead', null, h('tr', null, [
    h('th', { scope: 'col', text: 'Source' }),
    h('th', { scope: 'col', cls: 'num', text: 'GW' }),
    h('th', { scope: 'col', cls: 'num', text: 'Share' }),
    h('th', { scope: 'col', cls: 'spark-cell', text: 'Last 24 hours' })
  ])));
  var body = h('tbody');
  ORDER.forEach(function (key) {
    var item = key === 'storage' ? map.storage_out : map[key];
    var amount = item ? item.gw : 0;
    var share = item ? item.pct : 0;
    if (key === 'other') {
      ['coal', 'oil'].forEach(function (extra) {
        if (map[extra]) { amount += map[extra].gw; share += map[extra].pct; }
      });
    }
    var hint = null;
    if (key === 'wind' && item && item.parts && item.parts.embedded > 0.05) hint = gw(item.parts.embedded) + ' GW estimated from small farms';
    if (key === 'solar') hint = 'Estimated';
    if (key === 'gas' && item && item.parts && item.parts.ocgt >= 0.05) hint = gw(item.parts.ocgt) + ' GW from fast-start peaking plants';
    var explainId = 'about-' + key;
    var isOpen = !!state.openAbout[key];
    var row = h('tr', null, [
      h('th', { scope: 'row' }, h('button', {
        cls: 'name-btn', type: 'button', 'aria-expanded': String(isOpen), 'aria-controls': explainId,
        onclick: function (event) {
          var button = event.currentTarget, open = button.getAttribute('aria-expanded') !== 'true';
          button.setAttribute('aria-expanded', String(open));
          byId(explainId).hidden = !open;
          state.openAbout[key] = open;
        }
      }, [h('span', { cls: 'sw b-' + key }), h('span', { cls: 'label', text: NAMES[key] })])),
      h('td', { cls: 'num', text: gw(amount) }),
      h('td', { cls: 'num' }, [h('span', { cls: 'meter', 'aria-hidden': 'true' }, h('i', { cls: 'b-' + key, style: 'width:' + Math.min(100, share).toFixed(1) + '%' })), h('span', { cls: 'pct', text: pct(share).replace('under 1%', '<1%') })]),
      h('td', { cls: 'spark-cell' }, s('svg', { class: 'spark', 'data-key': key, 'aria-hidden': 'true' }))
    ]);
    if (hint) row.firstChild.appendChild(h('span', { cls: 'hint', text: hint }));
    body.appendChild(row);
    var about = h('tr', { cls: 'explain-row', id: explainId }, h('td', { colspan: '4' }, h('div', { cls: 'explain' }, h('p', { text: ABOUT[key] }))));
    about.hidden = !isOpen;
    body.appendChild(about);
  });
  table.appendChild(body);
  table.appendChild(h('tfoot', null, h('tr', null, h('td', { colspan: '4',
    text: 'Total supply ' + gw(live.supply_gw) + ' GW. ' + (live.exports_gw > 0.05 ? gw(live.exports_gw) + ' GW is being exported' : 'Nothing is being exported') +
      (live.storing_gw > 0.05 ? ' and ' + gw(live.storing_gw) + ' GW is going into storage.' : '.') }))));
  host.appendChild(table);
  renderSourceSparks();
}

function renderSourceSparks() {
  var hist = history24();
  if (!hist) return;
  var nodes = document.querySelectorAll('#sources svg.spark');
  Array.prototype.forEach.call(nodes, function (svg) {
    var key = svg.getAttribute('data-key');
    sparkline(svg, combinedSeries(hist, key), { min: 0, stroke: 'var(--c-' + key + ')' });
  });
}

/* ---------- flows ---------- */

function renderFlows(live) {
  var host = clear(byId('flows'));
  if (!live.time) {
    host.appendChild(h('p', { cls: 'empty', text: 'Waiting for interconnector data…' }));
    return;
  }
  var net = 0;
  (live.countries || []).forEach(function (country) { net += country.gw || 0; });
  var netText = net > 0.05 ? 'Britain is importing ' + gw(net) + ' GW overall.'
    : net < -0.05 ? 'Britain is exporting ' + gw(-net) + ' GW overall.'
    : 'Imports and exports are roughly balanced.';
  host.appendChild(h('p', { cls: 'net', text: netText }));
  host.appendChild(h('p', { cls: 'flow-key', 'aria-hidden': 'true' }, [
    h('span'), h('span', null, [h('span', null, ['← ', h('span', { cls: 'long', text: 'Exporting' }), h('span', { cls: 'short', text: 'Out' })]), h('span', null, [h('span', { cls: 'long', text: 'Importing' }), h('span', { cls: 'short', text: 'In' }), ' →'])]), h('span')
  ]));

  var links = {};
  (live.links || []).forEach(function (link) { (links[link.country] = links[link.country] || []).push(link); });
  var list = h('ul', { cls: 'flows' });
  (live.countries || []).forEach(function (country) {
    var cables = links[country.country] || [];
    var capacity = cables.reduce(function (a, link) { return a + (link.capacity || 0); }, 0) || Math.max(1, Math.abs(country.gw));
    list.appendChild(flowRow(country.country, country.gw, capacity, 'imports', cables.map(function (link) {
      var state = Math.abs(link.gw) < 0.01 ? 'idle' : (link.gw > 0 ? gw(link.gw) + ' in' : gw(-link.gw) + ' out');
      return link.name + ' ' + state;
    }).join(', ')));
  });
  host.appendChild(list);

  host.appendChild(h('h3', { cls: 'flows-sep', text: 'Storage' }));
  var storage = h('ul', { cls: 'flows' });
  var pumped = live.storage ? live.storage.pumped : 0;
  storage.appendChild(flowRow('Pumped hydro', pumped, 3, 'storage',
    Math.abs(pumped) < 0.01 ? 'Idle' : pumped < 0 ? 'Pumping water uphill to store spare power' : 'Releasing water to generate',
    { positive: 'Generating', negative: 'Pumping' }));
  var battery = live.storage ? live.storage.battery : 0;
  storage.appendChild(flowRow('Batteries', battery, 3, 'storage',
    'Discharging only; charging isn’t reported', { positive: 'Discharging', negative: 'Charging' }));
  host.appendChild(storage);
}

function flowRow(name, value, capacity, colourKey, detail, words) {
  words = words || { positive: 'Importing', negative: 'Exporting' };
  var share = Math.min(1, Math.abs(value) / capacity);
  var bar = h('div', { cls: 'diverge', 'aria-hidden': 'true' });
  if (Math.abs(value) >= 0.005) {
    bar.appendChild(h('i', { cls: (value > 0 ? 'in' : 'out') + ' b-' + colourKey + (value < 0 ? ' neg' : ''), style: 'width:' + (50 * share).toFixed(1) + '%' }));
  }
  var what = Math.abs(value) < 0.005 ? h('span', { text: 'Idle' })
    : [h('span', { text: (value > 0 ? words.positive : words.negative) + ' ' }), gw(Math.abs(value)) + ' GW'];
  return h('li', { cls: 'flow' }, [
    h('span', { cls: 'who', text: name }),
    bar,
    h('span', { cls: 'what' }, what),
    detail ? h('span', { cls: 'detail', text: detail }) : null
  ]);
}

/* ---------- history charts ---------- */

function renderRangeTabs() {
  var ranges = (state.live && state.live.ranges) || ['24h', '7d'];
  syncToggle(byId('range-toggle'), ranges.map(function (range) { return { value: range, label: RANGE_NAMES[range] }; }),
    state.range, function (range) {
      if (state.range === range) return;
      state.range = range;
      renderRangeTabs();
      if (state.history[range] && state.historyVersion === (state.live && state.live.history_version)) renderHistory();
      else if (state.live) loadHistory(state.live.history_version);
    });
}

function timeTicks(times, range) {
  var ticks = [];
  times.forEach(function (t, i) {
    var parts = hourMinute(t);
    if (parts[1] !== 0) return;
    if (range === '24h' && parts[0] % 3 === 0) ticks.push({ i: i, text: parts[0] === 0 ? weekdayFmt.format(new Date(t * 1000)) : hm(t), strong: parts[0] === 0 });
    else if (range === '7d' && parts[0] === 0) ticks.push({ i: i, text: weekdayFmt.format(new Date(t * 1000)) + ' ' + dateFmt.format(new Date(t * 1000)).split(' ')[0], strong: true });
    else if (range === '30d' && parts[0] === 0) {
      var weekday = weekdayFmt.format(new Date(t * 1000));
      if (weekday === 'Mon') ticks.push({ i: i, text: dateFmt.format(new Date(t * 1000)), strong: true });
    }
  });
  return ticks;
}

function renderHistoryViews() {
  renderKpiSparks();
  renderSourceSparks();
  renderHistory();
}

function renderHistory() {
  byId('history-title').firstChild.nodeValue = 'The last ' + RANGE_NAMES[state.range] + ' ';
  renderRangeTabs();
  var hist = state.history[state.range];
  var host = byId('history');
  var legend = clear(byId('history-legend'));
  if (!hist) return;
  clear(host);
  ORDER.forEach(function (key) { legend.appendChild(h('li', null, [h('span', { cls: 'sw b-' + key }), NAMES[key]])); });
  legend.appendChild(h('li', null, 'Below the line: exports and storage charging'));

  var n = hist.t.length;
  var series = {};
  ORDER.forEach(function (key) { series[key] = combinedSeries(hist, key); });
  var exportsSeries = hist.series.exports, chargingSeries = hist.series.storage_in;
  var stacks = {}, negatives = { exports: [], storage_in: [] }, totals = [];
  ORDER.forEach(function (key) { stacks[key] = []; });
  var maxPos = 0, minNeg = 0;
  for (var i = 0; i < n; i++) {
    if (!num(series.wind[i])) {
      ORDER.forEach(function (key) { stacks[key][i] = null; });
      negatives.exports[i] = negatives.storage_in[i] = null;
      totals[i] = null;
      continue;
    }
    var acc = 0;
    for (var k = 0; k < ORDER.length; k++) {
      var amount = Math.max(0, series[ORDER[k]][i] || 0);
      stacks[ORDER[k]][i] = [acc, acc + amount];
      acc += amount;
    }
    totals[i] = acc;
    maxPos = Math.max(maxPos, acc);
    var out = Math.min(0, exportsSeries[i] || 0), charge = Math.min(0, chargingSeries[i] || 0);
    negatives.exports[i] = [0, out];
    negatives.storage_in[i] = [out, out + charge];
    minNeg = Math.min(minNeg, out + charge);
  }

  var width = Math.max(320, host.clientWidth);
  var padL = 46, padR = 10;
  var plotW = width - padL - padR;
  var X = function (index) { return padL + (n <= 1 ? 0 : index * plotW / (n - 1)); };
  var ticks = timeTicks(hist.t, state.range);
  var charts = [];

  // Generation stack
  var mainH = width < 640 ? 230 : 290, padT = 10, padB = 26;
  var step = niceStep(Math.max(5, maxPos - minNeg), 5);
  var yMax = Math.max(step, Math.ceil(maxPos / step) * step);
  var yMin = minNeg < 0 ? Math.floor(minNeg / step) * step : 0;
  var Y = function (v) { return padT + (mainH - padT - padB) * (yMax - v) / (yMax - yMin); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + mainH, height: mainH, role: 'img',
    'aria-label': 'Electricity supply by source over the last ' + RANGE_NAMES[state.range] + ', in gigawatts' });
  for (var v = yMin; v <= yMax + 0.001; v += step) {
    svg.appendChild(s('line', { x1: padL, x2: width - padR, y1: Y(v), y2: Y(v), class: Math.abs(v) < 0.001 ? 'zero-line' : 'grid-line' }));
    svg.appendChild(s('text', { x: padL - 8, y: Y(v) + 4, 'text-anchor': 'end', class: 'axis-text', text: (Math.abs(v - yMax) < 0.001 ? v + ' GW' : String(v)).replace('-', '−') }));
  }
  function bandPath(band) {
    var d = '', run = [];
    var flush = function () {
      if (run.length < 2) { run = []; return; }
      d += 'M' + run.map(function (index) { return X(index).toFixed(1) + ' ' + Y(band[index][1]).toFixed(1); }).join('L');
      d += 'L' + run.slice().reverse().map(function (index) { return X(index).toFixed(1) + ' ' + Y(band[index][0]).toFixed(1); }).join('L') + 'Z';
      run = [];
    };
    for (var index = 0; index < n; index++) {
      if (band[index]) run.push(index); else flush();
    }
    flush();
    return d;
  }
  function edgePath(band) {
    var d = '', pen = false;
    for (var index = 0; index < n; index++) {
      if (!band[index]) { pen = false; continue; }
      d += (pen ? 'L' : 'M') + X(index).toFixed(1) + ' ' + Y(band[index][1]).toFixed(1);
      pen = true;
    }
    return d;
  }
  ORDER.forEach(function (key) { svg.appendChild(s('path', { d: bandPath(stacks[key]), class: 'f-' + key })); });
  svg.appendChild(s('path', { d: bandPath(negatives.exports), class: 'f-imports neg' }));
  svg.appendChild(s('path', { d: bandPath(negatives.storage_in), class: 'f-storage neg' }));
  ORDER.slice(0, -1).forEach(function (key) { svg.appendChild(s('path', { d: edgePath(stacks[key]), class: 'band-gap' })); });
  svg.appendChild(s('path', { d: edgePath(negatives.exports), class: 'band-gap' }));
  var tickLabels = ticks.map(function (tick) { return { x: X(tick.i), text: tick.text, strong: tick.strong }; });
  placeLabels(svg, tickLabels, mainH - 8);
  charts.push({ svg: svg, height: mainH, top: padT, bottom: mainH - padB });

  // Small multiples: carbon intensity and price, each with its own axis
  function lineChart(title, values, unit, formatTick) {
    var height = 118, top = 10, bottom = 24;
    var finite = values.filter(num);
    var lo = finite.length ? Math.min.apply(null, finite) : 0, hi = finite.length ? Math.max.apply(null, finite) : 1;
    var lineStep = niceStep(Math.max(1, hi - Math.min(0, lo)), 3);
    var max = Math.max(lineStep, Math.ceil(hi / lineStep) * lineStep);
    var min = lo < 0 ? Math.floor(lo / lineStep) * lineStep : 0;
    var LY = function (value) { return top + (height - top - bottom) * (max - value) / (max - min || 1); };
    var chart = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': title + ' over the last ' + RANGE_NAMES[state.range] });
    for (var value = min; value <= max + 0.001; value += lineStep) {
      chart.appendChild(s('line', { x1: padL, x2: width - padR, y1: LY(value), y2: LY(value), class: Math.abs(value) < 0.001 ? 'zero-line' : 'grid-line' }));
      chart.appendChild(s('text', { x: padL - 8, y: LY(value) + 4, 'text-anchor': 'end', class: 'axis-text', text: formatTick(value, Math.abs(value - max) < 0.001) }));
    }
    var d = '', area = '', pen = false;
    for (var index = 0; index < n; index++) {
      if (!num(values[index])) {
        if (pen) area += 'L' + X(index - 1).toFixed(1) + ' ' + LY(Math.max(min, 0)).toFixed(1) + 'Z';
        pen = false;
        continue;
      }
      var px = X(index).toFixed(1), py = LY(values[index]).toFixed(1);
      if (!pen) {
        area += 'M' + px + ' ' + LY(Math.max(min, 0)).toFixed(1) + 'L' + px + ' ' + py;
        d += 'M' + px + ' ' + py;
      } else {
        var prevY = LY(values[index - 1]).toFixed(1);
        d += 'L' + px + ' ' + prevY + 'L' + px + ' ' + py;
        area += 'L' + px + ' ' + prevY + 'L' + px + ' ' + py;
      }
      pen = true;
    }
    if (pen) area += 'L' + X(n - 1).toFixed(1) + ' ' + LY(Math.max(min, 0)).toFixed(1) + 'Z';
    chart.appendChild(s('path', { d: area, class: 'wash-area', style: 'fill:var(--line)' }));
    chart.appendChild(s('path', { d: d, class: 'data-line' }));
    placeLabels(chart, tickLabels, height - 7);
    return { svg: chart, height: height, top: top, bottom: height - bottom, values: values, Y: LY, title: title, unit: unit };
  }

  host.appendChild(charts[0].svg);
  var carbonChart = lineChart('Carbon intensity', hist.carbon, 'g/kWh', function (value, isTop) { return isTop ? Math.round(value) + ' g' : String(Math.round(value)); });
  var priceChart = lineChart('Wholesale price', hist.price, '£/MWh', function (value, isTop) { return (value < 0 ? '−' : '') + (isTop ? '£' : '') + Math.abs(Math.round(value)); });
  host.appendChild(h('p', { cls: 'mini-title', text: 'Carbon intensity, g/kWh' }));
  host.appendChild(carbonChart.svg);
  host.appendChild(h('p', { cls: 'mini-title', text: 'Wholesale price, £/MWh' }));
  host.appendChild(priceChart.svg);
  charts.push(carbonChart, priceChart);

  // Synchronised crosshair across the three charts
  var tip = makeTip(host);
  var hairs = charts.map(function (chart) {
    var line = s('line', { class: 'xhair', y1: chart.top, y2: chart.bottom, visibility: 'hidden' });
    chart.svg.appendChild(line);
    var dot = chart.values ? s('circle', { r: 4, class: 'hover-dot', visibility: 'hidden' }) : null;
    if (dot) chart.svg.appendChild(dot);
    return { line: line, dot: dot };
  });
  function show(index, anchorY) {
    index = Math.max(0, Math.min(n - 1, index));
    state.hoverIndex = index;
    charts.forEach(function (chart, c) {
      var x = X(index);
      hairs[c].line.setAttribute('x1', x);
      hairs[c].line.setAttribute('x2', x);
      hairs[c].line.setAttribute('visibility', 'visible');
      if (hairs[c].dot) {
        var value = chart.values[index];
        if (num(value)) {
          hairs[c].dot.setAttribute('cx', x);
          hairs[c].dot.setAttribute('cy', chart.Y(value));
          hairs[c].dot.setAttribute('visibility', 'visible');
        } else {
          hairs[c].dot.setAttribute('visibility', 'hidden');
        }
      }
    });
    var t = hist.t[index];
    var label = state.range === '24h' ? stamp(t) : stamp(t) + ' to ' + hm(t + hist.step);
    var rows = [];
    if (!num(totals[index])) {
      rows.push({ value: 'No data', label: 'for this period' });
    } else {
      rows.push({ cls: 'none', value: gw(hist.demand[index]) + ' GW', label: 'Demand' });
      rows.push({ sep: true });
      ORDER.slice().reverse().forEach(function (key) {
        var value = series[key][index];
        if (num(value) && value >= 0.005) rows.push({ cls: 'b-' + key, value: gw(value), label: NAMES[key] });
      });
      if ((exportsSeries[index] || 0) <= -0.005) rows.push({ cls: 'b-imports', value: signed(exportsSeries[index], gw), label: 'Exports' });
      if ((chargingSeries[index] || 0) <= -0.005) rows.push({ cls: 'b-storage', value: signed(chargingSeries[index], gw), label: 'Storage charging' });
    }
    rows.push({ sep: true });
    rows.push({ cls: 'b-line', value: num(hist.carbon[index]) ? hist.carbon[index] + ' g/kWh' : '–', label: hist.carbon_kind[index] === 'e' ? 'Carbon (estimate)' : 'Carbon' });
    rows.push({ cls: 'b-line', value: money(hist.price[index]), label: 'Price per MWh' });
    tip.show(label, rows, X(index), anchorY === undefined ? 60 : anchorY);
  }
  function hide() {
    state.hoverIndex = null;
    hairs.forEach(function (hair) {
      hair.line.setAttribute('visibility', 'hidden');
      if (hair.dot) hair.dot.setAttribute('visibility', 'hidden');
    });
    tip.hide();
  }
  charts.forEach(function (chart, c) {
    var overlay = s('rect', { x: padL, y: 0, width: plotW, height: chart.height, fill: 'transparent' });
    if (c === 0) {
      overlay.setAttribute('tabindex', '0');
      overlay.setAttribute('aria-label', 'History chart. Use the left and right arrow keys to read values.');
    }
    chart.svg.appendChild(overlay);
    overlay.addEventListener('pointermove', function (event) {
      var box = chart.svg.getBoundingClientRect(), hostBox = host.getBoundingClientRect();
      var x = (event.clientX - box.left) * width / box.width;
      show(Math.round((x - padL) / plotW * (n - 1)), event.clientY - hostBox.top);
    });
    overlay.addEventListener('pointerleave', hide);
    if (c === 0) {
      overlay.addEventListener('focus', function () { show(state.hoverIndex === null ? n - 1 : state.hoverIndex); });
      overlay.addEventListener('blur', hide);
      overlay.addEventListener('keydown', function (event) {
        var current = state.hoverIndex === null ? n - 1 : state.hoverIndex;
        var jump = event.shiftKey ? 12 : 1;
        if (event.key === 'ArrowRight') { event.preventDefault(); show(current + jump); }
        else if (event.key === 'ArrowLeft') { event.preventDefault(); show(current - jump); }
        else if (event.key === 'Escape') hide();
      });
    }
  });
  if (state.tableOpen) renderTable();
}

function renderTable() {
  var host = clear(byId('history-table'));
  var hist = state.history[state.range];
  if (!hist) return;
  var every = state.range === '24h' ? 12 : state.range === '7d' ? 6 : 12;
  var columns = ['demand'].concat(ORDER, ['exports', 'storage_in', 'carbon', 'price']);
  var table = h('table', { cls: 'data-table' });
  var headRow = h('tr', null, h('th', { scope: 'col', text: 'Time' }));
  columns.forEach(function (key) {
    var label = key === 'demand' ? 'Demand GW' : key === 'carbon' ? 'Carbon g/kWh' : key === 'price' ? 'Price £/MWh' : NAMES[key] + ' GW';
    headRow.appendChild(h('th', { scope: 'col', text: label }));
  });
  table.appendChild(h('thead', null, headRow));
  var body = h('tbody');
  for (var i = hist.t.length - 1; i >= 0; i -= every) {
    var row = h('tr', null, h('td', { text: stamp(hist.t[i]) }));
    columns.forEach(function (key) {
      var value;
      if (key === 'demand') value = gw(hist.demand[i]);
      else if (key === 'carbon') value = num(hist.carbon[i]) ? String(hist.carbon[i]) : '–';
      else if (key === 'price') value = num(hist.price[i]) ? signed(hist.price[i], function (v) { return v.toFixed(2); }) : '–';
      else if (key === 'exports' || key === 'storage_in') value = num(hist.series[key][i]) ? signed(hist.series[key][i], gw) : '–';
      else value = gw(combinedSeries(hist, key)[i]);
      row.appendChild(h('td', { text: value }));
    });
    body.appendChild(row);
  }
  table.appendChild(body);
  host.appendChild(table);
}

/* ---------- data feed status ---------- */

var FEED_ORDER = ['generation', 'frequency', 'carbon', 'region', 'price', 'solar', 'embedded', 'ddu', 'units'];
var STALE_AFTER = { generation: 1200, frequency: 600, price: 3 * 3600, solar: 2 * 3600, units: 1800 };

function statusIcon(kind) {
  var colour = kind === 'good' ? 'var(--good)' : kind === 'warn' ? 'var(--warn)' : kind === 'bad' ? 'var(--bad)' : 'var(--muted)';
  var svg = s('svg', { viewBox: '0 0 16 16', 'aria-hidden': 'true' });
  if (kind === 'warn') {
    svg.appendChild(s('path', { d: 'M8 1.5 15 14.5H1Z', fill: colour }));
    svg.appendChild(s('path', { d: 'M8 6v4M8 12v.5', stroke: '#14202b', 'stroke-width': 1.6, 'stroke-linecap': 'round' }));
  } else {
    svg.appendChild(s('circle', { cx: 8, cy: 8, r: 7, fill: colour }));
    var mark = kind === 'good' ? 'M4.8 8.3 7 10.4 11.3 5.8' : kind === 'bad' ? 'M5.5 5.5l5 5M10.5 5.5l-5 5' : 'M5 8h6';
    svg.appendChild(s('path', { d: mark, fill: 'none', stroke: '#ffffff', 'stroke-width': 1.7, 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  }
  return svg;
}

function renderFeeds(live) {
  var host = clear(byId('feeds'));
  var now = Date.now() / 1000;
  var feeds = live.status || {};
  FEED_ORDER.forEach(function (key) {
    var feed = feeds[key];
    if (!feed) return;
    var kind = 'good', word = 'Working';
    var staleLimit = STALE_AFTER[key];
    if (!feed.last_success) {
      kind = feed.failures ? 'bad' : 'idle';
      word = feed.failures ? 'Unavailable' : 'Starting';
    } else if (feed.failures) {
      kind = 'warn';
      word = 'Retrying';
    } else if (staleLimit && feed.data_time && now - feed.data_time > staleLimit) {
      kind = 'warn';
      word = 'Delayed';
    }
    var details = word;
    if (feed.last_success) details += ', updated ' + ago(now - feed.last_success);
    if (feed.data_time) details += '; latest data for ' + hm(feed.data_time) + (relDay(feed.data_time) === 'today' ? '' : ' ' + relDay(feed.data_time));
    if (feed.interval) details += '. Checked every ' + (feed.interval >= 3600 ? Math.round(feed.interval / 3600) + ' h' : feed.interval >= 60 ? Math.round(feed.interval / 60) + ' min' : feed.interval + ' s') + '.';
    host.appendChild(h('li', { cls: 'feed' }, [
      statusIcon(kind),
      h('span', { cls: 'name', text: feed.label }),
      h('span', { cls: 'state', text: details }),
      feed.last_error ? h('span', { cls: 'err', text: feed.last_error }) : null
    ]));
  });
  if (!live.persistent) {
    host.appendChild(h('li', { cls: 'feed' }, [statusIcon('warn'), h('span', { cls: 'name', text: 'Storage' }),
      h('span', { cls: 'state', text: 'The data folder isn’t writable, so history resets when the container restarts.' })]));
  }
}

/* ---------- orchestration ---------- */

function renderLive() {
  var live = state.live;
  if (!live) return;
  var width = document.documentElement.clientWidth;
  renderHeader();
  renderHero(live);
  renderKpis(live);
  if (changed('mix', [live.time, live.sources, live.groups, width, G.theme])) renderMix(live);
  if (changed('forecast', [live.carbon_past, live.forecast, live.region, width, G.theme])) renderForecast(live);
  if (changed('sources', [live.time, live.sources, live.supply_gw, live.exports_gw, live.storing_gw, width, G.theme])) renderSources(live);
  if (changed('flows', [live.time, live.links, live.countries, live.storage, width, G.theme])) renderFlows(live);
  renderFeeds(live);
  updateTitleAndIcon(live);
  renderRangeTabs();
}

function renderAll() {
  if (!state.live) return;
  signatures = {};
  renderLive();
  renderKpiSparks();
  renderSourceSparks();
  renderHistory();
}

window.addEventListener('gb:theme', renderAll);
/* Spare capacity, from the same poll as the grid notice banner */
window.addEventListener('gb:alerts', function (e) {
  var m = e.detail && e.detail.margin, strip = byId('margin-strip');
  if (!m) { strip.hidden = true; return; }
  clear(strip);
  strip.appendChild(h('i', { cls: 'lvl ' + m.level }));
  strip.appendChild(h('span', null, ['Spare capacity: ', h('b', { text: G.gw(m.margin) + ' GW' }), ' now, lowest ' + G.gw(m.min) + ' GW at ' + G.hm(m.min_t) + '. ' + m.label + ' over the next 12 hours.']));
  strip.appendChild(h('span', { cls: 'more', text: 'Outages and margin' }));
  strip.hidden = false;
});

document.addEventListener('click', function (event) {
  var button = event.target.closest ? event.target.closest('button.info') : null;
  if (!button) return;
  var open = button.getAttribute('aria-expanded') !== 'true';
  button.setAttribute('aria-expanded', String(open));
  var panel = byId(button.getAttribute('aria-controls'));
  if (panel) panel.hidden = !open;
});

byId('table-btn').addEventListener('click', function (event) {
  state.tableOpen = !state.tableOpen;
  event.currentTarget.setAttribute('aria-expanded', String(state.tableOpen));
  event.currentTarget.textContent = state.tableOpen ? 'Hide the table' : 'Show as a table';
  byId('history-table').hidden = !state.tableOpen;
  if (state.tableOpen) renderTable();
});

var lastWidth = 0, resizeTimer = null;
window.addEventListener('resize', function () {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () {
    var width = document.documentElement.clientWidth;
    if (Math.abs(width - lastWidth) < 8) return;
    lastWidth = width;
    renderAll();
  }, 160);
});

document.addEventListener('visibilitychange', function () {
  if (document.hidden) clearTimeout(state.timer);
  else tick();
});

lastWidth = document.documentElement.clientWidth;
setInterval(renderHeader, 1000);
setInterval(function () { if (state.live) { renderForecast(state.live); renderFeeds(state.live); } }, 60000);
tick();
