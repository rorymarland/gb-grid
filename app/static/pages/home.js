/* Your home: /home. Electricity and gas use from an Octopus smart meter (see gridlive/home.py) */
import G from '/static/js/gb.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num, hm = G.hm, niceStep = G.niceStep, makeTip = G.makeTip;
var state = { data: null, measure: 'kg', hours: 1, fuel: 'elec', pick: {}, drawn: {} };
try {
  var saved = localStorage.getItem('gb-home-measure');
  if (saved === 'kwh' || saved === 'cost' || saved === 'kg') state.measure = saved;
  var savedHours = Number(localStorage.getItem('gb-home-hours'));
  if (savedHours === 1 || savedHours === 6 || savedHours === 24) state.hours = savedHours;
} catch (e) { /* storage unavailable */ }

var dayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short', day: 'numeric', month: 'short', timeZone: G.TZ });
var monthFmt = new Intl.DateTimeFormat('en-GB', { month: 'long', year: 'numeric', timeZone: G.TZ });
var shortDate = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: G.TZ });
var BANDS = [['very low', 'Very low'], ['low', 'Low'], ['moderate', 'Moderate'], ['high', 'High'], ['very high', 'Very high']];

/* ---------- formatting ---------- */

function fixed(v, places) { return v.toLocaleString('en-GB', { minimumFractionDigits: places, maximumFractionDigits: places }); }
function kwh(v) { return !num(v) ? '–' : v < 10 ? fixed(v, 1) : G.int(v); }
function kg(v) { return !num(v) ? '–' : v < 10 ? fixed(v, 1) : G.int(v); }
function pounds(pence) { return !num(pence) ? '–' : '£' + (pence >= 100000 ? G.int(pence / 100) : fixed(pence / 100, 2)); }
function power(w) { return w >= 1000 ? [fixed(w / 1000, w >= 10000 ? 1 : 2), 'kW'] : [G.int(w), 'W']; }
function band(ci, idx) {
  if (idx) return String(idx).toLowerCase();
  if (!num(ci)) return null;
  // The Carbon Intensity API's national bands
  return ci < 40 ? 'very low' : ci < 120 ? 'low' : ci < 200 ? 'moderate' : ci < 280 ? 'high' : 'very high';
}
function bandClass(name) { return name ? 'ci-' + name.replace(' ', '-') : 'hm-unknown'; }
function bandName(name) { var found = BANDS.filter(function (b) { return b[0] === name; })[0]; return found ? found[1].toLowerCase() : ''; }
function figure(dl, label, value, unit, note) {
  dl.appendChild(h('div', null, [
    h('dt', { text: label }),
    h('dd', null, [value, unit ? h('small', { text: unit }) : null, note ? h('span', { cls: 'more', text: note }) : null])
  ]));
}

/* ---------- now ---------- */

function renderNow(d) {
  var fig = clear(byId('now-figure')), text = byId('now-text'), meta = byId('now-meta');
  var live = d.live, ci = d.grid && d.grid.ci;
  var stale = live && d.now - live.t > 900;
  fig.className = 'hm-figure' + (!live || stale ? ' idle' : '');
  if (!d.meters || !d.meters.live) {
    fig.appendChild(h('b', { text: '–' }));
    text.textContent = 'There’s no Octopus Home Mini on your account, so there are no live readings. Your use appears here about a day later, from the meter’s half-hourly readings.';
    meta.textContent = '';
    clear(byId('spark'));
    return;
  }
  if (!live) {
    fig.appendChild(h('b', { text: '–' }));
    text.textContent = 'Waiting for the first reading from your Home Mini.';
    meta.textContent = '';
    clear(byId('spark'));
    return;
  }
  var p = power(live.w);
  fig.appendChild(h('b', { text: p[0] }));
  fig.appendChild(h('span', { text: p[1] + (stale ? ' when last heard from' : ' now') }));
  if (stale) {
    text.textContent = 'Your Home Mini last reported at ' + G.when(live.t, d.now) + '. Check it’s plugged in and on your Wi-Fi.';
  } else if (num(ci)) {
    var grams = live.w / 1000 * ci;
    text.textContent = 'The grid is at ' + G.int(ci) + ' g/kWh' + (band(ci, d.grid.idx) ? ' (' + bandName(band(ci, d.grid.idx)) + ')' : '') +
      ', so that’s about ' + (grams >= 10 ? G.int(grams) : fixed(grams, 1)) + ' g of CO₂ an hour.';
  } else {
    text.textContent = '';
  }
  meta.textContent = 'Reading at ' + hm(live.t) + '. Updates every ' + (d.live_seconds >= 120 ? Math.round(d.live_seconds / 60) + ' minutes' : d.live_seconds === 60 ? 'minute' : d.live_seconds + ' seconds') + ' while this page is open.';
  drawSpark(live, d.now);
}

var RANGE_WORDS = { 1: 'the last hour', 6: 'the last 6 hours', 24: 'the last 24 hours' };

