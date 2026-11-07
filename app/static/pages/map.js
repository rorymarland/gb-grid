import G from '/static/js/gb.js';

var byId = G.byId, clear = G.clear, add = G.add, h = G.h, s = G.s, num = G.num, clamp = G.clamp, hm = G.hm, niceStep = G.niceStep, fetchText = G.fetchText, plural = G.plural, makeTip = G.makeTip, textWidth = G.textWidth, ago = G.ago;

var NS = 'http://www.w3.org/2000/svg';
var TZ = 'Europe/London';
var CATS = ['wind', 'solar', 'nuclear', 'gas', 'biomass', 'hydro', 'storage', 'other'];
var NAMES = { wind: 'Wind', solar: 'Solar', nuclear: 'Nuclear', gas: 'Gas', biomass: 'Biomass', hydro: 'Hydro', storage: 'Storage', other: 'Other' };
/* Great Britain plus its offshore wind farms, in map km (y down) */
var FOCUS = { x0: -430, x1: 340, y0: -1330, y1: -5 };
/* Where each interconnector's far-end label sits: dx, dy (px), anchor */
var LINK_LABEL = {
  INTFR: [9, 16, 'start'], INTELEC: [9, -10, 'start'], INTNEM: [8, 4, 'start'], INTNED: [8, 4, 'start'],
  INTIFA2: [0, 20, 'middle'], INTNSL: [8, 4, 'start'], INTVKL: [8, 4, 'start'], INTIRL: [-8, 4, 'end'],
  INTEW: [-8, 4, 'end'], INTGRNL: [-8, 4, 'end']
};
var LINK_BEND = { INTFR: -0.16, INTELEC: 0.16, INTNEM: 0.06, INTNED: -0.06 };

var state = {
  data: null, text: '', failures: 0, lastOk: 0, timer: null, view: 'map',
  hidden: {}, showIdle: true, selected: null, highlight: null, stationHistory: {},
  sort: { key: 'mw', dir: -1 }, query: '', nation: 'all', cats: {}, includeIdle: false,
  openRow: null, showAll: false, fuelOrder: null, fuelOrderTime: null
};
try {
  var saved = JSON.parse(localStorage.getItem('gb-grid-map') || '{}');
  if (saved && typeof saved === 'object') {
    if (saved.hidden) state.hidden = saved.hidden;
    if (saved.showIdle === false) state.showIdle = false;
  }
} catch (error) { /* storage unavailable */ }
function savePrefs() {
  try { localStorage.setItem('gb-grid-map', JSON.stringify({ hidden: state.hidden, showIdle: state.showIdle })); } catch (error) { /* ignore */ }
}

/* ---------- small helpers ---------- */


var timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: TZ });
var intFmt = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 0 });
function mw(v) { return num(v) ? intFmt.format(Math.round(v)) : '–'; }
function gw(vMw) {
  if (!num(vMw)) return '–';
  var v = vMw / 1000;
  return Math.abs(v) >= 1 ? v.toFixed(1) : v.toFixed(2);
}
var signatures = {};
function changed(name, inputs) {
  var signature = JSON.stringify(inputs);
  if (signatures[name] === signature) return false;
  signatures[name] = signature;
  return true;
}


/* ---------- data ---------- */

function stationsOf(data) { return (data && data.stations) || []; }
function category(data, key) {
  var found = null;
  ((data && data.categories) || []).forEach(function (c) { if (c.key === key) found = c; });
  return found;
}
function catTotal(c) {
  if (!c) return 0;
  return Math.max(0, c.measured || 0) + Math.max(0, c.embedded || 0);
}
function netImports(data) {
  var total = 0;
  ((data && data.links) || []).forEach(function (link) { if (num(link.mw)) total += link.mw; });
  return total;
}
function generationTotal(data) {
  var total = 0;
  CATS.forEach(function (key) { total += catTotal(category(data, key)); });
  return total;
}
function isIdle(st) { return Math.abs(st.mw) < 1; }
function loadOf(st) { return st.cap && st.mw > 0 ? Math.min(100, 100 * st.mw / st.cap) : null; }
function where(st) { return st.off ? 'Offshore, ' + (st.na || 'GB') : (st.na || ''); }


