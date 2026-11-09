import G from '/static/js/gb.js';
import { simulate, estimateInertia, LIMITS } from '/static/js/gridsim.js';

var h = G.h, s = G.s, byId = G.byId, clear = G.clear, num = G.num;
var SYNC = { nuclear: 1, gas: 1, biomass: 1, hydro: 1, coal: 1, other: 1, storage: 0.5 };
var NAMES = { nuclear: 'nuclear', gas: 'gas', biomass: 'biomass', hydro: 'hydro', coal: 'coal', other: 'other', wind: 'wind farm', solar: 'solar farm', storage: 'storage', imports: 'interconnector' };
var PARTS = [
  ['damping', 'Demand easing off', '--muted'], ['dc', 'Dynamic Containment', '--down'], ['dm', 'Dynamic Moderation', '--c-storage'],
  ['dr', 'Dynamic Regulation', '--c-wind'], ['gov', 'Generators’ governors', '--c-gas'], ['reserve', 'Reserve', '--c-nuclear'],
  ['shed', 'Demand disconnected', '--bad']
];
var PRESETS = {
  windy: {
    name: 'Windy night', note: 'Low demand and lots of wind, so few spinning machines and little inertia. The largest interconnector trips while importing.',
    demand: 22000, inertia: 110, trip: { name: 'North Sea Link', mw: 1400, sync: false },
    held: { DCL: 1400, DML: 500, DRL: 500, PQR: 500 }, governors: 200
  },
  winter: {
    name: 'Still winter evening', note: 'High demand, little wind, plenty of gas and nuclear spinning. Sizewell B’s reactor trips, taking both turbines.',
    demand: 42000, inertia: 240, trip: { name: 'Sizewell B', mw: 1230, sync: true },
    held: { DCL: 1400, DML: 500, DRL: 500, PQR: 500 }, governors: 500
  },
  double: {
    name: 'A double trip, like 9 August 2019',
    note: 'An offshore wind farm and a gas plant’s steam turbine trip together, small generators disconnect on old protection settings, and two more gas turbines follow within 80 seconds. Before Dynamic Containment; figures are illustrative, not the official reconstruction.',
    demand: 29000, inertia: 210, trip: { name: 'An offshore wind farm and a steam turbine', mw: 980, sync: false },
    held: {}, governors: 1000, old: true, noReserve: true, seconds: 120,
    later: [{ t: 57, mw: 210, sync: true, label: 'A gas turbine at the same plant tripped' }, { t: 80, mw: 190, sync: true, label: 'Its second gas turbine tripped' }]
  }
};

var state = { start: 'now', preset: 'winter', map: null, api: null, replay: null, at: null, base: null, p: null, result: null, anim: 0, drawn: 1 };

/* ---- starting points ------------------------------------------------------ */

function syncFrom(cats) {
  var gw = 0;
  Object.keys(SYNC).forEach(function (k) { gw += SYNC[k] * Math.max(0, cats[k] || 0) / 1000; });
  return gw;
}

function candidatesFromStations(list) {
  var out = [];
  list.forEach(function (st) {
    if (!(st.mw > 50)) return;
    var unit = st.c === 'wind' || st.c === 'solar' ? st.mw : st.mw / Math.max(1, st.on || Math.ceil(st.mw / Math.max(1, (st.cap || st.mw) / Math.max(1, st.u || 1)) - 0.05));
    if (unit < 100) return;
    var label = st.c === 'wind' ? st.n + ' (whole wind farm)' : st.c === 'nuclear' && (st.u || 1) > 1 ? st.n + ' (one reactor unit)' : (st.u || 1) > 1 ? st.n + ' (one unit)' : st.n;
    out.push({ name: st.n, label: label, mw: Math.round(unit), sync: !!SYNC[st.c] && st.c !== 'storage' });
  });
  return out;
}

function baseNow() {
  var m = state.map, cats = {};
  m.categories.forEach(function (c) { cats[c.key] = (c.measured || 0) + (c.embedded || 0); });
  var trips = candidatesFromStations(m.stations);
  (m.links || []).forEach(function (l) { if (l.mw > 300) trips.push({ name: l.name, label: l.name + ' (importing)', mw: Math.round(l.mw), sync: false }); });
  return finishBase({ t: m.time, demand: m.demand_gw * 1000, cats: cats, trips: trips, label: 'The grid at ' + G.hm(m.time) });
}