function drawSpark(live, now) {
  var box = clear(byId('spark')), points = live.points || [];
  if (points.length < 2) {
    box.appendChild(h('p', { cls: 'hm-spark-empty', text: 'Readings for ' + RANGE_WORDS[live.hours] + ' build up as they arrive.' }));
    return;
  }
  var span = live.hours * 3600, step = live.step || 10;
  var width = Math.max(240, box.clientWidth || 420), height = 132, padT = 18, padB = 20, padR = 2;
  var end = Math.max(now, points[points.length - 1][0] + step), start = end - span;
  var top = Math.max.apply(null, points.map(function (p) { return p[1]; }));
  var yStep = niceStep(top || 1, 2), hi = Math.ceil((top || 1) / yStep) * yStep;
  var x = function (t) { return (t - start) / span * (width - padR); };
  var y = function (v) { return padT + (1 - v / hi) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img',
    'aria-label': 'Power used over ' + RANGE_WORDS[live.hours] + ', up to ' + power(top).join(' ') });
  [hi, hi / 2, 0].forEach(function (v) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: 0, x2: width, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
  });
  svg.appendChild(s('text', { 'class': 'axis-text', x: 0, y: y(hi) - 5, text: hi >= 1000 ? +(hi / 1000).toFixed(1) + ' kW' : hi + ' W' }));
  /* Clock times along the bottom: every 15 minutes, hour or 6 hours */
  var tick = live.hours === 1 ? 900 : live.hours === 6 ? 3600 : 6 * 3600;
  for (var t = Math.ceil(start / tick) * tick; t < end - tick / 3; t += tick) {
    if (x(t) < 18) continue;
    svg.appendChild(s('line', { 'class': 'grid-line', x1: x(t).toFixed(1), x2: x(t).toFixed(1), y1: y(0), y2: y(0) + 4 }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: x(t).toFixed(1), y: height - 3, 'text-anchor': 'middle', text: hm(t) }));
  }
  svg.appendChild(s('text', { 'class': 'axis-text', x: width, y: height - 3, 'text-anchor': 'end', text: 'Now' }));
  /* Breaks where readings are missing (the Home Mini offline, or the server down) */
  var gap = Math.max(60, step * 3), line = '', area = '', run = [];
  function flush() {
    if (run.length < 2) { run = []; return; }
    var d = run.map(function (p, i) { return (i ? 'L' : 'M') + x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1); }).join('');
    line += d;
    area += d + 'L' + x(run[run.length - 1][0]).toFixed(1) + ' ' + y(0) + 'L' + x(run[0][0]).toFixed(1) + ' ' + y(0) + 'Z';
    run = [];
  }
  points.forEach(function (p) {
    if (p[0] < start) return;
    if (run.length && p[0] - run[run.length - 1][0] > gap) flush();
    run.push(p);
  });
  flush();
  svg.appendChild(s('path', { 'class': 'hm-spark-area', d: area }));
  svg.appendChild(s('path', { 'class': 'hm-spark-line', d: line }));
  var cross = s('line', { 'class': 'xhair', y1: padT, y2: height - padB, visibility: 'hidden' });
  svg.appendChild(cross);
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var rect = svg.getBoundingClientRect();
    var at = start + (e.clientX - rect.left) / rect.width * span, best = null;
    points.forEach(function (p) { if (!best || Math.abs(p[0] - at) < Math.abs(best[0] - at)) best = p; });
    if (!best || Math.abs(best[0] - at) > gap) { tip.hide(); cross.setAttribute('visibility', 'hidden'); return; }
    cross.setAttribute('x1', x(best[0]).toFixed(1));
    cross.setAttribute('x2', x(best[0]).toFixed(1));
    cross.setAttribute('visibility', 'visible');
    var title = step === 10 ? G.when(best[0], now) + ':' + new Date(best[0] * 1000).toISOString().slice(17, 19) : G.when(best[0], now);
    tip.show(title, [{ value: power(best[1]).join(' '), label: step === 10 ? 'being used' : 'average over ' + step / 60 + ' minutes' }],
      x(best[0]) * rect.width / width, 30);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); cross.setAttribute('visibility', 'hidden'); });
}

/* ---------- today ---------- */

function renderToday(d) {
  var today = d.today, total = today.total, slots = today.slots;
  var dl = clear(byId('today-figures'));
  var gridNow = {};
  today.ahead.forEach(function (a) { gridNow[a[0]] = a; });
  var used = 0, weighted = 0, gridSum = 0, gridN = 0;
  slots.forEach(function (sl) {
    if (num(sl[2])) { used += sl[1]; weighted += sl[1] * sl[2]; }
  });
  today.ahead.forEach(function (a) { if (a[0] + 1800 <= d.now) { gridSum += a[1]; gridN += 1; } });
  var liveNote = slots.length ? 'Up to ' + hm(slots[slots.length - 1][0] + 1800 > d.now ? d.now : slots[slots.length - 1][0] + 1800) : null;
  figure(dl, 'Electricity so far', total && num(total.e) ? kwh(total.e) : '–', 'kWh', liveNote);
  figure(dl, 'Its carbon', total && num(total.e_kg) ? (total.e_kg < 1 ? G.int(total.e_kg * 1000) : kg(total.e_kg)) : '–',
    total && num(total.e_kg) && total.e_kg < 1 ? 'g CO₂' : 'kg CO₂');
  figure(dl, 'Its cost', total && num(total.e_cost) ? pounds(total.e_cost) : '–', null,
    total && num(total.e_cost) ? 'With today’s standing charge' : d.tariffs.elec && !d.tariffs.elec.costed ? 'Not costed: more than one rate' : null);
  figure(dl, 'Its intensity', used > 0 ? G.int(weighted / used) : '–', 'g/kWh', gridN ? 'Grid average so far: ' + G.int(gridSum / gridN) : null);
  byId('today-sub').textContent = d.meters && d.meters.live
    ? 'Each half hour’s use, coloured by how clean the grid’s electricity was. The strip along the bottom carries on with the forecast for the rest of the day.'
    : 'Without a Home Mini, today’s use appears tomorrow. The strip shows how clean the grid’s electricity is, half hour by half hour.';
  drawToday(d);
  var legend = clear(byId('today-legend'));
  BANDS.forEach(function (b) {
    legend.appendChild(h('li', null, [h('span', { cls: 'sw ' + bandClass(b[0]) }), b[1]]));
  });
}