function tick() {
  clearTimeout(state.timer);
  fetchText('/api/map').then(function (text) {
    state.failures = 0;
    state.lastOk = Date.now();
    if (text !== state.text) {
      state.text = text;
      state.live = JSON.parse(text);
      if (R.index === null) {
        state.data = state.live;
        render();
      }
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
document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });

function renderHeader() {
  var data = state.live, dot = byId('live-dot'), text = byId('live-text');
  var cls = 'dot', message;
  var offline = state.failures > 0 && (!state.lastOk || Date.now() - state.lastOk > 25000);
  if (offline) {
    cls += ' bad';
    message = 'Can’t reach the dashboard server. Retrying.';
  } else if (!data) {
    message = 'Connecting to the dashboard server…';
  } else if (!data.model) {
    var feed = data.status && data.status.units;
    if (feed && feed.failures) { cls += ' warn'; message = 'No station data yet: ' + (feed.last_error || 'Elexon isn’t responding') + '.'; }
    else message = 'Collecting the first station data…';
  } else {
    var at = data.model.at, checked = ago((Date.now() - state.lastOk) / 1000);
    if (Date.now() / 1000 - at > 20 * 60) {
      cls += ' warn';
      message = 'Delayed. Station data is for ' + hm(at) + '; checked ' + checked + '.';
    } else {
      cls += ' good pulse';
      message = 'Live. Station data for ' + hm(data.model.time || at) + ', checked ' + checked + '.';
    }
  }
  if (dot.className !== cls) dot.className = cls;
  if (text.textContent !== message) text.textContent = message;
}

/* ---------- views ---------- */

function viewFromPath() { return /^\/stations/.test(location.pathname) ? 'stations' : 'map'; }
function setView(view, push) {
  if (view !== 'map' && isFullscreen()) exitFullscreen();
  if (view !== 'map' && R.index !== null) goLive(true);
  state.view = view;
  document.body.className = 'mode-' + view;
  byId('view-map').hidden = view !== 'map';
  byId('view-stations').hidden = view !== 'stations';
  Array.prototype.forEach.call(document.querySelectorAll('.nav a'), function (link) {
    if (link.getAttribute('data-view') === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  if (push) history.pushState({ view: view }, '', view === 'map' ? '/map' : '/stations');
  signatures = {};
  if (view === 'map') { mapResize(); }
  render();
  if (push) window.scrollTo(0, 0);
}
document.querySelector('.nav').addEventListener('click', function (event) {
  var link = event.target.closest('a[data-view]');
  if (!link || event.metaKey || event.ctrlKey || event.shiftKey || event.button) return;
  event.preventDefault();
  if (link.getAttribute('data-view') !== state.view) setView(link.getAttribute('data-view'), true);
});
window.addEventListener('popstate', function () { setView(viewFromPath(), false); });

function render() {
  var data = state.data;
  if (!data) return;
  updateTitle(data);
  if (state.view === 'map') {
    drawMapData();
    renderSide();
  } else {
    renderStationsView();
  }
}

function updateTitle(data) {
  var total = generationTotal(data);
  document.title = (state.view === 'map' ? 'GB grid map' : 'GB power stations') + (total ? ': ' + gw(total) + ' GW generating' : '');
}

/* ---------- the map ---------- */

var M = {
  ready: false, s: 1, tx: 0, ty: 0, W: 0, H: 0, minS: 0.1, maxS: 10, fitS: 1, moved: false,
  nodes: {}, order: [], screen: [], links: [], linkNodes: {}, hoverId: null, hoverLink: null, raf: 0, K: 0.42
};

function initMap() {
  M.stage = byId('stage');
  M.svg = byId('map');
  M.gLand = s('g');
  M.gGeoLabels = s('g');
  M.gLinks = s('g');
  M.gStations = s('g');
  M.gLabels = s('g');
  M.gEnds = s('g');
  [M.gLand, M.gGeoLabels, M.gLinks, M.gStations, M.gLabels, M.gEnds].forEach(function (g) { M.svg.appendChild(g); });
  M.tip = makeTip(M.stage);
  fetch('/geo.json').then(function (r) { return r.json(); }).then(function (geo) {
    M.geo = geo;
    var inner = s('g', { transform: 'scale(0.5)' });
    geo.units.forEach(function (unit) {
      inner.appendChild(s('path', { d: unit.d, 'class': unit.kind === 'gb' ? 'land' : 'land-near', 'fill-rule': 'evenodd' }));
    });
    M.gLand.appendChild(inner);
    M.geoLabels = geo.labels.map(function (label) {
      var node = s('text', { 'class': 'geo-label ' + (label.k === 'gb' ? 'gb' : label.k === 'sea' ? 'sea' : ''), text: label.t });
      M.gGeoLabels.appendChild(node);
      return { node: node, x: label.x, y: label.y };
    });
    M.ready = true;
    mapResize();
    drawMapData();
  }).catch(function () {
    showMapMessage('Couldn’t load the map outline. Reload the page to try again.');
  });

  M.svg.addEventListener('wheel', onWheel, { passive: false });
  M.svg.addEventListener('pointerdown', onDown);
  M.svg.addEventListener('pointermove', onMove);
  M.svg.addEventListener('pointerup', onUp);
  M.svg.addEventListener('pointercancel', onUp);
  M.svg.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse' && !drag) setHover(null, null); });
  M.svg.addEventListener('dblclick', function (e) { e.preventDefault(); zoomAt(pos(e), M.s * 2); });
  M.stage.addEventListener('keydown', onKey);
  byId('zoom-in').addEventListener('click', function () { interacted(); M.tip.hide(); zoomAt([M.W / 2, M.H / 2], M.s * 1.6); });
  byId('zoom-out').addEventListener('click', function () { interacted(); M.tip.hide(); zoomAt([M.W / 2, M.H / 2], M.s / 1.6); });
  byId('zoom-reset').addEventListener('click', function () { M.moved = false; fitDefault(); requestRedraw(); });
  byId('fs-btn').addEventListener('click', toggleFullscreen);
  if (window.ResizeObserver) new ResizeObserver(function () { mapResize(); drawDays(); }).observe(M.stage);
  else window.addEventListener('resize', mapResize);
}

function showMapMessage(text) {
  var node = byId('map-msg');
  node.hidden = !text;
  if (text) node.textContent = text;
}

function mapResize() {
  if (!M.stage || state.view !== 'map') return;
  var W = M.stage.clientWidth, H = M.stage.clientHeight;
  if (!W || !H) return;
  var first = !M.W;
  var centre = !first && M.moved ? [(M.W / 2 - M.tx) / M.s, (M.H / 2 - M.ty) / M.s] : null;
  M.W = W; M.H = H;
  M.K = 0.42 * clamp(Math.min(W, H * 0.8) / 760, 0.72, 1.12);
  if (!M.geo) return;
  var box = M.geo.box;
  M.minS = Math.max(W / box[2], H / box[3]);
  if (first || !M.moved || !centre) fitDefault();
  else {
    M.s = clamp(M.s, M.minS, M.maxS);
    M.tx = W / 2 - centre[0] * M.s;
    M.ty = H / 2 - centre[1] * M.s;
    clampView();
  }
  requestRedraw();
}

/* ---------- full screen ---------- */

var FS = { pseudo: false };
function fsElement() { return document.fullscreenElement || document.webkitFullscreenElement || null; }
function isFullscreen() { return Boolean(fsElement()) || FS.pseudo; }
function enterFullscreen() {
  var view = byId('view-map');
  var request = view.requestFullscreen || view.webkitRequestFullscreen;
  var allowed = document.fullscreenEnabled || document.webkitFullscreenEnabled;
  if (request && allowed) {
    try {
      var result = request.call(view, { navigationUI: 'hide' });
      if (result && result.catch) result.catch(function () { setPseudo(true); });
      return;
    } catch (error) { /* fall back below */ }
  }
  /* iPhone Safari can't make a page full screen, so fill the window instead */
  setPseudo(true);
}
function exitFullscreen() {
  if (FS.pseudo) { setPseudo(false); return; }
  var exit = document.exitFullscreen || document.webkitExitFullscreen;
  if (fsElement() && exit) exit.call(document);
}
function toggleFullscreen() {
  interacted();
  M.tip.hide();
  if (isFullscreen()) exitFullscreen(); else enterFullscreen();
}
function setPseudo(on) {
  FS.pseudo = on;
  byId('view-map').classList.toggle('pseudo-fs', on);
  document.documentElement.classList.toggle('fs-lock', on);
  syncFullscreen();
}
function syncFullscreen() {
  var on = isFullscreen();
  byId('view-map').classList.toggle('is-fs', on);
  var button = byId('fs-btn');
  button.setAttribute('aria-label', on ? 'Exit full screen' : 'Show the map full screen');
  button.setAttribute('title', on ? 'Exit full screen (F or Esc)' : 'Full screen (F)');
  if (on) { try { M.stage.focus({ preventScroll: true }); } catch (error) { M.stage.focus(); } }
  mapResize();
}
document.addEventListener('fullscreenchange', syncFullscreen);
document.addEventListener('webkitfullscreenchange', syncFullscreen);
document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && FS.pseudo) setPseudo(false); });

function renderFsKey(data) {
  var host = clear(byId('fs-key'));
  if (data.model) host.appendChild(h('span', { cls: 'when-chip', text: 'GW at ' + stamp(data.model.time || data.model.at) }));
  (state.fuelOrder || CATS).forEach(function (key) {
    var total = catTotal(category(data, key));
    if (total < 50) return;
    host.appendChild(h('span', null, [h('i', { cls: 'sw b-' + key }), NAMES[key] + ' ' + gw(total)]));
  });
  var net = netImports(data);
  if ((data.links || []).length && Math.abs(net) >= 50) {
    host.appendChild(h('span', null, [h('i', { cls: 'sw b-imports' }), (net > 0 ? 'Imports ' : 'Exports ') + gw(Math.abs(net))]));
  }
}

function fitDefault() {
  var pad = 18;
  var sx = (M.W - 2 * pad) / (FOCUS.x1 - FOCUS.x0), sy = (M.H - 2 * pad) / (FOCUS.y1 - FOCUS.y0);
  M.s = M.fitS = Math.max(M.minS, Math.min(sx, sy));
  M.maxS = M.fitS * 10;
  M.tx = M.W / 2 - (FOCUS.x0 + FOCUS.x1) / 2 * M.s;
  M.ty = M.H / 2 - (FOCUS.y0 + FOCUS.y1) / 2 * M.s;
  clampView();
}

function clampView() {
  if (!M.geo) return;
  var b = M.geo.box, x0 = b[0], y0 = b[1], w = b[2] * M.s, hgt = b[3] * M.s;
  if (w <= M.W) M.tx = (M.W - w) / 2 - x0 * M.s;
  else M.tx = clamp(M.tx, M.W - (x0 * M.s + w), -x0 * M.s);
  if (hgt <= M.H) M.ty = (M.H - hgt) / 2 - y0 * M.s;
  else M.ty = clamp(M.ty, M.H - (y0 * M.s + hgt), -y0 * M.s);
}

function zoomAt(p, target) {
  var next = clamp(target, M.minS, M.maxS);
  var mx = (p[0] - M.tx) / M.s, my = (p[1] - M.ty) / M.s;
  M.s = next;
  M.tx = p[0] - mx * next;
  M.ty = p[1] - my * next;
  M.moved = true;
  clampView();
  requestRedraw();
}

function pos(e) {
  var rect = M.svg.getBoundingClientRect();
  return [e.clientX - rect.left, e.clientY - rect.top];
}

var pointers = {}, drag = null, pinch = null;
function interacted() {
  var hint = byId('hint');
  if (!hint.hidden) hint.hidden = true;
}
function onWheel(e) {
  e.preventDefault();
  interacted();
  M.tip.hide();
  var factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0016));
  zoomAt(pos(e), M.s * factor);
}
function onDown(e) {
  if (e.button && e.button !== 0) return;
  var p = pos(e);
  pointers[e.pointerId] = p;
  try { M.svg.setPointerCapture(e.pointerId); } catch (error) { /* ignore */ }
  var ids = Object.keys(pointers);
  if (ids.length === 1) {
    drag = { start: p, last: p, moved: false, type: e.pointerType };
  } else if (ids.length === 2) {
    var a = pointers[ids[0]], b = pointers[ids[1]];
    var mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    pinch = { d: Math.max(10, Math.hypot(a[0] - b[0], a[1] - b[1])), s: M.s, mx: (mid[0] - M.tx) / M.s, my: (mid[1] - M.ty) / M.s };
    if (drag) drag.moved = true;
  }
}
function onMove(e) {
  var p = pos(e);
  if (!pointers[e.pointerId]) {
    if (e.pointerType === 'mouse') hoverAt(p);
    return;
  }
  pointers[e.pointerId] = p;
  var ids = Object.keys(pointers);
  if (ids.length >= 2 && pinch) {
    var a = pointers[ids[0]], b = pointers[ids[1]];
    var mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    M.s = clamp(pinch.s * Math.hypot(a[0] - b[0], a[1] - b[1]) / pinch.d, M.minS, M.maxS);
    M.tx = mid[0] - pinch.mx * M.s;
    M.ty = mid[1] - pinch.my * M.s;
    M.moved = true;
    clampView();
    requestRedraw();
  } else if (drag) {
    if (!drag.moved && Math.hypot(p[0] - drag.start[0], p[1] - drag.start[1]) > 5) {
      drag.moved = true;
      M.stage.classList.add('dragging');
      M.tip.hide();
      interacted();
    }
    if (drag.moved) {
      M.tx += p[0] - drag.last[0];
      M.ty += p[1] - drag.last[1];
      M.moved = true;
      clampView();
      requestRedraw();
    }
    drag.last = p;
  }
}
function onUp(e) {
  var p = pos(e);
  var wasClick = drag && !drag.moved && e.type === 'pointerup';
  delete pointers[e.pointerId];
  if (Object.keys(pointers).length < 2) pinch = null;
  if (!Object.keys(pointers).length) {
    M.stage.classList.remove('dragging');
    if (wasClick) clickAt(p);
    drag = null;
  }
}
function onKey(e) {
  if (e.target !== M.stage) return;
  var step = 70, handled = true;
  if (e.key === 'ArrowLeft') M.tx += step;
  else if (e.key === 'ArrowRight') M.tx -= step;
  else if (e.key === 'ArrowUp') M.ty += step;
  else if (e.key === 'ArrowDown') M.ty -= step;
  else if (e.key === '+' || e.key === '=') { zoomAt([M.W / 2, M.H / 2], M.s * 1.5); return e.preventDefault(); }
  else if (e.key === '-' || e.key === '_') { zoomAt([M.W / 2, M.H / 2], M.s / 1.5); return e.preventDefault(); }
  else if (e.key === '0') { M.moved = false; fitDefault(); requestRedraw(); return e.preventDefault(); }
  else if (e.key === 'Escape') { if (!FS.pseudo) select(null); return; }
  else if (e.key === ' ') { togglePlay(); return e.preventDefault(); }
  else if (e.key === ',' || e.key === '.') { stepFrame(e.key === ',' ? -1 : 1); return e.preventDefault(); }
  else if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey && !e.altKey) { toggleFullscreen(); return e.preventDefault(); }
  else handled = false;
  if (handled) { e.preventDefault(); interacted(); M.tip.hide(); M.moved = true; clampView(); requestRedraw(); }
}