function baseAt(i) {
  var r = state.replay, m = state.map, tot = r.totals;
  function v(k) { return tot[k] ? tot[k][i] || 0 : 0; }
  var cats = { nuclear: v('nuclear'), gas: v('gas'), biomass: v('biomass'), hydro: v('hydro'), storage: v('storage'), other: v('other'),
    wind: v('wind') + v('wind_embedded'), solar: v('solar'), imports: v('imports') };
  var stations = m.stations.map(function (st) {
    var series = r.stations[st.id];
    return { n: st.n, c: st.c, cap: st.cap, u: st.u, mw: series ? series[i] : null };
  }).filter(function (st) { return num(st.mw); });
  var trips = candidatesFromStations(stations);
  (m.links || []).forEach(function (l) {
    var series = r.links[l.code], mw = series ? series[i] : null;
    if (mw > 300) trips.push({ name: l.name, label: l.name + ' (importing)', mw: Math.round(mw), sync: false });
  });
  return finishBase({ t: r.t[i], demand: v('demand'), cats: cats, trips: trips, label: 'The grid at ' + G.when(r.t[i]) });
}

function finishBase(b) {
  b.trips.sort(function (a, c) { return c.mw - a.mw; });
  b.trips = b.trips.slice(0, 12);
  b.sync = syncFrom(b.cats);
  b.inertia = Math.round(estimateInertia(b.sync, state.api && state.api.fit));
  return b;
}

function basePreset() {
  var p = PRESETS[state.preset];
  return { t: null, demand: p.demand, sync: null, inertia: p.inertia, trips: [Object.assign({ label: p.trip.name }, p.trip)], label: p.name, preset: p };
}

function params(base) {
  var p = base.preset, held = p ? p.held : (state.api && state.api.held) || {};
  var trip = base.trips[0] || { name: 'A 1,000 MW unit', label: 'A 1,000 MW unit', mw: 1000, sync: true };
  return {
    trip: 0, loss: trip.mw, lossSync: trip.sync, inertia: base.inertia, demand: Math.round(base.demand || 28000),
    DCL: held.DCL || 0, DML: held.DML || 0, DRL: held.DRL || 0, PQR: p && p.noReserve ? 0 : held.PQR || 0,
    governors: p ? p.governors : 300, old: !!(p && p.old)
  };
}

function setBase(base) {
  state.base = base;
  state.p = params(base);
  renderStart();
  renderKnobs();
  run(false);
}

/* ---- the start panel ------------------------------------------------------- */

function renderStart() {
  var extra = clear(byId('start-extra')), b = state.base;
  if (state.start === 'past') {
    var r = state.replay;
    if (!r || r.t.length < 2) { extra.appendChild(h('p', { cls: 'empty', text: 'The time machine has no history yet. It builds up as the dashboard runs.' })); }
    else {
      var i = state.at === null ? r.t.length - 1 : state.at;
      var range = h('input', { type: 'range', min: '0', max: String(r.t.length - 1), step: '1', value: String(i), id: 'at', 'aria-label': 'Moment in the last week' });
      range.addEventListener('input', function () { state.at = +range.value; setBase(baseAt(state.at)); });
      extra.appendChild(h('label', { cls: 'at' }, [h('span', { text: G.when(r.t[i]) }), range]));
    }
  } else if (state.start === 'preset') {
    var list = h('div', { cls: 'presets', role: 'group', 'aria-label': 'Presets' });
    Object.keys(PRESETS).forEach(function (k) {
      var b2 = h('button', { type: 'button', cls: 'preset', 'aria-pressed': String(k === state.preset) }, [h('b', { text: PRESETS[k].name }), h('span', { text: PRESETS[k].note })]);
      b2.addEventListener('click', function () { state.preset = k; setBase(basePreset()); });
      list.appendChild(b2);
    });
    extra.appendChild(list);
  }
  var figs = clear(byId('start-figs'));
  function fig(label, value, note) { figs.appendChild(h('div', null, [h('dt', { text: label }), h('dd', null, [h('b', { text: value }), note ? h('small', { text: note }) : null])])); }
  fig('Demand', G.gw(b.demand) + ' GW', b.label);
  if (num(b.sync)) fig('Spinning generation', b.sync.toFixed(1) + ' GW', 'Nuclear, gas, biomass, hydro and pumped storage');
  var fit = state.api && state.api.fit;
  fig('Inertia', G.int(b.inertia) + ' GVA·s', b.preset ? 'Set by the preset' : fit && fit.fitted ? 'Estimated from the mix, fitted to NESO’s figures' : 'Estimated from the mix');
  var held = b.preset ? b.preset.held : (state.api && state.api.held) || {};
  fig('Dynamic Containment held', held.DCL ? G.int(held.DCL) + ' MW' : 'None', b.preset ? (b.preset.held.DCL ? 'Set by the preset' : 'The service didn’t exist yet') : 'For falls, in NESO’s current block');
}