function drawToday(d) {
  var box = clear(byId('today-chart')), today = d.today;
  var width = Math.max(300, box.clientWidth || 700), height = 220, padL = 62, padR = 6, padT = 12, padB = 40;
  var periods = today.periods, start = today.start, slotW = (width - padL - padR) / periods;
  var used = {};
  today.slots.forEach(function (sl) { used[sl[0]] = sl; });
  var grid = {};
  today.ahead.forEach(function (a) { grid[a[0]] = a; });
  var top = Math.max.apply(null, today.slots.map(function (sl) { return sl[1]; }).concat([0.2]));
  var step = niceStep(top, 3), hi = Math.ceil(top / step) * step;
  var base = height - padB, stripY = base + 6;
  var y = function (v) { return padT + (1 - v / hi) * (base - padT); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img',
    'aria-label': 'Electricity used in each half hour today, coloured by the grid’s carbon intensity' });
  for (var v = 0; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 8, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: (step < 0.1 ? fixed(v, 2) : step < 1 ? fixed(v, 1) : G.int(v)) + ' kWh' }));
  }
  var gap = slotW > 8 ? 2 : 1;
  for (var i = 0; i < periods; i++) {
    var t = start + i * 1800, x0 = padL + i * slotW;
    var g = grid[t], sl = used[t];
    var name = band(sl && num(sl[2]) ? sl[2] : g && g[1], g && g[2]);
    if (sl && sl[1] > 0) {
      var top0 = y(sl[1]);
      var hgt = Math.max(1.5, base - top0);
      svg.appendChild(s('path', { 'class': 'hm-bar ' + bandClass(name) + (t + 1800 > d.now ? ' current' : ''),
        d: roundedTop(x0 + gap / 2, base - hgt, Math.max(1, slotW - gap), hgt, Math.min(3, (slotW - gap) / 2)) }));
    }
    if (g || sl) {
      svg.appendChild(s('rect', { 'class': 'hm-strip ' + bandClass(name) + (t >= d.now ? ' ahead' : ''), x: (x0 + gap / 2).toFixed(1), y: stripY, width: Math.max(1, slotW - gap).toFixed(1), height: 6, rx: 1.5 }));
    }
  }
  var nowX = padL + (d.now - start) / 1800 * slotW;
  if (nowX > padL && nowX < width - padR) {
    svg.appendChild(s('line', { 'class': 'hm-now-line', x1: nowX.toFixed(1), x2: nowX.toFixed(1), y1: padT, y2: stripY + 6 }));
  }
  var every = slotW * 6 < 46 ? 12 : 6;   // a label every three hours, or six on a phone
  for (var k = 0; k <= periods; k += every) {
    var tx = padL + k * slotW;
    svg.appendChild(s('text', { 'class': 'axis-text', x: tx.toFixed(1), y: height - 6, 'text-anchor': k === 0 ? 'start' : k === periods ? 'end' : 'middle', text: k === periods ? '24:00' : hm(start + k * 1800) }));
  }
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var rect = svg.getBoundingClientRect();
    var px = (e.clientX - rect.left) * width / rect.width;
    var idx = Math.floor((px - padL) / slotW);
    if (idx < 0 || idx >= periods) { tip.hide(); return; }
    var t0 = start + idx * 1800, slot = used[t0], gr = grid[t0];
    var ci = slot && num(slot[2]) ? slot[2] : gr && gr[1];
    var rows = [];
    if (slot) rows.push({ value: fixed(slot[1], 2) + ' kWh', label: t0 + 1800 > d.now ? 'used so far' : 'used' });
    if (num(ci)) rows.push({ cls: bandClass(band(ci, gr && gr[2])), value: G.int(ci) + ' g/kWh', label: t0 >= d.now ? 'grid forecast' : 'grid' });
    if (slot && num(ci)) rows.push({ value: G.int(slot[1] * ci) + ' g', label: 'CO₂' });
    if (!rows.length) { tip.hide(); return; }
    tip.show(hm(t0) + ' to ' + hm(t0 + 1800), rows, (padL + (idx + 0.5) * slotW) * rect.width / width, 40);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); });
}

function roundedTop(x, y, w, hgt, r) {
  r = Math.max(0, Math.min(r, hgt, w / 2));
  return 'M' + x.toFixed(1) + ' ' + (y + hgt).toFixed(1) + 'V' + (y + r).toFixed(1) +
    'Q' + x.toFixed(1) + ' ' + y.toFixed(1) + ' ' + (x + r).toFixed(1) + ' ' + y.toFixed(1) +
    'H' + (x + w - r).toFixed(1) + 'Q' + (x + w).toFixed(1) + ' ' + y.toFixed(1) + ' ' + (x + w).toFixed(1) + ' ' + (y + r).toFixed(1) +
    'V' + (y + hgt).toFixed(1) + 'Z';
}

/* ---------- the last 30 days ---------- */