function hitStation(p) {
  var best = null;
  M.screen.forEach(function (item) {
    var d = Math.hypot(item.sx - p[0], item.sy - p[1]);
    var reach = Math.max(item.r + 2, 8);
    if (d <= reach && (!best || item.r < best.r || (item.r === best.r && d < best.d))) best = { id: item.id, r: item.r, d: d, item: item };
  });
  return best;
}
function hitLink(p) {
  var best = null;
  M.links.forEach(function (link) {
    if (!link.pts) return;
    for (var i = 1; i < link.pts.length; i++) {
      var a = link.pts[i - 1], b = link.pts[i];
      var dx = b[0] - a[0], dy = b[1] - a[1];
      var t = clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1), 0, 1);
      var d = Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
      if (d < 7 && (!best || d < best.d)) best = { link: link, d: d };
    }
  });
  return best && best.link;
}
function hoverAt(p) {
  var hit = hitStation(p);
  if (hit) { setHover(hit.id, null); showStationTip(hit.item.st, p); return; }
  var link = hitLink(p);
  if (link) { setHover(null, link.code); showLinkTip(link, p); return; }
  setHover(null, null);
}
function clickAt(p) {
  var hit = hitStation(p);
  if (hit) {
    select(hit.id);
    showStationTip(hit.item.st, p);
    return;
  }
  var link = hitLink(p);
  if (link) { setHover(null, link.code); showLinkTip(link, p); return; }
  M.tip.hide();
  setHover(null, null);
  select(null);
}
function setHover(id, code) {
  if (M.hoverId === id && M.hoverLink === code) return;
  if (M.hoverId && M.nodes[M.hoverId]) M.nodes[M.hoverId].node.classList.remove('hover');
  M.hoverId = id;
  M.hoverLink = code;
  if (id && M.nodes[id]) M.nodes[id].node.classList.add('hover');
  M.links.forEach(function (link) { link.g.classList.toggle('hover', link.code === code); });
  M.stage.classList.toggle('pointing', Boolean(id || code));
  if (!id && !code) M.tip.hide();
}

function stationRows(st) {
  var rows = [];
  if (st.mw <= -1) rows.push({ cls: 'b-' + st.c, value: mw(-st.mw) + ' MW', label: st.c === 'storage' ? 'storing (charging or pumping)' : 'using' });
  else rows.push({ cls: 'b-' + st.c, value: mw(Math.max(0, st.mw)) + ' MW', label: 'output now' });
  var load = loadOf(st);
  if (st.cap) rows.push({ value: load === null ? '–' : Math.round(load) + '%', label: 'of ' + mw(st.cap) + ' MW capacity' });
  rows.push({ sep: true });
  rows.push({ value: NAMES[st.c] || st.c, label: where(st) });
  if (!st.replay) rows.push({ value: st.on + ' of ' + st.u, label: st.u === 1 ? 'unit running' : 'units running' });
  if (st.i) rows.push({ value: String(st.i), label: st.i === 1 ? 'unit following a grid instruction' : 'units following grid instructions' });
  return rows;
}
function showStationTip(st, p) {
  var sideShown = byId('side').offsetParent !== null;
  M.tip.show(st.n, stationRows(st), p[0], p[1], 'Modelled from Elexon data.' + (sideShown ? ' Select for the last 24 hours.' : ' Exit full screen for its last 24 hours.'));
}
function linkWords(link) {
  if (!num(link.mw) || Math.abs(link.mw) < 10) return 'idle';
  return gw(Math.abs(link.mw)) + ' GW ' + (link.mw > 0 ? 'in' : 'out');
}
function showLinkTip(link, p) {
  var rows = [];
  if (!num(link.mw) || Math.abs(link.mw) < 10) rows.push({ cls: 'b-imports', value: '0 GW', label: 'idle now' });
  else rows.push({ cls: 'b-imports', value: gw(Math.abs(link.mw)) + ' GW', label: link.mw > 0 ? 'importing to GB' : 'exporting from GB' });
  if (link.cap && num(link.mw)) rows.push({ value: Math.round(100 * Math.abs(link.mw) / link.cap) + '%', label: 'of ' + gw(link.cap) + ' GW capacity' });
  rows.push({ sep: true });
  if (link.gb && link.far) rows.push({ value: link.gb[2], label: 'to ' + link.far[2] + ', ' + link.country });
  M.tip.show(link.name + ' (' + link.country + ')', rows, p[0], p[1], 'Measured flow, Elexon.');
}

function radius(st) {
  if (isIdle(st)) return 3;
  return Math.max(2.5, Math.sqrt(Math.abs(st.mw)) * M.K);
}

function drawMapData() {
  var data = state.data;
  if (!M.ready || !data || state.view !== 'map') return;
  if (!data.model) {
    var feed = data.status && data.status.units;
    showMapMessage(feed && feed.last_error ? 'Station data unavailable: ' + feed.last_error : 'Collecting the first power station data. This takes about a minute after the dashboard starts.');
  } else {
    showMapMessage(null);
  }
  refreshStations();
  drawLinks(data.links || []);
  requestRedraw();
}

function refreshStations() {
  var data = state.data;
  if (!M.ready || !data) return;
  var list = stationsOf(data);
  var seen = {};
  list.forEach(function (st) {
    seen[st.id] = true;
    var entry = M.nodes[st.id];
    if (!entry) {
      entry = M.nodes[st.id] = { node: s('circle', { 'class': 'st' }) };
    }
    entry.st = st;
    var cls = 'st ';
    if (st.mw <= -1) cls += 'using k-' + st.c;
    else if (isIdle(st)) cls += 'idle k-' + st.c;
    else cls += 'f-' + st.c + (st.off ? ' sea-st' : '');
    if (state.selected === st.id) cls += ' sel';
    if (M.hoverId === st.id) cls += ' hover';
    if (state.highlight && state.highlight !== st.c) cls += ' dim';
    entry.node.setAttribute('class', cls);
    var visible = !state.hidden[st.c] && (state.showIdle || !isIdle(st));
    entry.node.style.display = visible ? '' : 'none';
    entry.visible = visible;
  });
  Object.keys(M.nodes).forEach(function (id) {
    if (!seen[id]) { M.nodes[id].node.remove(); delete M.nodes[id]; }
  });
  M.order = list.map(function (st) { return M.nodes[st.id]; }).sort(function (a, b) { return radius(b.st) - radius(a.st); });
  M.order.forEach(function (entry) { M.gStations.appendChild(entry.node); });
}

function drawLinks(links) {
  var seen = {};
  M.links = links.filter(function (link) { return link.gb && link.far; }).map(function (data) {
    seen[data.code] = true;
    var link = M.linkNodes[data.code];
    if (!link) {
      link = M.linkNodes[data.code] = { code: data.code, g: s('g', { 'class': 'link' }) };
      link.base = s('path');
      link.flow = s('path', { 'class': 'link-flow' });
      link.arrow = s('path', { 'class': 'link-arrow' });
      link.g.appendChild(link.base);
      link.g.appendChild(link.flow);
      link.g.appendChild(link.arrow);
      M.gLinks.appendChild(link.g);
      link.end = s('circle', { 'class': 'link-end', r: 3.5 });
      link.gbEnd = s('circle', { 'class': 'link-gb', r: 3 });
      link.label = s('text', { 'class': 'link-label' }, [s('tspan'), s('tspan', { 'class': 'v' })]);
      M.gEnds.appendChild(link.end);
      M.gEnds.appendChild(link.gbEnd);
      M.gEnds.appendChild(link.label);
    }
    ['name', 'country', 'mw', 'cap', 'gb', 'far'].forEach(function (key) { link[key] = data[key]; });
    link.flowing = num(link.mw) && Math.abs(link.mw) >= 10;
    link.width = link.flowing ? 1.5 + 5 * Math.min(1, Math.abs(link.mw) / 1400) : 1.5;
    link.base.setAttribute('class', link.flowing ? 'link-base' : 'link-idle');
    link.base.style.strokeWidth = link.width + 'px';
    link.flow.style.display = link.flowing ? '' : 'none';
    link.arrow.style.display = link.flowing ? '' : 'none';
    if (link.flowing) {
      link.flow.style.strokeWidth = (link.width + 1.5) + 'px';
      var share = link.cap ? Math.min(1, Math.abs(link.mw) / link.cap) : 0.5;
      var duration = (2.6 - 1.6 * share).toFixed(1) + 's';
      if (link.flow.style.animationDuration !== duration) link.flow.style.animationDuration = duration;
    }
    link.label.childNodes[0].textContent = link.name;
    link.label.childNodes[1].textContent = linkWords(link);
    return link;
  });
  Object.keys(M.linkNodes).forEach(function (code) {
    if (seen[code]) return;
    var link = M.linkNodes[code];
    [link.g, link.end, link.gbEnd, link.label].forEach(function (node) { node.remove(); });
    delete M.linkNodes[code];
  });
}

function requestRedraw() {
  if (M.raf) return;
  M.raf = requestAnimationFrame(function () { M.raf = 0; redraw(); });
}

function sx(x) { return M.tx + x * M.s; }
function sy(y) { return M.ty + y * M.s; }

function redraw() {
  if (!M.ready || state.view !== 'map') return;
  var W = M.W, H = M.H;
  M.gLand.setAttribute('transform', 'translate(' + M.tx.toFixed(2) + ' ' + M.ty.toFixed(2) + ') scale(' + M.s.toFixed(5) + ')');
  M.screen = [];
  M.order.forEach(function (entry) {
    var st = entry.st, r = radius(st);
    var x = sx(st.x), y = sy(st.y);
    entry.node.setAttribute('cx', x.toFixed(1));
    entry.node.setAttribute('cy', y.toFixed(1));
    entry.node.setAttribute('r', r.toFixed(2));
    if (entry.visible && x > -40 && x < W + 40 && y > -40 && y < H + 40) M.screen.push({ id: st.id, sx: x, sy: y, r: r, st: st });
  });
  M.links.forEach(redrawLink);
  var taken = [];
  placeLinkLabels(taken);
  drawStationLabels(taken);
  placeGeoLabels(taken);
  drawSizeKey();
}