/* ---- the knobs -------------------------------------------------------------- */

function slider(key, label, min, max, step, unit, note) {
  var p = state.p, out = h('output', { text: G.int(p[key]) + ' ' + unit });
  var input = h('input', { type: 'range', min: String(min), max: String(max), step: String(step), value: String(p[key]), name: key, 'aria-label': label });
  input.addEventListener('input', function () {
    p[key] = +input.value;
    out.textContent = G.int(p[key]) + ' ' + unit;
    if (key === 'loss') byId('trip-select').value = 'custom';
    run(false);
  });
  return h('div', { cls: 'knob' }, [h('div', { cls: 'knob-head' }, [h('label', { text: label }), out]), input, note ? h('p', { cls: 'knob-note' }, note) : null]);
}

function renderKnobs() {
  var form = clear(byId('knobs')), b = state.base, p = state.p;
  var select = h('select', { id: 'trip-select', 'aria-label': 'What trips' });
  b.trips.forEach(function (t, i) { select.appendChild(h('option', { value: String(i), text: t.label + ', ' + G.int(t.mw) + ' MW' })); });
  select.appendChild(h('option', { value: 'custom', text: 'Something else (set the size below)' }));
  select.value = String(p.trip);
  select.addEventListener('change', function () {
    if (select.value === 'custom') return;
    var t = b.trips[+select.value];
    p.trip = +select.value; p.loss = t.mw; p.lossSync = t.sync;
    renderKnobs(); run(true);
  });
  form.appendChild(h('div', { cls: 'knob' }, [h('div', { cls: 'knob-head' }, [h('label', { 'for': 'trip-select', text: 'What trips' })]), select]));
  form.appendChild(slider('loss', 'Power lost', 0, 2500, 10, 'MW'));
  form.appendChild(slider('inertia', 'Inertia before the trip', 40, 400, 5, 'GVA·s', [G.term('inertia', 'Inertia'), ' sets how fast frequency falls. Halve it and the first fall is twice as steep.']));
  form.appendChild(h('h3', { text: 'Held in reserve' }));
  form.appendChild(slider('DCL', 'Dynamic Containment', 0, 2500, 10, 'MW', [G.term('dynamic-containment', 'Acts beyond ±0.2 Hz'), ', fully by 0.5 Hz, within a second.']));
  form.appendChild(slider('DML', 'Dynamic Moderation', 0, 1500, 10, 'MW'));
  form.appendChild(slider('DRL', 'Dynamic Regulation', 0, 1500, 10, 'MW'));
  form.appendChild(slider('governors', 'Generators’ governors', 0, 1500, 10, 'MW', ['Other generators raising output as frequency falls, over about ten seconds. An estimate.']));
  form.appendChild(slider('PQR', 'Quick reserve', 0, 2000, 10, 'MW', [G.term('reserve', 'Instructed'), ' after ten seconds, fully in by a minute.']));
  var old = h('input', { type: 'checkbox', id: 'old' });
  old.checked = p.old;
  old.addEventListener('change', function () { p.old = old.checked; run(true); });
  form.appendChild(h('label', { cls: 'check' }, [old, 'Old protection on small generators, as before 2022: 500 MW disconnect on the jolt of a big fault or a fall faster than 0.125 Hz/s, and 350 MW more at 49 Hz']));
}