var MEASURES = {
  kg: { e: 'e_kg', g: 'g_kg', unit: 'kg CO₂', fmt: kg, label: 'Carbon' },
  kwh: { e: 'e', g: 'g', unit: 'kWh', fmt: kwh, label: 'Energy' },
  cost: { e: 'e_cost', g: 'g_cost', unit: '£', fmt: function (v) { return pounds(v); }, label: 'Cost' }
};

function renderDays(d) {
  var days = d.days, past = days.slice(0, -1);
  var sum = function (key) {
    var values = past.map(function (x) { return x[key]; }).filter(num);
    return values.length ? values.reduce(function (a, b) { return a + b; }, 0) : null;
  };
  var gasDays = past.filter(function (x) { return num(x.g); }).length;
  var elecDays = past.filter(function (x) { return num(x.e); }).length;
  var dl = clear(byId('days-figures'));
  var eKg = sum('e_kg'), gKg = sum('g_kg');
  figure(dl, 'Electricity', kwh(sum('e')), 'kWh', elecDays < 30 && elecDays ? plural(elecDays, 'day') : null);
  if (d.meters && d.meters.gas) figure(dl, 'Gas', kwh(sum('g')), 'kWh', gasDays < 30 && gasDays ? plural(gasDays, 'day') + ' so far' : null);
  figure(dl, 'Carbon', kg((eKg || 0) + (gKg || 0)), 'kg CO₂', num(gKg) && num(eKg) ? Math.round(gKg / (eKg + gKg) * 100) + '% from gas' : null);
  var eCost = sum('e_cost'), gCost = sum('g_cost');
  figure(dl, 'Cost', num(eCost) || num(gCost) ? pounds((eCost || 0) + (gCost || 0)) : '–', null, 'With standing charges');
  drawDays(d);
  var legend = clear(byId('days-legend'));
  legend.appendChild(h('li', null, [h('span', { cls: 'sw hm-elec' }), 'Electricity']));
  if (d.meters && d.meters.gas) legend.appendChild(h('li', null, [h('span', { cls: 'sw hm-gas' }), 'Gas']));
  var late = 0;
  for (var i = past.length - 1; i >= 0 && d.meters && d.meters.gas && !num(past[i].g); i--) late++;
  var notes = [];
  notes.push('The 30 days before today. Today is the lighter bar on the right, so far.');
  if (late && late < past.length) notes.push('Gas for the last ' + (late === 1 ? 'day' : late + ' days') + ' isn’t in yet: gas meters report a day or two behind.');
  byId('days-note').textContent = notes.join(' ');
}

function plural(n, one) { return n + ' ' + one + (n === 1 ? '' : 's'); }

function drawDays(d) {
  var box = clear(byId('days-chart')), m = MEASURES[state.measure], days = d.days;
  var width = Math.max(300, box.clientWidth || 700), height = 230, padL = 62, padR = 6, padT = 12, padB = 26;
  var n = days.length, slotW = (width - padL - padR) / n;
  var totals = days.map(function (x) { return (num(x[m.e]) ? x[m.e] : 0) + (num(x[m.g]) ? x[m.g] : 0); });
  var top = Math.max.apply(null, totals.concat([state.measure === 'cost' ? 100 : 0.5]));
  var step = niceStep(top, 3), hi = Math.ceil(top / step) * step;
  var base = height - padB;
  var y = function (v) { return padT + (1 - v / hi) * (base - padT); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img',
    'aria-label': m.label + ' each day for the last 30 days, electricity and gas' });
  for (var v = 0; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    var label = state.measure === 'cost' ? '£' + (step < 100 ? fixed(v / 100, 2) : G.int(v / 100))
      : (step < 1 ? fixed(v, 1) : G.int(v)) + (state.measure === 'kg' ? ' kg' : ' kWh');
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 8, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end', text: label }));
  }
  var gap = Math.min(4, slotW * 0.25), barW = Math.max(2, slotW - gap);
  days.forEach(function (x, i) {
    var x0 = padL + i * slotW + gap / 2, e = num(x[m.e]) ? x[m.e] : 0, g = num(x[m.g]) ? x[m.g] : 0;
    var today = i === n - 1;
    if (e > 0) {
      var eh = base - y(e);
      svg.appendChild(s('path', { 'class': 'hm-day hm-elec' + (today ? ' current' : ''), d: g > 0 ? rect(x0, base - eh, barW, eh) : roundedTop(x0, base - eh, barW, eh, Math.min(3, barW / 2)) }));
    }
    if (g > 0) {
      var gh = base - y(g), gy = y(e) - gh;
      // A 2px gap separates the two
      svg.appendChild(s('path', { 'class': 'hm-day hm-gas' + (today ? ' current' : ''), d: roundedTop(x0, gy, barW, Math.max(1, gh - (e > 0 ? 2 : 0)), Math.min(3, barW / 2)) }));
    }
  });
  var every = slotW < 16 ? 7 : 5;
  days.forEach(function (x, i) {
    if ((n - 1 - i) % every) return;
    svg.appendChild(s('text', { 'class': 'axis-text', x: (padL + (i + 0.5) * slotW).toFixed(1), y: height - 6, 'text-anchor': 'middle', text: i === n - 1 ? 'Today' : shortDate.format(new Date((x.t + 43200) * 1000)) }));
  });
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (ev) {
    var r = svg.getBoundingClientRect();
    var idx = Math.floor(((ev.clientX - r.left) * width / r.width - padL) / slotW);
    if (idx < 0 || idx >= n) { tip.hide(); return; }
    var x = days[idx], rows = [];
    if (num(x[m.e])) rows.push({ cls: 'hm-elec', value: m.fmt(x[m.e]) + (state.measure === 'cost' ? '' : ' ' + m.unit), label: 'electricity' });
    if (d.meters && d.meters.gas) rows.push({ cls: 'hm-gas', value: num(x[m.g]) ? m.fmt(x[m.g]) + (state.measure === 'cost' ? '' : ' ' + m.unit) : 'Not in yet', label: 'gas' });
    if (!rows.length) { tip.hide(); return; }
    var title = dayFmt.format(new Date((x.t + 43200) * 1000)) + (idx === n - 1 ? ', so far' : '');
    tip.show(title, rows, (padL + (idx + 0.5) * slotW) * r.width / width, 40, state.measure === 'cost' ? 'Each fuel includes its standing charge.' : null);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); });
}