function overlaps(box, boxes) {
  return boxes.some(function (b) { return box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1]; });
}

/* Interconnector labels first (largest flows win), then stations, then place names */
function placeLinkLabels(taken) {
  M.links.slice().sort(function (a, b) { return Math.abs(b.mw || 0) - Math.abs(a.mw || 0); }).forEach(function (link) {
    var end = [sx(link.far[0]), sy(link.far[1])];
    var place = LINK_LABEL[link.code] || [8, 4, 'start'];
    var value = linkWords(link);
    var width = Math.max(textWidth(link.name, 11.5, 400), textWidth(value, 11.5, 650));
    var onScreen = end[0] > -30 && end[0] < M.W + 30 && end[1] > -30 && end[1] < M.H + 30;
    var chosen = null;
    if (onScreen) {
      [place[1], place[1] - 24, place[1] + 24].some(function (dy) {
        var x = end[0] + place[0], y = end[1] + dy;
        var left = place[2] === 'start' ? x : place[2] === 'end' ? x - width : x - width / 2;
        var shift = clamp(left, 6, M.W - width - 6) - left;
        var box = [left + shift - 2, y - 19, left + shift + width + 2, y + 11];
        if (box[1] < 2 || box[3] > M.H - 2 || overlaps(box, taken)) return false;
        chosen = { x: x + shift, y: y, box: box };
        return true;
      });
    }
    link.label.style.display = chosen ? '' : 'none';
    if (!chosen) return;
    taken.push(chosen.box);
    link.label.setAttribute('text-anchor', place[2]);
    var spans = link.label.childNodes;
    spans[0].setAttribute('x', chosen.x.toFixed(1));
    spans[0].setAttribute('y', (chosen.y - 7).toFixed(1));
    spans[1].setAttribute('x', chosen.x.toFixed(1));
    spans[1].setAttribute('y', (chosen.y + 7).toFixed(1));
    spans[1].textContent = value;
  });
}

function placeGeoLabels(taken) {
  M.geoLabels.forEach(function (label) {
    var x = sx(label.x), y = sy(label.y);
    var width = textWidth(label.node.textContent, 12.5, 400);
    var box = [x - width / 2 - 2, y - 12, x + width / 2 + 2, y + 4];
    var show = x > -width && x < M.W + width && y > 0 && y < M.H + 10 && !overlaps(box, taken);
    label.node.style.display = show ? '' : 'none';
    if (show) {
      label.node.setAttribute('x', x.toFixed(1));
      label.node.setAttribute('y', y.toFixed(1));
    }
  });
}

function redrawLink(link) {
  var a = [sx(link.gb[0]), sy(link.gb[1])], b = [sx(link.far[0]), sy(link.far[1])];
  var importing = num(link.mw) && link.mw > 0;
  var p0 = importing ? b : a, p2 = importing ? a : b;
  var dx = b[0] - a[0], dy = b[1] - a[1], len = Math.hypot(dx, dy) || 1;
  var bend = (LINK_BEND[link.code] || 0.05) * len;
  var c = [(a[0] + b[0]) / 2 - dy / len * bend, (a[1] + b[1]) / 2 + dx / len * bend];
  var d = 'M' + p0[0].toFixed(1) + ' ' + p0[1].toFixed(1) + 'Q' + c[0].toFixed(1) + ' ' + c[1].toFixed(1) + ' ' + p2[0].toFixed(1) + ' ' + p2[1].toFixed(1);
  link.base.setAttribute('d', d);
  if (link.flow) link.flow.setAttribute('d', d);
  link.pts = [];
  for (var i = 0; i <= 20; i++) {
    var t = i / 20, u = 1 - t;
    link.pts.push([u * u * p0[0] + 2 * u * t * c[0] + t * t * p2[0], u * u * p0[1] + 2 * u * t * c[1] + t * t * p2[1]]);
  }
  if (link.arrow) {
    var m = link.pts[10], ang = Math.atan2(p2[1] - p0[1], p2[0] - p0[0]) * 180 / Math.PI;
    var size = 4 + link.width;
    link.arrow.setAttribute('d', 'M' + size + ' 0L' + (-size * 0.8) + ' ' + (size * 0.75) + 'L' + (-size * 0.4) + ' 0L' + (-size * 0.8) + ' ' + (-size * 0.75) + 'Z');
    link.arrow.setAttribute('transform', 'translate(' + m[0].toFixed(1) + ' ' + m[1].toFixed(1) + ') rotate(' + ang.toFixed(1) + ')');
  }
  link.end.setAttribute('cx', b[0].toFixed(1));
  link.end.setAttribute('cy', b[1].toFixed(1));
  link.gbEnd.setAttribute('cx', a[0].toFixed(1));
  link.gbEnd.setAttribute('cy', a[1].toFixed(1));
}

function shortName(name) {
  var suffix = ' Wind Farm';
  return name.slice(-suffix.length) === suffix ? name.slice(0, -suffix.length) : name;
}

function drawStationLabels(taken) {
  clear(M.gLabels);
  var zoom = M.s / M.fitS;
  var minMw = 800 / Math.pow(Math.max(1, zoom), 1.6);
  var circles = [];
  M.screen.forEach(function (item) { if (item.r >= 6) circles.push([item.sx - item.r * 0.7, item.sy - item.r * 0.7, item.sx + item.r * 0.7, item.sy + item.r * 0.7]); });
  var candidates = M.screen.filter(function (item) { return item.st.mw >= minMw || item.id === state.selected; })
    .sort(function (a, b) { return (b.id === state.selected) - (a.id === state.selected) || b.st.mw - a.st.mw; });
  var placed = 0;
  candidates.forEach(function (item) {
    if (placed >= 14 + zoom * 4) return;
    var text = shortName(item.st.n);
    var width = textWidth(text, 12, 600), tall = 15;
    var options = [
      [item.sx + item.r + 4, item.sy + 4, 'start'],
      [item.sx - item.r - 4, item.sy + 4, 'end'],
      [item.sx, item.sy - item.r - 5, 'middle'],
      [item.sx, item.sy + item.r + 14, 'middle']
    ];
    for (var i = 0; i < options.length; i++) {
      var o = options[i];
      var left = o[2] === 'start' ? o[0] : o[2] === 'end' ? o[0] - width : o[0] - width / 2;
      var box = [left - 2, o[1] - tall + 3, left + width + 2, o[1] + 4];
      if (box[0] < 4 || box[2] > M.W - 4 || box[1] < 4 || box[3] > M.H - 4) continue;
      if (overlaps(box, taken) || overlaps(box, circles)) continue;
      taken.push(box);
      M.gLabels.appendChild(s('text', { x: o[0].toFixed(1), y: o[1].toFixed(1), 'text-anchor': o[2], 'class': 'st-label' + (item.st.off ? ' on-sea' : ''), text: text }));
      placed++;
      break;
    }
  });
}

function drawSizeKey() {
  var host = byId('size-key');
  var signature = M.K.toFixed(3);
  if (host.getAttribute('data-k') === signature) return;
  host.setAttribute('data-k', signature);
  clear(host);
  var sizes = [100, 500, 2000];
  var rMax = Math.sqrt(sizes[2]) * M.K;
  var x = 0, items = [];
  sizes.forEach(function (size) {
    var r = Math.sqrt(size) * M.K;
    var text = size >= 1000 ? (size / 1000) + ' GW' : size + ' MW';
    var width = Math.max(2 * r, textWidth(text, 11.5, 400));
    items.push({ r: r, cx: x + width / 2, text: text });
    x += width + 12;
  });
  var svg = s('svg', { width: Math.ceil(x - 12), height: Math.ceil(2 * rMax + 34) });
  svg.appendChild(s('text', { x: 0, y: 11, text: 'Output', 'font-weight': '600' }));
  items.forEach(function (item) {
    var base = 18 + 2 * rMax;
    svg.appendChild(s('circle', { cx: item.cx.toFixed(1), cy: (base - item.r).toFixed(1), r: item.r.toFixed(1) }));
    svg.appendChild(s('text', { x: item.cx.toFixed(1), y: base + 14, 'text-anchor': 'middle', text: item.text }));
  });
  host.appendChild(svg);
}