/* ---- running and drawing ---------------------------------------------------- */

function run(animate) {
  var p = state.p, preset = state.base.preset;
  state.result = simulate({
    inertia: p.inertia, loss: p.loss, lossSync: p.lossSync, demand: p.demand,
    held: { DCL: p.DCL, DML: p.DML, DRL: p.DRL, PQR: p.PQR }, governors: p.governors,
    lomMW: p.old ? 500 : 0, lomRocof: 0.125, lomJolt: p.old ? 500 : 0, ufMW: p.old ? 350 : 0, ufHz: 49.0,
    later: preset && preset.later ? preset.later : [], seconds: preset && preset.seconds ? preset.seconds : 90,
    reserveStart: preset && preset.noReserve ? 1e9 : 10
  });
  cancelAnimationFrame(state.anim);
  if (animate && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    var started = performance.now(), length = 2600;
    var step = function (now) {
      state.drawn = Math.min(1, (now - started) / length);
      draw();
      if (state.drawn < 1) state.anim = requestAnimationFrame(step);
    };
    state.anim = requestAnimationFrame(step);
  } else {
    state.drawn = 1;
    draw();
  }
}

function draw() {
  var r = state.result;
  drawFrequency(r);
  drawPower(r);
  if (state.drawn >= 1) { renderFigures(r); renderStory(r); }
}

function chartFrame(host, H, lo, hi, seconds) {
  var W = Math.max(320, host.clientWidth || 700), padL = 46, padR = 12, padT = 10, padB = 24;
  return { W: W, H: H, padL: padL, padR: padR, padT: padT, padB: padB,
    x: function (t) { return padL + t / seconds * (W - padL - padR); },
    y: function (v) { return padT + (hi - v) / (hi - lo) * (H - padT - padB); } };
}

function drawFrequency(r) {
  var host = clear(byId('f-chart')), seconds = r.t[r.t.length - 1];
  var lo = Math.min(49.4, Math.floor((r.summary.nadir - 0.05) * 10) / 10), hi = 50.1;
  var c = chartFrame(host, 260, lo, hi, seconds);
  var svg = s('svg', { viewBox: '0 0 ' + c.W + ' ' + c.H, height: c.H, role: 'img', 'aria-label': 'Frequency over the ' + seconds + ' seconds after the trip, lowest ' + r.summary.nadir.toFixed(2) + ' Hz' });
  var step = hi - lo > 0.8 ? 0.2 : 0.1;
  for (var v = Math.ceil(lo / step - 1e-6) * step; v <= hi + 1e-6; v += step) {
    svg.appendChild(s('line', { 'class': Math.abs(v - 50) < 1e-6 ? 'zero-line' : 'grid-line', x1: c.padL, x2: c.W - c.padR, y1: c.y(v), y2: c.y(v) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: c.padL - 6, y: c.y(v) + 3.5, 'text-anchor': 'end', text: v.toFixed(1) }));
  }
  LIMITS.forEach(function (l, i) {
    if (l[0] < lo) return;
    svg.appendChild(s('line', { 'class': 'limit l' + i, x1: c.padL, x2: c.W - c.padR, y1: c.y(l[0]), y2: c.y(l[0]) }));
    svg.appendChild(s('text', { 'class': 'limit-text l' + i, x: c.W - c.padR - 4, y: c.y(l[0]) - 4, 'text-anchor': 'end', text: l[0] + ' Hz: ' + l[1].toLowerCase() }));
  });
  for (var t = 0; t <= seconds; t += c.W < 560 ? 30 : seconds > 90 ? 20 : 10) svg.appendChild(s('text', { 'class': 'axis-text', x: c.x(t), y: c.H - 6, 'text-anchor': 'middle', text: t + ' s' }));
  var n = Math.max(2, Math.round(r.t.length * state.drawn));
  svg.appendChild(s('path', { 'class': 'sim-line', d: r.t.slice(0, n).map(function (t, i) { return (i ? 'L' : 'M') + c.x(t).toFixed(1) + ' ' + c.y(r.f[i]).toFixed(1); }).join('') }));
  if (state.drawn >= 1) {
    r.notes.forEach(function (note) {
      svg.appendChild(s('line', { 'class': 'note-line', x1: c.x(note[0]), x2: c.x(note[0]), y1: c.padT, y2: c.H - c.padB }));
    });
    svg.appendChild(s('circle', { 'class': 'nadir', cx: c.x(r.summary.nadirAt), cy: c.y(r.summary.nadir), r: 5 }));
  }
  host.appendChild(svg);
}