function rect(x, y, w, hgt) { return 'M' + x.toFixed(1) + ' ' + y.toFixed(1) + 'h' + w.toFixed(1) + 'v' + hgt.toFixed(1) + 'h' + (-w).toFixed(1) + 'Z'; }

/* ---------- timing ---------- */

function renderTiming(d) {
  var panel = byId('timing-panel'), t = d.timing;
  panel.hidden = !t;
  if (!t) return;
  var diff = (t.yours - t.flat) / t.flat;
  var pct = Math.abs(Math.round(diff * 100));
  var saved = (t.flat - t.yours) * t.kwh / 1000;
  byId('timing-sub').textContent = pct < 2
    ? 'Over the last 30 days, when you used electricity made little difference to how clean it was.'
    : diff < 0
      ? 'Over the last 30 days, your electricity was ' + pct + '% cleaner than steady use around the clock would have been: you use more of it when the grid is cleaner.'
      : 'Over the last 30 days, your electricity was ' + pct + '% more carbon-intensive than steady use around the clock would have been: you use more of it when the grid is dirtier, usually in the evening.';
  var box = clear(byId('timing'));
  var hi = Math.max(t.yours, t.flat);
  [['Your electricity', t.yours, 'yours'], ['Steady use at the same times', t.flat, 'flat']].forEach(function (row) {
    box.appendChild(h('div', { cls: 'hm-compare-row ' + row[2] }, [
      h('span', { cls: 'label', text: row[0] }),
      h('span', { cls: 'track' }, h('i', { style: 'width:' + (row[1] / hi * 100).toFixed(1) + '%' })),
      h('span', { cls: 'value', text: G.int(row[1]) + ' g/kWh' })
    ]));
  });
  var elec = d.tariffs.elec, notes = [];
  if (pct >= 2) notes.push((saved > 0 ? 'That saved about ' : 'That added about ') + kg(Math.abs(saved)) + ' kg of CO₂ on the ' + G.int(t.kwh) + ' kWh you used.');
  if (elec && elec.flat) notes.push('Your electricity costs the same at any time of day, so timing changes its carbon but not your bill.');
  else if (elec && elec.costed) notes.push('Your tariff’s price changes through the day, so timing changes your bill as well.');
  notes.push('The Overview lists the cleanest times to use electricity over the next day.');
  var note = clear(byId('timing-note'));
  note.appendChild(document.createTextNode(notes.slice(0, -1).join(' ') + (notes.length > 1 ? ' ' : '')));
  note.appendChild(h('a', { href: '/', text: 'The Overview' }));
  note.appendChild(document.createTextNode(' lists the cleanest times to use electricity over the next day.'));
}

/* ---------- months ---------- */

function renderMonths(d) {
  var panel = byId('months-panel');
  panel.hidden = !d.months.length;
  if (!d.months.length) return;
  var body = clear(byId('months')), gas = d.meters && d.meters.gas;
  var current = d.today.d.slice(0, 7);
  d.months.slice().reverse().forEach(function (m) {
    var first = d.since >= m.t && d.since < m.t + m.length * 86400;
    var partial = m.d.slice(0, 7) === current ? 'so far' : first && m.e_days < m.length ? 'from ' + shortDate.format(new Date(d.since * 1000)) : null;
    var carbon = num(m.e_kg) || num(m.g_kg) ? (m.e_kg || 0) + (m.g_kg || 0) : null;
    body.appendChild(h('tr', null, [
      h('th', { scope: 'row' }, [monthFmt.format(new Date((m.t + 43200) * 1000)), partial ? h('small', { text: partial }) : null]),
      h('td', { cls: 'num', text: num(m.e) ? G.int(m.e) : '–' }),
      h('td', { cls: 'num', text: !gas ? '–' : num(m.g) ? G.int(m.g) + (m.g_days < m.e_days ? '*' : '') : '–' }),
      h('td', { cls: 'num', text: num(carbon) ? G.int(carbon) : '–' }),
      h('td', { cls: 'num', text: num(m.cost) ? pounds(m.cost) : '–' })
    ]));
  });
  var foot = clear(byId('months-total')), y = d.year;
  if (y) {
    var carbon = (y.e_kg || 0) + (y.g_kg || 0);
    foot.appendChild(h('tr', null, [
      h('th', { scope: 'row' }, ['Last 365 days', y.e_days < 365 ? h('small', { text: 'from ' + shortDate.format(new Date(d.since * 1000)) }) : null]),
      h('td', { cls: 'num', text: num(y.e) ? G.int(y.e) : '–' }),
      h('td', { cls: 'num', text: gas && num(y.g) ? G.int(y.g) : '–' }),
      h('td', { cls: 'num', text: G.int(carbon) }),
      h('td', { cls: 'num', text: num(y.cost) ? pounds(y.cost) : '–' })
    ]));
  }
  var starred = d.months.some(function (m) { return num(m.g) && m.g_days < m.e_days; });
  var wrap = panel.querySelector('.hm-table-wrap');
  var old = panel.querySelector('.hm-note');
  if (old) old.remove();
  if (starred) wrap.after(h('p', { cls: 'hm-note', text: '* Gas for the latest days isn’t in yet.' }));
}