function select(id) {
  if (state.selected === id) {
    if (id !== null) renderDetail();
    return;
  }
  if (state.selected && M.nodes[state.selected]) M.nodes[state.selected].node.classList.remove('sel');
  state.selected = id;
  if (id && M.nodes[id]) M.nodes[id].node.classList.add('sel');
  renderDetail();
  requestRedraw();
  if (id && window.matchMedia && window.matchMedia('(max-width: 900px)').matches) {
    byId('detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

/* ---------- side panel ---------- */

function renderSide() {
  var data = state.data;
  if (!data) return;
  var model = data.model;
  var when = byId('when');
  if (model) {
    when.textContent = (data.replay ? 'Replaying ' + stamp(model.time) + '. Drag the slider or press play to move through time. ' : 'At ' + hm(model.time || model.at) + '. ') + 'Each circle is a power station, sized by what it’s generating.' +
      (model.basis === 'scheduled' ? ' Measured totals are delayed, so these are the stations’ own plans.' : '');
  } else {
    when.textContent = 'Waiting for the first station data…';
  }
  if (changed('stats', [data.time, data.links, data.categories, data.demand_gw])) renderStats(data);
  if (changed('fuels', [data.time, data.categories, state.hidden, state.highlight])) renderFuels(data);
  if (changed('ic', [data.time, data.links])) renderInterconnectors(data);
  if (changed('unmapped', [data.time, data.categories, data.model && data.model.unplaced_count])) renderUnmapped(data);
  if (changed('fskey', [data.time, data.categories, data.links, state.fuelOrder])) renderFsKey(data);
  byId('idle-toggle').checked = state.showIdle;
  if (state.selected) renderDetail();
}

function renderStats(data) {
  var host = clear(byId('stats'));
  var net = netImports(data);
  var stat = function (value, unit, label) {
    return h('div', { cls: 'stat' }, [h('b', null, [value, h('small', { text: unit })]), h('span', { text: label })]);
  };
  host.appendChild(stat(gw(generationTotal(data)), 'GW', 'generated in GB'));
  host.appendChild(stat(gw(Math.abs(net)), 'GW', net >= 0 ? 'net imports' : 'net exports'));
  host.appendChild(stat(num(data.demand_gw) ? data.demand_gw.toFixed(1) : '–', 'GW', 'being used'));
}

function renderFuels(data) {
  var host = clear(byId('fuels'));
  var rows = CATS.map(function (key) { return category(data, key) || { key: key, label: NAMES[key] }; });
  if (!state.fuelOrder || (state.fuelOrderTime !== data.time && !data.replay)) {
    state.fuelOrder = rows.slice().sort(function (a, b) { return catTotal(b) - catTotal(a); }).map(function (c) { return c.key; });
    state.fuelOrderTime = data.time;
  }
  var biggest = Math.max.apply(null, rows.map(catTotal).concat([1]));
  state.fuelOrder.forEach(function (key) {
    var c = category(data, key) || { key: key };
    var total = catTotal(c);
    if (total < 0.5 && key !== 'solar' && key !== 'wind' && !c.stations) return;
    var mapped = Math.min(total, Math.max(0, c.mapped || 0));
    var rest = Math.max(0, total - mapped);
    var note;
    if (key === 'solar') {
      note = c.stations ? 'Mostly rooftops and small farms; only the ' + plural(c.stations, 'large farm', 'large farms') + ' on the main grid are drawn.'
        : 'Mostly rooftops and small farms, which aren’t drawn individually.';
    } else {
      var parts = [];
      parts.push(c.stations ? 'From ' + plural(c.stations, 'station', 'stations') + ' on the map' : 'No stations generating');
      if (key === 'wind' && c.embedded >= 50) parts.push(gw(c.embedded) + ' GW from small wind farms not drawn');
      if (c.unplaced >= 50) parts.push(gw(c.unplaced) + ' GW from units without a location');
      note = parts.join('; ') + '.';
    }
    var shown = !state.hidden[key];
    var button = h('button', {
      type: 'button', cls: 'fuel-btn', 'aria-pressed': String(shown),
      'aria-label': NAMES[key] + ', ' + gw(total) + ' gigawatts. ' + (shown ? 'Shown on the map; select to hide.' : 'Hidden; select to show.'),
      onclick: function () {
        if (state.hidden[key]) delete state.hidden[key]; else state.hidden[key] = true;
        savePrefs();
        signatures.fuels = null;
        renderFuels(state.data);
        refreshStations();
        requestRedraw();
      },
      onmouseenter: function () { state.highlight = key; refreshStations(); requestRedraw(); },
      onmouseleave: function () { if (state.highlight) { state.highlight = null; refreshStations(); requestRedraw(); } }
    }, [
      h('span', { cls: 'sw b-' + key }),
      h('span', { cls: 'name', text: NAMES[key] }),
      h('span', { cls: 'val' }, [gw(total), h('small', { text: 'GW' })]),
      h('span', { cls: 'track', 'aria-hidden': 'true' }, [
        mapped > 0 ? h('i', { cls: 'b-' + key, style: 'width:' + (100 * mapped / biggest).toFixed(2) + '%' }) : null,
        rest > 0 ? h('i', { cls: 'rest b-' + key, style: 'width:' + (100 * rest / biggest).toFixed(2) + '%' }) : null
      ]),
      h('span', { cls: 'note', text: note })
    ]);
    host.appendChild(h('li', null, button));
  });
}

function countryGroups(links) {
  var groups = {}, order = [];
  links.forEach(function (link) {
    if (!groups[link.country]) { groups[link.country] = { country: link.country, mw: 0, links: [] }; order.push(link.country); }
    groups[link.country].mw += num(link.mw) ? link.mw : 0;
    groups[link.country].links.push(link);
  });
  return order.map(function (key) { return groups[key]; }).sort(function (a, b) { return b.mw - a.mw; });
}

function renderInterconnectors(data) {
  var host = clear(byId('ic'));
  var links = data.links || [];
  var net = netImports(data);
  byId('net').textContent = links.length ? (Math.abs(net) < 50 ? 'Balanced' : 'Net ' + gw(Math.abs(net)) + ' GW ' + (net > 0 ? 'in' : 'out')) : '';
  if (!links.length) { host.appendChild(h('li', { cls: 'empty', text: 'Waiting for interconnector data…' })); return; }
  countryGroups(links).forEach(function (group) {
    var v = Math.abs(group.mw) < 10 ? 'Idle' : gw(Math.abs(group.mw)) + ' GW ' + (group.mw > 0 ? 'in' : 'out');
    host.appendChild(h('li', null, [
      h('span', { cls: 'who', text: group.country }),
      h('span', { cls: 'fv', text: v }),
      h('span', { cls: 'cables', text: group.links.map(function (link) { return link.name + ' ' + linkWords(link); }).join(', ') })
    ]));
  });
}

function renderUnmapped(data) {
  var host = clear(byId('unmapped'));
  var wind = category(data, 'wind'), solar = category(data, 'solar');
  var model = data.model;
  var parts = [];
  if (solar && num(solar.embedded)) parts.push('solar (' + gw(solar.embedded) + ' GW)');
  if (wind && num(wind.embedded)) parts.push('small wind farms (' + gw(wind.embedded) + ' GW)');
  host.appendChild(h('p', { text: parts.length ? 'Power from thousands of small sites, such as ' + parts.join(' and ') + ', is only known as a national total, so it isn’t drawn.' : 'Waiting for data…' }));
  if (model && model.unplaced_count) {
    var unplaced = 0;
    (data.categories || []).forEach(function (c) { unplaced += c.unplaced || 0; });
    var p = h('p', null, [plural(model.unplaced_count, 'unit', 'units') + ' (' + gw(unplaced) + ' GW) aren’t matched to a location yet. ']);
    p.appendChild(h('a', { href: '/stations#unplaced-title', text: 'See the list', onclick: function (e) { e.preventDefault(); setView('stations', true); setTimeout(function () { byId('unplaced-title').scrollIntoView(); }, 50); } }));
    host.appendChild(p);
  }
}

function stationById(id) {
  var found = null;
  stationsOf(state.data).forEach(function (st) { if (st.id === id) found = st; });
  return found;
}

function renderDetail() {
  var host = byId('detail');
  var st = state.selected ? stationById(state.selected) : null;
  if (!st) { host.hidden = true; clear(host); return; }
  host.hidden = false;
  clear(host);
  host.className = 'detail';
  var load = loadOf(st);
  host.appendChild(h('div', { cls: 'detail-head' }, [
    h('div', null, [
      h('h2', { text: st.n }),
      h('p', { cls: 'meta' }, [h('span', { cls: 'sw b-' + st.c, style: 'margin-right:6px;vertical-align:-1px' }), NAMES[st.c] + ', ' + where(st)])
    ]),
    h('button', { type: 'button', cls: 'close', 'aria-label': 'Close station details', text: '×', onclick: function () { select(null); } })
  ]));
  var big = st.mw <= -1 ? [mw(-st.mw), h('small', { text: 'MW being stored' })] : [mw(Math.max(0, st.mw)), h('small', { text: 'MW now' })];
  host.appendChild(h('div', { cls: 'big' }, big));
  host.appendChild(h('p', { cls: 'cap', text: (st.cap ? (load === null ? '' : Math.round(load) + '% of its ' + mw(st.cap) + ' MW capacity. ') : '') +
    (st.replay ? 'Replaying ' + stamp(state.data.time) + '.' : st.on + ' of ' + plural(st.u, 'unit', 'units') + ' running' + (st.i ? ', ' + st.i + ' following grid instructions' : '') + '.') }));
  var box = h('div', { cls: 'chart-box' });
  host.appendChild(box);
  loadStationChart(st, box);
}

function loadStationChart(st, box) {
  var cached = state.stationHistory[st.id];
  var fresh = cached && Date.now() - cached.fetched < 60000;
  if (cached) drawStationChart(st, cached.data, box);
  else box.appendChild(h('p', { cls: 'empty', text: 'Loading the last 24 hours…' }));
  if (fresh) return;
  fetchText('/api/station?id=' + st.id).then(function (text) {
    var data = JSON.parse(text);
    state.stationHistory[st.id] = { fetched: Date.now(), data: data };
    if (box.isConnected) drawStationChart(st, data, box);
  }).catch(function () {
    if (box.isConnected && !cached) { clear(box); box.appendChild(h('p', { cls: 'empty', text: 'Couldn’t load this station’s history.' })); }
  });
}

function drawStationChart(st, history, box) {
  clear(box);
  var points = history.points || [];
  if (points.length < 2) {
    box.appendChild(h('p', { cls: 'empty', text: 'History builds up from when the dashboard started' + (history.since ? ' (' + hm(history.since) + ')' : '') + '. Check back in a few minutes.' }));
    return;
  }
  var width = Math.max(240, box.clientWidth || 300), height = 132;
  var padL = 34, padR = 8, padT = 10, padB = 20;
  var end = Math.floor(Date.now() / 1000), start = end - 86400;
  var values = points.map(function (p) { return p[1]; });
  var hi = Math.max(st.cap || 0, Math.max.apply(null, values), 10);
  var lo = Math.min(0, Math.min.apply(null, values));
  var step = niceStep(hi - lo, 3);
  hi = Math.ceil(hi / step) * step;
  lo = Math.floor(lo / step) * step;
  var x = function (t) { return padL + (t - start) / 86400 * (width - padL - padR); };
  var y = function (v) { return padT + (hi - v) / (hi - lo || 1) * (height - padT - padB); };
  var svg = s('svg', { viewBox: '0 0 ' + width + ' ' + height, height: height, role: 'img', 'aria-label': st.n + ' output over the last 24 hours' });
  for (var v = lo; v <= hi + 0.001; v += step) {
    svg.appendChild(s('line', { 'class': 'grid-line', x1: padL, x2: width - padR, y1: y(v).toFixed(1), y2: y(v).toFixed(1) }));
    svg.appendChild(s('text', { 'class': 'axis-text', x: padL - 6, y: (y(v) + 3.5).toFixed(1), 'text-anchor': 'end', text: v >= 1000 ? (v / 1000) + 'k' : String(v) }));
  }
  for (var hour = Math.ceil(start / 21600) * 21600; hour <= end; hour += 21600) {
    svg.appendChild(s('text', { 'class': 'axis-text', x: x(hour).toFixed(1), y: height - 4, 'text-anchor': 'middle', text: hm(hour) }));
  }
  var line = '', area = '', prev = null, segments = [];
  points.forEach(function (p) {
    if (p[0] < start) return;
    var gap = prev && p[0] - prev[0] > 900;
    if (!prev || gap) { if (line) segments.push(line); line = 'M'; } else line += 'L';
    line += x(p[0]).toFixed(1) + ' ' + y(p[1]).toFixed(1);
    prev = p;
  });
  if (line) segments.push(line);
  segments.forEach(function (d) {
    var first = d.slice(1).split('L')[0], last = d.split('L').pop().replace('M', '');
    area = d + 'L' + last.split(' ')[0] + ' ' + y(0).toFixed(1) + 'L' + first.split(' ')[0] + ' ' + y(0).toFixed(1) + 'Z';
    svg.appendChild(s('path', { d: area, 'class': 'area f-' + st.c }));
    svg.appendChild(s('path', { d: d, 'class': 'series k-' + st.c }));
  });
  if (st.cap) svg.appendChild(s('line', { x1: padL, x2: width - padR, y1: y(st.cap).toFixed(1), y2: y(st.cap).toFixed(1), stroke: 'var(--axis)', 'stroke-dasharray': '3 3' }));
  var cross = s('line', { 'class': 'xhair', y1: padT, y2: height - padB, visibility: 'hidden' });
  svg.appendChild(cross);
  box.appendChild(svg);
  var tip = makeTip(box);
  svg.addEventListener('pointermove', function (e) {
    var rect = svg.getBoundingClientRect();
    var px = (e.clientX - rect.left) * width / rect.width;
    var t = start + (px - padL) / (width - padL - padR) * 86400;
    var best = null;
    points.forEach(function (p) { if (!best || Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p; });
    if (!best || Math.abs(best[0] - t) > 1200) { tip.hide(); cross.setAttribute('visibility', 'hidden'); return; }
    cross.setAttribute('x1', x(best[0]).toFixed(1));
    cross.setAttribute('x2', x(best[0]).toFixed(1));
    cross.setAttribute('visibility', 'visible');
    tip.show(hm(best[0]), [{ cls: 'b-' + st.c, value: mw(best[1]) + ' MW', label: 'output' }], x(best[0]) * rect.width / width, 40);
  });
  svg.addEventListener('pointerleave', function () { tip.hide(); cross.setAttribute('visibility', 'hidden'); });
  if (history.since && history.since > start + 1800) {
    box.appendChild(h('p', { cls: 'empty', text: 'History starts at ' + hm(history.since) + ', when the dashboard began recording stations.' }));
  }
}

/* ---------- stations view ---------- */

function renderStationsView() {
  var data = state.data;
  if (!data) return;
  var model = data.model;
  byId('st-lede').textContent = model
    ? 'Every grid-connected power station and link to another country, with what each was doing at ' + hm(model.time || model.at) + '.'
    : 'Waiting for the first station data. This takes about a minute after the dashboard starts.';
  if (changed('nations', [data.time, data.stations && data.stations.length, model && model.at])) renderNations(data);
  if (changed('types', [data.time, data.categories, model && model.scale])) renderTypes(data);
  if (changed('links', [data.time, data.links])) renderLinksTable(data);
  renderStationFilters();
  renderStationsTable(data);
  if (changed('unplaced', [data.unplaced])) renderUnplaced(data);
  byId('method-model').textContent = model ? (plural(model.counts.units, 'unit', 'units') + ' are reporting; ' + model.counts.placed + ' of them are placed at ' +
    plural(stationsOf(data).length, 'station', 'stations') + ' on the map. ' + (model.reference_error ? 'The BM unit register couldn’t be loaded (' + model.reference_error + '), so units are matched by ID only.' : 'Unit names and types come from Elexon’s BM unit register (' + model.reference_units + ' units).')) : '';
}

function renderNations(data) {
  var host = clear(byId('nations'));
  var nations = ['Scotland', 'England', 'Wales'];
  var totals = {};
  nations.forEach(function (n) { totals[n] = { all: 0, offshore: 0, cats: {} }; });
  stationsOf(data).forEach(function (st) {
    var t = totals[st.na];
    if (!t || st.mw <= 0) return;
    t.all += st.mw;
    if (st.off) t.offshore += st.mw;
    t.cats[st.c] = (t.cats[st.c] || 0) + st.mw;
  });
  var biggest = Math.max.apply(null, nations.map(function (n) { return totals[n].all; }).concat([1]));
  var used = {};
  var tip = makeTip(byId('nations'));
  nations.forEach(function (n) {
    var t = totals[n];
    var bar = h('div', { cls: 'bar', role: 'img', 'aria-label': n + ': ' + CATS.filter(function (c) { return t.cats[c]; }).map(function (c) { return NAMES[c] + ' ' + gw(t.cats[c]) + ' GW'; }).join(', ') });
    CATS.forEach(function (c) {
      if (!t.cats[c]) return;
      used[c] = true;
      var seg = h('i', { cls: 'b-' + c, style: 'width:' + (100 * t.cats[c] / biggest).toFixed(2) + '%' });
      seg.addEventListener('pointermove', function (e) {
        var rect = byId('nations').getBoundingClientRect();
        tip.show(n + ': ' + NAMES[c], [{ cls: 'b-' + c, value: gw(t.cats[c]) + ' GW', label: Math.round(100 * t.cats[c] / t.all) + '% of ' + n + '’s mapped output' }], e.clientX - rect.left, e.clientY - rect.top);
      });
      seg.addEventListener('pointerleave', function () { tip.hide(); });
      bar.appendChild(seg);
    });
    host.appendChild(h('div', { cls: 'nation' }, [
      h('div', { cls: 'label' }, [n, h('small', { text: t.offshore >= 1 ? gw(t.offshore) + ' GW offshore' : '' })]),
      bar,
      h('div', { cls: 'total', text: gw(t.all) + ' GW' })
    ]));
  });
  var legend = clear(byId('nations-legend'));
  CATS.forEach(function (c) { if (used[c]) legend.appendChild(h('li', null, [h('span', { cls: 'sw b-' + c }), NAMES[c]])); });
}

function renderTypes(data) {
  var table = clear(byId('types-table'));
  var scale = (data.model && data.model.scale) || {};
  var scaleFor = { wind: ['WIND'], nuclear: ['NUCLEAR'], gas: ['CCGT', 'OCGT'], biomass: ['BIOMASS'], hydro: ['NPSHYD'], storage: ['PS', 'BATTERY', 'BESS'], other: ['OTHER', 'COAL', 'OIL'] };
  table.appendChild(h('thead', null, h('tr', null, [
    h('th', { text: 'Type' }), h('th', { cls: 'num', text: 'Now (GW)' }), h('th', { cls: 'num', text: 'On the map' }),
    h('th', { cls: 'num hide-sm', text: 'Stations' }), h('th', { cls: 'num hide-sm', text: 'Model scale' })
  ])));
  var body = h('tbody');
  var sum = 0, mapped = 0;
  CATS.forEach(function (key) {
    var c = category(data, key);
    if (!c) return;
    var total = catTotal(c);
    sum += total;
    mapped += Math.max(0, c.mapped || 0);
    var factors = (scaleFor[key] || []).map(function (code) { return scale[code]; }).filter(num);
    var factorText = factors.length ? '×' + factors.map(function (f) { return f.toFixed(2); }).join(', ×') : '–';
    body.appendChild(h('tr', null, [
      h('td', null, h('span', { cls: 'typ' }, [h('span', { cls: 'sw b-' + key }), NAMES[key]])),
      h('td', { cls: 'num', text: gw(total) }),
      h('td', { cls: 'num', text: total > 0 ? Math.round(100 * Math.min(total, c.mapped || 0) / total) + '%' : '–' }),
      h('td', { cls: 'num hide-sm', text: c.stations ? String(c.stations) : '–' }),
      h('td', { cls: 'num hide-sm muted', text: factorText })
    ]));
  });
  table.appendChild(body);
  table.appendChild(h('tfoot', null, h('tr', null, [
    h('td', { text: 'Total generated in GB' }), h('td', { cls: 'num', text: gw(sum) }),
    h('td', { cls: 'num', text: sum ? Math.round(100 * mapped / sum) + '%' : '–' }), h('td', { cls: 'hide-sm' }), h('td', { cls: 'hide-sm' })
  ])));
}

function renderLinksTable(data) {
  var table = clear(byId('links-table'));
  var links = (data.links || []).slice().sort(function (a, b) { return (b.mw || 0) - (a.mw || 0); });
  var net = netImports(data);
  byId('links-sub').textContent = 'Cables to other countries, measured. ' + (links.length ? (Math.abs(net) < 50 ? 'Imports and exports roughly balance.' : 'Net ' + gw(Math.abs(net)) + ' GW ' + (net > 0 ? 'into' : 'out of') + ' Great Britain.') : '') +
    ' Bars run right for imports and left for exports, against each cable’s capacity.';
  table.appendChild(h('thead', null, h('tr', null, [
    h('th', { text: 'Link' }), h('th', { cls: 'num', text: 'Flow' }), h('th', { cls: 'num hide-sm', text: 'Out and in' })
  ])));
  var body = h('tbody');
  links.forEach(function (link) {
    var share = link.cap && num(link.mw) ? Math.min(1, Math.abs(link.mw) / link.cap) : 0;
    var bar = h('span', { cls: 'diverge', 'aria-hidden': 'true' }, [h('i', { cls: (link.mw > 0 ? 'in' : 'out') + ' b-imports', style: 'width:' + (50 * share).toFixed(1) + '%' })]);
    body.appendChild(h('tr', null, [
      h('td', null, [
        h('span', { cls: 'name', text: link.name }), h('span', { cls: 'tag', text: link.country }),
        h('span', { cls: 'route', text: link.gb && link.far ? link.gb[2] + ' to ' + link.far[2] + (link.cap ? ', ' + gw(link.cap) + ' GW' : '') : (link.note || '') })
      ]),
      h('td', { cls: 'num', text: linkWords(link) }),
      h('td', { cls: 'num hide-sm' }, bar)
    ]));
  });
  table.appendChild(body);
}

function renderStationFilters() {
  var nationHost = byId('nation-filter');
  var options = [['all', 'All'], ['Scotland', 'Scotland'], ['England', 'England'], ['Wales', 'Wales'], ['offshore', 'Offshore']];
  if (!nationHost.childNodes.length) {
    options.forEach(function (o) {
      nationHost.appendChild(h('button', { type: 'button', text: o[1], 'data-v': o[0], onclick: function () { state.nation = o[0]; state.showAll = false; renderStationsTable(state.data); renderStationFilters(); } }));
    });
  }
  Array.prototype.forEach.call(nationHost.childNodes, function (b) { b.setAttribute('aria-pressed', String(b.getAttribute('data-v') === state.nation)); });
  var chips = byId('fuel-chips');
  if (!chips.childNodes.length) {
    CATS.forEach(function (c) {
      chips.appendChild(h('button', { type: 'button', cls: 'chip', 'data-c': c, onclick: function () {
        if (state.cats[c]) delete state.cats[c]; else state.cats[c] = true;
        state.showAll = false;
        renderStationFilters();
        renderStationsTable(state.data);
      } }, [h('span', { cls: 'sw b-' + c }), NAMES[c]]));
    });
  }
  var any = Object.keys(state.cats).length > 0;
  Array.prototype.forEach.call(chips.childNodes, function (b) { b.setAttribute('aria-pressed', String(any ? Boolean(state.cats[b.getAttribute('data-c')]) : false)); });
}

var COLUMNS = [
  { key: 'n', label: 'Station', cls: '' },
  { key: 'c', label: 'Type', cls: 'hide-sm' },
  { key: 'na', label: 'Where', cls: 'hide-sm' },
  { key: 'mw', label: 'Output (MW)', cls: 'num' },
  { key: 'cap', label: 'Capacity (MW)', cls: 'num hide-sm' },
  { key: 'load', label: 'Running at', cls: 'num' }
];

function filteredStations(data) {
  var q = state.query.trim().toLowerCase();
  var anyCat = Object.keys(state.cats).length > 0;
  return stationsOf(data).filter(function (st) {
    if (!state.includeIdle && isIdle(st)) return false;
    if (anyCat && !state.cats[st.c]) return false;
    if (state.nation === 'offshore' && !st.off) return false;
    if (state.nation !== 'all' && state.nation !== 'offshore' && st.na !== state.nation) return false;
    if (q && st.n.toLowerCase().indexOf(q) < 0) return false;
    return true;
  });
}

function sortValue(st, key) {
  if (key === 'load') return loadOf(st) === null ? -1 : loadOf(st);
  if (key === 'c') return NAMES[st.c] || st.c;
  if (key === 'na') return where(st);
  if (key === 'cap') return st.cap || 0;
  return st[key];
}

function renderStationsTable(data) {
  if (!data) return;
  var table = clear(byId('stations-table'));
  var rows = filteredStations(data);
  var key = state.sort.key, dir = state.sort.dir;
  rows.sort(function (a, b) {
    var va = sortValue(a, key), vb = sortValue(b, key);
    if (typeof va === 'string') return dir * va.localeCompare(vb);
    return dir * (va - vb) || a.n.localeCompare(b.n);
  });
  var total = rows.length, limit = state.showAll ? rows.length : 60;
  byId('st-count').textContent = plural(total, 'station', 'stations') + (state.includeIdle ? '' : ' generating') + (total > limit ? ', showing the top ' + limit : '');
  var more = byId('more-btn');
  more.hidden = total <= limit && !state.showAll;
  more.textContent = state.showAll ? 'Show fewer' : 'Show all ' + total;
  var head = h('tr');
  COLUMNS.forEach(function (col) {
    var active = col.key === key;
    var th = h('th', { cls: col.cls, 'aria-sort': active ? (dir > 0 ? 'ascending' : 'descending') : null }, h('button', {
      type: 'button', cls: 'sort-btn', onclick: function () {
        if (state.sort.key === col.key) state.sort.dir = -state.sort.dir;
        else state.sort = { key: col.key, dir: (col.key === 'n' || col.key === 'c' || col.key === 'na') ? 1 : -1 };
        renderStationsTable(state.data);
      }
    }, [col.label, active ? (dir > 0 ? ' ↑' : ' ↓') : '']));
    head.appendChild(th);
  });
  table.appendChild(h('thead', null, head));
  var body = h('tbody');
  if (!rows.length) body.appendChild(h('tr', null, h('td', { colspan: COLUMNS.length, cls: 'muted', text: stationsOf(data).length ? 'No stations match.' : 'Waiting for station data…' })));
  rows.slice(0, limit).forEach(function (st) {
    var load = loadOf(st);
    var open = state.openRow === st.id;
    var tr = h('tr', { cls: 'row' + (open ? ' open' : ''), tabindex: '0', 'aria-expanded': String(open), onclick: function () { toggleRow(st.id); },
      onkeydown: function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleRow(st.id); } } }, [
      h('td', null, [h('span', { cls: 'name', text: st.n }), st.off ? h('span', { cls: 'tag', text: 'Offshore' }) : null, st.i ? h('span', { cls: 'tag', text: 'Instructed' }) : null]),
      h('td', { cls: 'hide-sm' }, h('span', { cls: 'typ' }, [h('span', { cls: 'sw b-' + st.c }), NAMES[st.c]])),
      h('td', { cls: 'hide-sm muted', text: st.na }),
      h('td', { cls: 'num', text: st.mw <= -1 ? '−' + mw(-st.mw) : mw(Math.max(0, st.mw)) }),
      h('td', { cls: 'num hide-sm muted', text: st.cap ? mw(st.cap) : '–' }),
      h('td', { cls: 'num' }, load === null ? (st.mw <= -1 ? 'Storing' : '–') : [h('span', { cls: 'load', 'aria-hidden': 'true' }, h('i', { cls: 'b-' + st.c, style: 'width:' + load.toFixed(1) + '%' })), Math.round(load) + '%'])
    ]);
    body.appendChild(tr);
    if (open) {
      var cell = h('td', { colspan: COLUMNS.length });
      var box = h('div', { cls: 'chart-box' });
      cell.appendChild(h('p', { cls: 'cap', style: 'margin:8px 0 4px;font-size:13px;color:var(--ink-2)', text: NAMES[st.c] + ', ' + where(st) + '. ' + st.on + ' of ' + plural(st.u, 'unit', 'units') + ' running' + (st.i ? ', ' + st.i + ' following grid instructions' : '') + '. Last 24 hours:' }));
      cell.appendChild(box);
      body.appendChild(h('tr', { cls: 'detail-row' }, cell));
      loadStationChart(st, box);
    }
  });
  table.appendChild(body);
}

function toggleRow(id) {
  state.openRow = state.openRow === id ? null : id;
  renderStationsTable(state.data);
}

function renderUnplaced(data) {
  var table = clear(byId('unplaced-table'));
  var list = data.unplaced || [];
  var count = (data.model && data.model.unplaced_count) || 0;
  byId('unplaced-sub').textContent = count
    ? plural(count, 'unit is', 'units are') + ' generating or storing without a known location. Their output still counts in the totals. The dashboard log lists the largest once a day.'
    : 'Every unit that’s running is matched to a station on the map.';
  if (!list.length) return;
  table.appendChild(h('thead', null, h('tr', null, [h('th', { text: 'Unit' }), h('th', { text: 'Registered to' }), h('th', { cls: 'hide-sm', text: 'Type' }), h('th', { cls: 'num', text: 'MW' })])));
  var body = h('tbody');
  list.forEach(function (u) {
    body.appendChild(h('tr', null, [
      h('td', { cls: 'name', text: u.unit }), h('td', { cls: 'muted', text: u.name || '–' }),
      h('td', { cls: 'hide-sm' }, h('span', { cls: 'typ' }, [h('span', { cls: 'sw b-' + u.cat }), NAMES[u.cat] || u.cat])),
      h('td', { cls: 'num', text: u.mw < 0 ? '−' + mw(-u.mw) : mw(u.mw) })
    ]));
  });
  table.appendChild(body);
}

byId('search').addEventListener('input', function (e) { state.query = e.target.value; state.showAll = false; renderStationsTable(state.data); });
byId('idle-filter').addEventListener('change', function (e) { state.includeIdle = e.target.checked; renderStationsTable(state.data); });
byId('more-btn').addEventListener('click', function () { state.showAll = !state.showAll; renderStationsTable(state.data); });
byId('idle-toggle').addEventListener('change', function (e) { state.showIdle = e.target.checked; savePrefs(); refreshStations(); requestRedraw(); });
byId('how-btn').addEventListener('click', function (e) {
  var open = e.currentTarget.getAttribute('aria-expanded') !== 'true';
  e.currentTarget.setAttribute('aria-expanded', String(open));
  byId('how').hidden = !open;
});

/* ---------- time machine ---------- */

var R = { data: null, loading: false, fetched: 0, index: null, playing: false, speed: 0, raf: 0, last: 0, acc: 0, drawn: 0, error: null, pending: null };
/* Frames are ten minutes apart, so 6 frames a second plays an hour a second */
var SPEEDS = [[6, '1 h/s', '1 hour'], [24, '4 h/s', '4 hours'], [72, '12 h/s', '12 hours']];
var PLAY_ICON = '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3.5 2.2v9.6L11.6 7z" fill="currentColor"/></svg>';
var PAUSE_ICON = '<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3.4 2.5h2.5v9H3.4zM8.1 2.5h2.5v9H8.1z" fill="currentColor"/></svg>';
var weekdayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'short', timeZone: TZ });
var weekdayNarrow = new Intl.DateTimeFormat('en-GB', { weekday: 'narrow', timeZone: TZ });