function drawPower(r) {
  var host = clear(byId('p-chart')), seconds = r.t[r.t.length - 1];
  var top = Math.max.apply(null, r.lost) * 1.15 || 1000;
  var c = chartFrame(host, 170, 0, top, seconds);
  var svg = s('svg', { viewBox: '0 0 ' + c.W + ' ' + c.H, height: c.H, role: 'img', 'aria-label': 'What filled the gap, in MW, against the power lost' });
  var stepMW = G.niceStep(top, 3);
  for (var v = 0; v <= top; v += stepMW) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: c.padL, x2: c.W - c.padR, y1: c.y(v), y2: c.y(v) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: c.padL - 6, y: c.y(v) + 3.5, 'text-anchor': 'end', text: G.int(v) }));
  }
  for (var t = 0; t <= seconds; t += c.W < 560 ? 30 : seconds > 90 ? 20 : 10) svg.appendChild(s('text', { 'class': 'axis-text', x: c.x(t), y: c.H - 6, 'text-anchor': 'middle', text: t + ' s' }));
  var n = Math.max(2, Math.round(r.t.length * state.drawn)), base = new Array(n).fill(0);
  PARTS.forEach(function (part) {
    var series = r[part[0]];
    if (!series.some(function (x) { return x > 1; })) return;
    var upper = [], d = '';
    for (var i = 0; i < n; i++) { upper.push(base[i] + Math.max(0, series[i])); d += (i ? 'L' : 'M') + c.x(r.t[i]).toFixed(1) + ' ' + c.y(upper[i]).toFixed(1); }
    for (var j = n - 1; j >= 0; j--) d += 'L' + c.x(r.t[j]).toFixed(1) + ' ' + c.y(base[j]).toFixed(1);
    svg.appendChild(s('path', { d: d + 'Z', style: 'fill:var(' + part[2] + ')', 'class': 'area' }));
    base = upper;
  });
  svg.appendChild(s('path', { 'class': 'lost-line', d: r.t.slice(0, n).map(function (t, i) { return (i ? 'L' : 'M') + c.x(t).toFixed(1) + ' ' + c.y(r.lost[i]).toFixed(1); }).join('') }));
  host.appendChild(svg);
  var legend = clear(byId('p-legend'));
  legend.appendChild(h('li', null, [h('i', { cls: 'lost-key' }), 'Power lost']));
  PARTS.forEach(function (part) { if (r[part[0]].some(function (x) { return x > 1; })) legend.appendChild(h('li', null, [h('i', { cls: 'sw', style: 'background:var(' + part[2] + ')' }), part[1]])); });
}

function verdict(sm) {
  if (sm.lfdd) return ['bad', 'Demand disconnected'];
  if (sm.below492) return ['bad', 'Beyond what NESO plans for'];
  if (sm.below495) return ['warn', 'Below the normal limit'];
  return ['good', 'Held within limits'];
}

function renderFigures(r) {
  var sm = r.summary, host = clear(byId('sim-figs')), v = verdict(sm);
  function fig(label, value, note) { host.appendChild(h('div', null, [h('span', { text: label }), h('b', { text: value }), note ? h('small', { text: note }) : null])); }
  fig('Outcome', v[1]);
  host.lastChild.classList.add('verdict', v[0]);
  fig('Fastest fall', sm.rocof.toFixed(2) + ' Hz/s');
  fig('Lowest point', sm.nadir.toFixed(2) + ' Hz', 'After ' + sm.nadirAt.toFixed(1) + ' s');
  fig('After a minute', sm.at60.toFixed(2) + ' Hz');
}