/* ---------- other tariffs ---------- */

var longDate = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: G.TZ });
var monthShort = new Intl.DateTimeFormat('en-GB', { month: 'short', timeZone: G.TZ });
function isoDay(text) { return new Date(text + 'T12:00:00Z'); }
function poundsWhole(p) { return '£' + G.int(Math.abs(p) / 100); }

function renderCompare(d) {
  var panel = byId('compare-panel'), c = d.compare;
  panel.hidden = !c || !c.fuels || !c.fuels.elec;
  if (panel.hidden) return;
  var hasGas = Boolean(c.fuels.gas);
  byId('compare-fuel').hidden = !hasGas;
  if (!hasGas) state.fuel = 'elec';
  Array.prototype.forEach.call(byId('compare-fuel').querySelectorAll('button'), function (b) {
    b.setAttribute('aria-pressed', String(b.dataset.fuel === state.fuel));
  });
  var f = c.fuels[state.fuel], fuelWord = state.fuel === 'elec' ? 'electricity' : 'gas';
  var from = isoDay(c.from), to = isoDay(c.to);
  byId('compare-sub').textContent = 'Your ' + fuelWord + ' from ' + longDate.format(from) + ' to ' + longDate.format(to) +
    (f.days < 360 ? ' (' + f.days + ' days with readings)' : '') + ', ' + G.int(f.kwh) + ' kWh, priced half hour by half hour on each tariff, with its standing charges.';
  var options = f.options.filter(function (o) { return num(o.cost); });
  var yours = options.filter(function (o) { return o.key === 'yours'; })[0];
  var others = options.filter(function (o) { return o.key !== 'yours'; }).sort(function (a, b) { return a.cost - b.cost; });
  if (!state.pick[state.fuel] || !others.some(function (o) { return o.key === state.pick[state.fuel]; })) {
    state.pick[state.fuel] = others.length ? others[0].key : null;
  }
  var list = clear(byId('compare-list'));
  var top = Math.max.apply(null, options.map(function (o) { return o.cost; }));
  (yours ? [yours] : []).concat(others).forEach(function (o) {
    var mine = o.key === 'yours';
    var diff = mine ? 'What you paid' : !num(o.diff) ? '' : Math.abs(o.diff) < 50 ? 'About the same' : poundsWhole(o.diff) + (o.diff < 0 ? ' less' : ' more');
    var row = h(mine ? 'div' : 'button', { cls: 'hm-tariff' + (mine ? ' mine' : '') + (o.diff < -50 ? ' cheaper' : ''), type: mine ? null : 'button',
      'aria-pressed': mine ? null : String(o.key === state.pick[state.fuel]), 'data-key': mine ? null : o.key }, [
      h('span', { cls: 'nm' }, [o.name, h('small', { text: o.note })]),
      h('span', { cls: 'track' }, h('i', { style: 'width:' + (o.cost / top * 100).toFixed(1) + '%' })),
      h('span', { cls: 'cost' }, [poundsWhole(o.cost), h('small', { text: fixed(o.unit, 1) + 'p/kWh average' })]),
      h('span', { cls: 'diff', text: diff })
    ]);
    list.appendChild(row);
  });
  var pick = others.filter(function (o) { return o.key === state.pick[state.fuel]; })[0];
  byId('compare-month-title').textContent = pick && yours ? pick.name.replace(/, at today’s prices$/, ' at today’s prices') + ' against yours, month by month' : '';
  drawCompareMonths(f, yours, pick);
  var notes = [];
  if (state.fuel === 'elec') {
    notes.push('Agile and Go are priced as if you’d used electricity at exactly the same times. On those tariffs most people move some use to the cheap half hours, which would save more than this shows; Agile’s dearest half hours (usually 16:00 to 19:00) can also cost more than this when you can’t.');
    notes.push('Octopus Go is for people with an electric car.');
  }
  notes.push('Today’s fixed price is a guide to renewing: fixed prices change often, and this applies today’s to the whole of your last year.');
  if (state.fuel === 'elec') notes.push('Tracker isn’t open to new customers, and Cosy and Snug need a heat pump or storage heaters, so they aren’t shown.');
  byId('compare-note').textContent = notes.join(' ');
}