function stamp(t) { return G.when(t); }

function replayDomain() {
  if (R.data && R.data.t.length > 1) return { t0: R.data.t[0], t1: R.data.t[R.data.t.length - 1] };
  var now = Math.floor(Date.now() / 1000);
  return { t0: now - 7 * 86400, t1: now };
}

function loadReplay(then) {
  if (R.data && Date.now() - R.fetched < 10 * 60 * 1000) { then(); return; }
  R.pending = then;
  if (R.loading) return;
  R.loading = true;
  renderBar();
  fetchText('/api/replay').then(function (text) {
    R.data = JSON.parse(text);
    R.fetched = Date.now();
    R.error = null;
    byId('tb-range').max = String(Math.max(0, R.data.t.length - 1));
    drawDays();
  }).catch(function () {
    R.error = 'Couldn’t load the history. Try again in a moment.';
  }).then(function () {
    R.loading = false;
    var next = R.pending;
    R.pending = null;
    if (R.data && R.data.t.length > 1 && next) next();
    else if (R.data && R.data.t.length <= 1) R.error = 'History builds up as the dashboard runs. Check back in an hour.';
    renderBar();
  });
}

/* A map data object for one moment in the past, shaped like /api/map */
function frameData(i) {
  var live = state.live, d = R.data, t = d.t[i];
  var stations = [], mapped = {}, counts = {};
  (live.stations || []).forEach(function (st) {
    var series = d.stations[st.id];
    var v = series ? series[i] : null;
    if (v === null || v === undefined) return;
    var copy = {};
    Object.keys(st).forEach(function (k) { copy[k] = st[k]; });
    copy.mw = v;
    copy.i = 0;
    copy.replay = true;
    stations.push(copy);
    if (v > 0) mapped[st.c] = (mapped[st.c] || 0) + v;
    if (v >= 1) counts[st.c] = (counts[st.c] || 0) + 1;
  });
  stations.sort(function (a, b) { return Math.abs(b.mw) - Math.abs(a.mw); });
  var links = (live.links || []).map(function (link) {
    var copy = {};
    Object.keys(link).forEach(function (k) { copy[k] = link[k]; });
    var series = d.links[link.code];
    copy.mw = series ? series[i] : null;
    return copy;
  });
  function total(key) { var series = d.totals[key]; return series ? series[i] : null; }
  var measured = { wind: total('wind'), solar: num(total('solar')) ? 0 : null, nuclear: total('nuclear'), gas: total('gas'),
    biomass: total('biomass'), hydro: total('hydro'), storage: total('storage'), other: total('other') };
  var embedded = { wind: total('wind_embedded'), solar: total('solar') };
  var categories = CATS.map(function (key) {
    return { key: key, label: NAMES[key], measured: measured[key], embedded: num(embedded[key]) ? embedded[key] : null,
      mapped: mapped[key] || 0, unplaced: null, stations: counts[key] || 0 };
  });
  var demand = total('demand'), exports = total('exports');
  return {
    replay: true, time: t, status: live.status, refresh_seconds: live.refresh_seconds,
    model: { basis: 'replay', at: t, time: t, period: null, scale: {}, counts: { units: 0, placed: 0, instructed: 0 }, unplaced_count: 0 },
    stations: stations, links: links, categories: categories, unplaced: [],
    demand_gw: num(demand) ? demand / 1000 : null, exports_gw: num(exports) ? Math.abs(exports) / 1000 : null
  };
}