function renderStory(r) {
  var p = state.p, sm = r.summary, b = state.base, host = clear(byId('story'));
  var trip = b.trips[p.trip] && byId('trip-select').value !== 'custom' ? b.trips[p.trip].name : 'A unit';
  var steps = [];
  function step(t, text, at) { steps.push([at === undefined ? parseFloat(t) : at, t, text]); }
  step('0 s', [trip + ' trips and ' + G.int(p.loss) + ' MW vanishes. ' + (p.lossSync ? 'It was spinning, so inertia drops from ' + G.int(p.inertia) + ' to ' + G.int(sm.inertiaAfter) + ' GVA·s.' : 'It wasn’t a spinning machine, so inertia stays at ' + G.int(p.inertia) + ' GVA·s.')]);
  step('0.5 s', ['Frequency is falling at ' + sm.rocof.toFixed(2) + ' Hz/s. ', G.term('dynamic-containment', 'Dynamic Containment'), sm.peaks.dc > 1 ? ' starts to respond, peaking at ' + G.int(sm.peaks.dc) + ' MW of the ' + G.int(p.DCL) + ' MW held.' : ' isn’t held, so nothing fast responds yet.']);
  r.notes.forEach(function (n) { step(n[0].toFixed(1) + ' s', [n[1]]); });
  var where = sm.lfdd ? 'Automatic disconnection caught it.' : sm.below492 ? 'That is below 49.2 Hz, further than NESO plans for even the largest losses.' : sm.below495 ? 'That is below 49.5 Hz: allowed only for the largest losses, and frequency must be back above it within a minute.' : 'That stays above 49.5 Hz, inside the limit for a normal loss.';
  step(sm.nadirAt.toFixed(1) + ' s', ['The fall stops at ' + sm.nadir.toFixed(2) + ' Hz. ' + where]);
  if (sm.reserve > 0) step('10 to 60 s', [G.term('reserve', 'Reserve'), ' ramps up to ' + G.int(sm.reserve) + ' MW, taking over from the fast services. After a minute frequency is ' + sm.at60.toFixed(2) + ' Hz.']);
  else step('60 s', ['Without reserve in time, frequency is still ' + sm.at60.toFixed(2) + ' Hz after a minute, held up by the services alone.']);
  steps.sort(function (a, b) { return a[0] - b[0]; });
  steps.forEach(function (st) { host.appendChild(h('li', null, [h('time', { text: st[1] }), h('p', null, st[2])])); });
}

/* ---- start-up ------------------------------------------------------------------ */

byId('start').addEventListener('click', function (ev) {
  var b = ev.target.closest('button[data-start]');
  if (!b) return;
  state.start = b.dataset.start;
  byId('start').querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', String(x === b)); });
  if (state.start === 'now') setBase(baseNow());
  else if (state.start === 'preset') setBase(basePreset());
  else {
    var go = function () { if (state.replay && state.replay.t.length > 1) setBase(baseAt(state.at === null ? state.replay.t.length - 1 : state.at)); else { setBase(baseNow()); state.start = 'past'; renderStart(); } };
    if (state.replay) go();
    else G.fetchJSON('/api/replay').then(function (r) { state.replay = r; go(); });
  }
});
byId('go').addEventListener('click', function () { run(true); });
window.addEventListener('gb:width', function () { if (state.result) draw(); });

Promise.all([G.fetchJSON('/api/map'), G.fetchJSON('/api/sandbox')]).then(function (res) {
  state.map = res[0]; state.api = res[1];
  setBase(baseNow());
  G.setLive('good', 'Starting from the grid at ' + G.hm(state.map.time) + '.');
}).catch(function () {
  G.setLive('warn', 'Couldn’t load the live grid; starting from a preset.');
  state.start = 'preset';
  byId('start').querySelectorAll('button').forEach(function (x) { x.setAttribute('aria-pressed', String(x.dataset.start === 'preset')); });
  setBase(basePreset());
});