function drawCompareMonths(f, yours, pick) {
  var box = clear(byId('compare-chart'));
  if (!yours || !pick || !yours.months || !pick.months) return;
  var diffs = pick.months.map(function (v, i) { return v - yours.months[i]; });
  var width = Math.max(300, box.clientWidth || 700), height = 200, padL = 62, padR = 6, padT = 14, padB = 26;
  var n = diffs.length, slotW = (width - padL - padR) / n;
  var lo = Math.min(0, Math.min.apply(null, diffs)), hi = Math.max(0, Math.max.apply(null, diffs));
  var step = niceStep(Math.max(hi - lo, 100), 4);
  lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
  if (hi === lo) hi = lo + step;
  var y = function (v) { return padT + (hi - v) / (hi - lo) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img',
    'aria-label': pick.name + ' against your tariffs, each month: bars below the line would have saved money' });
  for (var v = lo; v <= hi + 1e-9; v += step) {
    svg.appendChild(s('line', { 'class': v === 0 ? 'hm-zero' : 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 8, y: (y(v) + 4).toFixed(1), 'text-anchor': 'end',
      text: v === 0 ? '£0' : (v < 0 ? '−£' : '+£') + (step < 100 ? fixed(Math.abs(v) / 100, 2) : G.int(Math.abs(v) / 100)) }));
  }
  var gap = Math.min(10, slotW * 0.3), barW = Math.max(3, slotW - gap);
  diffs.forEach(function (dv, i) {
    var x0 = padL + i * slotW + gap / 2, top = y(Math.max(0, dv)), bottom = y(Math.min(0, dv));
    svg.appendChild(s('rect', { 'class': 'hm-diff ' + (dv < 0 ? 'less' : 'more'), x: x0.toFixed(1), y: top.toFixed(1),
      width: barW.toFixed(1), height: Math.max(1, bottom - top).toFixed(1), rx: 2 }));
    if (slotW >= 34 || (n - 1 - i) % 2 === 0) {   // every other month on a phone
      svg.appendChild(s('text', { 'class': 'axis-text', x: (x0 + barW / 2).toFixed(1), y: height - 6, 'text-anchor': 'middle',
        text: monthShort.format(isoDay(f.months[i] + '-15')) }));
    }
  });
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var r = svg.getBoundingClientRect(), i = Math.floor(((e.clientX - r.left) * width / r.width - padL) / slotW);
    if (i < 0 || i >= n) { tip.hide(); return; }
    var dv = diffs[i];
    tip.show(monthFmt.format(isoDay(f.months[i] + '-15')), [
      { value: pounds(yours.months[i]), label: 'your tariffs' },
      { value: pounds(pick.months[i]), label: pick.name.replace(/, at today’s prices$/, '') },
      { value: (dv < 0 ? '−' : '+') + pounds(Math.abs(dv)), label: dv < 0 ? 'less' : 'more' }
    ], (padL + (i + 0.5) * slotW) * r.width / width, 40);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); });
}

/* ---------- Saving Sessions ---------- */

function points(n) { return G.int(n) + ' points'; }
function sessionWhen(sv, now) {
  return G.when(sv.start, now).replace('Tomorrow', 'tomorrow') + ' to ' + hm(sv.end);
}

function renderSessions(d) {
  var panel = byId('sessions'), sv = d.sessions;
  var any = sv && (num(sv.points) || sv.joined_campaign || sv.upcoming.length || sv.active || sv.past.length);
  panel.hidden = !any;
  var alert = byId('session-alert');
  alert.hidden = true;
  if (!any) return;
  var dl = clear(byId('sessions-figures'));
  if (num(sv.points)) figure(dl, 'Octoplus points', G.int(sv.points), null, 'Worth about ' + pounds(sv.pounds * 100) + ' (800 to £1)');
  var season = sv.past.filter(function (p) { return p.joined; });
  var given = season.reduce(function (a, p) { return a + (num(p.given) ? p.given : 0); }, 0);
  if (season.length) figure(dl, 'Sessions joined lately', String(season.length), null, given ? points(given) + ' earned' : null);
  byId('sessions-sub').textContent = sv.joined_campaign === false
    ? (sv.can_join ? 'You haven’t joined Saving Sessions this season. Join in the Octopus app to be paid in Octoplus points for using less electricity when the grid is stretched.' : 'Your account isn’t in Saving Sessions.')
    : 'When the grid is stretched, Octopus pays Octoplus points for every kWh you use below your usual amount.';
  var next = clear(byId('sessions-next'));
  var live = sv.active ? [sv.active] : [];
  live.concat(sv.upcoming).forEach(function (e) {
    var active = e === sv.active;
    var lines = [];
    if (e.kind === 'down') {
      if (num(e.baseline)) lines.push('Your baseline is ' + (e.baseline_complete ? '' : 'about ') + fixed(e.baseline, 2) + ' kWh: use less than that to earn points.' +
        (e.baseline_complete ? '' : ' It settles once the days before the session are in.'));
      if (active && num(e.used) && num(e.baseline)) {
        var elapsed = Math.min(1, Math.max(0, (d.now - e.start) / (e.end - e.start)));
        lines.push('So far: ' + fixed(e.used, 2) + ' kWh, against about ' + fixed(e.baseline * elapsed, 2) + ' kWh by this point.');
      }
    } else {
      lines.push('A power-up: points for every kWh you use above your usual amount' +
        (num(e.baseline) ? ' (' + (e.baseline_complete ? '' : 'about ') + fixed(e.baseline, 2) + ' kWh)' : '') + ', so it’s a good time to run the washing machine or charge things.');
    }
    if (!e.joined && e.kind === 'down') lines.push('You haven’t joined this one yet: join in the Octopus app.');
    next.appendChild(h('div', { cls: 'hm-session' + (active ? ' active' : '') }, [
      h('p', { cls: 'when' }, [h('b', { text: (active ? 'On now, until ' + hm(e.end) : G.when(e.start, d.now) + ' to ' + hm(e.end)) }),
        e.reward ? h('span', { text: G.int(e.reward) + ' points per kWh saved' }) : null]),
      h('p', { cls: 'what', text: lines.join(' ') })
    ]));
  });
  var first = live.concat(sv.upcoming)[0];
  if (first && first.kind === 'down' && first.start - d.now < 36 * 3600) {
    alert.hidden = false;
    clear(alert);
    alert.appendChild(h('a', { href: '#sessions', text: first === sv.active ? 'Saving Session on now, until ' + hm(first.end) : 'Saving Session ' + sessionWhen(first, d.now) }));
    if (num(first.baseline)) alert.appendChild(document.createTextNode(': aim for under ' + fixed(first.baseline, 2) + ' kWh.'));
  }
  var past = clear(byId('sessions-past'));
  byId('sessions-past-wrap').hidden = !sv.past.length;
  sv.past.forEach(function (e) {
    var pts = num(e.given) ? points(e.given) : e.joined && num(e.estimate) ? 'About ' + points(e.estimate) : e.joined ? '–' : 'Not joined';
    past.appendChild(h('tr', { cls: e.joined ? '' : 'off' }, [
      h('th', { scope: 'row' }, [dayFmt.format(new Date(e.start * 1000)), h('small', { text: hm(e.start) + ' to ' + hm(e.end) + (e.kind === 'up' ? ', power-up' : '') })]),
      h('td', { cls: 'num', text: num(e.baseline) ? fixed(e.baseline, 2) : '–' }),
      h('td', { cls: 'num', text: num(e.used) ? fixed(e.used, 2) : '–' }),
      h('td', { cls: 'num', text: num(e.saved) ? (e.kind === 'up' ? '+' : '') + fixed(e.saved, 2) : '–' }),
      h('td', { cls: 'num', text: pts })
    ]));
  });
  byId('sessions-note').textContent = 'Baselines follow Octopus’s method: your use in the same half hours, averaged over the previous 10 weekdays, leaving out session days and bank holidays (England and Wales); weekend and bank holiday sessions use the previous 4 weekend days. Each half hour counts on its own, so a half hour below its baseline earns points even if the session as a whole didn’t. For power-ups, the figure in the Saved column is how much more than usual you used. Points marked “about” are this page’s estimate until Octopus confirms them, usually a few days later.';
}