function showFrame(i) {
  if (!R.data || R.data.t.length < 2 || !state.live) return;
  R.index = clamp(Math.round(i), 0, R.data.t.length - 1);
  state.data = frameData(R.index);
  render();
  renderBar();
}

function goLive(quiet) {
  stopPlay();
  R.index = null;
  if (state.live) {
    state.data = state.live;
    if (!quiet) render();
  }
  renderBar();
}

function startPlay() {
  loadReplay(function () {
    var last = R.data.t.length - 1;
    if (R.index === null || R.index >= last) showFrame(Math.max(0, last - Math.round(86400 / R.data.step)));
    R.playing = true;
    R.last = 0;
    R.acc = 0;
    R.raf = requestAnimationFrame(playStep);
    renderBar();
  });
}
function stopPlay() {
  if (!R.playing) return;
  R.playing = false;
  cancelAnimationFrame(R.raf);
  R.raf = 0;
  renderBar();
}
function togglePlay() { if (R.playing) stopPlay(); else startPlay(); }
function playStep(now) {
  if (!R.playing) return;
  if (R.last) R.acc += (now - R.last) / 1000 * SPEEDS[R.speed][0];
  R.last = now;
  if (R.acc >= 1 && now - R.drawn >= 70) {
    var steps = Math.floor(R.acc);
    R.acc -= steps;
    R.drawn = now;
    if (R.index + steps >= R.data.t.length - 1) { goLive(); return; }
    showFrame(R.index + steps);
  }
  R.raf = requestAnimationFrame(playStep);
}
function stepFrame(delta) {
  loadReplay(function () {
    stopPlay();
    showFrame((R.index === null ? R.data.t.length - 1 : R.index) + delta);
  });
}

function renderBar() {
  var live = R.index === null;
  var play = byId('tb-play');
  play.innerHTML = R.playing ? PAUSE_ICON : PLAY_ICON;
  play.setAttribute('aria-label', R.playing ? 'Pause' : live ? 'Play the last day' : 'Play from here');
  var speed = byId('tb-speed');
  speed.textContent = SPEEDS[R.speed][1];
  speed.setAttribute('aria-label', 'Playback speed: ' + SPEEDS[R.speed][2] + ' of history a second. Select to change.');
  var liveBtn = clear(byId('tb-live'));
  liveBtn.setAttribute('aria-pressed', String(live));
  liveBtn.appendChild(h('span', { cls: live ? 'dot good' : 'dot' }));
  if (!live) liveBtn.appendChild(h('span', { cls: 'tb-back', text: 'Back to ' }));
  liveBtn.appendChild(document.createTextNode(live ? 'Live' : 'live'));
  var range = byId('tb-range');
  range.value = live ? range.max : String(R.index);
  range.setAttribute('aria-valuetext', live ? 'Live' : stamp(R.data.t[R.index]));
  var pill = clear(byId('replay-when'));
  var message = R.loading ? 'Loading the stored history…' : R.error;
  if (!live) {
    pill.appendChild(document.createTextNode(stamp(R.data.t[R.index])));
    pill.appendChild(h('small', { text: R.playing ? 'playing' : 'replay' }));
  } else if (message) {
    pill.appendChild(document.createTextNode(message));
  }
  pill.hidden = live && !message;
}

function drawDays() {
  var host = clear(byId('tb-days'));
  var dom = replayDomain(), span = dom.t1 - dom.t0;
  if (span <= 0) return;
  var narrow = host.clientWidth / (span / 86400) < 46;
  for (var t = Math.ceil(dom.t0 / 3600) * 3600; t <= dom.t1; t += 3600) {
    if (hm(t) !== '00:00') continue;
    var pct = 100 * (t - dom.t0) / span;
    if (pct > 2 && pct < 95) host.appendChild(h('span', { style: 'left:' + pct.toFixed(2) + '%', text: (narrow ? weekdayNarrow : weekdayFmt).format(new Date(t * 1000)) }));
  }
}

(function setupTimeMachine() {
  var range = byId('tb-range');
  range.addEventListener('pointerdown', function () { stopPlay(); loadReplay(function () {}); });
  range.addEventListener('input', function () {
    var value = +range.value;
    stopPlay();
    loadReplay(function () {
      if (R.index === null && value >= R.data.t.length - 1) return;
      showFrame(Math.min(+range.value, R.data.t.length - 1));
    });
  });
  byId('tb-play').addEventListener('click', togglePlay);
  byId('tb-speed').addEventListener('click', function () { R.speed = (R.speed + 1) % SPEEDS.length; renderBar(); });
  byId('tb-live').addEventListener('click', function () { if (R.index !== null) goLive(); });
  renderBar();
  drawDays();
})();

/* ---------- theme ---------- */

window.addEventListener('gb:theme', function () { signatures = {}; render(); });

var lastWidth = 0, resizeTimer = null;
window.addEventListener('resize', function () {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function () {
    var width = document.documentElement.clientWidth;
    if (Math.abs(width - lastWidth) < 8) return;
    lastWidth = width;
    signatures = {};
    render();
  }, 160);
});

initMap();
setView(viewFromPath(), false);
tick();