/* ---------- the page ---------- */

function render(d) {
  state.data = d;
  byId('setup').hidden = d.enabled;
  if (!d.enabled) {
    byId('content').hidden = true;
    byId('waiting').hidden = true;
    G.setLive('', 'Not connected to Octopus');
    return;
  }
  var any = d.since || d.live;
  byId('content').hidden = !any;
  var waiting = byId('waiting');
  waiting.hidden = !!any;
  if (!any) {
    waiting.textContent = d.problem ? 'Octopus isn’t answering yet: ' + d.problem : 'Fetching your readings from Octopus. The first ones take a minute or so.';
    G.setLive(d.problem ? 'warn' : '', d.problem ? 'Waiting for Octopus' : 'Fetching your readings');
    return;
  }
  byId('gas-factor').textContent = String(d.gas_factor);
  renderNow(d);
  renderToday(d);
  renderDays(d);
  renderTiming(d);
  renderCompare(d);
  renderSessions(d);
  renderMonths(d);
  var live = d.live && d.now - d.live.t < 900;
  G.setLive(d.problem ? 'warn' : live ? 'good' : '', d.problem ? 'Octopus: ' + d.problem.slice(0, 90)
    : live ? 'Live. Home Mini reading at ' + hm(d.live.t) + '.' : 'Half-hourly readings from Octopus.');
}

function setMeasure(measure) {
  state.measure = measure;
  try { localStorage.setItem('gb-home-measure', measure); } catch (e) { /* storage unavailable */ }
  Array.prototype.forEach.call(byId('days-measure').querySelectorAll('button'), function (b) {
    b.setAttribute('aria-pressed', String(b.dataset.measure === measure));
  });
  if (state.data && state.data.enabled) drawDays(state.data);
}

byId('compare-fuel').addEventListener('click', function (e) {
  var button = e.target.closest('button[data-fuel]');
  if (!button) return;
  state.fuel = button.dataset.fuel;
  if (state.data) renderCompare(state.data);
});
byId('compare-list').addEventListener('click', function (e) {
  var button = e.target.closest('button[data-key]');
  if (!button) return;
  state.pick[state.fuel] = button.dataset.key;
  if (state.data) renderCompare(state.data);
});

byId('days-measure').addEventListener('click', function (e) {
  var button = e.target.closest('button[data-measure]');
  if (button) setMeasure(button.dataset.measure);
});
setMeasure(state.measure);

var poller = G.poll('/api/home?hours=' + state.hours, {
  interval: function () { return 15; },
  onData: render,
  onTick: function (p) { if (G.offline(p)) G.setLive('bad', 'Can’t reach the dashboard server'); }
});

function setHours(hours) {
  state.hours = hours;
  try { localStorage.setItem('gb-home-hours', String(hours)); } catch (e) { /* storage unavailable */ }
  Array.prototype.forEach.call(byId('live-range').querySelectorAll('button'), function (b) {
    b.setAttribute('aria-pressed', String(Number(b.dataset.hours) === hours));
  });
}
setHours(state.hours);
byId('live-range').addEventListener('click', function (e) {
  var button = e.target.closest('button[data-hours]');
  if (!button || Number(button.dataset.hours) === state.hours) return;
  setHours(Number(button.dataset.hours));
  poller.setUrl('/api/home?hours=' + state.hours);
});

var resizeTimer = null;
window.addEventListener('resize', function () {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () { if (state.data && state.data.enabled && (state.data.since || state.data.live)) render(state.data); }, 150);
});
